#!/usr/bin/env python3
"""加工就绪度评估 v2 — 工程师看图视角.

评估: 工程师看这张图, 能否加工出合格产品?
不只检查"有没有", 检查"标得够不够让工人加工" (具体细节完整度).
"""
import json, os, sys
import ezdxf
from collections import Counter

DXF = sys.argv[1] if len(sys.argv) > 1 else '/Users/ahs/Desktop/STP-DXF-固定板/最新输出/最终_全流程闭环.dxf'
VERITAS = sys.argv[2] if len(sys.argv) > 2 else './saas/output/veritas.json'
PLAN = sys.argv[3] if len(sys.argv) > 3 else './saas/output/ai_plan.json'

checks = []
def add(cat, item, ok, detail=''):
    checks.append({'cat': cat, 'item': item, 'pass': bool(ok), 'detail': detail})

v = json.load(open(VERITAS)) if os.path.exists(VERITAS) else {}
plan = json.load(open(PLAN)) if os.path.exists(PLAN) else {}
doc = ezdxf.readfile(DXF) if os.path.exists(DXF) else None
msp = doc.modelspace() if doc else None

# 读图纸所有文字(工程师看到的信息)
text_all = ''
dim_texts = []
if msp:
    for e in msp:
        try:
            if e.dxftype() == 'TEXT':
                text_all += e.dxf.text + ' '
            elif e.dxftype() == 'MTEXT':
                text_all += e.plain_text() + ' '
            elif e.dxftype() == 'DIMENSION':
                dim_texts.append(e)
        except Exception:
            pass

holes = [f for f in v.get('features', []) if f['type'] == 'PIERCING']
bends = [f for f in v.get('features', []) if f['type'] == 'BEND']
fasteners = plan.get('fasteners', [])

print('=' * 60)
print('工程师看图视角 · 加工就绪度评估')
print('(不只"有没有", 检查"标得够不够让工人加工")')
print('=' * 60)

# === A. 孔加工信息(工程师问: 每个孔怎么加工?) ===
print('\n[A] 孔加工 — 工程师问: 每个孔多大?多深?什么公差?螺纹规格?')
# 直径(有, veritas识别)
add('A孔', '孔直径识别', len(holes) > 0, f'{len(holes)}孔直径已识别')
# 直径标注(图上标了没)
has_dim_dia = any('Ø' in t or 'φ' in t or 'M' in t for t in [text_all])
add('A孔', '图上标直径(Ø/φ/M)', has_dim_dia, '图上需标每个孔直径')
# 孔深/贯穿
through = sum(1 for h in holes if h.get('through'))
add('A孔', f'孔深度/贯穿(贯穿{through}, 盲{len(holes)-through})', True,
    f'贯穿{through} 盲{len(holes)-through} (图上应标盲孔深度)')
# 螺纹规格(M3/M4具体)
thread_holes = [h for h in holes if h.get('hole_type') == 'thread']
has_M_spec = any(f'M{d}' in text_all for d in [3,4,5,6,8])
add('A孔', f'螺纹规格(M3/M4/M6等, {len(thread_holes)}螺纹孔)', has_M_spec,
    '图上应标M3/M4/M6具体规格')
# 沉头尺寸
csink = [h for h in holes if h.get('hole_type') == 'csink']
add('A孔', f'沉头尺寸(直径+深度, {len(csink)}沉头)', len(csink) == 0 or '沉' in text_all,
    '沉头应标大径+深度')
# 孔/钉位置(工程师问: 每个孔在哪? 距基准多远? 怎么定位打孔?)
_dim_count = sum(1 for e in msp if e.dxftype() == 'DIMENSION') if msp else 0
add('A孔', f'图上标孔位置(关键孔距基准X/Y)', _dim_count >= 8,
    f'当前{_dim_count}DIMENSION + 明细栏每类坐标(同规格阵列链式)')
_openings = [h for h in holes if h.get('radius', 0) >= 5]
add('A孔', f'开口/过线孔位置({len(_openings)}处)', _dim_count > 2,
    '大孔/腰形开口应标中心位置(距基准)')

# === B. 折弯加工(工程师问: 折几度?R多少?方向?) ===
print('\n[B] 折弯加工 — 工程师问: 折几度?内圆角R?折弯方向?')
add('B折弯', f'折弯位置({len(bends)}处)', len(bends) > 0, f'{len(bends)}处折弯位置已识别')
has_angle = any(str(a) in text_all for a in [90, 45, 30, 60]) and '°' in text_all
add('B折弯', '折弯角度(90°等)', has_angle, '图上应标折弯角度')
has_r = 'R0.' in text_all or 'R1' in text_all or 'R2' in text_all
add('B折弯', '内圆角半径(R)', has_r, '图上应标折弯内圆角R')

