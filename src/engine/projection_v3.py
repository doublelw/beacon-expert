#!/usr/bin/env python3
"""M3-projection v3: 钣金感知投影 (OCCT HLR 三路合并).

三路 HLR 输出合并, 覆盖钣金全部可见特征:
  VCompound        可见锐边     — 孔口圆 / 直线 / 冲压BSpline边界
  Rg1LineVCompound 可见相切线   — 折弯圆角轮廓 (G1相切处)
  OutLineVCompound 可见silhouette — 折弯圆柱母线 / 冲压曲面侧影

替代旧 projection_v2 (拓扑锐边只覆盖锐边, 对曲面silhouette失效).
端点来自 HLR 参数化边, BSpline→SPLINE (对齐客户金标准).
"""
import sys, os, json, math
import FreeCAD   # 加载 OCC.Core 环境

STP = os.environ.get('STP', '')
OUT = os.environ.get('OUT', 'output/proj_v3.json')
MIN_EDGE = 0.5
ARC_TOL = math.pi + 1e-2

from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCC.Core.HLRAlgo import HLRAlgo_Projector
from OCC.Core.gp import gp_Ax2, gp_Pnt, gp_Dir
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_EDGE
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve

VIEWS = [
    # name, 投影方向N(视线), 平面X轴Vx(统一正向第一轴), axes语义
    # HLR 投影后顶点存局部(u,v,0), proj_pnt 取(X,Y)=(u,v)
    # Back/Bottom/Right 因N反向, 第二轴v镜像(工程图惯例), verify符号容忍
    ('Front',  (0, 0, 1),   (1, 0, 0), 'xy'),   # N=+Z → HLR显示-Z钉头/沉头面 (沉头Cone在-Z)
    ('Back',   (0, 0, -1),  (-1, 0, 0), 'xy'),  # N=-Z → 显示+Z面(底孔穿出) u=-X镜像, v=Y
    ('Top',    (0, -1, 0),  (1, 0, 0), 'xz'),   # u=X, v=Z (俯视)
    ('Bottom', (0, 1, 0),   (1, 0, 0), 'xz'),   # u=X, v=-Z (镜像)
    ('Left',   (1, 0, 0),   (0, 0, 1), 'yz'),   # u=Z(板厚水平), v=Y(板高竖直) → 竖向视图
    ('Right',  (-1, 0, 0),  (0, 0, -1), 'yz'),   # u=-Z(镜像), v=Y
]
MIRROR_VIEWS = {'Back', 'Bottom', 'Right'}   # 第二轴 v 镜像
# HLR 三类 (方法名带 Compound 后缀)
HLR_CLASSES = ['VCompound', 'Rg1LineVCompound', 'OutLineVCompound']


def proj_pnt(p, axes):
    """gp_Pnt → 2D. HLR 投影后顶点存局部平面坐标 (u,v,0), 统一取 (X,Y).
    各视图 Vx 已选使 (u,v) 对齐全局正向两轴."""
    return (p.X(), p.Y())


def _r2(x, y):
    return round(x, 3), round(y, 3)


def _arc_angles(c2, pts2d):
    """离散点(OCC参数u0→u1递增) → 起止角, 用物理扫角方向保证凸向正确.
    半圆180°时逆时针/顺时针跨度相同, 必须用有向扫角判物理方向, 否则凸向反(腰形变凹)."""
    angs = [math.atan2(p[1] - c2[1], p[0] - c2[0]) for p in pts2d]
    if len(angs) < 2:
        return None
    total, cur = 0.0, angs[0]
    for a in angs[1:]:
        d = a - cur
        if d > math.pi:    d -= 2 * math.pi
        elif d < -math.pi: d += 2 * math.pi
        total += d
        cur = a
    if total >= 0:    # 逆时针: start=首端点
        a_s, a_e = angs[0], angs[-1]
    else:             # 顺时针: 反转端点, ezdxf逆时针画等价弧(凸向正确)
        a_s, a_e = angs[-1], angs[0]
    while a_e < a_s:
        a_e += 2 * math.pi
    if a_e - a_s >= 2 * math.pi - 1e-3:
        return None   # 整圆
    return a_s, a_e


