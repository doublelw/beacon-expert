#!/usr/bin/env python3
"""M1-veritas: 3D特征真值提取 (钣金件).

复用 understand() 的孔提取 (去重/沉头/各轴向), 新增钣金特征分类:
  PIERCING  孔(各向)        — understand holes
  BEND      折弯圆柱面      — Cylinder 非孔壁, 轴⊥板厚, 跨度>2×板厚
  STAMP     冲压曲面        — BSplineSurface (异形凸起/型腔)
  CHAMFER   倒角            — Cone 面

输出 veritas.json, 供 verify 按特征预测各视图2D几何核对.
"""
import sys, os, json
import FreeCAD, Part

STP = os.environ.get('STP', '')
OUT = os.environ.get('OUT', 'output/veritas.json')
Q = 3   # 小数精度

shape = Part.read(STP)
bbox = shape.BoundBox

# 板厚方向 = bbox 最小维
dims = [('X', bbox.XLength), ('Y', bbox.YLength), ('Z', bbox.ZLength)]
dims.sort(key=lambda d: d[1])
thick_axis = dims[0][0]
thickness = dims[0][1]
print(f'bbox: X{bbox.XLength:.1f} × Y{bbox.YLength:.1f} × Z{bbox.ZLength:.1f}')
print(f'板厚方向: {thick_axis} = {thickness:.2f}mm')

# === 复用 understand 提取孔 ===
print('\n加载 understand 提取孔...')
try:
    from understand import understand
    u = understand(STP)
    holes = u.get('holes', [])
    print(f'  孔: {len(holes)} (understand)')
except Exception as e:
    print(f'  understand 失败({e}), 手工提取')
    holes = []

hole_face_ids = set()
for h in holes:
    for fid in h.get('face_ids', []):
        hole_face_ids.add(fid)

# === 特征清单 ===
features = []
fid = [0]
def nid():
    fid[0] += 1
    return f'F{fid[0]:04d}'

# 孔 → PIERCING (含贯穿判断)
import math as _math
PLATE = {'X': bbox.XLength, 'Y': bbox.YLength, 'Z': bbox.ZLength}
for h in holes:
    d = h.get('direction', 'Z+')
    ad = 'Z' if d.startswith('Z') else ('X' if d.startswith('X') else ('Y' if d.startswith('Y') else 'other'))
    # 贯穿判断: 找轴向圆柱面 span vs 板该方向尺寸 (span>85%板尺寸=贯穿)
    max_span = 0.0
    if ad in PLATE:
        for face in shape.Faces:
            try:
                s = face.Surface
                if s.__class__.__name__ != 'Cylinder':
                    continue
                ax = s.Axis; ctr = s.Center; fr = float(s.Radius)
                axdir_ok = (ad == 'Z' and abs(ax.z) > 0.7) or \
                           (ad == 'X' and abs(ax.x) > 0.7) or \
                           (ad == 'Y' and abs(ax.y) > 0.7)
                if not axdir_ok:
                    continue
                if ad == 'Z':   dd = _math.hypot(ctr.x - h['x'], ctr.y - h['y'])
                elif ad == 'X': dd = _math.hypot(ctr.y - h['y'], ctr.z - h['z'])
                else:           dd = _math.hypot(ctr.x - h['x'], ctr.z - h['z'])
                if dd > 2 or abs(fr - h['radius']) > 0.3:
                    continue
                bb = face.BoundBox
                span = bb.XLength if ad == 'X' else (bb.YLength if ad == 'Y' else bb.ZLength)
                if span > max_span:
                    max_span = span
            except Exception:
                pass
    # 板厚方向孔(Z)默认贯穿(穿板厚常见); 侧向孔(X/Y)按span判断盲/贯穿
    if ad == thick_axis:
        through = True
    elif ad in PLATE:
        through = max_span > 0.85 * PLATE[ad]
    else:
        through = True
    features.append({
        'id': nid(), 'type': 'PIERCING', 'axis_dir': ad, 'direction': d,
        'position': [round(h['x'], Q), round(h['y'], Q), round(h['z'], Q)],
        'radius': round(h['radius'], Q), 'diameter': round(h['diameter'], Q),
        'all_radii': h.get('all_radii', []),
        'through': through, 'span': round(max_span, Q),
        'source': 'understand',
    })

# 遍历面 → 折弯/冲压/倒角
bends, stamps, chamfers, fillets = [], [], [], []
for fi, face in enumerate(shape.Faces):
    try:
        s = face.Surface
        cn = s.__class__.__name__
        # 注: 不依赖 understand face_ids (索引可能不一致), 用几何判折弯
        if cn == 'Cylinder':
            ax = s.Axis
            r = float(s.Radius)
            if r < 0.5:
                fillets.append({'face': fi, 'r': r})
                continue   # 小半径=圆角
            bb = face.BoundBox
            if abs(ax.x) > 0.7:   span = bb.XLength
            elif abs(ax.y) > 0.7: span = bb.YLength
            else:                 span = bb.ZLength
            perp_thick = (thick_axis == 'Z' and abs(ax.z) < 0.5) or \
                         (thick_axis == 'X' and abs(ax.x) < 0.5) or \
                         (thick_axis == 'Y' and abs(ax.y) < 0.5)
            # 折弯: 轴⊥板厚 + 跨度>1.2×板厚 (孔壁/侧向孔span≈板厚, 被过滤)
            if perp_thick and span > 1.2 * thickness:
                ctr = s.Center
                bends.append({
                    'face': fi, 'radius': round(r, Q),
                    'axis': [round(ax.x, 3), round(ax.y, 3), round(ax.z, 3)],
                    'center': [round(ctr.x, Q), round(ctr.y, Q), round(ctr.z, Q)],
                    'span': round(span, Q),
                })
            # 轴∥板厚的圆柱 = 孔壁(understand已计); 短侧向圆柱忽略

        elif cn == 'BSplineSurface':
            bb = face.BoundBox
            stamps.append({
                'face': fi,
                'bbox': [round(bb.XMin, Q), round(bb.YMin, Q), round(bb.ZMin, Q),
                         round(bb.XMax, Q), round(bb.YMax, Q), round(bb.ZMax, Q)],
                'center': [round(bb.Center.x, Q), round(bb.Center.y, Q), round(bb.Center.z, Q)],
            })

        elif cn == 'Cone':
            ax = s.Axis; ctr = s.Center; r = float(s.Radius)
            chamfers.append({
                'face': fi, 'radius': round(r, Q),
                'axis': [round(ax.x, 3), round(ax.y, 3), round(ax.z, 3)],
                'center': [round(ctr.x, Q), round(ctr.y, Q), round(ctr.z, Q)],
            })
    except Exception:
        pass

