"""B线 Phase 2 确定性引擎回归: spec→DXF→验证→预览 全链 (无LLM).

覆盖: 校验门 (非法spec/超幅拒绘)、生成 (GB图幅/DIMLFAC真实mm)、
回读核对 (门禁/计数)、预览渲染。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine.cad2d_gen import (
    validate_spec, generate_dxf, verify_output, render_preview_png, run_2d_pipeline,
)


def _plate_spec():
    """安装底板: 120×80 + 四角φ9 + 中央φ25 + 外形/孔径标注 + 技术要求."""
    return {
        'title': '安装底板', 'sheet': 'A4',
        'geometry': [
            {'type': 'rect', 'x': 0, 'y': 0, 'w': 120, 'h': 80},
            {'type': 'hole', 'cx': 15, 'cy': 15, 'r': 4.5},
            {'type': 'hole', 'cx': 105, 'cy': 15, 'r': 4.5},
            {'type': 'hole', 'cx': 15, 'cy': 65, 'r': 4.5},
            {'type': 'hole', 'cx': 105, 'cy': 65, 'r': 4.5},
            {'type': 'hole', 'cx': 60, 'cy': 40, 'r': 12.5},
        ],
        'dims': [
            {'type': 'linear', 'p1': [0, 0], 'p2': [120, 0], 'side': 'bottom', 'offset': 12},
            {'type': 'linear', 'p1': [0, 0], 'p2': [0, 80], 'side': 'left', 'offset': 12},
            {'type': 'diameter', 'cx': 60, 'cy': 40, 'r': 12.5},
        ],
        'notes': ['1. 未注公差按GB/T 1804-m。', '2. 材料Q235，t=3mm。'],
    }


def test_validate_spec_rejects_bad():
    assert validate_spec({}) != []
    assert validate_spec({'title': 'x', 'geometry': [{'type': 'triangle'}]}) != []
    assert validate_spec({'title': 'x', 'geometry': [
        {'type': 'circle', 'cx': 0, 'cy': 0, 'r': -1}]}) != []
    assert validate_spec(_plate_spec()) == []


def test_generate_rejects_oversize():
    spec = _plate_spec()
    spec['geometry'][0] = {'type': 'rect', 'x': 0, 'y': 0, 'w': 5000, 'h': 4000}
    with pytest.raises(ValueError, match='放不下'):
        generate_dxf(spec, 'should_not_exist.dxf')


def test_pipeline_end_to_end(tmp_path):
    r = run_2d_pipeline(_plate_spec(), str(tmp_path))
    assert r['ok'], r['verify']['gate_findings']
    # 产物齐全
    for f in ('drawing.dxf', 'preview.png', 'spec.json', 'report.json'):
        assert (tmp_path / f).exists(), f
    # 计数: 4×hole圆 + 中心孔圆 = 5 circle; 4×2中心线+0 = 8 line (报告口径)
    c = r['generate']['counts']
    assert c['circle'] == 5 and c['dimension'] == 3
    # DIMLFAC 链路: 显示值 = 真实mm (120 / 80 / φ25)
    shown = sorted(d['displayed'] for d in r['verify']['dim_samples'])
    assert 120.0 in shown and 80.0 in shown and 25.0 in shown


def test_verify_readback_counts(tmp_path):
    out = tmp_path / 'x.dxf'
    gen = generate_dxf(_plate_spec(), str(out))
    ver = verify_output(str(out), _plate_spec())
    assert ver['ok']
    by = ver['entity_counts']
    assert by.get('CIRCLE', 0) == gen['counts']['circle']
    assert by.get('DIMENSION', 0) == gen['counts']['dimension']


def test_preview_png(tmp_path):
    out = tmp_path / 'x.dxf'
    generate_dxf(_plate_spec(), str(out))
    png = tmp_path / 'p.png'
    render_preview_png(str(out), str(png))
    assert png.exists() and png.stat().st_size > 5000
