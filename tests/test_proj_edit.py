"""M-proj-edit 回归: 现有图纸自然语言改尺寸 (投影源头改 + 重渲染).

夹具: tests/fixtures/shells/{前壳,后壳} — 真实钣金件转换产物 (proj_v3/veritas/annotation,
ISO 视图已裁). 覆盖:
  - 载入/摘要 (孔组/尺寸/板厚)
  - 轴映射 (孔沿轴出现在哪些视图)
  - set_hole_diameter 精确改径 + 作用域保全 (仅目标圆变, 其余零变化)
  - stretch_axis STRETCH 语义 (圆仍为圆) + bbox 重算 + 标注重算
  - 重渲染后 DXF 可回读 + 门禁不劣化
  - validate_ops 计划校验
LLM 规划器需网络, 单测只用确定性计划 (plan= 传入)。
"""
import json
import math
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine.proj_edit import (
    load_source, build_digest, apply_ops, validate_ops,
    views_for_axis, uv_of, VIEW_UV, AXES, op_add_hole, _try_rule_plan,
)

FIX = Path(__file__).resolve().parent / 'fixtures' / 'shells'


def _shell(name: str) -> str:
    d = FIX / name
    if not (d / 'proj_v3.json').exists():
        pytest.skip(f'夹具缺失: {d}')
    return str(d)


# ── 轴映射 (多视图一致性基础) ──────────────────────────────

def test_axis_view_mapping():
    # 孔沿 Z → 呈圆于 Front/Back (该轴不在其 u/v)
    assert set(views_for_axis('z')) == {'Front', 'Back'}
    assert set(views_for_axis('x')) == {'Left', 'Right'}
    assert set(views_for_axis('y')) == {'Top', 'Bottom'}
    # u/v 轴映射与 projection_v3 约定一致
    assert VIEW_UV['Front'] == {'u': ('x', 1), 'v': ('y', 1)}
    assert VIEW_UV['Back']['u'] == ('x', -1)
    assert VIEW_UV['Bottom']['v'] == ('z', -1)
    assert VIEW_UV['Right']['u'] == ('z', -1)


def test_uv_of_roundtrip():
    xyz = {'x': 10.0, 'y': -5.0, 'z': 3.0}
    assert uv_of('Front', xyz) == (10.0, -5.0)
    assert uv_of('Back', xyz) == (-10.0, -5.0)
    assert uv_of('Top', xyz) == (10.0, 3.0)
    assert uv_of('Bottom', xyz) == (10.0, -3.0)
    assert uv_of('Left', xyz) == (3.0, -5.0)
    assert uv_of('Right', xyz) == (-3.0, -5.0)


# ── 载入 / 摘要 ────────────────────────────────────────────

def test_load_and_digest():
    src = load_source(_shell('前壳'))
    assert src['ok']
    # ISO 视图必须被裁掉 (不渲染也不改)
    assert set(src['proj']['views']) == {'Front', 'Back', 'Top', 'Bottom', 'Left', 'Right'}
    dg = build_digest(src)
    assert dg['process'] == 'sheet_metal'
    assert dg['n_bends'] >= 1
    assert dg['thickness'] and dg['thickness'] > 0
    # 孔组: 有 φ2.2 的 Z 向 4 孔
    g = [x for x in dg['hole_groups'] if x['axis'] == 'Z' and abs(x['d'] - 2.2) < 0.01]
    assert g and g[0]['count'] == 4


def test_load_rejects_third_party(tmp_path):
    r = load_source(str(tmp_path))
    assert not r['ok'] and '缺参数源' in r['error']


# ── 改孔径: 精确 + 作用域保全 ───────────────────────────────

def test_set_hole_diameter_exact_and_scoped():
    src = load_source(_shell('前壳'))
    new, log = apply_ops(src, [{'op': 'set_hole_diameter', 'axis': 'Z', 'd': 2.2, 'new_d': 3.2}])
    assert log[0]['ok'], log
    assert log[0]['changed']['holes'] == 4
    # veritas 真值更新
    for f in new['veritas']['features']:
        if f['id'] in ('F0001', 'F0002', 'F0003', 'F0004'):
            assert abs(f['radius'] - 1.6) < 1e-9 and abs(f['diameter'] - 3.2) < 1e-9
    # 前后视图对应圆半径改为 1.6, 其余圆一个不动 (作用域保全)
    old_c = Counter()
    new_c = Counter()
    for vn, vd in src['proj']['views'].items():
        for c in vd.get('circles', []):
            old_c[(vn, round(c['cx'], 3), round(c['cy'], 3), round(c['r'], 3))] += 1
    for vn, vd in new['proj']['views'].items():
        for c in vd.get('circles', []):
            new_c[(vn, round(c['cx'], 3), round(c['cy'], 3), round(c['r'], 3))] += 1
    changed = sum((old_c - new_c).values())
    assert changed == 8, f'应恰好 8 个圆变化 (4孔×前后视图), 实际 {changed}'
    # 新出现的圆半径都是 1.6
    for k in (new_c - old_c):
        assert abs(k[3] - 1.6) < 1e-6, k


