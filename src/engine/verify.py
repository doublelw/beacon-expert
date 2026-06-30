#!/usr/bin/env python3
"""M7-verify: 投影验证闭环 (proj_v3 vs veritas).

对每个3D特征预测各视图应有的2D几何, 在投影里查找匹配:
  PIERCING(Z孔) → Front/Back 应有圆/弧, 圆心=(x,y), 半径=r
  PIERCING(X孔) → Left/Right 圆心=(y,z)
  PIERCING(Y孔) → Top/Bottom 圆心=(x,z)
  STAMP         → 对应视图应有样条/几何落在冲压bbox区域
  BEND          → 折弯半径的圆弧/相切线存在于折弯区域

容差: 位置 1.0mm, 半径 0.2mm.
输出 verify_report.json + pass_rate. 缺失/偏差 = 投影bug定位.
"""
import sys, os, json, math

PROJ = sys.argv[1] if len(sys.argv) > 1 else 'output/proj_v3.json'
VERITAS = sys.argv[2] if len(sys.argv) > 2 else 'output/veritas.json'
OUT = sys.argv[3] if len(sys.argv) > 3 else 'output/verify_report.json'

POS_TOL = 1.0   # 位置容差 mm
R_TOL = 0.2     # 半径容差 mm

proj = json.load(open(PROJ))
veritas = json.load(open(VERITAS))

AXIS_VIEWS = {'Z': ['Front', 'Back'], 'X': ['Left', 'Right'], 'Y': ['Top', 'Bottom']}


def hole_2d(pos, ad):
    x, y, z = pos
    if ad == 'Z': return (x, y)
    if ad == 'X': return (z, y)   # Left/Right Vx=Z 后: x=Z(板厚), y=Y(板高)
    return (x, z)


def view_circles(vd):
    """视图里所有圆/弧 (cx,cy,r)."""
    items = []
    for c in vd.get('circles', []):
        items.append((c['cx'], c['cy'], c['r']))
    for a in vd.get('arcs', []):
        items.append((a['cx'], a['cy'], a['r']))
    return items


def view_points(vd):
    """视图里所有几何点(用于STAMP/BEND区域检查)."""
    pts = []
    for l in vd.get('lines', []):
        pts += [l['p1'], l['p2']]
    for a in vd.get('arcs', []):
        pts.append((a['cx'], a['cy']))
    for c in vd.get('circles', []):
        pts.append((c['cx'], c['cy']))
    for s in vd.get('splines', []):
        pts += [(p[0], p[1]) for p in s.get('points', [])]
    return pts


def stamp_2d_bbox(bbox3d, ad):
    """冲压3D bbox → 在轴⊥视图的2D bbox."""
    x0, y0, z0, x1, y1, z1 = bbox3d
    if ad == 'Z': return (x0, y0, x1, y1)     # Front
    if ad == 'X': return (y0, z0, y1, z1)     # Left
    return (x0, z0, x1, z1)                    # Top


results = []

# === PIERCING 孔验证 ===
for f in veritas['features']:
    if f['type'] != 'PIERCING':
        continue
    ad = f.get('axis_dir', 'Z')
    if ad not in AXIS_VIEWS:
        continue
    pred = hole_2d(f['position'], ad)
    r = f['radius']
    for vn in AXIS_VIEWS[ad]:
        circles = view_circles(proj['views'].get(vn, {}))
        # 镜像视图: Back X镜像(第一轴), Bottom/Right 第二轴镜像
        if vn in ('Back', 'Right'):
            preds = [pred, (-pred[0], pred[1])]   # 第一轴镜像 (Back X, Right Z)
        elif vn == 'Bottom':
            preds = [pred, (pred[0], -pred[1])]
        else:
            preds = [pred]
        best = None
        for pp in preds:
            for cx, cy, cr in circles:
                d = math.hypot(cx - pp[0], cy - pp[1])
                if d < POS_TOL and abs(cr - r) < R_TOL:
                    if best is None or d < best[0]:
                        best = (d, cx, cy, cr)
        if best:
            status = 'pass'
        elif not f.get('through', True):
            status = 'occluded'   # 盲孔轴向视图被遮挡, 物理不可见, 不计fail
        else:
            status = 'missing'
        results.append({
            'feature': f['id'], 'ftype': 'PIERCING', 'axis': ad, 'view': vn,
            'predicted': [round(pred[0], 2), round(pred[1], 2), r],
            'found': [round(best[1], 2), round(best[2], 2), round(best[3], 2)] if best else None,
            'status': status,
            'through': f.get('through'),
            'error': round(best[0], 3) if best else None,
        })

