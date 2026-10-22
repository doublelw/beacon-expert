"""装配工程图回归: 客户装配体STEP → 花叔drawing.py → GB装配工程图DXF.

覆盖: 回转类(主轴Z)出全剖主视图+俯视图+明细栏; 非回转类(主轴X/Y)如实降级.
用 venv 驱动 (build123d/ezdxf 隔离环境), 行星减速器5比1作夹具 (6件, 主轴Z).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine.explode_gen import build_assembly_json, venv_python
from src.config import VENDOR_HUASHU

PLANETARY = Path(__file__).resolve().parent.parent / 'tmp' / 'planetary' / 'asm.step'
PLANETARY_ALT = PLANETARY.parent / '行星减速器5比1.step'


def _venv_or_skip():
    if venv_python() is None:
        pytest.skip('缺 .venv-cad (先跑 tools/install_cad_env.py)')
    assert (VENDOR_HUASHU / 'scripts' / 'mech' / 'drawing.py').exists(), '缺 vendor drawing.py'


@pytest.fixture(scope='module')
def planetary_step() -> str:
    _venv_or_skip()
    for p in (PLANETARY, PLANETARY_ALT):
        if p.exists():
            return str(p)
    pytest.skip('行星减速器 STEP 夹具缺失')


def test_assembly_drawing_rotary(planetary_step, tmp_path):
    """回转类(主轴Z): run_explode 两条腿全出 — 爆炸图HTML + 装配工程图DXF."""
    from src.engine.explode_gen import run_explode
    r = run_explode(planetary_step, str(tmp_path), title='行星减速器')
    assert r['ok'], r.get('error')
    # 腿1: 交互爆炸图
    assert r.get('html') and Path(r['html']).exists()
    # 腿2: 装配工程图 (行星减速器主轴=Z, 花叔drawing.py适用)
    assert r.get('assembly_dxf') and Path(r['assembly_dxf']).exists(), \
        f'缺装配工程图: {r.get("drawing_note")}'
    import ezdxf
    doc = ezdxf.readfile(r['assembly_dxf'])
    types = {e.dxftype() for e in doc.modelspace()}
    assert 'HATCH' in types, '装配全剖应有剖面线(HATCH)'
    assert 'TEXT' in types, '应有明细栏/技术要求文字'


def test_assembly_drawing_degrades_non_rotary(tmp_path):
    """非回转类(主轴≠Z): 装配工程图如实降级, 不硬造图."""
    _venv_or_skip()
    from src.engine import explode_gen
    m = {
        'title': 't', 'units': 'mm',
        'bbox': {'min': [0, 0, 0], 'max': [50, 20, 20]},
        'mass_total_g': 1.0, 'checks_ok': True, 'planetary': None,
        'parts': [],
        '_beacon': {'source': 'customer_step', 'main_axis': 0,
                    'explode_scale': 0.8, 'n_parts': 0},
    }
    r = explode_gen._assembly_drawing('x.step', str(tmp_path), 't',
                                      {'manifest': m, 'ok': True})
    assert r['dxf'] is None
    assert '非回转类' in r['note']


def test_assembly_drawing_requires_assembly_json(tmp_path):
    """缺 assembly.json → 如实报 input 阶段错误."""
    _venv_or_skip()
    from src.engine import explode_gen
    r = explode_gen._assembly_drawing('x.step', str(tmp_path), 't',
                                      {'manifest': {'_beacon': {'main_axis': 2}}, 'ok': True})
    assert r['dxf'] is None
    assert '缺 assembly.json' in r['note']
