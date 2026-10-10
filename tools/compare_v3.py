#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Beacon 确定性对比 v3 — 以 3D 真值为锚。

对每张图纸（我方/参考）：
  1) 提取几何（SPLINE 采样为折线），排除标注/文字/图框后做稳健聚类
  2) 在所有簇×{0,90,180,270}°×自动比例 中搜索"主平面视图"：
     使真值孔位与图中圆的匹配数最大
  3) 逐孔判定：找到/缺失/多余（零件坐标，容差 0.05mm）
  4) 边界形状保真：曲线离散为点，双向距离覆盖率
  5) 尺寸值：DIMENSION 测量值（比例换算后）与真值距离集合核对

用法：python compare_v3.py <gt.json> <our.dxf> <ref.dxf>
"""
import sys, os, json, math
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import ezdxf

TOL = 0.05            # 零件坐标容差 mm
CLUSTER_GAP = 8.0     # 聚类合并距离（图纸单位）
SPLINE_SEG = 12       # 样条采样段数

# ── 真值 ─────────────────────────────────────────────────────

def load_gt(path):
    d = json.load(open(path, encoding="utf-8"))
    holes = []
    for f in d.get("features", []):
        if f.get("type") == "PIERCING":
            x, y = f["position"][0], f["position"][1]
            holes.append({"id": f["id"], "x": x, "y": y,
                          "r": f["radius"], "d": f["diameter"],
                          "axis": f.get("axis_dir"), "type": f.get("hole_type")})
    bb = d["bbox"]
    return {"holes": holes, "bb": bb,
            "W": bb["xmax"] - bb["xmin"], "H": bb["ymax"] - bb["ymin"]}

# ── 几何提取 ─────────────────────────────────────────────────

EXCLUDE_KINDS = {"DIMENSION", "TEXT", "MTEXT", "LEADER", "MULTILEADER",
                 "HATCH", "INSERT", "TOLERANCE", "ATTRIB"}

def extract_geom(path):
    """返回 primitives: list of dict
    line: (x1,y1,x2,y2); circle:(cx,cy,r); arc 采样为折线点; spline 采样。
    每个 primitive 带 points（用于聚类的锚）。
    """
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    prims = []
    dims = []
    for e in msp:
        k = e.dxftype()
        layer = e.dxf.get("layer", "0")
        if k in EXCLUDE_KINDS:
            if k == "DIMENSION":
                info = {"layer": layer, "text": e.dxf.text}
                try:
                    info["meas"] = float(e.get_measurement())
                except Exception:
                    info["meas"] = None
                for a in ("defpoint", "defpoint2", "defpoint3"):
                    v = e.dxf.get(a)
                    if v is not None:
                        info[a] = (v.x, v.y)
                dims.append(info)
            continue
        try:
            if k == "LINE":
                ln = (e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y)
                prims.append({"k": "L", "line": ln,
                              "pts": [(ln[0], ln[1]), (ln[2], ln[3]),
                                      ((ln[0]+ln[2])/2, (ln[1]+ln[3])/2)]})
            elif k == "CIRCLE":
                cx, cy, r = e.dxf.center.x, e.dxf.center.y, e.dxf.radius
                prims.append({"k": "C", "circle": (cx, cy, r),
                              "pts": [(cx, cy)]})
            elif k == "ARC":
                cx, cy, r = e.dxf.center.x, e.dxf.center.y, e.dxf.radius
                a0, a1 = e.dxf.start_angle, e.dxf.end_angle
                seg = max(4, int(abs(a1 - a0) / 15) + 2)
                pts = []
                for i in range(seg + 1):
                    a = math.radians(a0 + (a1 - a0) * i / seg)
                    pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
                prims.append({"k": "A", "arc": (cx, cy, r, a0, a1), "pts": pts})
            elif k == "SPLINE":
                try:
                    pts = [(p.x, p.y) for p in e.flattening(2.0, SPLINE_SEG)]
                except Exception:
                    pts = [(p.x, p.y) for p in e.control_points]
                if len(pts) >= 2:
                    prims.append({"k": "S", "pts": pts})
            elif k in ("LWPOLYLINE", "POLYLINE"):
                if k == "LWPOLYLINE":
                    pts = [(p[0], p[1]) for p in e.get_points()]
                else:
                    pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                if len(pts) >= 2:
                    prims.append({"k": "P", "pts": pts})
        except Exception as ex:
            print(f"  warn {k}: {ex}", file=sys.stderr)
    return prims, dims, doc

# ── 聚类（union-find，仅几何）────────────────────────────────

def cluster(prims):
    n = len(prims)
    parent = list(range(n))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[rb] = ra

    # 性能：按 bbox 分桶粗筛
    boxes = []
    for p in prims:
        xs = [q[0] for q in p["pts"]]; ys = [q[1] for q in p["pts"]]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    for i in range(n):
        for j in range(i+1, n):
            ax0, ay0, ax1, ay1 = boxes[i]
            bx0, by0, bx1, by1 = boxes[j]
            # bbox 距离粗判
            dx = max(0, max(ax0, bx0) - min(ax1, bx1))
            dy = max(0, max(ay0, by0) - min(ay1, by1))
            if math.hypot(dx, dy) > CLUSTER_GAP:
                continue
            # 精确：最近点
            best = 1e9
            pi, pj = prims[i]["pts"], prims[j]["pts"]
            # 采样上限
            A = pi if len(pi) <= 20 else [pi[k] for k in range(0, len(pi), max(1, len(pi)//20))]
            B = pj if len(pj) <= 20 else [pj[k] for k in range(0, len(pj), max(1, len(pj)//20))]
            hit = False
            for a in A:
                for b in B:
                    d = math.hypot(a[0]-b[0], a[1]-b[1])
                    if d < CLUSTER_GAP:
                        hit = True; break
                if hit: break
            if hit: union(i, j)

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(prims[i])
    clusters = list(groups.values())

    # 识别并剔除图框簇：实体多为4条直线、bbox 宽高 > 600
    keep = []
    for cl in clusters:
        xs = [pt[0] for p in cl for pt in p["pts"]]
        ys = [pt[1] for p in cl for pt in p["pts"]]
        w, h = max(xs)-min(xs), max(ys)-min(ys)
        nlines = sum(1 for p in cl if p["k"] == "L")
        if (w > 700 or h > 700) and nlines <= 8:
            continue  # sheet frame
        keep.append(cl)
    return keep

def cluster_bbox(cl):
    xs = [pt[0] for p in cl for pt in p["pts"]]
    ys = [pt[1] for p in cl for pt in p["pts"]]
    return min(xs), min(ys), max(xs), max(ys)

# ── 主视图搜索（真值孔位匹配）────────────────────────────────

def circles_of(cl):
    return [p["circle"] for p in cl if p["k"] == "C"]

def find_main_view(clusters, gt):
    """对每个簇尝试 4 旋转 × 比例（由bbox推断），返回最佳匹配信息。

    变换：paper = T(ox,oy) ∘ R(θ) ∘ S(s) ∘ part
    """
    holes = gt["holes"]
    best = None
    for cl in clusters:
        x0, y0, x1, y1 = cluster_bbox(cl)
        w, h = x1-x0, y1-y0
        circles = circles_of(cl)
        if len(circles) < 3:
            continue
        for theta in (0, 90, 180, 270):
            rad = math.radians(theta)
            cosT, sinT = math.cos(rad), math.sin(rad)
            # 比例候选：bbox 两个方向与 GT W/H 之比
            if theta in (0, 180):
                s_guess = w / gt["W"]
                s2 = h / gt["H"]
            else:
                s_guess = w / gt["H"]
                s2 = h / gt["W"]
            for s in (s_guess, (s_guess + s2)/2):
                if s <= 0: continue
                # GT 孔 → paper 坐标
                def to_paper(hx, hy):
                    return (x0 + s*(cosT*hx - sinT*hy),
                            y0 + s*(sinT*hx + cosT*hy))
                # 匹配（纸面容差 = s*TOL）
                ptol = s * TOL + 0.02
                used = [False]*len(circles)
                matches = []
                for hh in holes:
                    px, py = to_paper(hh["x"], hh["y"])
                    bestd, bi = 1e9, -1
                    for ci, (ccx, ccy, cr) in enumerate(circles):
                        if used[ci]: continue
                        d = math.hypot(px-ccx, py-ccy)
                        if d < bestd:
                            bestd, bi = d, ci
                    if bi >= 0 and bestd <= ptol:
                        used[bi] = True
                        # 半径也核对
                        r_paper = circles[bi][2]
                        r_err = abs(r_paper/s - hh["r"])
                        matches.append({"id": hh["id"], "err_mm": bestd/s,
                                        "r_err_mm": r_err,
                                        "r_ok": r_err <= TOL})
                score = len(matches)
                if best is None or score > best["score"]:
                    best = {"score": score, "cl": cl, "theta": theta, "s": s,
                            "bbox": (x0, y0, x1, y1), "matches": matches,
                            "circles": circles, "used": used,
                            "n_circles": len(circles)}
    return best

# ── 边界保真（点云双向覆盖率）────────────────────────────────

def cluster_point_cloud(cl, s):
    """簇内所有曲线 → paper 点 → 零件坐标（去平移/缩放/旋转由调用方？）

    这里直接在 paper 空间与"投影到 paper 的 GT 边界"比较；
    但 GT 无边界数据。改为：返回点云（paper），双方点云在各自主视图变换下
    统一换算回零件坐标比较。
    """
    pts = []
    for p in cl:
        pts.extend(p["pts"])
    return pts

def paper_to_part_factory(best, gt):
    x0, y0 = best["bbox"][0], best["bbox"][1]
    theta = math.radians(-best["theta"])
    s = best["s"]
    cosT, sinT = math.cos(theta), math.sin(theta)
    def f(px, py):
        u, v = (px-x0)/s, (py-y0)/s
        return (cosT*u - sinT*v, sinT*u + cosT*v)
    return f

def boundary_fidelity(best_a, best_b, label):
    """两张图主视图点云都换算到零件坐标，双向最近距离统计。"""
    fa = paper_to_part_factory(best_a, None)
    fb = paper_to_part_factory(best_b, None)
    A = [fa(*p) for p in cluster_point_cloud(best_a["cl"], best_a["s"])]
    B = [fb(*p) for p in cluster_point_cloud(best_b["cl"], best_b["s"])]
    # 网格化加速（5mm 栅格）
    def cover(src, dst, tol):
        cell = tol
        grid = defaultdict(list)
        for p in dst:
            grid[(int(p[0]//cell), int(p[1]//cell))].append(p)
        hit = 0; worst_missing = []
        for p in src:
            gx, gy = int(p[0]//cell), int(p[1]//cell)
            found = False
            for dxx in (-1, 0, 1):
                for dyy in (-1, 0, 1):
                    for q in grid.get((gx+dxx, gy+dyy), ()):
                        if math.hypot(p[0]-q[0], p[1]-q[1]) <= tol:
                            found = True; break
                    if found: break
                if found: break
            if found: hit += 1
            else: worst_missing.append(p)
        return hit/len(src) if src else 1.0, worst_missing
    cov_ab, miss_ab = cover(A, B, 0.3)
    cov_ba, miss_ba = cover(B, A, 0.3)
    return {"A_in_B": round(cov_ab, 4), "B_in_A": round(cov_ba, 4),
            "n_points": (len(A), len(B)),
            "n_missing_in_ref": len(miss_ab), "n_missing_in_ours": len(miss_ba)}

# ── 尺寸值核对 ───────────────────────────────────────────────

def analyze_dims(dims, best):
    """DIMENSION meas（paper）→ part 单位；统计并分类。"""
    s = best["s"]
    out = []
    for d in dims:
        if d.get("meas") is None: continue
        val = d["meas"] / s
        out.append(round(val, 2))
    return Counter(out), out

# ── 主流程 ───────────────────────────────────────────────────

def evaluate_drawing(label, path, gt):
    prims, dims, doc = extract_geom(path)
    cls = cluster(prims)
    main = find_main_view(cls, gt)
    result = {"label": label, "n_prims": len(prims),
              "n_clusters": len(cls), "n_dims_entities": len(dims)}
    if main is None:
        result["error"] = "no main view found"
        return result, None, dims
    result.update({
        "main_theta": main["theta"], "scale": round(main["s"], 4),
        "circles_in_main": main["n_circles"],
        "holes_matched": main["score"],
        "total_holes": len(gt["holes"]),
    })
    # 逐孔状态
    matched_ids = {m["id"] for m in main["matches"]}
    missing = [h["id"] for h in gt["holes"] if h["id"] not in matched_ids]
    r_bad = [m["id"] for m in main["matches"] if not m["r_ok"]]
    max_pos_err = max((m["err_mm"] for m in main["matches"]), default=0)
    result["missing_holes"] = missing
    result["radius_mismatch"] = r_bad
    result["max_pos_err_mm"] = round(max_pos_err, 3)
    result["hole_pass"] = len(missing) == 0 and len(r_bad) == 0
    return result, main, dims

def main():
    gt_path, our_path, ref_path = sys.argv[1], sys.argv[2], sys.argv[3]
    gt = load_gt(gt_path)
    print(f"GT: {len(gt['holes'])} holes, {gt['W']:.0f}x{gt['H']:.0f}")

    r_our, main_our, dims_our = evaluate_drawing("our", our_path, gt)
    r_ref, main_ref, dims_ref = evaluate_drawing("ref", ref_path, gt)

    print(json.dumps(r_our, ensure_ascii=False, indent=2))
    print(json.dumps(r_ref, ensure_ascii=False, indent=2))

    if main_our and main_ref:
        bf = boundary_fidelity(main_our, main_ref, "x")
        print("boundary:", json.dumps(bf))

    # 尺寸值
    if main_our:
        c_our, vals_our = analyze_dims(dims_our, main_our)
        print(f"our dimension values (n={len(vals_our)}): {sorted(vals_our)[:40]}")
    if main_ref:
        c_ref, vals_ref = analyze_dims(dims_ref, main_ref)
        print(f"ref dimension values (n={len(vals_ref)}): {sorted(vals_ref)[:40]}")
        # 共同值/差异
        if main_our:
            only_o = sorted(set(vals_our)-set(vals_ref))
            only_r = sorted(set(vals_ref)-set(vals_our))
            print(f"values only ours ({len(only_o)}): {only_o[:25]}")
            print(f"values only ref ({len(only_r)}): {only_r[:25]}")

    # 保存机读结果
    out = {"gt": gt_path, "our": r_our, "ref": r_ref}
    json.dump(out, open(os.path.join(os.path.dirname(our_path), "v3_result.json"), "w"),
              ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
