"""M-explode 回归: 客户装配体STEP → assembly.json → 交互爆炸图HTML (无LLM).

覆盖: 装配体判定/主轴判定/爆炸向量语义/assembly.json契约/viewer直连HTML.
行星减速器5比1作标准夹具: 6件沿Z主轴(最薄轴), 三行星件同层径向散开.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine.explode_gen import build_assembly_json, venv_python
from src.config import VENDOR_HUASHU

# 行星减速器 (Phase 1 全链验证过的装配体, 6件)
PLANETARY = Path(__file__).resolve().parent.parent / 'tmp' / 'planetary' / 'asm.step'
PLANETARY_ALT = PLANETARY.parent / '行星减速器5比1.step'


def _venv_or_skip():
    if venv_python() is None:
        pytest.skip('缺 .venv-cad (先跑 tools/install_cad_env.py)')
    assert (VENDOR_HUASHU / 'scripts' / 'mech' / 'viewer.py').exists(), '缺 vendor viewer.py'


@pytest.fixture(scope='module')
def planetary_step() -> str:
    _venv_or_skip()
    for p in (PLANETARY, PLANETARY_ALT):
        if p.exists():
            return str(p)
    pytest.skip('行星减速器 STEP 夹具缺失 (需先跑 examples/planetary_reducer.py 生成)')


def test_planetary_manifest(planetary_step, tmp_path):
    """6件装配体 → 主轴Z, 同层行星件径向散开, assembly.json合法."""
    r = build_assembly_json(planetary_step, str(tmp_path), title='行星减速器', explode_scale=1.0)
    assert r['ok'], r.get('error')
    assert r['n_parts'] == 6
    assert r['main_axis'] == 2  # 减速器沿Z堆叠
    m = r['manifest']
    # 行星件 (P02-P04, 同Z层) 必须有径向非零偏移, 否则爆炸后三件重叠
    radial = [p for p in m['parts']
              if abs(p['explode'][0]) > 0.5 or abs(p['explode'][1]) > 0.5]
    assert len(radial) >= 2, f'径向散开件不足: {[p["code"] for p in m["parts"]]}'
    # 轴件 (太阳轮/内齿圈/输出轴, 质心近轴) 主轴偏移方向相反
    z_offsets = [p['explode'][2] for p in m['parts']]
    assert min(z_offsets) < 0 < max(z_offsets), z_offsets
    # 契约字段齐全 (viewer.py 直读)
    for p in m['parts']:
        for k in ('name', 'code', 'material', 'explode', 'spin_axis',
                  'spin_center', 'motion', 'mesh'):
            assert k in p, f'缺 {k}'
        assert p['mesh']['nv'] > 0 and p['mesh']['nt'] > 0
    aj = tmp_path / 'assembly.json'
    assert aj.exists()
    json.loads(aj.read_text(encoding='utf-8'))  # 必须合法 (无np标量)


def test_exploded_html(planetary_step, tmp_path):
    """viewer.py 直连出单文件HTML (双击即开, 不联网)."""
    _venv_or_skip()
    r = build_assembly_json(planetary_step, str(tmp_path), explode_scale=1.0)
    assert r.get('html'), r.get('error')
    html = Path(r['html'])
    assert html.exists() and html.stat().st_size > 1_000_000, 'HTML异常小, 疑似缺viewer bundle'
    text = html.read_text(encoding='utf-8')
    assert 'THREE' in text and '__DATA__' not in text, 'viewer未内联'


def test_single_solid_rejected(tmp_path):
    """单一零件STEP → 如实拒绝 (爆炸图无意义, 不造假)."""
    _venv_or_skip()
    import subprocess
    venv_py = str(venv_python())
    solo = str(tmp_path / 'solo.step')
    drv = tmp_path / '_solo.py'
    drv.write_text(
        'from build123d import Box, export_step\n'
        f'export_step(Box(20, 10, 5), r"{solo}")\n', encoding='utf-8')
    r = subprocess.run([venv_py, str(drv)], capture_output=True,
                       text=True, timeout=120, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr[-300:]
    res = build_assembly_json(solo, str(tmp_path / 'out'))
    assert not res['ok'] and res['stage'] == 'input', res
    assert '<2' in res['error'] or '单一零件' in res['error']


def test_run_explode_both_legs(planetary_step, tmp_path):
    """run_explode 一次调用出两条腿: 爆炸图HTML + 装配工程图DXF (回转类主轴Z)."""
    _venv_or_skip()
    from src.engine.explode_gen import run_explode
    r = run_explode(planetary_step, str(tmp_path), title='行星减速器')
    assert r['ok'], r.get('error')
    # 腿1: 交互爆炸图
    assert r.get('html') and Path(r['html']).exists()
    # 腿2: 装配工程图 (行星减速器主轴=Z, 花叔drawing.py适用)
    assert r.get('assembly_dxf') and Path(r['assembly_dxf']).exists(), \
        f'缺装配工程图: {r.get("drawing_note")}'
    # 装配工程图 DXF 可回读, 含剖面线(HATCH)与明细栏(TEXT)
    import ezdxf
    doc = ezdxf.readfile(r['assembly_dxf'])
    types = {e.dxftype() for e in doc.modelspace()}
    assert 'HATCH' in types, '缺剖面线 (装配全剖应有 HATCH)'
    assert 'TEXT' in types, '缺明细栏/技术要求文字'


def test_assembly_drawing_degrades_for_non_rotary(tmp_path):
    """非回转类 (主轴≠Z) → 装配工程图如实降级, 不硬造."""
    _venv_or_skip()
    from src.engine import explode_gen
    # 手工造一个 manifest, 把 _beacon.main_axis 改成 0 (X主轴)
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
    assert r['dxf'] is None and '非回转类' in r['note']