def edge_to_2d(edge, axes):
    """HLR 边 → 2D 几何 (line/circle/arc/spline)."""
    try:
        ac = BRepAdaptor_Curve(edge)
    except Exception:
        return None
    ct = ac.GetType()
    try:
        u0, u1 = ac.FirstParameter(), ac.LastParameter()
    except Exception:
        return None
    try:
        p0 = ac.Value(u0); p1 = ac.Value(u1)
    except Exception:
        return None
    a = proj_pnt(p0, axes); b = proj_pnt(p1, axes)

    # Line
    if ct == 0:
        if math.hypot(a[0] - b[0], a[1] - b[1]) < MIN_EDGE:
            return None
        return ('line', {'p1': list(_r2(*a)), 'p2': list(_r2(*b))})

    # Circle (整圆或弧)
    if ct == 1:
        try:
            circ = ac.Circle()
            ctr = circ.Location()
            r = circ.Radius()
        except Exception:
            return None
        c2 = proj_pnt(ctr, axes)
        # 整圆: 参数范围≈2π 或 端点重合
        if (u1 - u0) > 2 * math.pi - 0.01 or math.hypot(a[0] - b[0], a[1] - b[1]) < MIN_EDGE:
            return ('circle', {'cx': round(c2[0], 3), 'cy': round(c2[1], 3), 'r': round(r, 3)})
        # 弧: 离散定方向
        N = 24
        pts2d = [proj_pnt(ac.Value(u0 + (u1 - u0) * i / N), axes) for i in range(N + 1)]
        ang = _arc_angles(c2, pts2d)
        if ang is None:
            return ('circle', {'cx': round(c2[0], 3), 'cy': round(c2[1], 3), 'r': round(r, 3)})
        a0, a1 = ang
        if abs(a1 - a0) >= 2 * math.pi - 1e-3:
            return ('circle', {'cx': round(c2[0], 3), 'cy': round(c2[1], 3), 'r': round(r, 3)})
        return ('arc', {'cx': round(c2[0], 3), 'cy': round(c2[1], 3), 'r': round(r, 3),
                        'start_angle': round(a0, 5), 'end_angle': round(a1, 5)})

    # BSpline / Bezier / Ellipse / 其它 → 离散点 (spline)
    N = 24
    try:
        pts2d = [proj_pnt(ac.Value(u0 + (u1 - u0) * i / N), axes) for i in range(N + 1)]
    except Exception:
        return None
    # 退化检查 (全点重合)
    if all(math.hypot(p[0] - pts2d[0][0], p[1] - pts2d[0][1]) < MIN_EDGE for p in pts2d[1:]):
        return None
    pts = [list(_r2(*p)) for p in pts2d]
    return ('spline', {'points': pts})


def collect_edges(compound, axes):
    """遍历 compound 的边 → 几何列表."""
    out = []
    if compound is None or compound.IsNull():
        return out
    exp = TopExp_Explorer(compound, TopAbs_EDGE)
    while exp.More():
        e = exp.Current()
        g = edge_to_2d(e, axes)
        if g:
            out.append(g)
        exp.Next()
    return out


