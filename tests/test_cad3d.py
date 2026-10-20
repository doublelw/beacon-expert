"""B线 Phase 3 + C线改图回归.

- cad3d_plan.lint_build_code 围栏 (无环境依赖, 必跑)
- 全管线冒烟 (需 .venv-cad + FreeCAD, 缺失跳过)
- cad_modify.diff_spec / diff_params 确定性对比 (必跑)
- LLM 改图实测在 CI 外手工跑 (有 key 时), 单测不依赖网络
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine.cad3d_plan import lint_build_code
from src.engine.cad3d_gen import venv_available
from src.engine.cad_modify import diff_spec, diff_params

GOOD_CODE = '''PARAMS = {"长": 100, "宽": 60, "厚": 8}
from build123d import Box, Cylinder, Align
BASE = (Align.CENTER, Align.CENTER, Align.MIN)
part = Box(PARAMS["长"], PARAMS["宽"], PARAMS["厚"], align=BASE) - Cylinder(5, 10, align=BASE)
'''


def test_lint_passes_good_code():
    assert lint_build_code(GOOD_CODE) == []


def test_lint_blocks_dangerous():
    for evil in [
        'PARAMS = {}\nimport subprocess\npart = None',
        'PARAMS = {}\nimport os\npart = None',
        'PARAMS = {}\npart = eval("1")',
        'PARAMS = {}\npart = __import__("os")',
        'PARAMS = {}\nfrom urllib.request import urlopen\npart = None',
        'x = 1  # PARAMS 不在第一行\nPARAMS = {}\npart = None',
        'PARAMS = {}  # 无 part',
        'part = Box(1,1,1)  # 无 PARAMS',
    ]:
        assert lint_build_code(evil), f'应拦截: {evil[:40]!r}'


def test_diff_spec_reports_scope():
    old = {'title': 'A', 'geometry': [
        {'type': 'rect', 'x': 0, 'y': 0, 'w': 120, 'h': 80},
        {'type': 'hole', 'cx': 15, 'cy': 40, 'r': 4.5},      # 左侧孔不动
        {'type': 'hole', 'cx': 105, 'cy': 40, 'r': 12.5},    # 右侧孔随宽度移动
    ], 'dims': [{'type': 'linear', 'p1': [0, 0], 'p2': [120, 0], 'side': 'bottom'}],
        'notes': ['n1']}
    new = {'title': 'A', 'geometry': [
        {'type': 'rect', 'x': 0, 'y': 0, 'w': 150, 'h': 80},
        {'type': 'hole', 'cx': 15, 'cy': 40, 'r': 4.5},
        {'type': 'hole', 'cx': 135, 'cy': 40, 'r': 12.5},
    ], 'dims': [{'type': 'linear', 'p1': [0, 0], 'p2': [150, 0], 'side': 'bottom'}],
        'notes': ['n1', '修改: 宽度120→150']}
    d = diff_spec(old, new)
    assert any('w' in c for c in d['changes'])
    assert d['unchanged_geometry'] == 1        # 左侧孔未受影响 → 保全证明
    assert d['total_geometry'] == 3


def test_diff_params():
    d = diff_params({'长': 120, '宽': 80}, {'长': 150, '宽': 80})
    assert d['changes'] == ['长: 120 → 150']
    assert d['unchanged_params'] == 1


@pytest.mark.skipif(not venv_available(), reason='需 .venv-cad (tools/install_cad_env.py)')
def test_3d_pipeline_fixed_code(tmp_path):
    """固定构建体全链: STEP验证→veritas→标注→投影→渲染→门禁 (含FreeCAD)."""
    from src.engine.cad3d_gen import run_3d_pipeline
    code = '''PARAMS = {"长": 100, "宽": 60, "厚": 8, "孔径": 9}
from build123d import Box, Cylinder, Pos, Align
BASE = (Align.CENTER, Align.CENTER, Align.MIN)
part = (Box(PARAMS["长"], PARAMS["宽"], PARAMS["厚"], align=BASE)
        - Pos(-35, -20, -1) * Cylinder(PARAMS["孔径"]/2, 10, align=BASE)
        - Pos(35, -20, -1) * Cylinder(PARAMS["孔径"]/2, 10, align=BASE)
        - Pos(-35, 20, -1) * Cylinder(PARAMS["孔径"]/2, 10, align=BASE)
        - Pos(35, 20, -1) * Cylinder(PARAMS["孔径"]/2, 10, align=BASE))
'''
    r = run_3d_pipeline(code, str(tmp_path))
    assert r['ok'], r.get('error')
    assert r['verify']['valid'] and r['verify']['solids'] == 1
    assert Path(r['dxf']).exists()
    assert Path(r['dxf']).stat().st_size > 20000
