"""unfold 钣金中面展开 + 展开视图渲染回归.

后壳实测(11段/13折弯线/365×200mm)的机理在合成件上锁定:
T1 L折弯件: 逐法兰展开 + 折弯补偿
T2 平板: 无折弯退化
T3 render(flat=...) 集成: BEND层 + dxf_checks门禁
"""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

build123d = pytest.importorskip('build123d')

from build123d import Box, Circle, Pos, Rectangle, BuildPart, BuildSketch, extrude, Mode  # noqa: E402

from src.engine.unfold import unfold  # noqa: E402


def _l_bracket():
    """L件: 底80×50×2 + 立边2×50×30 (折弯高30)."""
    return Box(80, 50, 2) + Pos(40, 0, 15) * Box(2, 50, 30)


def _plate_with_hole():
    """平板 100×60×2 带一孔φ10."""
    with BuildPart() as part:
        with BuildSketch() as sk:
            Rectangle(100, 60)
            Circle(5, mode=Mode.SUBTRACT, align=())
        extrude(amount=2)
    return part.part


def test_l_bracket_unfold():
    out = unfold(_l_bracket(), thickness=2.0)
    assert out['segments_total'] >= 2
    assert out['segments_unfolded'] >= 2
    assert len(out['bend_lines']) >= 1
    # 展开后总宽 ≈ 底80 + 立边高30 (+BA), 总高 ≈ 50
    xs = [v for ln in out['lines'] for v in (ln['p1'][0], ln['p2'][0])]
    ys = [v for ln in out['lines'] for v in (ln['p1'][1], ln['p2'][1])]
    assert max(xs) - min(xs) == pytest.approx(110, abs=4)
    assert max(ys) - min(ys) == pytest.approx(50, abs=4)


def test_plate_no_bend():
    out = unfold(_plate_with_hole(), thickness=2.0)
    assert out['segments_unfolded'] == out['segments_total'] == 1
    assert out['bend_lines'] == []
    assert out['lines']  # 轮廓存在
    assert out['arcs'] or out['circles']  # 孔弧存在


def test_render_with_flat_view(tmp_path):
    from src.engine.render_engine import render
    from src.engine.dxf_checks import validate_dxf_file
    import ezdxf

    flat = unfold(_l_bracket(), thickness=2.0)
    proj = {
        'bbox': {'width': 80, 'height': 50, 'depth': 2,
                 'xmin': 0, 'xmax': 80, 'ymin': 0, 'ymax': 50,
                 'zmin': 0, 'zmax': 2},
        'views': {
            'Front': {'lines': [{'p1': [0, 0], 'p2': [80, 0]},
                                {'p1': [80, 0], 'p2': [80, 50]},
                                {'p1': [80, 50], 'p2': [0, 50]},
                                {'p1': [0, 50], 'p2': [0, 0]}],
                      'arcs': [], 'circles': [], 'splines': []},
            'Top': {'lines': [{'p1': [0, 0], 'p2': [80, 0]}],
                    'arcs': [], 'circles': [], 'splines': []},
        },
    }
    out = str(tmp_path / 'with_flat.dxf')
    report = render(proj, None, None, None, out, flat=flat)
    assert report['flat_view']['status'] == 'ok'
    doc = ezdxf.readfile(out)
    msp = doc.modelspace()
    assert len(msp.query('*[layer=="BEND"]')) >= 1
    assert any('展开' in t.dxf.text for t in msp.query('TEXT'))
    errors = [f for f in validate_dxf_file(out) if f.severity == 'error']
    assert errors == [], [e.render() for e in errors]