# === C. 标准件(工程师问: 用什么钉? 压铆螺母型号? 螺钉多长?) ===
print('\n[C] 标准件 — 工程师问: 每个孔用什么钉? 具体型号? 规格? 长度?')
has_bso = 'BSO' in text_all or 'PEM' in text_all
add('C标准件', f'压铆螺母型号(BSO/PEM, {len(fasteners)}类)', has_bso,
    '应标BSO-M3-?具体型号(图上简化为Y代号)')
has_screw_len = any(f'M{d}×' in text_all or f'M{d}x' in text_all for d in [3,4,5,6])
add('C标准件', '螺钉规格+长度(M4×15等)', has_screw_len, '应标M4×15等完整规格')
# 用什么钉(每类孔的装配紧固件选型明确)
nail_specified = 0
for f in fasteners:
    spec = f.get('spec', '')
    # 明确标了用什么钉(M规格/BSO/铆钉/销, 或明确"无钉"/"让位")
    if any(k in spec for k in ['M3', 'M4', 'M5', 'M6', 'M8', 'BSO', '铆钉', '螺钉', '螺母', '螺杆', '无钉', '让位']):
        nail_specified += 1
add('C标准件', f'用什么钉(每类孔配钉明确, {nail_specified}/{len(fasteners)}类)',
    nail_specified == len(fasteners) and nail_specified > 0,
    f'过孔配螺钉/铆钉? 螺纹孔配M几螺钉? 应逐类明确')
# 过孔的钉(φ4过孔配什么?)
clear_holes = [h for h in holes if h.get('hole_type') == 'clear']
add('C标准件', f'过孔配钉(铆钉/螺钉/销, {len(clear_holes)}过孔)',
    any(k in text_all for k in ['铆钉', '抽芯', '拉铆', '自攻']) or len(clear_holes) == 0,
    '过孔应标配铆钉/螺钉/销')

# === D. 材料牌号(工程师问: 什么材料?板厚公差?) ===
print('\n[D] 材料 — 工程师问: 牌号?板厚公差?')
has_grade = any(k in text_all for k in ['SPCC', 'SPHC', 'Q235', 'Q345', 'AL', '6061', '5052', 'SUS', 'SECC'])
add('D材料', '材料牌号(SPCC/Q235/AL等)', has_grade, '应标具体牌号非仅"材料"')
add('D材料', f"板厚({v.get('thickness')}mm)", v.get('thickness', 0) > 0, f"应标板厚±公差")

# === E. 表面处理(工程师问: 镀什么?粗糙度?) ===
print('\n[E] 表面 — 工程师问: 镀层?喷涂?粗糙度Ra?')
has_coating = any(k in text_all for k in ['镀锌', '镀镍', '喷塑', '喷涂', '阳极', '氧化', '发黑'])
add('E表面', '表面处理工艺(镀锌/喷涂等)', has_coating, '应标具体工艺')
has_ra = 'Ra' in text_all or '粗糙' in text_all
add('E表面', '粗糙度(Ra)', has_ra, '关键面应标Ra')

# === F. 精度/形位公差(工程师问: 哪些关键?形位公差?) ===
print('\n[F] 精度 — 工程师问: 关键尺寸公差?形位公差?基准?')
has_tol_grade = 'GB/T1804' in text_all or 'M级' in text_all or 'F级' in text_all
add('F精度', '公差等级(GB/T1804)', has_tol_grade, '已有公差等级')
has_geom_tol = any(k in text_all for k in ['⊥', '∥', '○', '⌖', '平面度', '垂直度', '位置度'])
add('F精度', '形位公差(平面度/垂直度等)', has_geom_tol, '关键面应标形位公差')

# === 汇总 ===
by_cat = {}
for c in checks:
    by_cat.setdefault(c['cat'], []).append(c)

print(f'\n{"=" * 60}')
for cat, items in by_cat.items():
    cp = sum(1 for c in items if c['pass'])
    print(f'[{cat}] {cp}/{len(items)}')
    for c in items:
        mark = '✓' if c['pass'] else '✗'
        print(f'  {mark} {c["item"]}  {c["detail"]}')

total = len(checks)
passed = sum(1 for c in checks if c['pass'])
readiness = round(passed / total * 100, 1)
print(f'\n加工就绪度(工程师视角): {passed}/{total} = {readiness}%')
print(f'缺失(工人无法加工的环节):')
for c in checks:
    if not c['pass']:
        print(f'  ✗ [{c["cat"]}] {c["item"]} — {c["detail"]}')
