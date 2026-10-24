"""M-proj-edit: 现有图纸的自然语言改尺寸 (投影源头精确改 + 重渲染).

为什么不在 DXF 上改: DXF 是渲染产物 — 在成品图上挪几何要同步维护尺寸线/文字/
图框, 稍有不慎就出现"文字说 120 实际几何 100"的假图 (huashu-cad 纪律:
不能只改文字假装改几何)。改图的正解是回到**参数源**。

本模块的参数源 = 平台出图链的中间产物 (每次转换都落盘):
  proj_v3.json   分视图几何, **模型坐标** (lines/circles/arcs/splines)
  veritas.json   3D 特征真值 (PIERCING 孔: id/position/radius/axis_dir; BEND; STAMP)
  annotation.json 标注 (p1/p2 模型坐标 + value/text)

改法: 在模型坐标改 → 重跑 render_engine → 标注由几何重新派生 (数值必然一致)、
布局/图框/图层自动重排、门禁自动复跑。这比 DXF 后处理精确且多视图自洽。

轴映射 (对齐 projection_v3 VIEWS 约定: u/v 轴与符号):
  Front  u=+x v=+y   Back   u=-x v=+y   Top    u=+x v=+z
  Bottom u=+x v=-z   Left   u=+z v=+y   Right  u=-z v=+y
推论: 孔沿某轴时, 只见于"该轴不出现在 u/v 里"的两个视图 (法向视图) —
  沿 Z 的孔 → Front/Back 呈圆; 沿 X → Left/Right; 沿 Y → Top/Bottom。
"""
import json
import math
import os
from copy import deepcopy
from pathlib import Path
from typing import Optional

from src.engine.llm_call import call_llm

RENDER_VIEWS = ('Front', 'Back', 'Top', 'Bottom', 'Left', 'Right')

# 每个渲染视图: u/v 轴 + 符号 (与 projection_v3 VIEWS 一致)
VIEW_UV = {
    'Front':  {'u': ('x', 1), 'v': ('y', 1)},
    'Back':   {'u': ('x', -1), 'v': ('y', 1)},
    'Top':    {'u': ('x', 1), 'v': ('z', 1)},
    'Bottom': {'u': ('x', 1), 'v': ('z', -1)},
    'Left':   {'u': ('z', 1), 'v': ('y', 1)},
    'Right':  {'u': ('z', -1), 'v': ('y', 1)},
}
AXES = ('x', 'y', 'z')


# ─────────────────────────────────────────────────────────────
# 载入 / 摘要 (LLM 的眼睛)
# ─────────────────────────────────────────────────────────────

def load_source(task_dir: str) -> dict:
    """读参数源三件套. 缺 proj_v3 视为不可改 (如实拒绝)."""
    d = Path(task_dir)
    proj_p = d / 'proj_v3.json'
    if not proj_p.exists():
        return {'ok': False, 'error': f'缺参数源 {proj_p} (该图纸不是本平台生成, 请用 DXF 拉伸路径)'}
    proj = json.loads(proj_p.read_text(encoding='utf-8'))
    proj['views'] = {k: v for k, v in proj.get('views', {}).items() if k in RENDER_VIEWS}
    veritas = {}
    if (d / 'veritas.json').exists():
        veritas = json.loads((d / 'veritas.json').read_text(encoding='utf-8'))
    ann = {}
    if (d / 'annotation.json').exists():
        ann = json.loads((d / 'annotation.json').read_text(encoding='utf-8'))
    return {'ok': True, 'proj': proj, 'veritas': veritas, 'annotation': ann, 'dir': str(d)}


def build_digest(src: dict) -> dict:
    """工程语义摘要 (给 LLM 选操作, 不给原始实体海)."""
    proj, veritas, ann = src['proj'], src.get('veritas', {}), src.get('annotation', {})
    feats = veritas.get('features', [])
    holes = []
    for f in feats:
        if f.get('type') != 'PIERCING':
            continue
        holes.append({'id': f['id'], 'axis': f.get('axis_dir'), 'd': round(f.get('diameter', 0), 2),
                      'r': round(f.get('radius', 0), 2), 'pos': [round(v, 2) for v in f.get('position', [])],
                      'through': f.get('through'), 'hole_type': f.get('hole_type')})
    # 孔按 (轴, 直径) 分组 — 工程上"4 个 φ9 孔"是操作粒度
    groups = {}
    for h in holes:
        k = (h['axis'], h['d'])
        groups.setdefault(k, []).append(h['id'])
    hole_groups = [{'axis': k[0], 'd': k[1], 'count': len(v), 'ids': v} for k, v in sorted(groups.items())]
    dims = ann.get('dimensions', [])
    dim_rows = [{'i': i, 'view': d.get('view'), 'type': d.get('type'),
                 'value': round(d.get('value', 0), 2), 'text': d.get('text'),
                 'p1': [round(v, 2) for v in d.get('p1', [])],
                 'p2': [round(v, 2) for v in d.get('p2', [])]}
                for i, d in enumerate(dims)]
    bb = proj.get('bbox', {})
    return {
        'size': {'x': round(bb.get('xmax', 0) - bb.get('xmin', 0), 2),
                 'y': round(bb.get('ymax', 0) - bb.get('ymin', 0), 2),
                 'z': round(bb.get('zmax', 0) - bb.get('zmin', 0), 2)},
        'bbox': {k: round(v, 2) for k, v in bb.items()},
        'thickness': veritas.get('thickness'), 'thickness_axis': veritas.get('thickness_axis'),
        'process': veritas.get('process'),
        'hole_groups': hole_groups,
        'n_bends': sum(1 for f in feats if f.get('type') == 'BEND'),
        'dims': dim_rows[:120],
        'n_dims': len(dim_rows),
    }


