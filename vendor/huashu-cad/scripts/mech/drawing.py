"""回转类装配（轴线＝Z）出 A3 装配工程图 DXF：全剖主视图＋俯视图、齿轮简化画法、
尺寸（取自几何）、序号气泡、明细栏、齿轮参数表、标题栏、技术要求。

    python drawing.py 输出目录/assembly.json 输出目录/装配.step --out 装配图.dxf [--designer 名字] [--png 预览.png]

约定：主视图沿 XZ 平面全剖（从 -Y 看）；俯视图沿 -Z 看。齿轮在俯视图按 GB/T 4459.2 简化：
齿顶圆粗实线、分度圆点画线、齿根圆细实线。尺寸是原生 DIMENSION，数值来自几何，
DIMLFAC＝1/比例，让图面显示真实毫米。
"""
import argparse, json, math, datetime
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment
from build123d import import_step, Face, Plane, Keep, Compound, CenterOf, GeomType

A3 = (420, 297)
SCALES = [5, 4, 2.5, 2, 1.5, 1, 0.5, 0.4, 0.25, 0.2, 0.1, 0.05]
FONT = "Arial Unicode MS"


def scale_label(s):
    return f"{s:g}:1" if s >= 1 else f"1:{1 / s:g}"


def wire_pts(w, step=0.25):
    n = max(8, min(3000, int(w.length / step)))
    return [w.position_at(i / n) for i in range(n)]