for b in bends:
    features.append({'id': nid(), 'type': 'BEND', **b, 'source': 'cylinder_face'})
for st in stamps:
    features.append({'id': nid(), 'type': 'STAMP', 'surface': 'BSpline', **st, 'source': 'bspline_face'})
for c in chamfers:
    features.append({'id': nid(), 'type': 'CHAMFER', **c, 'source': 'cone_face'})

# 外形轮廓 (板 bbox 极值)
features.append({'id': nid(), 'type': 'OUTLINE',
                 'bbox': [round(bbox.XMin, Q), round(bbox.YMin, Q), round(bbox.ZMin, Q),
                          round(bbox.XMax, Q), round(bbox.YMax, Q), round(bbox.ZMax, Q)],
                 'width': round(bbox.XLength, Q), 'height': round(bbox.YLength, Q),
                 'depth': round(bbox.ZLength, Q)})

# 自动孔类型识别 (通用, 从STEP几何判断, 非硬编码)
# Cone面关联 = 沉头孔(csink); 多圆柱大小径 = 螺纹孔(thread); 单圆柱 = 过孔(clear)
import math as _m2
from collections import Counter
csink_xy = [(c['center'][0], c['center'][1]) for c in chamfers]
hole_type_count = Counter()
for f in features:
    if f['type'] != 'PIERCING':
        continue
    px, py, pz = f['position']
    near_cone = any(_m2.hypot(cx - px, cy - py) < 3 for cx, cy in csink_xy)
    if near_cone:
        f['hole_type'] = 'csink'               # 沉头(Cone几何关联)
        # 钉头面: 关联Cone的z接近板哪面 (zmin=-Z面 / zmax=+Z面)
        near_c = min(chamfers, key=lambda c: _m2.hypot(c['center'][0]-px, c['center'][1]-py))
        cz = near_c['center'][2]
        f['head_face'] = '-Z' if abs(cz - bbox.ZMin) < abs(cz - bbox.ZMax) else '+Z'
    elif len(f.get('all_radii', [])) > 1:
        f['hole_type'] = 'thread'              # 螺纹(大小径双圆柱)
    else:
        f['hole_type'] = 'clear'               # 过孔(单圆柱)
    hole_type_count[f['hole_type']] += 1

# 汇总
tc = Counter(f['type'] for f in features)
ac = Counter(f.get('axis_dir', '') for f in features if f['type'] == 'PIERCING')

# M0 工艺分类 (钣金/注塑/机加工, 决定工程图模板与标准件库)
_dims = sorted([bbox.XLength, bbox.YLength, bbox.ZLength])
_is_thin_plate = _dims[0] < 0.35 * _dims[1]   # 板厚 << 第二维
# 钣金板厚通常<5mm; 注塑/机加工最小维>=5
if _dims[0] < 5 and _is_thin_plate:
    # 钣金: 薄板(板厚<5mm) + 折弯
    process = 'sheet_metal' if len(bends) > 0 else 'sheet_metal_flat'
else:
    # 非钣金: 注塑(BSpline薄壁面多) vs 机加工(实心/金属)
    _bspline_n = sum(1 for f in shape.Faces
                     if f.Surface.__class__.__name__ == 'BSplineSurface')
    process = 'injection_molding' if _bspline_n > 5 else 'machining'

result = {
    'source': STP, 'method': 'veritas_v1',
    'process': process,            # 工艺分类(M0): 决定下游模板
    'bbox': {'xmin': round(bbox.XMin, Q), 'ymin': round(bbox.YMin, Q), 'zmin': round(bbox.ZMin, Q),
             'xmax': round(bbox.XMax, Q), 'ymax': round(bbox.YMax, Q), 'zmax': round(bbox.ZMax, Q)},
    'thickness_axis': thick_axis, 'thickness': round(thickness, Q),
    'features': features,
    'summary': {
        'total_features': len(features),
        'by_type': dict(tc),
        'holes_by_axis': dict(ac),
        'bends': len(bends), 'stamps': len(stamps),
        'chamfers': len(chamfers), 'fillets_excluded': len(fillets),
    },
}

os.makedirs(os.path.dirname(OUT) or '.', exist_ok=True)
json.dump(result, open(OUT, 'w'), ensure_ascii=False, indent=1)

print(f'\n=== veritas 特征清单 ===')
print(f'  PIERCING(孔): {len(holes)}  → {dict(ac)}')
print(f'  BEND(折弯): {len(bends)}')
print(f'  STAMP(冲压): {len(stamps)}')
print(f'  CHAMFER(倒角): {len(chamfers)}')
print(f'  FILLET(圆角,排除): {len(fillets)}')
print(f'  总特征: {len(features)}')
print(f'\n输出: {OUT}')
