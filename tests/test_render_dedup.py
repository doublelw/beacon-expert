"""render_engine LINE去重/零长度拦截回归 + dxf_checks 门禁.

背景: 后壳_GB_rich.dxf 曾有40处OUTLINE精确重复LINE (HLR双发射忠实重画,
送CNC/激光双切风险). b7a77ef 在 render_projection 加每视图精确去重.
本测试用合成投影验证守卫, 不依赖 storage/tasks 中间产物.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine.render_engine import render
from src.engine.dxf_checks import validate_dxf_file


def _mini_projection():
    """最小可用投影: Front/Top 各几条线+一个圆."""
    return {
        'bbox': {'width': 100, 'height': 60, 'depth': 3,
                 'xmin': 0, 'xmax': 100, 'ymin': 0, 'ymax': 60,
                 'zmin': 0, 'zmax': 3},
        'views': {
            'Front': {'lines': [
                {'p1': [0, 0], 'p2': [100, 0]},
                {'p1': [100, 0], 'p2': [100, 60]},
                {'p1': [100, 60], 'p2': [0, 60]},
                {'p1': [0, 60], 'p2': [0, 0]},
            ], 'arcs': [], 'circles': [{'cx': 50, 'cy': 30, 'r': 5}],
                      'splines': []},
            'Top': {'lines': [
                {'p1': [0, 0], 'p2': [100, 0]},
            ], 'arcs': [], 'circles': [], 'splines': []},
        },
    }


def _render_to_tmp(proj, tmp_path, name):
    out = str(tmp_path / name)
    report = render(proj, None, None, None, out)
    findings = validate_dxf_file(out)
    errors = [f for f in findings if f.severity == 'error']
    return report, errors


def test_baseline_clean(tmp_path):
    report, errors = _render_to_tmp(_mini_projection(), tmp_path, 'base.dxf')
    assert errors == [], [e.render() for e in errors]


def test_duplicate_lines_deduped(tmp_path):
    proj = _mini_projection()
    lines = proj['views']['Front']['lines']
    proj['views']['Front']['lines'] = lines + [dict(l) for l in lines]  # 全量x2
    report, errors = _render_to_tmp(proj, tmp_path, 'dup.dxf')
    assert errors == [], [e.render() for e in errors]
    assert report['geometry_counts'].get('line_dedup') == 4


def test_zero_length_lines_skipped(tmp_path):
    proj = _mini_projection()
    proj['views']['Front']['lines'] += [
        {'p1': [10, 10], 'p2': [10, 10]} for _ in range(3)]
    report, errors = _render_to_tmp(proj, tmp_path, 'zero.dxf')
    assert errors == [], [e.render() for e in errors]
    assert report['geometry_counts'].get('line_skip') == 3


def test_tech_requirements_without_geometry():
    """geometry=None 不再 UnboundLocalError (b7a77ef 顺带修复)."""
    tech = __import__(
        'src.engine.render_engine', fromlist=['_build_tech_requirements']
    )._build_tech_requirements(None, None)
    assert any('材料' in t for t in tech)
