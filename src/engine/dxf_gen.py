"""M-gen: 自然语言 → 结构化图纸描述 → GB 工程图 DXF (确定性生成器).

定位: B线「自然语言生成图纸」的出图端。LLM 只做 自然语言→spec 的翻译,
DXF 实体一律由本模块确定性绘制, 保证可回读校验、可复现。

spec schema (结构化图纸描述, LLM 产出):
{
  "title": "轴承座",                       # 图名
  "material": "HT250",                    # 材料 (可选)
  "scale_text": "1:2",                    # 标题栏比例文字 (可选, 默认 1:1)
  "frame": {"width": 120, "height": 80, "thickness": 4},  # 图框外形 (可选)
  "geometry": [                            # 视图几何 (第一象限坐标, mm)
    {"type": "line",  "p1": [0,0], "p2": [30,0], "layer": "OUTLINE"},
    {"type": "rect",  "x": 0, "y": 0, "w": 30, "h": 20},
    {"type": "circle","cx": 15, "cy": 10, "r": 5, "layer": "HOLE"},
    {"type": "arc",   "cx": 15, "cy": 10, "r": 5, "start_angle": 0, "end_angle": 180},
    {"type": "text",  "at": [15,10], "text": "M6", "height": 3.5}
  ],
  "dimensions": [                          # 原生 DIMENSION (数值取自几何)
    {"type": "linear", "p1": [0,0], "p2": [30,0], "offset": 6, "angle": 0},
    {"type": "diameter","center": [15,10], "r": 5},
    {"type": "radius",  "center": [15,10], "r": 5}
  ],
  "tech_reqs": ["未注公差按GB/T 1804-m", "材料 HT250", "去毛刺"]
}
"""
import math
import os
from typing import List, Optional, Tuple

import ezdxf
from ezdxf.enums import TextEntityAlignment

from src.engine.render_engine import (
    FONT_CJK, FONT_LATIN, SHEET_SIZES,
    setup_chinese_font, setup_layers, setup_dimstyle,
)

# 默认图幅 (宽×高, mm); 几何超界自动放大到 A3
A4 = (297, 210)
A3 = (420, 297)

# 图面边距: 几何区 25/10 装订边 (GB/T 14689)
MARGIN = 25.0


def _pick_sheet(geometry: List[dict]) -> Tuple[str, Tuple[float, float]]:
    """按几何包围盒选图幅 + 留白."""
    bounds = _geom_bounds(geometry)
    w, h = bounds[2] - bounds[0], bounds[3] - bounds[1]
    # 需要 边距×2 + 尺寸标注带(顶部20/左20) + 标题栏(右180/下32)
    need_w, need_h = w + 40 + 60, h + 40 + 40
    if need_w <= A4[0] - 50 and need_h <= A4[1] - 50:
        return 'A4', A4
    return 'A3', A3


def _geom_bounds(geometry: List[dict]) -> Tuple[float, float, float, float]:
    xs, ys = [], []
    for g in geometry:
        t = g.get('type')
        if t == 'line':
            xs += [g['p1'][0], g['p2'][0]]; ys += [g['p1'][1], g['p2'][1]]
        elif t == 'rect':
            xs += [g['x'], g['x'] + g['w']]; ys += [g['y'], g['y'] + g['h']]
        elif t in ('circle', 'arc'):
            xs += [g['cx'] - g['r'], g['cx'] + g['r']]
            ys += [g['cy'] - g['r'], g['cy'] + g['r']]
        elif t == 'text':
            xs.append(g['at'][0]); ys.append(g['at'][1])
    if not xs:
        return (0, 0, 50, 40)
    return (min(xs), min(ys), max(xs), max(ys))


def _layout_origin(geometry: List[dict], sheet_w: float, sheet_h: float) -> Tuple[float, float]:
    """几何包围盒平移到图面中心偏左上, 避开标题栏."""
    gx0, gy0, gx1, gy1 = _geom_bounds(geometry)
    gw, gh = gx1 - gx0, gy1 - gy0
    # 绘图区: 左25上10 → 右留180(标题栏) 下留32(标题栏带)
    area_w = sheet_w - 25 - 180
    area_h = sheet_h - 10 - 60
    cx = 25 + area_w / 2 - gw / 2 + 60  # 偏左让位标注带
    cy = 10 + area_h / 2 - gh / 2 + 30
    return (cx - gx0, cy - gy0)