# ─────────────────────────────────────────────────────────────
# 轴映射工具 (多视图一致性核心)
# ─────────────────────────────────────────────────────────────

def views_for_axis(axis: str) -> list:
    """孔沿 axis 时呈圆的视图 (该轴不在其 u/v 中)."""
    return [v for v in RENDER_VIEWS if axis not in (VIEW_UV[v]['u'][0], VIEW_UV[v]['v'][0])]


def uv_of(view: str, xyz: dict) -> tuple:
    """3D 坐标 → 视图 (u, v)."""
    m = VIEW_UV[view]
    return (xyz[m['u'][0]] * m['u'][1], xyz[m['v'][0]] * m['v'][1])


def _feature_uv(view: str, feat: dict) -> tuple:
    """veritas 特征 (3D position) → 视图 (u,v) 模型坐标."""
    pos = feat.get('position') or [0.0, 0.0, 0.0]
    return uv_of(view, dict(zip(AXES, pos)))


def _next_feat_id(veritas: dict) -> str:
    n = 0
    for f in veritas.get('features', []):
        fid = str(f.get('id', ''))
        if fid.startswith('F'):
            try:
                n = max(n, int(fid[1:]))
            except ValueError:
                pass
    return f'F{n + 1:04d}'


# ─────────────────────────────────────────────────────────────
# 新增特征发射器 (工程语义 → 几何, 多视图一致, 投影器同构)
# ─────────────────────────────────────────────────────────────

def _emit_new_hole(proj: dict, veritas: dict, feat: dict) -> None:
    """按特征在三类视图发射几何: 法向整圆 / 侧向贯穿线 / 斜轴近似线 (标'投影')."""
    va = (feat.get('axis_dir') or 'Z').upper()
    r = feat.get('radius', 0.0)
    d = feat.get('diameter', 2 * r)
    pos = feat.get('position') or [0, 0, 0]
    bb = proj.get('bbox', {})
    lo, hi = bb.get(f'{va.lower()}min', 0.0), bb.get(f'{va.lower()}max', 0.0)
    # 法向视图 (整圆): 该轴不出现在 u/v
    for vn in views_for_axis(va.lower()):
        u, v = _feature_uv(vn, feat)
        vd = proj['views'].get(vn)
        if vd is None:
            continue
        vd.setdefault('circles', []).append(
            {'cx': round(u, 3), 'cy': round(v, 3), 'r': round(r, 3),
             'hole_type': feat.get('hole_type', 'clear'), 'axis_dir': va})
    # 侧向视图 (贯穿线): 该轴出现在 u/v, 孔投影为两条平行贯穿边
    for vn in RENDER_VIEWS:
        m = VIEW_UV[vn]
        if va in (m['u'][0], m['v'][0]):
            continue
        uv_ = _feature_uv(vn, feat)
        vd = proj['views'].get(vn)
        if vd is None:
            continue
        # 贯穿边在视图中沿哪个坐标轴?
        # 孔沿 va, 视图中垂直于 va 的两个轴 = u/v 本身 (va 不在其中), 贯穿边长度
        # 取 板厚跨度 (lo..hi 映射到视图中包含 va 的轴? 不: va 不在 u/v → 取特征所在
        # 主面跨度, 用最保守的 bbox 该轴跨度)
        # 简化: 用 bbox[va]min/max 作为贯穿跨度, 映射到视图 u/v 中对应轴
        # 该视图中 va 对应的物理轴坐标 = lo*sign..hi*sign (va 不在u/v, 所以跨度沿 u/v 之一)
        # 实际: 侧视图中孔的两条贯穿线 = 在 u 方向偏移 ±r*proj, 沿 v 方向贯穿
        # 最保守且正确的表达: 两条线在 u 坐标 = cu ± r (法向距离), 沿 v 贯穿 lo..hi
        p3 = dict(zip(AXES, pos))
        cu = p3[m['u'][0]] * m['u'][1]
        cv = p3[m['v'][0]] * m['v'][1]
        # 孔在侧视图中: u 坐标处有两条线 (cu ± r), 跨度 = va 轴跨度投影到 v?
        # 不: va 不在该视图 u/v 里, 所以跨度只能取 bbox 在 va 轴上的 lo..hi,
        # 但映射回纸面时 va 轴无坐标 → 侧视图的"贯穿深度"画成沿视图 u 轴 ±r 的短边
        # (工程上侧视孔 = 两条与视图 u 平行的线, 间距 = d, 长度 = 板厚在视图 v 上的投影)
        # 板厚轴 = thickness_axis (veritas), 映射到该视图
        ta = (veritas.get('thickness_axis') or 'Z').upper()
        tlo, thi = bb.get(f'{ta.lower()}min', 0.0), bb.get(f'{ta.lower()}max', 0.0)
        # 板厚在视图 u/v 中的坐标分量
        if m['u'][0] == ta:
            tstart, tend = tlo * m['u'][1], thi * m['u'][1]
            # 两条贯穿线: 沿 u 轴从 tstart..tend, 在 v = cv ± r
            for sgn in (1, -1):
                vd.setdefault('lines', []).append(
                    {'p1': [round(tstart, 3), round(cv + sgn * r, 3)],
                     'p2': [round(tend, 3), round(cv + sgn * r, 3)],
                     'role': 'hole_side'})
        elif m['v'][0] == ta:
            tlo_v, thi_v = tlo * m['v'][1], thi * m['v'][1]
            # 符号轴: lo*sign 可能 > hi*sign (sign=-1 时), 排序保证 p1<p2
            a, b = sorted((tlo_v, thi_v))
            # 两条贯穿线: 沿 v 轴从 a..b, 在 u = cu ± r
            for sgn in (1, -1):
                vd.setdefault('lines', []).append(
                    {'p1': [round(cu + sgn * r, 3), round(a, 3)],
                     'p2': [round(cu + sgn * r, 3), round(b, 3)],
                     'role': 'hole_side'})
        # 斜轴 (va 与 ta 不同且视图 u/v 都不含 va 的轴 → 近似): 用 bbox va 跨度沿 v
        elif va not in (m['u'][0], m['v'][0]):
            for sgn in (1, -1):
                vd.setdefault('lines', []).append(
                    {'p1': [round(cu + sgn * r, 3), round(lo, 3)],
                     'p2': [round(cu + sgn * r, 3), round(hi, 3)],
                     'role': 'hole_side_approx'})


