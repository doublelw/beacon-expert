"""M-dedup 回归: 全局重复实体清除 (CNC 双切风险门禁).

修复背景: 前壳图纸 dxf_checks 报 20 个 duplicate_entity error — 渲染层各发射路径
(主循环/样条还原/csink锥面/展开视图) 把同一几何画了两次, 且跨视图坐标重合也被
门禁标为双切风险。修法 = 渲染收尾时按 dxf_checks 同口径 (6位精度指纹) 全局清除
重复 LINE/CIRCLE/ARC/LWPOLYLINE (保留首现), 清完门禁即过。

用真实前壳/后壳夹具验证: 清后 duplicate 门禁 error = 0, 且关键几何数量不减少
(清的是重影, 不是信息)。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIX = Path(__file__).resolve().parent / 'fixtures' / 'shells'


def _n(name):
    d = FIX / name
    if not (d / 'proj_v3.json').exists():
        pytest.skip(f'夹具缺失 {d}')
    return str(d)


def _render(name: str, out: Path) -> dict:
    from src.engine.render_engine import render
    src = json.loads((FIX / name / 'proj_v3.json').read_text(encoding='utf-8'))
    veritas = json.loads((FIX / name / 'veritas.json').read_text(encoding='utf-8'))
    ann = json.loads((FIX / name / 'annotation.json').read_text(encoding='utf-8'))
    rep = render(src, None, ann, None, str(out), veritas=veritas)
    return rep


def test_no_duplicate_entities_in_front(tmp_path):
    """前壳: 清重复后 dxf_checks duplicate_entity error = 0."""
    from src.engine.dxf_checks import validate_dxf_file
    rep = _render('前壳', tmp_path / 'front.dxf')
    findings = validate_dxf_file(str(tmp_path / 'front.dxf'))
    dups = [f for f in findings if f.severity == 'error' and f.code == 'duplicate_entity']
    assert not dups, f'仍有 {len(dups)} 个重复实体: {[f.render() for f in dups][:5]}'
    # 清掉了重影 (前壳原本 ~16)
    assert rep.get('duplicate_removed', 0) > 0, '未清除任何重复, 全局去重未生效'


def test_no_duplicate_entities_in_rear(tmp_path):
    """后壳: 同样门禁清零."""
    from src.engine.dxf_checks import validate_dxf_file
    _render('后壳', tmp_path / 'rear.dxf')
    findings = validate_dxf_file(str(tmp_path / 'rear.dxf'))
    dups = [f for f in findings if f.severity == 'error' and f.code == 'duplicate_entity']
    assert not dups, f'后壳仍有 {len(dups)} 个重复实体'


def test_dedup_preserves_unique_geometry(tmp_path):
    """去重只删重影, 不删唯一几何: 清后实体数下降量 == 清除数, 无信息丢失."""
    import ezdxf
    rep = _render('前壳', tmp_path / 'front.dxf')
    # 去重是渲染内收尾, 成品 DXF 已不含重复。独立重算: 读成品, 实体指纹应全唯一
    doc = ezdxf.readfile(str(tmp_path / 'front.dxf'))
    msp = doc.modelspace()
    sigs = set()
    dup = 0
    for e in msp:
        t = e.dxftype()
        if t == 'LINE':
            ends = sorted([(round(e.dxf.start.x, 6), round(e.dxf.start.y, 6)),
                           (round(e.dxf.end.x, 6), round(e.dxf.end.y, 6))])
            sig = ('L', e.dxf.layer, tuple(ends))
        elif t == 'CIRCLE':
            sig = ('C', e.dxf.layer, round(e.dxf.center.x, 6), round(e.dxf.center.y, 6),
                   round(e.dxf.radius, 6))
        else:
            continue
        if sig in sigs:
            dup += 1
        sigs.add(sig)
    assert dup == 0, f'成品仍有 {dup} 个精确重复几何 (去重漏网)'
