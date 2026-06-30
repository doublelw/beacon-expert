#!/usr/bin/env python3
"""从 veritas 自动提取所有尺寸 → 生成完整尺寸 ai_plan.
AI识别零部件 + 全部制造尺寸(直径/深度/位置/间距/贯穿/折弯角度/冲压尺寸).
"""
import json, math
from collections import defaultdict

v = json.load(open('output/veritas.json'))
bb = v['bbox']
W = bb['xmax'] - bb['xmin']
H = bb['ymax'] - bb['ymin']
D = bb['zmax'] - bb['zmin']

# 按规格分组孔(同 radii+轴向+类型)
groups = defaultdict(list)
for f in v['features']:
    if f['type'] != 'PIERCING':
        continue
    ar = tuple(sorted(round(x, 1) for x in f.get('all_radii', [f['radius']])))
    key = (ar, f.get('axis_dir'), f.get('hole_type'))
    groups[key].append(f)


def spacing(positions, axis_idx):
    """同规格孔在某轴的相邻间距(阵列检测)"""
    if len(positions) < 2:
        return None
    vals = sorted(set(round(p[axis_idx], 1) for p in positions))
    if len(vals) < 2:
        return None
    diffs = [round(vals[i+1] - vals[i], 1) for i in range(len(vals)-1)]
    # 主间距(出现最多)
    from collections import Counter
    common = Counter(diffs).most_common(1)[0]
    return {'min_spacing_mm': min(diffs), 'typical_mm': common[0], 'count_at_typical': common[1]}


# AI识别零部件(基于几何组合+GB知识) + 配钉选型(用什么钉)
def identify_part(radii, axis, hole_type, head_face):
    rmax = max(radii)
    rmin = min(radii)
    d = round(rmax * 2, 1)
    if hole_type == 'csink':
        m = int(d)
        return f'沉头螺钉 M{m}×15 (底孔φ{round(rmin*2,1)}/沉头φ{d})', d
    if hole_type == 'thread' and len(radii) > 1:
        m = int(d)
        return f'配 M{m} 螺钉 (压铆螺母BSO-M{m}, 底径φ{round(rmin*2,1)}/大径φ{d})', d
    # clear 单圆柱 → 配钉选型
    if d <= 3:
        return f'小过孔φ{d} (配M{max(int(d)-1,2)}螺钉)', d
    if d >= 14:
        return f'过线/让位孔φ{d} (无钉)', d
    m = max(int(d) - 1, 3)
    return f'过孔φ{d} (配M{m}螺钉 或 φ{d:g}抽芯铆钉)', d


fasteners = []
_callout_idx = [0]
def _next_callout():
    _callout_idx[0] += 1
    return f'Y{_callout_idx[0]}'

for (radii, axis, ht), holes in sorted(groups.items(), key=lambda x: -len(x[1])):
    hf = holes[0].get('head_face')
    part, d = identify_part(radii, axis, ht, hf)
    positions = [h['position'] for h in holes]
    ai_idx = {'X': 0, 'Y': 1, 'Z': 2}[axis]
    sp = spacing(positions, ai_idx)
    # AI分配紧固件代号 + 配钉选型(用什么钉, 对标样例 Y1:9-S-M3-1)
    callout = _next_callout()
    spec = f'{callout}: {len(holes)}个 — {part}'   # part含配钉规格
    fasteners.append({
        'callout': callout,
        'spec': spec,
        'part': part,
        'spec_diameter_mm': d,
        'all_radii': list(radii),
        'axis': axis,
        'through': bool(holes[0].get('through', False)),
        'depth_mm': round(D, 1) if holes[0].get('through') else holes[0].get('span'),
        'head_face': hf,
        'count': len(holes),
        'positions': positions,
        'spacing': sp,
    })

# 折弯/冲压/倒角尺寸
bends = []
for f in v['features']:
    if f['type'] == 'BEND':
        bends.append({'radius_mm': f['radius'], 'axis': f['axis'],
                      'center': f['center'], 'span_mm': f.get('span')})
stamps = []
for f in v['features']:
    if f['type'] == 'STAMP':
        bx = f['bbox']
        stamps.append({'size_mm': [round(bx[3]-bx[0],1), round(bx[4]-bx[1],1), round(bx[5]-bx[2],1)],
                       'center': f['center']})
chamfers = [{'radius_mm': f.get('radius'), 'center': f.get('center')} for f in v['features'] if f['type'] == 'CHAMFER']

plan = {
    '_meta': {'method': 'AI识别零部件+全部尺寸提取', 'source': 'veritas.json'},
    'part_outline': {'width_mm': round(W,1), 'height_mm': round(H,1), 'depth_mm': round(D,1),
                     'bbox': [bb['xmin'], bb['ymin'], bb['zmin'], bb['xmax'], bb['ymax'], bb['zmax']]},
    'fasteners': fasteners,
    'bends': bends,
    'stamps': stamps,
    'chamfers': chamfers,
    'total_holes': sum(f['count'] for f in fasteners),
}
json.dump(plan, open('output/ai_plan.json', 'w'), ensure_ascii=False, indent=1)

print(f'=== AI完整尺寸识别 ({len(fasteners)}类钉/孔) ===')
for f in fasteners:
    sp_str = f" 间距{f['spacing']['typical_mm']}mm×{f['spacing']['count_at_typical']}" if f['spacing'] else ''
    print(f"  {f['part']}: {f['count']}个 φ{f['spec_diameter_mm']} {f['axis']}轴 {'贯穿' if f['through'] else '盲'}{sp_str}")
print(f"\n折弯:{len(bends)} 冲压:{len(stamps)} 倒角:{len(chamfers)}")
print(f"板外形: {W:.0f}×{H:.0f}×{D:.0f}mm")
print("输出: output/ai_plan.json")