def op_add_hole(proj: dict, veritas: dict, ann: dict,
                axis: str, pos: list, d: float,
                through: bool = True, hole_type: str = 'clear') -> dict:
    """新增孔: 法向整圆 + 侧向贯穿边 + veritas 特征登记 + 孔径标注.

    几何按投影器同构规则发射 (法向圆 + 侧向贯穿线), 保证重渲染后
    各视图一致。侧向视图的贯穿边用 bbox 板厚跨度近似, 斜轴件标注 '投影'。
    """
    axis = (axis or '').upper()
    if axis not in ('X', 'Y', 'Z'):
        return {'ok': False, 'error': f'axis 必须是 X/Y/Z, 收到 {axis!r}'}
    if not (pos and len(pos) == 3):
        return {'ok': False, 'error': 'pos 必须是 [x,y,z] 模型坐标'}
    if not d or d <= 0:
        return {'ok': False, 'error': 'd (直径) 必须 > 0'}
    feat = {
        'id': _next_feat_id(veritas), 'type': 'PIERCING',
        'axis_dir': axis, 'direction': axis + '+',
        'position': [float(v) for v in pos],
        'radius': d / 2.0, 'diameter': float(d),
        'all_radii': [d / 2.0], 'through': through,
        'hole_type': hole_type, 'span': d / 2.0,
    }
    veritas.setdefault('features', []).append(feat)
    _emit_new_hole(proj, veritas, feat)
    n_new_dim = 0
    # 孔径标注 (法向视图, 过孔心)
    nn_views = views_for_axis(axis.lower())
    if nn_views:
        u0, v0 = _feature_uv(nn_views[0], feat)
        ann.setdefault('dimensions', []).append({
            'type': 'diameter', 'view': nn_views[0], 'level': 0,
            'p1': [round(u0 - d / 2, 3), round(v0, 3)],
            'p2': [round(u0, 3), round(v0, 3)],
            'value': float(d), 'text': f'φ{d:g}',
            'layer': 'DIM', 'leader_pts': []})
        n_new_dim = 1
    return {'ok': True, 'op': 'add_hole', 'feature': feat['id'],
            'changed': {'circles': len(nn_views), 'veritas': 1,
                        'dims': n_new_dim}}


def _annotate_undeclared_holes(proj: dict, veritas: dict, ann: dict) -> int:
    """标注链式补齐: 每个 PIERCING 特征若其法向视图无对应孔径标注, 补一条.

    改图后标注密度不缩水 (删/移孔不会带走其它孔的标注), 且新孔必带标注 —
    精准加工图: 每个孔有尺寸可依。已在 annotation.dimensions 里有
    diameter 标注且 p1 落在该孔法向圆心的特征不再补 (去重)。
    """
    dims = ann.setdefault('dimensions', [])
    # 已有 diameter 标注覆盖的 (视图, 孔心u, 孔心v)
    covered = set()
    for dd in dims:
        if dd.get('type') != 'diameter' or dd.get('view') not in VIEW_UV:
            continue
        p1 = dd.get('p1') or []
        if len(p1) == 2:
            covered.add((dd['view'], round(p1[0], 1), round(p1[1], 1)))
    n = 0
    for f in veritas.get('features', []):
        if f.get('type') != 'PIERCING':
            continue
        va = (f.get('axis_dir') or 'Z').lower()
        nn = views_for_axis(va)
        if not nn:
            continue
        vn = nn[0]
        u, v = _feature_uv(vn, f)
        key = (vn, round(u, 1), round(v, 1))
        if key in covered:
            continue
        r = f.get('radius', 0.0)
        dd = {
            'type': 'diameter', 'view': vn, 'level': 0,
            'p1': [round(u - r, 3), round(v, 3)],
            'p2': [round(u, 3), round(v, 3)],
            'value': round(r * 2, 2), 'text': f'φ{r * 2:g}',
            'layer': 'DIM', 'leader_pts': [],
        }
        dims.append(dd)
        covered.add(key)
        n += 1
    return n