def build_sheet(spec: dict) -> Tuple[ezdxf.document.Drawing, dict]:
    """spec → 完整 DXF (未保存), 返回 (doc, meta)."""
    geometry = spec.get('geometry', [])
    sheet, (sw, sh) = _pick_sheet(geometry)
    ox, oy = _layout_origin(geometry, sw, sh)

    doc = ezdxf.new('R2018', setup=True)
    doc.units = ezdxf.units.MM
    setup_chinese_font(doc)
    setup_layers(doc)
    setup_dimstyle(doc, scale=1.0)  # 1:1 模型单位, 文字直接真实 mm
    msp = doc.modelspace()

    _draw_frame(msp, sw, sh)
    _draw_geometry(msp, geometry, ox, oy)
    _draw_dimensions(msp, spec.get('dimensions', []), ox, oy)
    _draw_tech_reqs(msp, spec, ox, oy, sw, sh)
    _draw_title_block(msp, spec, sw, sh)

    meta = {'sheet': sheet, 'sheet_size': [sw, sh], 'origin': [ox, oy]}
    return doc, meta


def _draw_frame(msp, sw: float, sh: float) -> None:
    msp.add_lwpolyline([(0, 0), (sw, 0), (sw, sh), (0, sh)], close=True,
                       dxfattribs={'layer': 'FRAME'})
    msp.add_lwpolyline([(MARGIN, 10), (sw - 10, 10), (sw - 10, sh - 10),
                        (MARGIN, sh - 10)], close=True,
                       dxfattribs={'layer': 'FRAME'})


def _draw_geometry(msp, geometry: List[dict], ox: float, oy: float) -> None:
    def T(p):
        return (p[0] + ox, p[1] + oy)

    for g in geometry:
        t = g.get('type')
        layer = g.get('layer', 'OUTLINE')
        try:
            if t == 'line':
                msp.add_line(T(g['p1']), T(g['p2']), dxfattribs={'layer': layer})
            elif t == 'rect':
                x, y, w, h = g['x'] + ox, g['y'] + oy, g['w'], g['h']
                msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                                    close=True, dxfattribs={'layer': layer})
            elif t == 'circle':
                msp.add_circle(T((g['cx'], g['cy'])), g['r'], dxfattribs={'layer': layer})
            elif t == 'arc':
                msp.add_arc(T((g['cx'], g['cy'])), g['r'],
                            g.get('start_angle', 0), g.get('end_angle', 360),
                            dxfattribs={'layer': layer})
            elif t == 'text':
                msp.add_text(g.get('text', ''), dxfattribs={'layer': 'TEXT',
                                                            'style': FONT_CJK,
                                                            'height': g.get('height', 3.5)}
                            ).set_placement(T(g['at']), align=TextEntityAlignment.MIDDLE_CENTER)
        except Exception:
            continue


def _dim_linear(msp, g: dict, ox: float, oy: float) -> None:
    p1 = (g['p1'][0] + ox, g['p1'][1] + oy)
    p2 = (g['p2'][0] + ox, g['p2'][1] + oy)
    offset = g.get('offset', 6)
    # 基准线: 尺寸线沿垂直方向偏移 offset
    dx, dy = g['p2'][0] - g['p1'][0], g['p2'][1] - g['p1'][1]
    L = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / L * offset, dx / L * offset
    base = (p1[0] + nx, p1[1] + ny)
    angle = g.get('angle', 0 if L > abs(dy) else 90)
    dim = msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle,
                              dimstyle='GB_DIM', dxfattribs={'layer': 'DIM'})
    dim.render()


def _draw_dimensions(msp, dims: List[dict], ox: float, oy: float) -> None:
    for d in dims:
        try:
            if d.get('type') == 'linear':
                _dim_linear(msp, d, ox, oy)
            elif d.get('type') == 'diameter':
                c = (d['center'][0] + ox, d['center'][1] + oy)
                dim = msp.add_diameter_dim(c, radius=d['r'], angle=45,
                                            dimstyle='GB_DIM', dxfattribs={'layer': 'DIM'})
                dim.render()
            elif d.get('type') == 'radius':
                c = (d['center'][0] + ox, d['center'][1] + oy)
                dim = msp.add_radius_dim(c, radius=d['r'], angle=45,
                                          dimstyle='GB_DIM', dxfattribs={'layer': 'DIM'})
                dim.render()
        except Exception:
            continue


def _draw_tech_reqs(msp, spec: dict, ox: float, oy: float, sw: float, sh: float) -> None:
    tech = spec.get('tech_reqs') or []
    if not tech:
        return
    x = MARGIN + 60
    y = sh - 10 - 15
    msp.add_text('技术要求', dxfattribs={'layer': 'TECH', 'style': FONT_CJK, 'height': 4}
                 ).set_placement((x, y), align=TextEntityAlignment.MIDDLE_LEFT)
    y -= 6
    for i, t in enumerate(tech, 1):
        msp.add_text(f'{i}. {t}', dxfattribs={'layer': 'TECH', 'style': FONT_CJK, 'height': 3}
                     ).set_placement((x + 8, y), align=TextEntityAlignment.MIDDLE_LEFT)
        y -= 5


