"""M-gen 确定性生成器回归: spec→DXF→回读校验 (不依赖 LLM/网络).

覆盖: 几何/原生标注/图层齐全/回读校验/预览渲染 全链.
LLM 规划器 (gen2d.plan_from_nl) 需网络, 此处只测 validate_spec/extract_json.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine.dxf_gen import generate, verify_dxf, render_preview_png, build_sheet
from src.engine.gen2d import validate_spec, extract_json


def _plate_spec():
    """安装底板: 120×80, 四角φ9 + 中央φ25 通孔 (真实尺寸, 原点左下)."""
    return {
        'title': '安装底板', 'material': 'Q235', 'scale_text': '1:1',
        'frame': {'width': 120, 'height': 80, 'thickness': 4},
        'geometry': [
            {'type': 'rect', 'x': 0, 'y': 0, 'w': 120, 'h': 80},
            {'type': 'circle', 'cx': 15, 'cy': 15, 'r': 4.5, 'layer': 'HOLE'},
            {'type': 'circle', 'cx': 105, 'cy': 15, 'r': 4.5, 'layer': 'HOLE'},
            {'type': 'circle', 'cx': 15, 'cy': 65, 'r': 4.5, 'layer': 'HOLE'},
            {'type': 'circle', 'cx': 105, 'cy': 65, 'r': 4.5, 'layer': 'HOLE'},
            {'type': 'circle', 'cx': 60, 'cy': 40, 'r': 12.5, 'layer': 'HOLE'},
        ],
        'dimensions': [
            {'type': 'linear', 'p1': [0, 0], 'p2': [120, 0], 'offset': 12, 'angle': 0},
            {'type': 'linear', 'p1': [0, 0], 'p2': [0, 80], 'offset': 12, 'angle': 90},
            {'type': 'diameter', 'center': [60, 40], 'r': 12.5},
        ],
        'tech_reqs': ['未注公差按GB/T 1804-m', '材料 Q235', '去毛刺'],
    }


def test_build_sheet_layers():
    """图层/图幅/原生标注全链 (确定性, 无 LLM)."""
    spec = _plate_spec()
    doc, meta = build_sheet(spec)
    msp = doc.modelspace()
    by_type, layers = {}, set()
    for e in msp:
        by_type[e.dxftype()] = by_type.get(e.dxftype(), 0) + 1
        layers.add(e.dxf.layer)
    assert by_type.get('DIMENSION') == 3, by_type
    for need in ('FRAME', 'TITLE', 'OUTLINE', 'DIM', 'TECH'):
        assert need in layers, f'缺图层 {need}: {sorted(layers)}'
    assert meta['sheet'] in ('A4', 'A3')


def test_generate_and_verify(tmp_path):
    spec = _plate_spec()
    r = generate(spec, str(tmp_path))
    assert r['verify']['ok'], r['verify']
    assert r['dxf_path'].endswith('gen_drawing.dxf')
    v = verify_dxf(r['dxf_path'], spec)
    assert v['dim_count'] == 3
    assert not v['missing_layers']


def test_preview_png(tmp_path):
    spec = _plate_spec()
    r = generate(spec, str(tmp_path))
    png = str(tmp_path / 'preview.png')
    render_preview_png(r['dxf_path'], png, r['sheet'])
    assert Path(png).exists() and Path(png).stat().st_size > 2000


def test_validate_spec_rejects_bad():
    # 缺 title → 报错; 空 geometry → 报错; 无标注 → 报错; 无技术要求 → 报错
    bad1 = {'geometry': [{'type': 'rect', 'x': 0, 'y': 0, 'w': 10, 'h': 10}],
            'dimensions': [{'type': 'linear', 'p1': [0, 0], 'p2': [10, 0]}]}
    assert validate_spec(bad1)
    bad2 = {'title': 'x', 'geometry': [], 'dimensions': [{'type': 'linear', 'p1': [0, 0], 'p2': [10, 0]}]}
    assert validate_spec(bad2)
    bad3 = {'title': 'x', 'geometry': [{'type': 'rect', 'x': 0, 'y': 0, 'w': 10, 'h': 10}],
            'dimensions': []}
    assert validate_spec(bad3)
    good = dict(_plate_spec())
    good['assumptions'] = []
    assert validate_spec(good) == []


def test_extract_json_variants():
    import json
    s = json.dumps({'title': 'x'})
    assert extract_json(s) == {'title': 'x'}
    assert extract_json('```json\n' + s + '\n```') == {'title': 'x'}
    assert extract_json('前导废话\n' + s + '\n尾注') == {'title': 'x'}
    assert extract_json('没有json') is None