def shift_uv(view: str, u: float, v: float, axis: str, delta: float, at: float):
    """STRETCH 语义: 该视图中全局坐标 >= at 的分量平移 delta.
    仅移动越界分量本身 → 圆仍为圆 (不做缩放, 否则孔变椭圆)."""
    m = VIEW_UV[view]
    if m['u'][0] == axis and (u * m['u'][1]) >= at:
        u = u + delta * m['u'][1]
    if m['v'][0] == axis and (v * m['v'][1]) >= at:
        v = v + delta * m['v'][1]
    return u, v


def _shift_view_entity(e: dict, view: str, axis: str, delta: float, at: float) -> bool:
    """按 STRETCH 语义改一个实体 (原地). 返回是否被改."""
    t = e.get('type') or ('line' if 'p1' in e else 'circle' if 'r' in e else None)
    if 'p1' in e and 'p2' in e:                       # line
        nu1, nv1 = shift_uv(view, e['p1'][0], e['p1'][1], axis, delta, at)
        nu2, nv2 = shift_uv(view, e['p2'][0], e['p2'][1], axis, delta, at)
        if (nu1, nv1, nu2, nv2) != (e['p1'][0], e['p1'][1], e['p2'][0], e['p2'][1]):
            e['p1'], e['p2'] = [nu1, nv1], [nu2, nv2]
            return True
        return False
    if 'cx' in e and 'cy' in e:                        # circle / arc
        ncu, ncv = shift_uv(view, e['cx'], e['cy'], axis, delta, at)
        if (ncu, ncv) != (e['cx'], e['cy']):
            e['cx'], e['cy'] = ncu, ncv
            return True
        return False
    if 'points' in e:                                  # spline
        changed = False
        pts = []
        for p in e['points']:
            nu, nv = shift_uv(view, p[0], p[1], axis, delta, at)
            changed = changed or (nu, nv) != (p[0], p[1])
            pts.append([nu, nv])
        if changed:
            e['points'] = pts
        return changed
    return False


def _bump_view(view_data: dict, view: str, axis: str, delta: float, at: float) -> int:
    n = 0
    for key in ('lines', 'circles', 'arcs', 'splines'):
        for e in view_data.get(key, []):
            if _shift_view_entity(e, view, axis, delta, at):
                n += 1
    return n


# ─────────────────────────────────────────────────────────────
# 操作 (工程语义 → 确定性几何修改)
# ─────────────────────────────────────────────────────────────

def op_stretch_axis(proj: dict, veritas: dict, ann: dict,
                    axis: str, delta: float, at: float) -> dict:
    """沿全局轴拉伸: 坐标 >= at 的几何整体位移 delta (CAD STRETCH).
    同步: 各视图几何 + veritas 特征位置 + 标注端点 (数值随后重新派生)."""
    axis = axis.lower()
    if axis not in AXES:
        return {'ok': False, 'error': f'轴必须是 {AXES}'}
    n_geom = 0
    for vn, vd in proj.get('views', {}).items():
        n_geom += _bump_view(vd, vn, axis, delta, at)
    # veritas 特征位置
    n_feat = 0
    for f in veritas.get('features', []):
        p = f.get('position')
        if not p:
            continue
        idx = AXES.index(axis)
        if (p[idx]) >= at:
            p[idx] = p[idx] + delta
            n_feat += 1
    # 标注端点 (p1/p2 在各自 view 的模型坐标)
    n_dim = 0
    for d in ann.get('dimensions', []):
        vn = d.get('view')
        if vn not in VIEW_UV:
            continue
        for k in ('p1', 'p2'):
            if isinstance(d.get(k), list) and len(d[k]) == 2:
                nu, nv = shift_uv(vn, d[k][0], d[k][1], axis, delta, at)
                if (nu, nv) != (d[k][0], d[k][1]):
                    d[k] = [nu, nv]
                    n_dim += 1
    _revalue_dims(ann)
    return {'ok': True, 'op': 'stretch_axis', 'axis': axis, 'delta': delta, 'at': at,
            'changed': {'view_entities': n_geom, 'veritas_features': n_feat, 'dim_endpoints': n_dim}}


def _find_holes(veritas: dict, ids=None, axis=None, d=None, d_tol=0.05) -> list:
    out = []
    for f in veritas.get('features', []):
        if f.get('type') != 'PIERCING':
            continue
        if ids is not None and f['id'] not in ids:
            continue
        if axis is not None and f.get('axis_dir') != axis.upper():
            continue
        if d is not None and abs(f.get('diameter', -1) - d) > d_tol:
            continue
        out.append(f)
    return out