def _draw_title_block(msp, spec: dict, sw: float, sh: float) -> None:
    """右下标题栏: 图名/材料/比例/图幅/单位 (GB 简化版)."""
    tw, th = 180, 32
    x0, y0 = sw - 10 - tw, 10
    msp.add_lwpolyline([(x0, y0), (x0 + tw, y0), (x0 + tw, y0 + th), (x0, y0 + th)],
                       close=True, dxfattribs={'layer': 'TITLE'})
    # 分割线
    msp.add_line((x0 + tw * 0.5, y0), (x0 + tw * 0.5, y0 + th), dxfattribs={'layer': 'TITLE'})
    msp.add_line((x0, y0 + th * 0.5), (x0 + tw * 0.5, y0 + th * 0.5), dxfattribs={'layer': 'TITLE'})
    frame = spec.get('frame') or {}
    dims_txt = '×'.join(str(v) for v in [frame.get('width'), frame.get('height')]
                        if v is not None)
    cells = [
        ('图名', spec.get('title', '—')),
        ('材料', spec.get('material', '—')),
        ('比例', spec.get('scale_text', '1:1')),
        ('单位', 'mm'),
        ('外形', dims_txt or '—'),
        ('批注', ''),
    ]
    for i, (label, val) in enumerate(cells):
        cx = x0 + (i % 3) * tw * 0.333
        cy = y0 + th - (i // 3) * th * 0.5 - 4
        msp.add_text(label, dxfattribs={'layer': 'TITLE', 'style': FONT_CJK, 'height': 2.5}
                     ).set_placement((cx + 2, cy + 4), align=TextEntityAlignment.MIDDLE_LEFT)
        msp.add_text(str(val), dxfattribs={'layer': 'TITLE', 'style': FONT_CJK, 'height': 3}
                     ).set_placement((cx + 10, cy - 2), align=TextEntityAlignment.MIDDLE_LEFT)


def generate(spec: dict, out_dir: str) -> dict:
    """spec → 落盘 DXF + 回读校验. 返回 {dxf_path, verify, sheet, meta}."""
    out_dir = out_dir or '.'
    os.makedirs(out_dir, exist_ok=True)
    doc, meta = build_sheet(spec)
    dxf_path = os.path.join(out_dir, 'gen_drawing.dxf')
    doc.saveas(dxf_path)
    verify = verify_dxf(dxf_path, spec)
    return {'dxf_path': dxf_path, 'sheet': meta['sheet'], 'meta': meta, 'verify': verify}


def verify_dxf(dxf_path: str, spec: dict) -> dict:
    """回读核对 (huashu-cad 纪律): 几何数/标注数/图层齐全/文件可开."""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    by_type = {}
    layers = set()
    for e in msp:
        by_type[e.dxftype()] = by_type.get(e.dxftype(), 0) + 1
        layers.add(e.dxf.layer)
    need_layers = {'FRAME', 'TITLE', 'OUTLINE', 'DIM', 'TECH'}
    present = need_layers & layers
    ok = (by_type.get('LWPOLYLINE', 0) + by_type.get('LINE', 0) >= 2
          and by_type.get('DIMENSION', 0) >= 1
          and len(present) == len(need_layers))
    return {
        'ok': ok,
        'entities': by_type,
        'layers': sorted(layers),
        'missing_layers': sorted(need_layers - layers),
        'dim_count': by_type.get('DIMENSION', 0),
    }


def render_preview_png(dxf_path: str, png_path: str, sheet: str = 'A4') -> None:
    """ezdxf matplotlib 渲染白底黑线预览."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.config import (
        Configuration, BackgroundPolicy, ColorPolicy, LineweightPolicy)
    doc = ezdxf.readfile(dxf_path)
    sw, sh = SHEET_SIZES[sheet]
    fig = plt.figure(figsize=(sw / 50, sh / 50))
    ax = fig.add_axes([0, 0, 1, 1])
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE,
                        color_policy=ColorPolicy.BLACK,
                        lineweight_policy=LineweightPolicy.ABSOLUTE,
                        lineweight_scaling=1.5)
    Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg
             ).draw_layout(doc.modelspace(), finalize=True)
    ax.set_xlim(-3, sw + 3)
    ax.set_ylim(-3, sh + 3)
    ax.set_aspect('equal')
    fig.savefig(png_path, dpi=150, facecolor='white')
    plt.close(fig)