class Sheet:
    def __init__(self, s):
        self.s = s
        self.doc = ezdxf.new("R2018", setup=True)
        self.doc.units = ezdxf.units.MM
        self.doc.styles.add("CN", font=FONT)
        for name, color, lw, lt in [("OUTLINE", 7, 50, "CONTINUOUS"), ("THIN", 8, 18, "CONTINUOUS"),
                                    ("CENTER", 1, 18, "CENTER"), ("HATCH", 8, 13, "CONTINUOUS"),
                                    ("DIM", 3, 18, "CONTINUOUS"), ("TEXT", 7, 25, "CONTINUOUS"),
                                    ("FRAME", 7, 50, "CONTINUOUS"), ("TABLE", 7, 25, "CONTINUOUS")]:
            self.doc.layers.add(name, color=color, lineweight=lw, linetype=lt)
        ds = self.doc.dimstyles.new("MECH")
        ds.dxf.dimtxsty = "CN"; ds.dxf.dimtxt = 3.0; ds.dxf.dimasz = 2.5; ds.dxf.dimexe = 1.5
        ds.dxf.dimexo = 1.0; ds.dxf.dimgap = 0.8; ds.dxf.dimdec = 1; ds.dxf.dimlfac = 1 / s
        ds.dxf.dimclrd = 3; ds.dxf.dimclre = 3; ds.dxf.dimclrt = 7; ds.dxf.dimtad = 1
        self.msp = self.doc.modelspace()

    def line(self, a, b, layer="OUTLINE"):
        self.msp.add_line(a, b, dxfattribs={"layer": layer})

    def poly(self, pts, layer="OUTLINE", closed=True):
        self.msp.add_lwpolyline(pts, close=closed, dxfattribs={"layer": layer})

    def text(self, s, p, h=3.5, align=TextEntityAlignment.MIDDLE_CENTER, layer="TEXT"):
        self.msp.add_text(s, height=h, dxfattribs={"layer": layer, "style": "CN"}).set_placement(p, align=align)

    def rect(self, x, y, w, h, layer="TABLE"):
        self.poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], layer)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("assembly_json"); ap.add_argument("step")
    ap.add_argument("--out", required=True); ap.add_argument("--designer", default="")
    ap.add_argument("--png")
    a = ap.parse_args()
    man = json.loads(Path(a.assembly_json).read_text())
    comp = import_step(a.step)
    solids = {c.label: c for c in comp.children}
    parts = []
    for k, p in enumerate(man["parts"], 1):
        sol = solids.get(p["code"])
        if sol is None:
            raise SystemExit(f"STEP 里找不到 {p['code']}（{p['name']}）")
        parts.append(dict(no=k, meta=p, solid=sol))

    bmin, bmax = man["bbox"]["min"], man["bbox"]["max"]
    wx, wy, wz = bmax[0] - bmin[0], bmax[1] - bmin[1], bmax[2] - bmin[2]
    s = next(v for v in SCALES if wx * v <= 165 and wz * v <= 150 and wy * v <= 150)
    sh = Sheet(s)
    X0, Y0 = 18 + 10, 20       # 图框内左下
    # 视图中心（纸面）
    fc = (X0 + 95, 175)        # 主视图
    tc = (X0 + 285, 185)       # 俯视图
    cx, cz, cy = (bmin[0] + bmax[0]) / 2, (bmin[2] + bmax[2]) / 2, (bmin[1] + bmax[1]) / 2
    F = lambda x, z: (fc[0] + (x - cx) * s, fc[1] + (z - cz) * s)
    T = lambda x, y: (tc[0] + (x - cx) * s, tc[1] + (y - cy) * s)

    # ---------- 主视图：XZ 全剖 ----------
    cut_plane = Face.make_rect(max(wx, wz) * 4, max(wx, wz) * 4, Plane.XZ)
    angles = [45, 135, 60, 120, 30, 150]
    face_center = {}
    for k, it in enumerate(parts):
        sec = it["solid"] & cut_plane
        faces = sec.faces() if sec is not None else []
        if not faces:
            continue
        best = max(faces, key=lambda f: f.area)
        c = best.center()
        face_center[it["no"]] = F(c.X, c.Z)
        hatch = sh.msp.add_hatch(dxfattribs={"layer": "HATCH"})
        for f in faces:
            outer = [F(p.X, p.Z) for p in wire_pts(f.outer_wire())]
            sh.poly(outer, "OUTLINE")
            hatch.paths.add_polyline_path(outer, is_closed=True, flags=ezdxf.const.BOUNDARY_PATH_EXTERNAL)
            for w in f.inner_wires():
                inner = [F(p.X, p.Z) for p in wire_pts(w)]
                sh.poly(inner, "OUTLINE")
                hatch.paths.add_polyline_path(inner, is_closed=True)
        hatch.set_pattern_fill("ANSI31", scale=max(0.5, 0.9 + 0.25 * (k % 3)), angle=angles[k % len(angles)] - 45)
        # 剖切面后方可见轮廓（非齿轮件；齿轮按惯例只画剖面）
        if not it["meta"].get("gear"):
            try:
                half = it["solid"].split(Plane.XZ, keep=Keep.BOTTOM)
                vis, _ = half.project_to_viewport((0, -1e4, 0), (0, 0, 1), (0, 0, 0))
                for e in vis:
                    pts = [e.position_at(i / 12) for i in range(13)] if e.geom_type != GeomType.LINE else [e.position_at(0), e.position_at(1)]
                    sh.poly([F(p.X, p.Y) for p in pts], "OUTLINE", closed=False)
            except Exception:
                pass
    # 轴线
    sh.line(F(0, bmin[2] - wz * .06), F(0, bmax[2] + wz * .06), "CENTER")
    sh.text("A-A", (fc[0], fc[1] + wz * s / 2 + 14), 5)

    # ---------- 俯视图 ----------
    for it in parts:
        g = it["meta"].get("gear")
        sol = it["solid"]
        com = sol.center(CenterOf.MASS)
        if g:
            m, z = g["module"], g["teeth"]
            mt = m / math.cos(math.radians(g.get("helix_deg", 0)))
            rp = mt * z / 2
            ra, rf = (rp - m, rp + 1.25 * m) if g.get("internal") else (rp + m, rp - 1.25 * m)
            c = T(com.X, com.Y)
            sh.msp.add_circle(c, ra * s, dxfattribs={"layer": "OUTLINE"})
            sh.msp.add_circle(c, rp * s, dxfattribs={"layer": "CENTER"})
            sh.msp.add_circle(c, rf * s, dxfattribs={"layer": "THIN"})
        vis, _ = sol.project_to_viewport((0, 0, 1e4), (0, 1, 0), (0, 0, 0))
        for e in vis:
            if g:   # 齿轮只保留齿形以外的圆（孔、轮毂、外壳外圆）
                if e.geom_type != GeomType.CIRCLE:
                    continue
                r = e.radius
                if g.get("internal") and r < (rp + 1.25 * m) * 1.02:
                    continue
                if not g.get("internal") and r > (rp - 1.25 * m) * 0.98:
                    continue
            if e.geom_type == GeomType.CIRCLE and abs(e.length - 2 * math.pi * e.radius) < 1e-3:
                cc = e.arc_center
                sh.msp.add_circle(T(cc.X, cc.Y), e.radius * s, dxfattribs={"layer": "OUTLINE"})
            else:
                n = 2 if e.geom_type == GeomType.LINE else 16
                sh.poly([T(*(lambda p: (p.X, p.Y))(e.position_at(i / (n - 1)))) for i in range(n)], "OUTLINE", closed=False)
    L = max(wx, wy) * .58
    sh.line(T(-L, 0), T(L, 0), "CENTER"); sh.line(T(0, -L), T(0, L), "CENTER")
    sh.text("俯视图（齿轮按简化画法）", (tc[0], tc[1] + wy * s / 2 + 12), 4)

    # ---------- 尺寸（取自几何） ----------
    d1 = sh.msp.add_linear_dim(base=(0, F(0, bmin[2])[1] - 12), p1=F(bmin[0], bmin[2]), p2=F(bmax[0], bmin[2]),
                               dimstyle="MECH", text="%%c<>", dxfattribs={"layer": "DIM"})
    d1.render()
    d2 = sh.msp.add_linear_dim(base=(F(bmin[0], 0)[0] - 14, 0), p1=F(bmin[0], bmin[2]), p2=F(bmin[0], bmax[2]),
                               angle=90, dimstyle="MECH", dxfattribs={"layer": "DIM"})
    d2.render()
    pl = man.get("planetary")
    if pl:
        byname = {it["meta"]["name"]: it for it in parts}
        sun, pla = byname[pl["sun"]], byname[pl["planets"][0]]
        c1, c2 = sun["solid"].center(CenterOf.MASS), pla["solid"].center(CenterOf.MASS)
        sh.msp.add_aligned_dim(p1=T(c1.X, c1.Y), p2=T(c2.X, c2.Y), distance=-14, dimstyle="MECH",
                               dxfattribs={"layer": "DIM"}).render()

    # ---------- 序号气泡 ----------
    left = sorted([(n, p) for n, p in face_center.items() if p[0] <= fc[0]], key=lambda t: -t[1][1])
    right = sorted([(n, p) for n, p in face_center.items() if p[0] > fc[0]], key=lambda t: -t[1][1])
    for side, items in ((-1, left), (1, right)):
        if not items:
            continue
        xb = fc[0] + side * (wx * s / 2 + 22)
        top = fc[1] + wz * s / 2 + 4
        gap = min(11, (wz * s + 8) / max(1, len(items)))
        for k, (n, p) in enumerate(items):
            yb = top - k * gap
            sh.line(p, (xb - side * 3.5, yb), "THIN")
            sh.msp.add_circle(p, 0.6, dxfattribs={"layer": "THIN"})
            sh.msp.add_circle((xb, yb), 3.5, dxfattribs={"layer": "THIN"})
            sh.text(str(n), (xb, yb), 3.2)

    missing = [it for it in parts if it["no"] not in face_center]
    for k, it in enumerate(missing):
        com = it["solid"].center(CenterOf.MASS)
        p = T(com.X, com.Y)
        ang = math.atan2(p[1] - tc[1], p[0] - tc[0]) if p != tc else math.pi / 4
        rb = max(wx, wy) * s / 2 + 12
        b = (tc[0] + rb * math.cos(ang), tc[1] + rb * math.sin(ang))
        sh.line(p, (b[0] - 3.5 * math.cos(ang), b[1] - 3.5 * math.sin(ang)), "THIN")
        sh.msp.add_circle(p, 0.6, dxfattribs={"layer": "THIN"})
        sh.msp.add_circle(b, 3.5, dxfattribs={"layer": "THIN"})
        sh.text(str(it["no"]), b, 3.2)

    # ---------- 图框、标题栏、明细栏 ----------
    sh.rect(0, 0, *A3, "FRAME"); sh.rect(25, 5, A3[0] - 30, A3[1] - 10, "FRAME")
    tbw, tbx, tby = 180, A3[0] - 5 - 180, 5
    sh.rect(tbx, tby, tbw, 32)
    for x in (tbx + 25, tbx + 70, tbx + 100, tbx + 140):
        sh.line((x, tby), (x, tby + 32), "TABLE")
    for y in (tby + 8, tby + 16, tby + 24):
        sh.line((tbx, y), (tbx + 100, y), "TABLE")
    sh.text(man["title"], (tbx + 140 + 20, tby + 22), min(5, 29 / max(1, len(man["title"]))))
    sh.text("装配图", (tbx + 140 + 20, tby + 10), 4)
    cells = [("比例", scale_label(s)), ("单位", "mm"), ("质量", f"{man['mass_total_g']:g} g"),
             ("日期", datetime.date.today().isoformat())]
    for k, (lab, val) in enumerate(cells):
        y = tby + 28 - k * 8
        sh.text(lab, (tbx + 12.5, y), 3); sh.text(val, (tbx + 47.5, y), 3)
    sh.text("设计", (tbx + 85, tby + 28), 3); sh.text(a.designer or "", (tbx + 120, tby + 28), 3)
    sh.text("审核", (tbx + 85, tby + 20), 3)
    sh.text("图号", (tbx + 85, tby + 12), 3); sh.text("ASM-001", (tbx + 120, tby + 12), 3)
    sh.text("共1张", (tbx + 85, tby + 4), 3); sh.text("第1张", (tbx + 120, tby + 4), 3)
    # 明细栏（自下而上）
    cols = [("序号", 12), ("代号", 18), ("名称", 50), ("数量", 12), ("材料", 50), ("质量g", 20), ("备注", 18)]
    rh, y = 7, tby + 32
    def row(vals, yy, bold=False):
        x = tbx
        for (lab, w), v in zip(cols, vals):
            sh.rect(x, yy, w, rh); sh.text(str(v), (x + w / 2, yy + rh / 2), 3 if not bold else 3.2); x += w
    row([c[0] for c in cols], y, True)
    for it in parts:
        y += rh
        p = it["meta"]
        row([it["no"], p["code"], p["name"], p["qty"], p["material_name"], f"{p['mass_g']:g}", ""], y)
    # 齿轮参数表
    gears = [it for it in parts if it["meta"].get("gear")]
    if gears:
        gx, gy = 32, A3[1] - 5 - 12
        gcols = [("齿轮", 46), ("模数m", 22), ("齿数z", 22), ("压力角", 22), ("类型", 38)]
        seen, rows = set(), []
        for it in gears:
            g = it["meta"]["gear"]
            key = (g["module"], g["teeth"], g.get("internal", False))
            if key in seen:
                continue
            seen.add(key)
            rows.append([it["meta"]["name"].rstrip("0123456789"), g["module"], g["teeth"], "20°",
                         ("内齿" if g.get("internal") else "外齿") + (f" 斜{g['helix_deg']}°" if g.get("helix_deg") else " 直齿")])
        for r_i, vals in enumerate([[c[0] for c in gcols]] + rows):
            x = gx
            for (lab, w), v in zip(gcols, vals):
                sh.rect(x, gy - r_i * 7, w, 7); sh.text(str(v), (x + w / 2, gy - r_i * 7 + 3.5), 3); x += w
    # 技术要求
    notes = ["技术要求", "1. 未注尺寸单位为 mm；尺寸取自三维模型几何。",
             "2. 齿形为渐开线；装配干涉、啮合中心距、行星装配条件已由脚本校验，见验证报告。",
             "3. 强度、公差配合、表面处理与制造工艺未校核，不作为加工图使用。"]
    if pl and pl.get("ratio"):
        notes.insert(1, "0. 传动比 " + " × ".join(f"(1+{st['zr']}/{st['zs']})" for st in pl["stages"]) + f" = {pl['ratio']:g}（齿圈固定、太阳轮输入、行星架输出）。")
    for k, t in enumerate(notes):
        sh.text(t, (32, 62 - k * 6.5), 3.6 if k == 0 else 3.1, TextEntityAlignment.MIDDLE_LEFT)

    sh.doc.saveas(a.out)
    print(a.out, "比例", scale_label(s))
    if a.png:
        render_png(a.out, a.png)


def render_png(dxf, png):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy, LineweightPolicy
    doc = ezdxf.readfile(dxf)
    fig = plt.figure(figsize=(16.8, 11.88))
    ax = fig.add_axes([0, 0, 1, 1])
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK,
                        lineweight_policy=LineweightPolicy.ABSOLUTE, lineweight_scaling=1.6)
    Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg).draw_layout(doc.modelspace(), finalize=True)
    ax.set_xlim(-2, A3[0] + 2); ax.set_ylim(-2, A3[1] + 2); ax.set_aspect("equal")
    fig.savefig(png, dpi=200, facecolor="white")
    print(png)


if __name__ == "__main__":
    main()