def op_set_hole_diameter(proj: dict, veritas: dict, ann: dict,
                         ids=None, axis=None, d=None, new_d: float = None) -> dict:
    """改孔/圆直径: 按 id 列表或 (axis, 原直径d) 组选定, 各法向视图同步改半径."""
    if not new_d or new_d <= 0:
        return {'ok': False, 'error': 'new_d 必须 > 0'}
    holes = _find_holes(veritas, ids, axis, d)
    if not holes:
        return {'ok': False, 'error': f'未找到匹配孔 (ids={ids} axis={axis} d={d})'}
    new_r = new_d / 2.0
    n_circ = 0
    for h in holes:
        old_r = h.get('radius')
        va = (h.get('axis_dir') or 'Z').lower()
        xyz = dict(zip(AXES, h.get('position', [0, 0, 0])))
        for vn in views_for_axis(va):
            u, v = uv_of(vn, xyz)
            vd = proj['views'].get(vn, {})
            for key in ('circles', 'arcs'):
                for e in vd.get(key, []):
                    if abs(e['cx'] - u) < 0.6 and abs(e['cy'] - v) < 0.6 and abs(e.get('r', 0) - old_r) < 0.3:
                        e['r'] = new_r
                        n_circ += 1
        h['radius'] = new_r
        h['diameter'] = new_d
        h['all_radii'] = [new_r]
    n_dim = 0
    for d_ in ann.get('dimensions', []):
        if d_.get('type') == 'diameter' and d_ is not None:
            v0 = d_.get('value', 0)
            if any(abs(v0 - (old * 2)) < 0.2 for old in
                   [h0.get('radius') for h0 in holes] + [(new_d / 2)]):
                pass  # 直径标注的具体匹配在 _revalue_dims 里按几何重算
    _revalue_dims(ann)
    return {'ok': True, 'op': 'set_hole_diameter', 'new_d': new_d,
            'holes': [h['id'] for h in holes],
            'changed': {'circles': n_circ, 'holes': len(holes), 'dim_endpoints': n_dim}}


def op_move_hole(proj: dict, veritas: dict, ann: dict,
                 ids: list, dx: float = 0, dy: float = 0, dz: float = 0) -> dict:
    """平移孔: 3D 位移 → 各法向视图 du/dv; veritas 位置与相关标注端点同步."""
    holes = _find_holes(veritas, ids=ids)
    if not holes:
        return {'ok': False, 'error': f'未找到孔 {ids}'}
    dvec = {'x': dx, 'y': dy, 'z': dz}
    n_circ = n_dim = 0
    for h in holes:
        va = (h.get('axis_dir') or 'Z').lower()
        old_xyz = dict(zip(AXES, list(h.get('position', [0, 0, 0]))))
        new_xyz = {k: old_xyz[k] + dvec[k] for k in AXES}
        for vn in views_for_axis(va):
            ou, ov = uv_of(vn, old_xyz)
            nu, nv = uv_of(vn, new_xyz)
            du, dv = nu - ou, nv - ov
            vd = proj['views'].get(vn, {})
            for key in ('circles', 'arcs'):
                for e in vd.get(key, []):
                    if abs(e['cx'] - ou) < 0.6 and abs(e['cy'] - ov) < 0.6:
                        e['cx'] += du
                        e['cy'] += dv
                        n_circ += 1
        # 其它视图里孔表现为直线/腰形边 — 一并平移 (按 3D 位移映射)
        for vn in RENDER_VIEWS:
            if vn in views_for_axis(va):
                continue
            m = VIEW_UV[vn]
            du = dvec[m['u'][0]] * m['u'][1]
            dv = dvec[m['v'][0]] * m['v'][1]
            if du == 0 and dv == 0:
                continue
            ou, ov = uv_of(vn, old_xyz)
            vd = proj['views'].get(vn, {})
            for key in ('circles', 'arcs'):
                for e in vd.get(key, []):
                    if abs(e['cx'] - ou) < 0.6 and abs(e['cy'] - ov) < 0.6:
                        e['cx'] += du
                        e['cy'] += dv
                        n_circ += 1
        h['position'] = [new_xyz[k] for k in AXES]
        # 标注端点落在旧孔心 → 一并平移
        for d_ in ann.get('dimensions', []):
            vn = d_.get('view')
            if vn not in VIEW_UV:
                continue
            ou, ov = uv_of(vn, old_xyz)
            m = VIEW_UV[vn]
            du = dvec[m['u'][0]] * m['u'][1]
            dv = dvec[m['v'][0]] * m['v'][1]
            for k in ('p1', 'p2'):
                if isinstance(d_.get(k), list) and abs(d_[k][0] - ou) < 0.6 and abs(d_[k][1] - ov) < 0.6:
                    d_[k] = [d_[k][0] + du, d_[k][1] + dv]
                    n_dim += 1
    _revalue_dims(ann)
    return {'ok': True, 'op': 'move_hole', 'holes': [h['id'] for h in holes],
            'delta': [dx, dy, dz],
            'changed': {'circles': n_circ, 'dim_endpoints': n_dim}}