def merge_arcs_to_circles(arcs):
    """HLR 常把整圆输出为多段弧/单段开弧 → 合并为整圆.
    同圆心(±1mm)+半径(±0.1)的弧组, 角度采样覆盖≈整圆(>45不同角) → 输出整圆.
    解决"圆孔画成腰线/断弧".
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for a in arcs:
        groups[(round(a['cx'], 0), round(a['cy'], 0), round(a['r'], 1))].append(a)
    remain = []
    circles = []
    for g in groups.values():
        angles = set()
        for a in g:
            s, e = a['start_angle'], a['end_angle']
            for i in range(31):
                angles.add(round((s + (e - s) * i / 30) % (2 * math.pi), 2))
        if len(angles) > 45:   # 覆盖近整圆 → 合并
            circles.append({'cx': g[0]['cx'], 'cy': g[0]['cy'], 'r': g[0]['r']})
        else:
            remain.extend(g)
    return remain, circles


def compute_view(topods, vdir, xdir, axes):
    algo = HLRBRep_Algo()
    algo.Add(topods)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*vdir), gp_Dir(*xdir))))
    algo.Update(); algo.Hide()
    hls = HLRBRep_HLRToShape(algo)

    vd = {'lines': [], 'arcs': [], 'circles': [], 'splines': []}
    seen = set()
    cls_count = {}
    for cls in HLR_CLASSES:
        try:
            comp = getattr(hls, cls)()
        except Exception:
            comp = None
        edges = collect_edges(comp, axes)
        cls_count[cls] = len(edges)
        for t, d in edges:
            if t == 'circle':
                k = ('c', d['cx'], d['cy'], d['r'])
            elif t == 'arc':
                k = ('a', d['cx'], d['cy'], d['r'], d['start_angle'], d['end_angle'])
            elif t == 'line':
                k = ('l', frozenset((tuple(d['p1']), tuple(d['p2']))))
            else:
                k = ('s', tuple(map(tuple, d['points'])))
            if k in seen:
                continue
            seen.add(k)
            {'circle': vd['circles'], 'arc': vd['arcs'],
             'line': vd['lines'], 'spline': vd['splines']}[t].append(d)
    # 弧合并: HLR 多段弧/开弧 → 整圆 (解决圆孔画成腰线/断弧)
    vd['arcs'], merged = merge_arcs_to_circles(vd['arcs'])
    vd['circles'] += merged
    return vd, cls_count


# === 主流程 ===
print(f'读 STEP: {STP}')
reader = STEPControl_Reader()
reader.ReadFile(STP)
reader.TransferRoots()
topods = reader.OneShape()

import Part as _Part
_fc = _Part.read(STP)
_bb = _fc.BoundBox
xmin, ymin, zmin, xmax, ymax, zmax = _bb.XMin, _bb.YMin, _bb.ZMin, _bb.XMax, _bb.YMax, _bb.ZMax
W, H, D = _bb.XLength, _bb.YLength, _bb.ZLength

result = {
    'bbox': {'xmin': xmin, 'ymin': ymin, 'zmin': zmin, 'xmax': xmax, 'ymax': ymax, 'zmax': zmax},
    'width': W, 'height': H, 'depth': D,
    'method': 'hlr_v3_three_class',
    'views': {},
}

print(f'bbox: W{W:.1f}×H{H:.1f}×D{D:.1f}\n')
for vname, vdir, xdir, axes in VIEWS:
    vd, cls = compute_view(topods, vdir, xdir, axes)
    result['views'][vname] = vd
    print(f'{vname:7s}: L{len(vd["lines"]):4d} A{len(vd["arcs"]):3d} C{len(vd["circles"]):3d} S{len(vd["splines"]):3d}  '
          f'| HLR {cls}')

os.makedirs(os.path.dirname(OUT) or '.', exist_ok=True)

# === 特征ID贯穿链: 关联投影几何到veritas源特征(一次性建映射, render通过ID确定性绘制) ===
# HLR投影(OCC)不追溯STEP特征 → 必须一次性匹配建ID映射(per-view坐标系)
# 之后render用hole_type直接画GB符号(不猜/不重复匹配)
try:
    _veritas_path = OUT.replace('proj_v3', 'veritas').replace('proj_', 'veritas')
    if not os.path.exists(_veritas_path):
        _veritas_path = 'output/veritas.json'
    _vrt = json.load(open(_veritas_path))
    _pierce = [f for f in _vrt.get('features', []) if f['type'] == 'PIERCING']
    # per-view坐标映射 (视图坐标系 → 全局坐标索引)
    _view_map = {'Front': ('x', 'y'), 'Back': ('x', 'y'),
                 'Top': ('x', 'z'), 'Bottom': ('x', 'z'),
                 'Left': ('z', 'y'), 'Right': ('z', 'y')}
    _idx = {'x': 0, 'y': 1, 'z': 2}
    _linked = 0
    for _vn, _vd in result['views'].items():
        _ax1, _ax2 = _view_map.get(_vn, ('x', 'y'))
        for _ci in _vd.get('circles', []) + _vd.get('arcs', []):
            _cx, _cy, _r = _ci['cx'], _ci['cy'], _ci['r']
            for _f in _pierce:
                _fx = _f['position'][_idx[_ax1]]
                _fy = _f['position'][_idx[_ax2]]
                # abs(abs())比较: 解决镜像视图(Right/Bottom)坐标变换后符号反转
                if abs(abs(_cx) - abs(_fx)) < 2 and abs(abs(_cy) - abs(_fy)) < 2 and abs(_r - _f['radius']) < 0.3:
                    _ci['feature_id'] = _f['id']
                    _ci['hole_type'] = _f.get('hole_type', 'clear')
                    _ci['head_face'] = _f.get('head_face')
                    _ci['callout'] = _f.get('callout', '')
                    _linked += 1
                    break
    print(f'特征ID关联: {_linked}个几何 ↔ veritas特征 (render将通过hole_type确定性绘制)')
except Exception as _e:
    print(f'特征ID关联跳过: {_e}')

json.dump(result, open(OUT, 'w'), ensure_ascii=False, indent=1)
print(f'\n输出: {OUT}')