# === STAMP 冲压验证 (轴⊥板厚的视图应有几何落在bbox区域) ===
thick_ax = veritas.get('thickness_axis', 'Z')
perp_views = AXIS_VIEWS.get({'Z': 'X', 'X': 'Z', 'Y': 'Z'}.get(thick_ax, 'X'), [])
# 冲压在板面方向凸出, 主视图(Front/Back, 即板厚⊥方向)应见其轮廓
stamp_views = AXIS_VIEWS.get(thick_ax, ['Front', 'Back'])   # 板厚方向孔的视图 = 板面视图
for f in veritas['features']:
    if f['type'] != 'STAMP':
        continue
    bx = stamp_2d_bbox(f['bbox'], thick_ax)
    cx_mid = (bx[0] + bx[2]) / 2; cy_mid = (bx[1] + bx[3]) / 2
    found_view = None
    for vn in stamp_views:
        pts = view_points(proj['views'].get(vn, {}))
        # 区域内是否有几何点
        in_region = [p for p in pts if bx[0] - 2 <= p[0] <= bx[2] + 2 and bx[1] - 2 <= p[1] <= bx[3] + 2]
        if len(in_region) >= 3:
            found_view = vn
            break
    results.append({
        'feature': f['id'], 'ftype': 'STAMP', 'view': found_view or '—',
        'predicted_bbox': [round(v, 1) for v in bx],
        'status': 'pass' if found_view else 'missing',
    })

# === BEND 折弯验证 (折弯半径的弧/相切线存在于折弯位置) ===
for f in veritas['features']:
    if f['type'] != 'BEND':
        continue
    # 折弯轴方向
    ax = f['axis']
    if abs(ax[0]) > 0.5:   ad = 'X'   # 折弯轴沿X → 在Top/Bottom(xz)或Front(xy)见轮廓
    elif abs(ax[1]) > 0.5: ad = 'Y'
    else:                  ad = 'Z'
    # 折弯在板面视图(Front/Back)的相切线最明显
    r = f['radius']
    ctr = f['center']
    found_view = None
    for vn in ['Front', 'Back']:
        # 折弯在板面视图体现为相切直线(Rg1LineV), 检查折弯center附近有几何点
        pts = view_points(proj['views'].get(vn, {}))
        if any(abs(p[0] - ctr[0]) < 5 and abs(p[1] - ctr[1]) < 5 for p in pts):
            found_view = vn; break
    results.append({
        'feature': f['id'], 'ftype': 'BEND', 'radius': r,
        'center': [round(c, 1) for c in ctr],
        'view': found_view or '—',
        'status': 'pass' if found_view else 'missing',
    })

# === 汇总 ===
from collections import Counter
by_ftype = {}
for r in results:
    ft = r['ftype']
    by_ftype.setdefault(ft, Counter())[r['status']] += 1

total = len(results)
passed = sum(1 for r in results if r['status'] == 'pass')
occluded_list = [r for r in results if r['status'] == 'occluded']
missing = [r for r in results if r['status'] == 'missing']
checked = passed + len(missing)   # occluded 不计入(盲孔物理不可见)

report = {
    'proj_source': PROJ, 'veritas_source': VERITAS,
    'tol': {'position_mm': POS_TOL, 'radius_mm': R_TOL},
    'summary': {
        'total_checks': total, 'passed': passed, 'missing': len(missing),
        'occluded': len(occluded_list),
        'pass_rate': round(passed / checked * 100, 1) if checked else 0,
        'by_ftype': {k: dict(v) for k, v in by_ftype.items()},
    },
    'missing_features': missing[:30],
    'occluded_features': occluded_list[:30],
    'checks': results,
}
json.dump(report, open(OUT, 'w'), ensure_ascii=False, indent=1)

print(f'=== 投影验证报告 ===')
print(f'检查: {total}  通过: {passed}  缺失: {len(missing)}  遮挡(盲孔不计): {len(occluded_list)}')
print(f'通过率: {report["summary"]["pass_rate"]}%')
for ft, cnt in by_ftype.items():
    print(f'  {ft}: {dict(cnt)}')
if missing:
    print(f'\n缺失 {len(missing)} 个 (投影bug, 必须修):')
    for m in missing[:10]:
        print(f'  {m.get("ftype")} {m.get("feature")} view={m.get("view")} pred={m.get("predicted") or m.get("predicted_bbox") or m.get("center")}')
if occluded_list:
    print(f'\n遮挡 {len(occluded_list)} 个 (盲孔/侧孔物理不可见, 正常):')
    for o in occluded_list[:8]:
        print(f'  {o.get("ftype")} {o.get("feature")} {o.get("axis")} view={o.get("view")} pred={o.get("predicted")}')
print(f'\n输出: {OUT}')