def op_delete_hole(proj: dict, veritas: dict, ann: dict, ids: list) -> dict:
    """删孔: 各法向视图移除对应圆/弧 + 移除该孔相关标注."""
    holes = _find_holes(veritas, ids=ids)
    if not holes:
        return {'ok': False, 'error': f'未找到孔 {ids}'}
    removed_c = removed_dim = 0
    centers = []
    for h in holes:
        va = (h.get('axis_dir') or 'Z').lower()
        xyz = dict(zip(AXES, h.get('position', [0, 0, 0])))
        for vn in views_for_axis(va):
            u, v = uv_of(vn, xyz)
            centers.append((vn, u, v, h.get('radius')))
            vd = proj['views'].get(vn, {})
            for key in ('circles', 'arcs'):
                keep = [e for e in vd.get(key, [])
                        if not (abs(e['cx'] - u) < 0.6 and abs(e['cy'] - v) < 0.6
                                and abs(e.get('r', 0) - h.get('radius', 0)) < 0.3)]
                removed_c += len(vd.get(key, [])) - len(keep)
                vd[key] = keep
    new_dims = []
    for d_ in ann.get('dimensions', []):
        vn = d_.get('view')
        hit = False
        for (cv, cu, cvv, cr) in centers:
            if vn != cv:
                continue
            if d_.get('type') == 'diameter' and abs(d_.get('value', 0) - 2 * cr) < 0.3:
                hit = True
            for k in ('p1', 'p2'):
                if isinstance(d_.get(k), list) and abs(d_[k][0] - cu) < 0.6 and abs(d_[k][1] - cvv) < 0.6:
                    hit = True
        if hit:
            removed_dim += 1
        else:
            new_dims.append(d_)
    ann['dimensions'] = new_dims
    _revalue_dims(ann)
    return {'ok': True, 'op': 'delete_hole', 'holes': [h['id'] for h in holes],
            'changed': {'circles': removed_c, 'dims_removed': removed_dim}}


def _refresh_bbox(proj: dict) -> dict:
    """从各视图几何反推全局 bbox (6 视图覆盖 3 轴, 取并集).

    改图后必须重算: render 用 bbox 选图幅/比例, 用旧值会缩放错。
    """
    mins = {a: math.inf for a in AXES}
    maxs = {a: -math.inf for a in AXES}
    for vn, vd in proj.get('views', {}).items():
        m = VIEW_UV.get(vn)
        if not m:
            continue
        pts = []
        for e in vd.get('lines', []):
            pts += [e['p1'], e['p2']]
        for e in vd.get('circles', []) + vd.get('arcs', []):
            pts += [[e['cx'] - e.get('r', 0), e['cy'] - e.get('r', 0)],
                    [e['cx'] + e.get('r', 0), e['cy'] + e.get('r', 0)]]
        for e in vd.get('splines', []):
            pts += e.get('points', [])
        for p in pts:
            g = {m['u'][0]: p[0] * m['u'][1], m['v'][0]: p[1] * m['v'][1]}
            for a, val in g.items():
                mins[a] = min(mins[a], val)
                maxs[a] = max(maxs[a], val)
    bb = {}
    for a in AXES:
        if mins[a] is math.inf:
            continue
        bb[f'{a}min'] = round(mins[a], 3)
        bb[f'{a}max'] = round(maxs[a], 3)
    proj['bbox'] = bb
    if all(f'{a}min' in bb for a in AXES):
        proj['width'] = round(bb['xmax'] - bb['xmin'], 3)
        proj['height'] = round(bb['ymax'] - bb['ymin'], 3)
        proj['depth'] = round(bb['zmax'] - bb['zmin'], 3)
    return bb


def _revalue_dims(ann: dict) -> int:
    """按几何重算标注数值与文字 (改图后标注永不说谎)."""
    n = 0
    for d in ann.get('dimensions', []):
        p1, p2 = d.get('p1'), d.get('p2')
        if not (isinstance(p1, list) and isinstance(p2, list)):
            continue
        t = d.get('type')
        if t in ('linear', 'aligned'):
            v = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            # 水平/垂直标注取分量距离
            if t == 'linear':
                ang = d.get('angle', 0)
                v = abs(p2[0] - p1[0]) if abs(math.cos(math.radians(ang))) > 0.5 else abs(p2[1] - p1[1])
        elif t == 'diameter':
            v = math.hypot(p2[0] - p1[0], p2[1] - p1[1]) * 2
        elif t == 'radius':
            v = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        else:
            continue
        if abs(v - d.get('value', -1)) > 1e-6:
            d['value'] = v
            d['text'] = f'{v:.2f}'.rstrip('0').rstrip('.')
            n += 1
    return n


OPS = {
    'stretch_axis': op_stretch_axis,
    'set_hole_diameter': op_set_hole_diameter,
    'move_hole': op_move_hole,
    'delete_hole': op_delete_hole,
    'add_hole': op_add_hole,
}


def apply_ops(src: dict, ops: list) -> tuple:
    """在副本上按序应用操作 → (new_src, log)."""
    proj = deepcopy(src['proj'])
    veritas = deepcopy(src.get('veritas', {}))
    ann = deepcopy(src.get('annotation', {}))
    log = []
    for op in ops:
        fn = OPS.get(op.get('op'))
        if fn is None:
            log.append({'ok': False, 'error': f"未知操作 {op.get('op')!r}"})
            continue
        kw = {k: v for k, v in op.items() if k != 'op'}
        try:
            log.append(fn(proj, veritas, ann, **kw))
        except Exception as ex:  # noqa: BLE001
            log.append({'ok': False, 'error': f'{type(ex).__name__}: {ex}'})
    _refresh_bbox(proj)
    return {'proj': proj, 'veritas': veritas, 'annotation': ann}, log


# ─────────────────────────────────────────────────────────────
# LLM 规划器: 自然语言 → 工程语义操作 (确定性解析到几何)
# ─────────────────────────────────────────────────────────────

