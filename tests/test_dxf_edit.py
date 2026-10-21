"""C线 Phase 4: 任意DXF确定性编辑回归 (无LLM).

- STRETCH: 窗口内线端点移动/跨窗线拉伸/窗口外不动
- 圆/文字整体移动; DIMENSION defpoint 联动+重渲染真实测量值
- 作用域审计: 窗口外实体指纹逐位不变
- validate_ops 计划校验
"""
import sys
from pathlib import Path

import ezdxf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine.dxf_edit import extract_summary, apply_edits, scope_audit
from src.engine.dxf_edit_plan import validate_ops


def _plate_dxf(path):
    """合成: 矩形板(0,0)-(120,80) + 右侧圆(105,40) + 左侧圆(15,40) + 宽度标注."""
    doc = ezdxf.new('R2013', setup=True)
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (120, 0), (120, 80), (0, 80)], close=True,
                       dxfattribs={'layer': 'OUTLINE'})
    msp.add_circle((105, 40), 4.5, dxfattribs={'layer': 'HOLE'})
    msp.add_circle((15, 40), 4.5, dxfattribs={'layer': 'HOLE'})
    d = msp.add_linear_dim(base=(0, -12), p1=(0, 0), p2=(120, 0), angle=0,
                           dimstyle='EZDXF', dxfattribs={'layer': 'DIM'})
    d.render()
    doc.saveas(str(path))
    return str(path)


def test_extract_summary(tmp_path):
    f = _plate_dxf(tmp_path / 'p.dxf')
    s = extract_summary(f)
    assert s['entity_counts']['CIRCLE'] == 2
    assert s['entity_counts']['DIMENSION'] == 1
    assert abs(s['dims'][0]['measured'] - 120.0) < 0.01
    assert any(abs(c['c'][0] - 105) < 0.01 for c in s['circles'])


def test_stretch_right_edge_and_audit(tmp_path):
    f = _plate_dxf(tmp_path / 'p.dxf')
    out = str(tmp_path / 'p2.dxf')
    # 窗口套右边界+右侧孔 (x>100), 平移 +30 → 宽 120→150
    r = apply_edits(f, [{'op': 'stretch', 'window': [100, -20, 130, 100], 'dx': 30, 'dy': 0}], out)
    a = r['audit']
    assert a['ok']
    doc = ezdxf.readfile(out)
    msp = doc.modelspace()
    # 右侧孔移到 135, 左侧孔不动
    xs = sorted(round(c.dxf.center.x, 1) for c in msp.query('CIRCLE'))
    assert xs == [15.0, 135.0]
    # 多段线右边界顶点 120→150
    poly = msp.query('LWPOLYLINE')[0]
    px = sorted({round(x, 1) for x, y, *_ in poly.get_points('xyb')})
    assert 150.0 in px and 0.0 in px
    # 标注重测 150 (defpoint3 在窗口内联动)
    dim = msp.query('DIMENSION')[0]
    assert abs(dim.get_measurement() - 150.0) < 0.01
    # 审计: 左侧孔(窗口外)指纹不变
    assert a['unchanged'] >= 1


def test_delete_scope(tmp_path):
    f = _plate_dxf(tmp_path / 'p.dxf')
    out = str(tmp_path / 'p3.dxf')
    r = apply_edits(f, [{'op': 'delete', 'window': [100, 34, 110, 46]}], out)
    doc = ezdxf.readfile(out)
    assert len(doc.modelspace().query('CIRCLE')) == 1   # 只删了右侧孔
    assert r['audit']['ok']


def test_validate_ops():
    assert validate_ops({'ops': [{'op': 'stretch', 'window': [0, 0, 10, 10],
                                  'dx': 1, 'dy': 0}]}) == []
    assert validate_ops({'ops': []}) != []
    assert validate_ops({'ops': [{'op': 'warp', 'window': [0, 0, 1, 1]}]}) != []
    assert validate_ops({'ops': [{'op': 'stretch', 'window': 'x', 'dx': 1, 'dy': 0}]}) != []


def test_no_edit_no_touch(tmp_path):
    """空窗口外操作: 不许有任何实体指纹变化."""
    f = _plate_dxf(tmp_path / 'p.dxf')
    out = str(tmp_path / 'p4.dxf')
    r = apply_edits(f, [{'op': 'stretch', 'window': [1000, 1000, 1010, 1010],
                         'dx': 5, 'dy': 5}], out)
    assert r['touched_total'] == []
    a = r['audit']
    assert a['changed'] == [] and a['removed'] == [] and a['ok']


def test_scale_region_resizes_hole(tmp_path):
    """改孔径: scale_region 绕孔心缩放, 半径×factor, 窗口外圆不变."""
    f = _plate_dxf(tmp_path / 'p.dxf')
    out = str(tmp_path / 'p5.dxf')
    # 右侧孔 (105,40) r4.5 → φ9→φ12 → factor=4/3
    r = apply_edits(f, [{'op': 'scale_region', 'window': [98, 33, 112, 47],
                         'factor': 4 / 3}], out)
    assert r['audit']['ok']
    doc = ezdxf.readfile(out)
    rs = {(round(c.dxf.center.x, 1), round(c.dxf.radius, 3))
          for c in doc.modelspace().query('CIRCLE')}
    assert (105.0, 6.0) in rs          # 4.5×4/3=6
    assert (15.0, 4.5) in rs           # 左孔不变
