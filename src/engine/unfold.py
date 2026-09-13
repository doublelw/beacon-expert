"""钣金中面展开 (unfold): STP壳体 → 平面展开要素 (轮廓/孔/折弯线).

算法 (数据驱动, 不预设盒体形状):
  1. build_segments: 平面按(法向,偏移)分组, 反向组配对(距≈板厚) → 板段
     (真实OCC面 + 中面 n·p=(o1-o2)/2)
  2. build_bend_graph: 折弯圆柱面(水平轴) 经共享边找两张切平面 → 折弯边
  3. unfold: BFS 从底板(最大段), 子段绕中面交线旋转到父段平面
     (OCC刚性变换, ARC/CIRCLE保真), 折弯补偿 BA=π(R+K·t)θ/180 沿展开方向平移
  4. to_2d: 基面局部坐标 → {lines, arcs, circles, splines, bend_lines}

输出为纯 dict(JSON友好), 供 render_engine 展开视图使用.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Optional

from build123d import GeomType, Plane, import_step
from OCP.BRepAdaptor import BRepAdaptor_Surface

# 平面分组容差
_NORMAL_TOL = 0.08
_OFFSET_TOL = 0.15
# 折弯圆柱半径识别窗口 (相对板厚)
_BEND_R_MIN, _BEND_R_MAX = 0.5, 3.0
# 厚度配对窗口 (相对板厚倍数)
_THICK_PAIR_TOL = 0.45


def _pkey(f, ntol=_NORMAL_TOL, otol=_OFFSET_TOL):
    n = f.normal_at()
    c = f.center()
    off = n.X * c.X + n.Y * c.Y + n.Z * c.Z
    return (round(n.X / ntol) * ntol, round(n.Y / ntol) * ntol,
            round(n.Z / ntol) * ntol, round(off / otol) * otol)


def _cyl(f):
    ad = BRepAdaptor_Surface(f.wrapped)
    cy = ad.Cylinder()
    d = cy.Axis().Direction()
    return (d.X(), d.Y(), d.Z()), cy.Radius(), cy.Location()


class Segment:
    def __init__(self, sid, n, off_mid, thickness, keys):
        L = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
        self.sid = sid
        self.n = (n[0] / L, n[1] / L, n[2] / L)  # 单位法向
        self.off = off_mid                    # 中面 n·p = off
        self.t = thickness
        self.keys = keys                      # 该段的两个平面组key (归属判定)
        self.faces = []                       # 该段全部真实面 (两面, 邻接/校验用)
        self.draw_faces = []                  # 只画一面 (两面投影重合, 双画=重线)
        self.area = 0.0
        self.T = None                         # 展开变换链 (BFS填充, Location)

    def __repr__(self):
        return f'S{self.sid}(n={tuple(round(x,2) for x in self.n)},off={self.off:.1f},A={self.area:.0f})'


def build_segments(shape, thickness: float):
    """平面分组 + 反向配对 → Segment 列表."""
    groups = defaultdict(lambda: {'area': 0.0, 'faces': []})
    for f in shape.faces():
        if f.geom_type != GeomType.PLANE:
            continue
        g = groups[_pkey(f)]
        g['area'] += f.area
        g['faces'].append(f)

    segs = []
    used = set()
    keys = list(groups.keys())
    for i, k1 in enumerate(keys):
        if k1 in used:
            continue
        n1, o1 = k1[:3], k1[3]
        for k2 in keys[i + 1:]:
            if k2 in used:
                continue
            n2, o2 = k2[:3], k2[3]
            if not all(abs(n1[j] + n2[j]) < _NORMAL_TOL * 1.5 for j in range(3)):
                continue
            d = abs(o1 + o2)  # n1·p=o1, n2=-n1·p=o2 → 面距=|o1+o2|
            if abs(d - thickness) <= thickness * _THICK_PAIR_TOL:
                seg = Segment(len(segs), n1, (o1 - o2) / 2, thickness, (k1, k2))
                seg.faces = groups[k1]['faces'] + groups[k2]['faces']
                # 画信息更全的一面 (面数多者优先, 同数取面积大)
                s1, s2 = groups[k1], groups[k2]
                seg.draw_faces = (s1 if (len(s1['faces']), s1['area']) >=
                                  (len(s2['faces']), s2['area']) else s2)['faces']
                seg.area = groups[k1]['area'] + groups[k2]['area']
                segs.append(seg)
                used.update((k1, k2))
                break
    return segs


def _face_segment(face, segs):
    """面 → 所属段 (_pkey确定性分组, key即组名)."""
    k = _pkey(face)
    for s in segs:
        if k in s.keys:
            return s
    return None


def _face_segment_unused():
    pass


def _same_dir(a, b):
    return sum(a[j] * b[j] for j in range(3)) > 0


class Bend:
    def __init__(self, pt, direction, radius, sa, sb):
        self.pt = pt            # 轴上一点
        self.dir = direction    # 轴向 (单位)
        self.r = radius
        self.sa, self.sb = sa, sb  # 连接的两段


def build_bend_graph(shape, segs, thickness):
    """折弯图 (虚交点法): 非平行段对的中面交线 + 折弯圆柱见证.

    S弯区域折弯圆柱的邻接面可能是另一个圆柱(双折弯无平段),
    拓扑邻接法在此失效; 改用: 段对中面交线附近存在 r∈窗口
    圆柱(轴向∥交线且贴近) → 真折弯. 轴点=见证圆柱心投影到交线.
    """
    # 收集折弯圆柱 (去重)
    cyls = []
    seen_cyl = set()
    for f in shape.faces():
        if f.geom_type != GeomType.CYLINDER:
            continue
        d, r, loc = _cyl(f)
        if abs(d[2]) > 0.5:
            continue
        if not (_BEND_R_MIN <= r / thickness <= _BEND_R_MAX):
            continue
        loc = (loc.X(), loc.Y(), loc.Z())
        key = (round(d[0], 2), round(d[1], 2), round(loc[0], 1),
               round(loc[1], 1), round(loc[2], 1), round(r, 2))
        if key in seen_cyl:
            continue
        seen_cyl.add(key)
        cyls.append((d, r, loc))

    def _line_dist(p, pt, u):
        vx, vy, vz = p[0] - pt[0], p[1] - pt[1], p[2] - pt[2]
        dot = vx * u[0] + vy * u[1] + vz * u[2]
        dx, dy, dz = vx - dot * u[0], vy - dot * u[1], vz - dot * u[2]
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def _seg_line_dist(seg, pt, u):
        """段顶点到直线的最小距离 (折弯贴着段的边, 面心会因段长而偏远)."""
        best = 1e9
        for f in seg.faces:
            for v in f.vertices():
                best = min(best, _line_dist((v.X, v.Y, v.Z), pt, u))
        return best

    bends = []
    seen = set()
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            a, b = segs[i], segs[j]
            n1, o1, n2, o2 = a.n, a.off, b.n, b.off
            dx = n1[1] * n2[2] - n1[2] * n2[1]
            dy = n1[2] * n2[0] - n1[0] * n2[2]
            dz = n1[0] * n2[1] - n1[1] * n2[0]
            L = math.sqrt(dx * dx + dy * dy + dz * dz)
            if L < 1e-6:
                continue  # 平行段对, 无中面交线
            u = (dx / L, dy / L, dz / L)
            # 交线上一点: 两中面方程 + 过两段代表面中点中点的正交平面
            import numpy as _np
            m = _np.array([(a.faces[0].center().X + b.faces[0].center().X) / 2,
                           (a.faces[0].center().Y + b.faces[0].center().Y) / 2,
                           (a.faces[0].center().Z + b.faces[0].center().Z) / 2])
            A = _np.array([[n1[0], n1[1], n1[2]], [n2[0], n2[1], n2[2]], u])
            rhs = _np.array([o1, o2, u[0] * m[0] + u[1] * m[1] + u[2] * m[2]])
            try:
                p0 = _np.linalg.solve(A, rhs)
            except _np.linalg.LinAlgError:
                continue
            p0 = (float(p0[0]), float(p0[1]), float(p0[2]))
            # 见证圆柱: 轴∥交线 且 圆柱心距交线 < R+2t (真折弯, 带BA)
            # 面积下限: 退化碎面(≈0, 冲压垃圾)不参与 (固定板46个零面积面的教训)
            if a.area < 5 or b.area < 5:
                continue
            wit = None
            for (d, r, loc) in cyls:
                if abs(d[0] * u[0] + d[1] * u[1] + d[2] * u[2]) < 0.95:
                    continue
                if _line_dist(loc, p0, u) < r + 2 * thickness:
                    if wit is None or r < wit[1]:
                        wit = (loc, r)
            if wit is not None:
                # 两段真实贴近交线 (排除跨件远配)
                thr = wit[1] + 3 * thickness + 1.0
                if _seg_line_dist(a, p0, u) > thr or _seg_line_dist(b, p0, u) > thr:
                    continue
                key = (a.sid, b.sid)
                if key in seen:
                    continue
                seen.add(key)
                loc, r = wit
                bends.append(Bend(loc, (u[0], u[1], u[2]), r, a, b))
            else:
                # 无圆柱: 45°倒角/直角过渡 (顶点贴合交线即连接, BA=0)
                # 面积门槛(不对称): 大法兰带小卷边=真结构(后壳S10),
                # 小-小相连=冲压噪声 (固定板127噪声折弯线的教训)
                lo, hi = sorted((a.area, b.area))
                if lo < 5 or hi < 40:
                    continue
                thr2 = 1.5 * thickness + 1.5
                if _seg_line_dist(a, p0, u) > thr2 or _seg_line_dist(b, p0, u) > thr2:
                    continue
                key = (a.sid, b.sid)
                if key in seen:
                    continue
                seen.add(key)
                bends.append(Bend(p0, (u[0], u[1], u[2]), 0.0, a, b))
    return bends


def _midplane_bend_axis(bend: Bend):
    """两段中面的交线 = 折弯轴 (点+方向).

    非平行: 解 n1·p=o1, n2·p=o2, d·p=const.
    平行(180°折回): 中面无交线, 用圆柱轴线兜底 (刚体展开的近似轴).
    """
    a, b = bend.sa, bend.sb
    n1, o1, n2, o2 = a.n, a.off, b.n, b.off
    dx = n1[1] * n2[2] - n1[2] * n2[1]
    dy = n1[2] * n2[0] - n1[0] * n2[2]
    dz = n1[0] * n2[1] - n1[1] * n2[0]
    L = math.sqrt(dx * dx + dy * dy + dz * dz)
    if L < 1e-6:
        # 平行中面: 折回, 轴 = 圆柱轴线 (方向即圆柱轴)
        return bend.pt, (bend.dir[0], bend.dir[1], bend.dir[2])
    d = (dx / L, dy / L, dz / L)
    import numpy as _np
    A = _np.array([[n1[0], n1[1], n1[2]], [n2[0], n2[1], n2[2]], [d[0], d[1], d[2]]])
    rhs = _np.array([o1, o2, d[0] * bend.pt[0] + d[1] * bend.pt[1] + d[2] * bend.pt[2]])
    try:
        p = _np.linalg.solve(A, rhs)
    except _np.linalg.LinAlgError:
        return None
    return (float(p[0]), float(p[1]), float(p[2])), d


def unfold(shape, thickness: Optional[float] = None, k_factor: float = 0.33):
    """主入口: shape(单一solid) → 展开要素 dict."""
    if thickness is None:
        thickness = _guess_thickness(shape)
    segs = build_segments(shape, thickness)
    if not segs:
        raise RuntimeError('未识别到板段 (配对平行面失败)')
    bends = build_bend_graph(shape, segs, thickness)
    base = max(segs, key=lambda s: s.area)

    # BFS
    from build123d import Plane as BPlane, Vector, Location
    base_plane = BPlane(origin=(base.n[0] * base.off, base.n[1] * base.off,
                                base.n[2] * base.off),
                        z_dir=(base.n[0], base.n[1], base.n[2]))
    adj = defaultdict(list)
    for bd in bends:
        adj[bd.sa.sid].append(bd)
        adj[bd.sb.sid].append(bd)

    T = {base.sid: Location()}
    visited = {base.sid}
    queue = [base]
    bend_axes_2d = []  # 折弯线(基面2D)
    while queue:
        cur = queue.pop(0)
        for bd in adj[cur.sid]:
            other = bd.sb if bd.sa is cur else bd.sa
            if other.sid in visited:
                continue
            axis = _midplane_bend_axis(bd)
            if axis is None:
                continue
            (px, py, pz), d = axis
            theta = math.degrees(math.acos(max(-1, min(1,
                cur.n[0] * other.n[0] + cur.n[1] * other.n[1] + cur.n[2] * other.n[2]))))
            # 候选旋转角: ±(180-θ) 常规折弯; ±θ 180°折回(θ=180时 180-θ=0无效)
            angles = []
            for a_ in (180 - theta, theta):
                for s_ in (1, -1):
                    if abs(s_ * a_) > 1e-6 and s_ * a_ not in angles:
                        angles.append(s_ * a_)
            ok = False
            best = None  # (子段心离父段心距离, ang, chained, c)
            pc = (T[cur.sid] * cur.faces[0]).center()  # 父段面心(已展开系)
            for ang in angles:
                # 绕过(px,py,pz)方向d的空间直线旋转 = 移到原点→转→移回
                # (Location(pt,dir,ang)是绕原点轴转后再平移, 不是绕空间线!)
                rot = Location((0, 0, 0), (d[0], d[1], d[2]), ang)
                loc = Location((px, py, pz)) * rot * Location((-px, -py, -pz))
                # 顺序: 先原空间绕轴转, 再套父链到基面 (T父在前)
                chained = T[cur.sid] * loc
                # 检验: 子段面心落在基面±板厚内 (表面离中面t/2), 法向与base法向平行
                f0 = other.faces[0]
                moved = chained * f0
                c = moved.center()
                n = moved.normal_at()
                off_err = abs(base.n[0] * c.X + base.n[1] * c.Y + base.n[2] * c.Z - base.off)
                align = abs(n.X * base.n[0] + n.Y * base.n[1] + n.Z * base.n[2])
                if off_err < 0.8 * other.t + 0.3 and align > 0.9:
                    # ±角共面时离心距镜像相等: 改用「子段心离父段心最远」
                    # (外翻远离母段材料, 内折靠近 — 保证展开向外张开)
                    spread = math.hypot(c.X - pc.X, c.Y - pc.Y, c.Z - pc.Z)
                    if best is None or spread > best[0]:
                        best = (spread, ang, chained, c)
            if best is not None:
                _, ang, chained, c = best
                # 折弯补偿 BA: 轴点先过父链到基面系, 再取外推方向
                bd_r = bd.r
                ba = math.pi * (bd_r + k_factor * thickness) * (abs(ang) / 180)
                if ba > 1e-6:
                    import numpy as _np
                    from build123d import Vertex as _Vtx
                    cax = T[cur.sid] * _Vtx((px, py, pz))
                    cvec = _np.array([c.X - cax.X, c.Y - cax.Y, c.Z - cax.Z])
                    bn = _np.array(base.n)
                    inplane = cvec - bn * _np.dot(cvec, bn)
                    if _np.linalg.norm(inplane) > 1e-9:
                        inplane /= _np.linalg.norm(inplane)
                        ba_loc = Location(Vector((inplane[0] * ba, inplane[1] * ba,
                                                  inplane[2] * ba)))
                        chained = ba_loc * chained
                T[other.sid] = chained
                ok = True
            if ok:
                visited.add(other.sid)
                queue.append(other)

    # 2D 输出: 每段每面 (变换后) → 基面局部坐标
    out = {'thickness': thickness, 'segments_unfolded': len(T),
           'segments_total': len(segs), 'lines': [], 'arcs': [],
           'circles': [], 'splines': [], 'bend_lines': []}
    from build123d import GeomType as GT, Edge
    # 共享边去重: 相邻段共享边被两面各画一次 (E2E dxf_checks duplicate_entity教训)
    raw = {'lines': [], 'arcs': [], 'circles': [], 'splines': []}
    for s in segs:
        if s.sid not in T:
            continue
        for f in s.draw_faces:
            moved = T[s.sid] * f
            local = base_plane.to_local_coords(moved)
            for e in local.edges():
                _emit_edge(e, raw)
    # 按几何签名去重 (线: 端点对; 弧: 圆心+半径+角度)
    out['lines'] = []
    seen_lines = set()
    for ln in raw['lines']:
        k = tuple(sorted([(round(ln['p1'][0], 4), round(ln['p1'][1], 4)),
                          (round(ln['p2'][0], 4), round(ln['p2'][1], 4))]))
        if k not in seen_lines:
            seen_lines.add(k)
            out['lines'].append(ln)
    out['arcs'] = []
    seen_arcs = set()
    for a in raw['arcs']:
        k = (round(a['cx'], 3), round(a['cy'], 3), round(a['r'], 3),
             round(a.get('start_angle', 0) % 6.28318, 3),
             round(a.get('end_angle', 0) % 6.28318, 3))
        if k not in seen_arcs:
            seen_arcs.add(k)
            out['arcs'].append(a)
    out['circles'] = raw['circles']
    out['splines'] = raw['splines']
    seen_bends = set()
    # 折弯线: 已展开bend的轴投影到基面2D, 按两段顶点投影区间裁剪
    for bd in bends:
        if bd.sa.sid in T and bd.sb.sid in T:
            axis = _midplane_bend_axis(bd)
            if axis is None:
                continue
            (px, py, pz), d = axis
            L = 500.0
            q1 = base_plane.to_local_coords(_mk_line_pt(px, py, pz, d, -L))
            q2 = base_plane.to_local_coords(_mk_line_pt(px, py, pz, d, L))
            ux, uy = q2.X - q1.X, q2.Y - q1.Y
            un = math.hypot(ux, uy)
            if un < 1e-9:
                continue
            ux, uy = ux / un, uy / un
            ts = []
            for seg2 in (bd.sa, bd.sb):
                for f in seg2.faces:
                    moved = T[seg2.sid] * f
                    local = base_plane.to_local_coords(moved)
                    for v in local.vertices():
                        ts.append((v.X - q1.X) * ux + (v.Y - q1.Y) * uy)
            if not ts:
                continue
            t0, t1 = max(min(ts), -L), min(max(ts), L)
            cand = {'p1': [q1.X + ux * t0, q1.Y + uy * t0],
                    'p2': [q1.X + ux * t1, q1.Y + uy * t1]}
            kb = tuple(sorted([(round(cand['p1'][0], 3), round(cand['p1'][1], 3)),
                               (round(cand['p2'][0], 3), round(cand['p2'][1], 3))]))
            if kb not in seen_bends:
                seen_bends.add(kb)
                out['bend_lines'].append(cand)
    return out


def _mk_line_pt(px, py, pz, d, s):
    from build123d import Vertex
    return Vertex((px + d[0] * s, py + d[1] * s, pz + d[2] * s))


def _emit_edge(e, out):
    t = e.geom_type
    try:
        if t == GeomType.LINE:
            s, p2 = e.start_point(), e.end_point()
            out['lines'].append({'p1': [s.X, s.Y], 'p2': [p2.X, p2.Y]})
        elif t == GeomType.CIRCLE:
            c = e.center()
            if abs(e.length - 2 * math.pi * e.radius) < 1e-3 * max(1, e.length):
                out['circles'].append({'cx': c.X, 'cy': c.Y, 'r': e.radius})
            else:
                # 圆弧: 起终角 (build123d 弧参数)
                from OCP.BRepAdaptor import BRepAdaptor_Curve
                ad = BRepAdaptor_Curve(e.wrapped)
                circ = ad.Circle()
                a0 = math.degrees(ad.FirstParameter())
                a1 = math.degrees(ad.LastParameter())
                out['arcs'].append({'cx': c.X, 'cy': c.Y, 'r': e.radius,
                                    'start_angle': a0, 'end_angle': a1})
        elif t == GeomType.ELLIPSE:
            pts = [e.position_at(i / 24) for i in range(25)]
            out['splines'].append({'points': [[p.X, p.Y] for p in pts]})
        else:
            n = max(8, int(e.length / 2))
            n = min(n, 200)
            pts = [e.position_at(i / n) for i in range(n + 1)]
            out['splines'].append({'points': [[p.X, p.Y] for p in pts]})
    except Exception:
        pass


def _guess_thickness(shape):
    """平行平面组最小面距 (众数) → 板厚."""
    groups = defaultdict(float)
    keys = []
    for f in shape.faces():
        if f.geom_type != GeomType.PLANE:
            continue
        k = _pkey(f, otol=0.1)
        if k not in groups:
            keys.append(k)
        groups[k] += f.area
    dists = []
    for i, k1 in enumerate(keys):
        for k2 in keys[i + 1:]:
            n1, n2 = k1[:3], k2[:3]
            if all(abs(n1[j] + n2[j]) < _NORMAL_TOL * 1.5 for j in range(3)):
                d = abs(k1[3] + k2[3])
                if 0.3 < d < 6:
                    dists.append((d, min(groups[k1], groups[k2])))
    if not dists:
        return 2.0
    dists.sort(key=lambda x: -x[1])
    return dists[0][0]


def unfold_step(stp_path: str, solid_index: Optional[int] = None, k_factor: float = 0.33):
    """STP文件入口: 展开指定solid.

    solid_index=None (默认) 取最大体积solid (零件本体, 跳过压铆标准件等小件);
    显式传index则按文件顺序取.
    """
    shape = import_step(stp_path)
    solids = shape.solids()
    if not solids:
        raise RuntimeError(f'{stp_path} 无 solid')
    if solid_index is None:
        shell = max(solids, key=lambda s: s.volume)
    else:
        shell = solids[min(solid_index, len(solids) - 1)]
    return unfold(shell, k_factor=k_factor)


if __name__ == '__main__':
    import json
    import sys
    stp = sys.argv[1] if len(sys.argv) > 1 else \
        'storage/tasks/ea6ac5da403d4c23968269441f5ec7f7/后壳.stp'
    out = unfold_step(stp)
    if len(sys.argv) > 2:
        json.dump(out, open(sys.argv[2], 'w'), ensure_ascii=False)
    print(json.dumps({k: (len(v) if isinstance(v, list) else v)
                      for k, v in out.items()}, ensure_ascii=False))