PLAN_SYSTEM = """你是工程图改图规划器。输入是图纸的**工程语义摘要**(尺寸/孔组/标注/材料)
和用户的修改指令。输出一份操作计划 JSON (只输出 JSON, 无解释)。

可用操作 (全部在模型坐标上确定性执行, 改完自动重出图+重算标注):
1. 改孔径:  {"op":"set_hole_diameter","axis":"Z","d":2.2,"new_d":3.2}
   - axis: 孔轴向 (X/Y/Z); d: 原直径(摘要 hole_groups 里的值, 用来选中该组); new_d: 新直径
   - 也可用 {"ids":["F0001","F0002"]} 精确指定孔
2. 移孔:    {"op":"move_hole","ids":["F0005"],"dx":10,"dy":0,"dz":0}
   - dx/dy/dz 是 3D 位移(模型坐标, 各视图自动换算)
3. 沿轴拉伸: {"op":"stretch_axis","axis":"x","delta":20,"at":135.9}
   - 全局坐标 >= at 的几何整体平移 delta (CAD STRETCH 语义, 孔保持圆形)
   - "把宽度 120 改成 150": 先找摘要 dims 里 value=120 的那条, 看它 p1/p2 沿哪个轴
     → axis 取该轴, delta = 150-120, at 取 p1/p2 中较小的那个坐标值
   - "右端加长 20": axis=x, delta=20, at = bbox.xmax - 该段长度
4. 删孔:    {"op":"delete_hole","ids":["F0005"]}
5. 新增孔:  {"op":"add_hole","axis":"Z","pos":[0,0,-14.3],"d":8,"through":true}
   - 在**已有板材范围内**新增 PIERCING 孔: pos=[x,y,z] 模型坐标 (孔中心),
     d=直径(mm), axis=孔轴向, through=true 贯穿 (钣金件默认贯穿)。
   - 自动发射几何: 法向视图整圆 + 侧向视图贯穿边 + veritas 特征登记 + 孔径标注。
   - 仅当用户明确要求"加孔/开孔/多一个孔"时用; 改已有孔用 set_hole_diameter/move_hole。
   - pos 的 z 取摘要里同类孔的 z (板材面高度), 不要臆造。

规则:
1. 指令没提到的内容一律不动 (作用范围外必须保全)。
2. 尺寸/孔位一律用**模型真实毫米**, 不要换算图纸比例 (重出图会自动处理)。
3. 需要多个改动就多个 op, 按顺序。
4. 拿不准时优先用工程语义操作, 不要臆造坐标; 摘要里没有的特征不要编
   (如实报 {"ops":[],"note":"..."})。
5. "note" 用一句话说明改了什么。
"""


def validate_ops(plan) -> list:
    """操作计划校验 (LLM 产出与人工输入同一道门)."""
    errs = []
    if not isinstance(plan, dict) or not isinstance(plan.get('ops'), list):
        return ['计划必须是 {"ops":[...]}']
    for i, op in enumerate(plan['ops']):
        k = op.get('op')
        if k not in OPS:
            errs.append(f'ops[{i}] 未知操作 {k!r} (允许 {sorted(OPS)})')
            continue
        if k == 'set_hole_diameter':
            if not op.get('new_d') or op['new_d'] <= 0:
                errs.append(f'ops[{i}] set_hole_diameter 需要 new_d>0')
            if not (op.get('ids') or (op.get('axis') and op.get('d'))):
                errs.append(f'ops[{i}] set_hole_diameter 需要 ids 或 (axis+d)')
        elif k == 'move_hole':
            if not op.get('ids'):
                errs.append(f'ops[{i}] move_hole 需要 ids')
            if not any(op.get(x) for x in ('dx', 'dy', 'dz')):
                errs.append(f'ops[{i}] move_hole 需要 dx/dy/dz 之一')
        elif k == 'stretch_axis':
            if str(op.get('axis', '')).lower() not in AXES:
                errs.append(f'ops[{i}] stretch_axis 需要 axis∈{AXES}')
            if not isinstance(op.get('delta'), (int, float)) or op['delta'] == 0:
                errs.append(f'ops[{i}] stretch_axis 需要非零 delta')
            if not isinstance(op.get('at'), (int, float)):
                errs.append(f'ops[{i}] stretch_axis 需要 at (切分平面坐标)')
        elif k == 'delete_hole':
            if not op.get('ids'):
                errs.append(f'ops[{i}] delete_hole 需要 ids')
        elif k == 'add_hole':
            if str(op.get('axis', '')).upper() not in ('X', 'Y', 'Z'):
                errs.append(f'ops[{i}] add_hole 需要 axis∈X/Y/Z')
            pos = op.get('pos')
            if not (isinstance(pos, list) and len(pos) == 3
                    and all(isinstance(v, (int, float)) for v in pos)):
                errs.append(f'ops[{i}] add_hole 需要 pos=[x,y,z] 模型坐标')
            if not (isinstance(op.get('d'), (int, float)) and op['d'] > 0):
                errs.append(f'ops[{i}] add_hole 需要 d>0 (直径mm)')
    return errs


def _extract_json(text: str):
    import re
    if not text:
        return None
    t = text.strip()
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', t, re.S)
    if m:
        t = m.group(1)
    else:
        i, j = t.find('{'), t.rfind('}')
        if i < 0 or j <= i:
            return None
        t = t[i:j + 1]
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


