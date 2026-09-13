"""P2标注引擎(veritas适配+annotate) + 验收门(census) 回归.

2026-09-13 事故教训锁定: 标注引擎7月重建时丢失, E2E只有4个DIMENSION
仍报"0错误" — dxf_checks查完整性, acceptance查可用性, 两者缺一不可.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ezdxf = pytest.importorskip('ezdxf')

from src.engine.veritas_geom import veritas_to_geometry  # noqa: E402
from src.engine.annotator import annotate  # noqa: E402
from src.engine.acceptance import census, accept  # noqa: E402


def _veritas(n_holes=20):
    """合成 veritas: 100×60×3 板, n_holes 个 Z 向孔."""
    import math
    feats = []
    for i in range(n_holes):
        x = -45 + 90 * (i % 5) / 4
        y = -25 + 50 * (i // 5) / (max(n_holes // 5 - 1, 1))
        feats.append({
            'id': f'F{i:03d}', 'type': 'PIERCING', 'axis_dir': 'Z',
            'position': [x, y, 0.0], 'radius': 2.0, 'diameter': 4.0,
            'all_radii': [2.0], 'through': True, 'hole_type': 'clear',
        })
    return {'bbox': {'xmin': -50, 'ymin': -30, 'zmin': 0,
                     'xmax': 50, 'ymax': 30, 'zmax': 3},
            'features': feats}


def test_veritas_adapter():
    g = veritas_to_geometry(_veritas())
    assert g is not None
    assert g['width'] == 100 and g['height'] == 60 and g['depth'] == 3
    assert len(g['holes_2d']) == 20
    assert all(h['d'] == 4.0 for h in g['holes_2d'])


def test_veritas_adapter_empty():
    assert veritas_to_geometry({'features': []}) is None


def test_annotate_density():
    """孔板标注密度: 20孔5×4栅格 → 外形6+X链+Y链+角部去重+孔径 ≈ 23+.

    09-14起: 定位尺寸按(分量,值)去重(145.1×2教训), 极微段<1.2mm剔除.
    """
    r = annotate(veritas_to_geometry(_veritas()), None, target_count=20)
    s = r['stats']
    assert s['total'] >= 20
    assert s['linear'] >= 12
    # 同径只标一次: 20孔同径 → 1条 radius 或 leader (len≥3走leader分支)
    assert s['radius'] + s['leader'] >= 1
    assert s['radius'] + s['leader'] <= 3
    # 全部标注有 view/type
    for d in r['dimensions']:
        assert d['view'] in ('Top', 'Front', 'Left')
        assert d['type'] in ('linear', 'radius', 'leader', 'diameter', 'angular')


def test_acceptance_gate_catches_empty_drawing():
    """无标注的图必须被验收门拦下 (4尺寸废图教训)."""
    doc = ezdxf.new('R2013', setup=True)
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0))
    msp.add_line((100, 0), (100, 60))
    import tempfile, os
    p = os.path.join(tempfile.mkdtemp(), 'empty.dxf')
    doc.saveas(p)
    result = accept(p)
    assert result['pass'] is False
    dims = [c for c in result['checks'] if c['metric'] == 'dimension']
    assert dims and dims[0]['ok'] is False


def test_acceptance_gate_passes_annotated_drawing(tmp_path):
    """有足够标注的图通过验收门."""
    doc = ezdxf.new('R2013', setup=True)
    doc.styles.add('GB', font='simsun.ttf')
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0))
    for i in range(80):
        d = msp.add_linear_dim(
            base=(0, -10 - (i % 5) * 5), p1=(i, 0), p2=(i + 1, 0))
        d.render()
    for i in range(5):
        msp.add_text(f'note{i}', dxfattribs={'height': 3, 'insert': (i * 10, 70)})
    for i in range(4):
        msp.add_leader([(i * 20, 0), (i * 20 + 5, 5), (i * 20 + 15, 5)])
    p = str(tmp_path / 'full.dxf')
    doc.saveas(p)
    c = census(p)
    assert c['dimension'] >= 75
    # text_min 放低: 本测试只验门禁逻辑, 50文字下限由真实图纸承担
    result = accept(p, thresholds={'text_min': 3})
    assert result['pass'] is True, [x for x in result['checks'] if not x['ok']]