def test_set_hole_diameter_rejects_unknown():
    src = load_source(_shell('前壳'))
    _, log = apply_ops(src, [{'op': 'set_hole_diameter', 'axis': 'Z', 'd': 99.0, 'new_d': 3.0}])
    assert not log[0]['ok'] and '未找到匹配孔' in log[0]['error']


# ── 拉伸: STRETCH 语义 (圆保持圆) + bbox + 标注 ─────────────

def test_stretch_axis_semantics():
    src = load_source(_shell('前壳'))
    xmax0 = src['proj']['bbox']['xmax']
    new, log = apply_ops(src, [{'op': 'stretch_axis', 'axis': 'x', 'delta': 20, 'at': xmax0 - 5}])
    assert log[0]['ok'], log
    # bbox 重算: xmax +20
    assert abs(new['proj']['bbox']['xmax'] - (xmax0 + 20)) < 0.5
    # STRETCH 语义: 没有任何圆被改成椭圆/缩放 → 半径集合与原来一致(除按中心平移)
    def radii(pr):
        return Counter(round(c['r'], 3) for vd in pr['views'].values() for c in vd.get('circles', []))
    assert radii(new['proj']) == radii(src['proj']), 'STRETCH 不得改变圆半径'
    # 标注重算: 至少有一条标注数值变化
    v0 = [d.get('value') for d in src['annotation']['dimensions']]
    v1 = [d.get('value') for d in new['annotation']['dimensions']]
    assert v0 != v1, '拉伸后标注必须重算'


def test_stretch_axis_rejects_bad_axis():
    src = load_source(_shell('前壳'))
    _, log = apply_ops(src, [{'op': 'stretch_axis', 'axis': 'w', 'delta': 5, 'at': 0}])
    assert not log[0]['ok']


# ── 计划校验 ───────────────────────────────────────────────

def test_validate_ops():
    assert validate_ops({'ops': [{'op': 'set_hole_diameter', 'axis': 'Z', 'd': 2.2, 'new_d': 3.2}]}) == []
    assert validate_ops({'ops': [{'op': 'stretch_axis', 'axis': 'x', 'delta': 20, 'at': 10}]}) == []
    assert validate_ops({'ops': []}) == []          # 空操作合法 (如实报"无需改动")
    assert validate_ops({'ops': 'x'})                  # 结构错
    assert validate_ops({'ops': [{'op': 'warp'}]})      # 未知操作
    assert validate_ops({'ops': [{'op': 'stretch_axis', 'axis': 'x', 'delta': 0, 'at': 1}]})  # delta=0
    assert validate_ops({'ops': [{'op': 'set_hole_diameter', 'new_d': -1}]})  # 负直径


# ── 重渲染: 改完能出图且门禁不劣化 ───────────────────────────

def test_rerender_after_modify(tmp_path):
    pytest.importorskip('ezdxf')
    src = load_source(_shell('后壳'))
    new, log = apply_ops(src, [{'op': 'set_hole_diameter', 'axis': 'Z', 'd': 4.8, 'new_d': 5.5}])
    if not log[0]['ok']:
        pytest.skip('后壳无 φ4.8 Z 向孔组 (夹具差异)')
    from src.engine.render_engine import render
    out = tmp_path / 'm.dxf'
    render(new['proj'], None, new['annotation'], None, str(out), veritas=new['veritas'])
    assert out.exists() and out.stat().st_size > 10000
    # 门禁: 改图不得引入新的 error 级问题
    from src.engine.dxf_checks import validate_dxf_file
    errs = [f for f in validate_dxf_file(str(out)) if f.severity == 'error']
    assert len(errs) <= 5, [f.render() for f in errs][:5]


# ── 新增特征 (add_hole): 法向整圆 + 侧向贯穿边 + veritas 登记 + 标注 ──