async def plan_ops(src: dict, instructions: str, max_repair: int = 2) -> tuple:
    """NL → (ops|None, meta). 校验 + 带错误回炉 (huashu-cad 纪律)."""
    digest = json.dumps(build_digest(src), ensure_ascii=False)
    if len(digest) > 20000:
        digest = digest[:20000] + '...'
    meta = {'attempts': 0, 'errors': []}
    user_msg = f'图纸摘要:\n{digest}\n\n修改指令: {instructions}'
    for _ in range(max_repair + 1):
        meta['attempts'] += 1
        r = await call_llm(user_msg, system=PLAN_SYSTEM, max_tokens=1500, temperature=0)
        meta['llm'] = {'provider': r.get('provider'), 'model': r.get('model')}
        if r.get('error') or not r.get('text'):
            meta['errors'].append(f"LLM调用失败: {r.get('error') or '空回复'}")
            return None, meta
        plan = _extract_json(r['text'])
        if plan is None:
            meta['errors'].append('回复中找不到 JSON 计划')
        else:
            errs = validate_ops(plan)
            if not errs:
                return plan, meta
            meta['errors'] = errs
        user_msg = (f'图纸摘要:\n{digest}\n\n修改指令: {instructions}'
                    + '\n\n上次计划问题, 修正后重新输出完整 JSON:\n'
                    + '\n'.join(f'- {e}' for e in meta['errors'][:8]))
    return None, meta


# ─────────────────────────────────────────────────────────────
# 全链: 改参数源 → 重渲染 → 验证 → 前后对比
# ─────────────────────────────────────────────────────────────

async def run_proj_modify(task_dir: str, instructions: str, out_dir: str,
                          plan: Optional[dict] = None) -> dict:
    """现有图纸自然语言改尺寸 (投影源头改 + 重渲染).

    plan 传入时跳过 LLM (确定性测试/复用); 否则 NL → 操作计划。
    """
    src = load_source(task_dir)
    if not src.get('ok'):
        return {'ok': False, 'stage': 'input', 'error': src.get('error'),
                'hint': '第三方 DXF 请走 /api/dxfmod 的 STRETCH 路径'}
    meta = {'attempts': 0, 'llm': None}
    if plan is None:
        plan, meta = await plan_ops(src, instructions)
        if plan is None:
            return {'ok': False, 'stage': 'plan',
                    'error': '; '.join(meta['errors'][:5]), 'llm': meta.get('llm')}
    if not plan.get('ops'):
        return {'ok': False, 'stage': 'plan',
                'error': f"计划无操作: {plan.get('note', '')}", 'llm': meta.get('llm')}

    new_src, log = apply_ops(src, plan['ops'])
    bad = [l for l in log if not l.get('ok')]
    if bad and all(not l.get('ok') for l in log):
        return {'ok': False, 'stage': 'apply',
                'error': json.dumps(bad, ensure_ascii=False)[:400]}

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'proj_modified.json').write_text(
        json.dumps(new_src['proj'], ensure_ascii=False), encoding='utf-8')
    (out / 'veritas_modified.json').write_text(
        json.dumps(new_src['veritas'], ensure_ascii=False), encoding='utf-8')

    # 重渲染: 标注由修改后的 annotation 传入 (数值已按几何重算)
    try:
        from src.engine.render_engine import render
        dxf = str(out / 'modified.dxf')
        render(new_src['proj'], None, new_src['annotation'], None, dxf,
               veritas=new_src['veritas'])
    except Exception as ex:  # noqa: BLE001
        return {'ok': False, 'stage': 'render', 'error': f'{type(ex).__name__}: {ex}'[:400]}

    # 门禁 + 标注复核 (回读落盘 DXF)
    verify = {'dims': [], 'gate_errors': None}
    try:
        from src.engine.dxf_checks import validate_dxf_file
        findings = validate_dxf_file(dxf)
        verify['gate_errors'] = sum(1 for f in findings if f.severity == 'error')
        verify['gate_detail'] = [f.render() for f in findings if f.severity == 'error'][:5]
    except Exception as ex:  # noqa: BLE001
        verify['gate_detail'] = [f'gate 运行失败: {ex}']
    try:
        import ezdxf
        doc = ezdxf.readfile(dxf)
        lf = 1.0
        try:
            lf = doc.dimstyles.get('GB_DIM').dxf.dimlfac
        except Exception:
            pass
        for d in doc.modelspace().query('DIMENSION'):
            verify['dims'].append(round(d.get_measurement() * lf, 2))
            if len(verify['dims']) >= 12:
                break
    except Exception:
        pass

    # 前后对比图 (源目录有 output.dxf 时)
    compare = None
    old_dxf = Path(task_dir) / 'output.dxf'
    if old_dxf.exists():
        try:
            from src.engine.cad_modify import side_by_side_png
            compare = str(out / 'compare.png')
            side_by_side_png(str(old_dxf), dxf, compare, '修改前', '修改后')
        except Exception:
            compare = None

    (out / 'modify_plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=1),
                                          encoding='utf-8')
    report = {'ok': True, 'kind': 'proj_source', 'note': plan.get('note', ''),
              'ops': [{'op': l.get('op'), 'changed': l.get('changed')} for l in log],
              'dxf': dxf, 'compare_png': compare, 'verify': verify,
              'llm': meta.get('llm'), 'attempts': meta.get('attempts', 0),
              'out_dir': str(out)}
    (out / 'modify_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                            encoding='utf-8')
    return report
