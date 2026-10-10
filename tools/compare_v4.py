#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Beacon 确定性对比 v4 — 真值为锚，多视图逐孔验证。

每张图纸：
  1) 聚类（几何；标注/文字/图框排除；<3 实体的碎片忽略）
  2) 全局比例 s：最大簇宽 / 真值242
  3) 簇按宽高比归类：flat(≈1.74) / side(≈.23) / edge(≈7.6)
  4) 每簇枚举轴映射(纸u,v ← 零件 X,Y,Z 组合 + 4方向)，
     按"该视图应可见的轴向孔→圆"匹配数选最优
  5) 汇总：34孔逐孔 PASS/FAIL（位置≤0.05mm，半径≤0.05mm）
  6) 边界点云保真 + 尺寸值(defpoint距离/s)集合对比
"""
import sys, os, json, math
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import ezdxf

TOL = 0.05
CLUSTER_GAP = 8.0

def load_gt(path):
    d = json.load(open(path, encoding="utf-8"))
    holes = []
    for f in d.get("features", []):
        if f.get("type") == "PIERCING":
            holes.append({"id": f["id"], "x": f["position"][0], "y": f["position"][1],
                          "z": f["position"][2], "r": f["radius"],
                          "axis": f.get("axis_dir"), "htype": f.get("hole_type")})
    bb = d["bbox"]
    return {"holes": holes, "bb": bb,
            "W": bb["xmax"]-bb["xmin"], "H": bb["ymax"]-bb["ymin"],
            "D": bb["zmax"]-bb["zmin"]}

EXCLUDE = {"DIMENSION","TEXT","MTEXT","LEADER","MULTILEADER","HATCH","INSERT","TOLERANCE","ATTRIB"}

def extract(path):
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    prims, dims = [], []
    for e in msp:
        k = e.dxftype()
        if k in EXCLUDE:
            if k == "DIMENSION":
                info = {"text": e.dxf.text}
                dp2, dp3 = e.dxf.get("defpoint2"), e.dxf.get("defpoint3")
                if dp2 and dp3:
                    info["paper"] = math.hypot(dp3.x-dp2.x, dp3.y-dp2.y)
                else:
                    info["paper"] = None
                dims.append(info)
            continue
        try:
            if k == "LINE":
                ln = (e.dxf.start.x,e.dxf.start.y,e.dxf.end.x,e.dxf.end.y)
                prims.append({"k":"L","line":ln,
                              "pts":[(ln[0],ln[1]),(ln[2],ln[3]),((ln[0]+ln[2])/2,(ln[1]+ln[3])/2)]})
            elif k == "CIRCLE":
                prims.append({"k":"C","circle":(e.dxf.center.x,e.dxf.center.y,e.dxf.radius),
                              "pts":[(e.dxf.center.x,e.dxf.center.y)]})
            elif k == "ARC":
                cx,cy,r = e.dxf.center.x,e.dxf.center.y,e.dxf.radius
                a0,a1 = e.dxf.start_angle,e.dxf.end_angle
                seg = max(4,int(abs(a1-a0)/15)+2)
                pts = [(cx+r*math.cos(math.radians(a0+(a1-a0)*i/seg)),
                        cy+r*math.sin(math.radians(a0+(a1-a0)*i/seg))) for i in range(seg+1)]
                prims.append({"k":"A","arc":(cx,cy,r,a0,a1),"pts":pts})
            elif k == "SPLINE":
                try: pts = [(p.x,p.y) for p in e.flattening(2.0,12)]
                except Exception: pts = [(p.x,p.y) for p in e.control_points]
                if len(pts)>=2: prims.append({"k":"S","pts":pts})
            elif k in ("LWPOLYLINE","POLYLINE"):
                pts = [(p[0],p[1]) for p in e.get_points()] if k=="LWPOLYLINE" else \
                      [(v.dxf.location.x,v.dxf.location.y) for v in e.vertices]
                if len(pts)>=2: prims.append({"k":"P","pts":pts})
        except Exception:
            pass
    return prims, dims

def cluster(prims):
    n = len(prims); parent = list(range(n))
    def find(x):
        while parent[x]!=x: parent[x]=parent[parent[x]]; x=parent[x]
        return x
    def union(a,b):
        ra,rb = find(a),find(b)
        if ra!=rb: parent[rb]=ra
    boxes = []
    for p in prims:
        xs=[q[0] for q in p["pts"]]; ys=[q[1] for q in p["pts"]]
        boxes.append((min(xs),min(ys),max(xs),max(ys)))
    for i in range(n):
        for j in range(i+1,n):
            ax0,ay0,ax1,ay1=boxes[i]; bx0,by0,bx1,by1=boxes[j]
            dx=max(0,max(ax0,bx0)-min(ax1,bx1)); dy=max(0,max(ay0,by0)-min(ay1,by1))
            if math.hypot(dx,dy)>CLUSTER_GAP: continue
            pi,pj = prims[i]["pts"],prims[j]["pts"]
            A = pi if len(pi)<=20 else [pi[k] for k in range(0,len(pi),max(1,len(pi)//20))]
            B = pj if len(pj)<=20 else [pj[k] for k in range(0,len(pj),max(1,len(pj)//20))]
            if any(math.hypot(a[0]-b[0],a[1]-b[1])<CLUSTER_GAP for a in A for b in B):
                union(i,j)
    groups = defaultdict(list)
    for i in range(n): groups[find(i)].append(prims[i])
    out = []
    for cl in groups.values():
        if len(cl) < 3: continue
        xs=[pt[0] for p in cl for pt in p["pts"]]; ys=[pt[1] for p in cl for pt in p["pts"]]
        w,h = max(xs)-min(xs),max(ys)-min(ys)
        # drop sheet-frame pieces
        if (w>700 or h>700) and sum(1 for p in cl if p["k"]=="L")<=8: continue
        out.append({"prims":cl,"bbox":(min(xs),min(ys),max(xs),max(ys)),"w":w,"h":h})
    return out

# ── 视图映射 ─────────────────────────────────────────────────

# 候选映射：纸(u,v) ← (partAxisU, partAxisV, signU, signV)，从簇 bbox 原点起算
MAPPINGS = [
    # flat: u=X v=Y
    ("flat", "X", 1, "Y", 1),
    ("flat", "X", 1, "Y", -1),
    ("flat", "X", -1, "Y", 1),
    ("flat", "X", -1, "Y", -1),
    # side: u=Y v=Z
    ("side", "Y", 1, "Z", 1),
    ("side", "Y", 1, "Z", -1),
    ("side", "Y", -1, "Z", 1),
    ("side", "Y", -1, "Z", -1),
    # edge: u=X v=Z
    ("edge", "X", 1, "Z", 1),
    ("edge", "X", 1, "Z", -1),
    ("edge", "X", -1, "Z", 1),
    ("edge", "X", -1, "Z", -1),
]
IDX = {"X":0,"Y":1,"Z":2}
# 真值各轴范围（用于换算 part→归一化0..1）
def ranges(gt):
    bb = gt["bb"]
    return {"X":(bb["xmin"],bb["xmax"]), "Y":(bb["ymin"],bb["ymax"]), "Z":(bb["zmin"],bb["zmax"])}

def fit_cluster(cluster_view, gt, s):
    """枚举映射，返回每个映射的孔圆匹配明细。"""
    x0,y0,x1,y1 = cluster_view["bbox"]
    circles = [p["circle"] for p in cluster_view["prims"] if p["k"]=="C"]
    R = ranges(gt)
    results = []
    for label, aU, sU, aV, sV in MAPPINGS:
        loU,hiU = R[aU]; loV,hiV = R[aV]
        def paper(h):
            nu = (h[aU]-loU)/(hiU-loU)
            nv = (h[aV]-loV)/(hiV-loV)
            if sU < 0: nu = 1-nu
            if sV < 0: nv = 1-nv
            return (x0+nu*cluster_view["w"], y0+nv*cluster_view["h"])
        used=[False]*len(circles)
        matches=[]
        # 该视图对应的孔轴：flat→Z, side→X, edge→Y
        view_axis = {"flat":"Z","side":"X","edge":"Y"}[label]
        cands = [h for h in gt["holes"] if h["axis"]==view_axis]
        ptol = s*TOL+0.02
        for h in cands:
            pu,pv = paper({"X":h["x"],"Y":h["y"],"Z":h["z"]})
            bestd,bi=1e9,-1
            for ci,(ccx,ccy,cr) in enumerate(circles):
                if used[ci]: continue
                d=math.hypot(pu-ccx,pv-ccy)
                if d<bestd: bestd,bi=d,ci
            if bi>=0 and bestd<=ptol:
                used[bi]=True
                r_err=abs(circles[bi][2]/s-h["r"])
                matches.append({"id":h["id"],"pos_err":round(bestd/s,3),
                                "r_err":round(r_err,3),"ok":r_err<=TOL})
        results.append({"label":label,"map":(aU,sU,aV,sV),"score":len(matches),
                        "n_cand":len(cands),"matches":matches})
    results.sort(key=lambda r:-r["score"])
    return results

# ── 评估整张图纸 ─────────────────────────────────────────────

def evaluate(label, path, gt):
    prims,dims = extract(path)
    cls = cluster(prims)
    cls.sort(key=lambda c:-(c["w"]*c["h"]))
    if not cls: return {"label":label,"error":"no clusters"}, None, dims
    # scale: widest cluster / GT W
    s = max(c["w"] for c in cls)/gt["W"]
    per_cluster = []
    for c in cls:
        fits = fit_cluster(c, gt, s)
        per_cluster.append({"view":c,"fits":fits})
    # 选每个簇的最佳映射；同一孔只在首个命中簇计
    hole_state = {}
    chosen = []
    for pc in per_cluster:
        best = pc["fits"][0]
        chosen.append({"kind":best["label"],"score":best["score"],"n_cand":best["n_cand"],
                       "bbox":(round(pc["view"]["w"],1),round(pc["view"]["h"],1))})
        for m in best["matches"]:
            if m["id"] not in hole_state:
                hole_state[m["id"]] = m
    missing = [h["id"] for h in gt["holes"] if h["id"] not in hole_state]
    rbad = [hid for hid,m in hole_state.items() if not m["ok"]]
    maxerr = max((m["pos_err"] for m in hole_state.values()), default=0)
    res = {"label":label,"scale":round(s,4),
           "n_dim_entities":len(dims),
           "clusters":chosen,
           "holes_ok":len(hole_state)-len(rbad),
           "missing":missing,"radius_bad":rbad,
           "max_pos_err":maxerr,"hole_pass":len(missing)==0 and len(rbad)==0}
    return res, {"clusters":per_cluster,"s":s}, dims

# ── 边界点云保真 ─────────────────────────────────────────────

def flat_cluster(ev):
    for pc in ev["clusters"]:
        if pc["fits"][0]["label"]=="flat" and pc["fits"][0]["score"]>=3:
            return pc["view"]
    return None

def to_unit_factory(view, gt):
    x0,y0,_,_ = view["bbox"]
    best = None
    # 找 flat 的映射参数（score 最高）
    return view

def boundary(ev_o, ev_r, gt):
    vo, vr = flat_cluster(ev_o), flat_cluster(ev_r)
    if not vo or not vr: return {"error":"flat view missing"}
    # 用 bbox 归一化比较（宽高都归一到1）
    def cloud(v):
        x0,y0,x1,y1 = v["bbox"]; w,h = x1-x0,y1-y0
        return [((x-x0)/w,(y-y0)/h) for p in v["prims"] for x,y in p["pts"]]
    A, B = cloud(vo), cloud(vr)
    # 归一化容差 0.3mm / 242 ≈ .00124
    tol = 0.0015
    grid = defaultdict(list)
    cell = tol
    for p in B: grid[(int(p[0]//cell),int(p[1]//cell))].append(p)
    def cover(src,dstgrid):
        hit=0
        for p in src:
            gx,gy=int(p[0]//cell),int(p[1]//cell); found=False
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    for q in dstgrid.get((gx+dx,gy+dy),()):
                        if math.hypot(p[0]-q[0],p[1]-q[1])<=tol: found=True;break
                    if found:break
                if found:break
            if found:hit+=1
        return hit/len(src)
    g2 = defaultdict(list)
    for p in A: g2[(int(p[0]//cell),int(p[1]//cell))].append(p)
    return {"our_covered_by_ref":round(cover(A,grid),4),
            "ref_covered_by_ours":round(cover(B,g2),4),
            "npts":(len(A),len(B))}

def main():
    gt = load_gt(sys.argv[1])
    print(f"GT {len(gt['holes'])} holes {gt['W']:.0f}x{gt['H']:.0f}x{gt['D']:.1f}")
    ro, evo, do_ = evaluate("our", sys.argv[2], gt)
    rr, evr, dr = evaluate("ref", sys.argv[3], gt)
    print(json.dumps(ro, ensure_ascii=False, indent=2))
    print(json.dumps(rr, ensure_ascii=False, indent=2))
    bf = boundary(evo, evr, gt)
    print("boundary:", json.dumps(bf))
    # dims
    so_ = sorted(round(d["paper"]/ro["scale"],2) for d in do_ if d["paper"] is not None)
    sr_ = sorted(round(d["paper"]/rr["scale"],2) for d in dr if d["paper"] is not None)
    print(f"our dim values n={len(so_)}: {so_}")
    print(f"ref dim values n={len(sr_)}: {sr_}")
    print("only ours:", sorted(set(so_)-set(sr_)))
    print("only ref:", sorted(set(sr_)-set(so_)))
    json.dump({"our":ro,"ref":rr,"boundary":bf},
              open(os.path.join(os.path.dirname(sys.argv[2]),"v4_result.json"),"w"),
              ensure_ascii=False, indent=2)

if __name__=="__main__":
    main()