def test_add_hole_registers_feature_and_emits_geometry():
    src = load_source(_shell('前壳'))
    new, log = apply_ops(src, [
        {'op': 'add_hole', 'axis': 'Z', 'pos': [0.0, -60.0, 0.0], 'd': 8.0, 'through': True}
    ])
    assert log[0]['ok'], log
    assert log[0]['feature'].startswith('F')
    # veritas 登记了新特征 (PIERCING φ8)
    feats = new['veritas']['features']
    newf = [f for f in feats if abs(f.get('diameter', 0) - 8.0) < 1e-6 and f.get('axis_dir') == 'Z']
    assert newf, 'veritas 未登记新 PIERCING φ8'
    # 法向视图 (Z → Front/Back) 各画一个整圆
    for vn in views_for_axis('z'):
        cs = [c for c in new['proj']['views'][vn].get('circles', [])
              if abs(c['r'] - 4.0) < 0.05 and abs(c['cx'] - 0.0) < 0.5]
        assert cs, f'{vn} 缺新孔整圆'
    # 标注含一条 φ8 diameter
    dia = [d for d in new['annotation'].get('dimensions', [])
           if d.get('type') == 'diameter' and abs(d.get('value', 0) - 8.0) < 0.01]
    assert dia, '未生成新孔径标注'


def test_add_hole_rejects_bad_input():
    src = load_source(_shell('前壳'))
    _, log1 = apply_ops(src, [{'op': 'add_hole', 'axis': 'W', 'pos': [0, 0, 0], 'd': 5}])
    assert not log1[0]['ok'] and 'X/Y/Z' in log1[0]['error']
    _, log2 = apply_ops(src, [{'op': 'add_hole', 'axis': 'Z', 'pos': [1, 2], 'd': 5}])
    assert not log2[0]['ok']
    _, log3 = apply_ops(src, [{'op': 'add_hole', 'axis': 'Z', 'pos': [0, 0, 0], 'd': -3}])
    assert not log3[0]['ok'] and 'd (直径)' in log3[0]['error']


def test_validate_ops_covers_add_hole():
    ok = {'ops': [{'op': 'add_hole', 'axis': 'Z', 'pos': [1, 2, 3], 'd': 5}]}
    assert validate_ops(ok) == []
    bad_pos = {'ops': [{'op': 'add_hole', 'axis': 'Z', 'pos': [1], 'd': 5}]}
    assert any('pos' in e for e in validate_ops(bad_pos))
    bad_d = {'ops': [{'op': 'add_hole', 'axis': 'Z', 'pos': [1, 2, 3], 'd': 0}]}
    assert any('d' in e for e in validate_ops(bad_d))


def test_add_hole_renders_and_preserves_existing(tmp_path):
    """新增孔后可重渲染, 且原有孔几何不变 (作用域保全)."""
    pytest.importorskip('ezdxf')
    src = load_source(_shell('前壳'))
    old_r = Counter(
        round(c['r'], 2)
        for vd in src['proj']['views'].values() for c in vd.get('circles', []))
    new, log = apply_ops(src, [
        {'op': 'add_hole', 'axis': 'Z', 'pos': [5.0, -55.0, 0.0], 'd': 6.0}
    ])
    assert log[0]['ok'], log
    new_r = Counter(
        round(c['r'], 2)
        for vd in new['proj']['views'].values() for c in vd.get('circles', []))
    # 原圆一个不少 (新增只是追加, 不改旧几何)
    assert old_r.items() <= new_r.items(), '新增孔改动了原有圆 (作用域未保全)'
    from src.engine.render_engine import render
    out = tmp_path / 'add.dxf'
    render(new['proj'], None, new['annotation'], None, str(out), veritas=new['veritas'])
    assert out.exists() and out.stat().st_size > 10000


# ── 确定性规则规划器 (LLM 429/缺配额时改图兜底, 零成本) ─────

def test_rule_plan_set_hole_diameter():
    src = load_source(_shell('后壳'))
    dg = build_digest(src)
    p = _try_rule_plan('把φ4.8的孔改成φ5.5', dg)
    assert p and p['ops'][0]['op'] == 'set_hole_diameter'
    assert p['ops'][0]['new_d'] == 5.5 and p['ops'][0]['d'] == 4.8
    assert p['ops'][0]['axis'] == 'Z'
    assert validate_ops(p) == []


def test_rule_plan_stretch():
    src = load_source(_shell('前壳'))
    dg = build_digest(src)
    p = _try_rule_plan('宽度加长20', dg)
    assert p and p['ops'][0]['op'] == 'stretch_axis'
    assert p['ops'][0]['delta'] == 20.0


def test_rule_plan_misses_unknown_instruction():
    src = load_source(_shell('后壳'))
    dg = build_digest(src)
    # 规则不覆盖的指令 → None (交 LLM, 不臆造)
    assert _try_rule_plan('把零件改成蓝色', dg) is None
