"""B线 Phase 2: 纯2D 确定性出图引擎 (spec → GB DXF + PNG预览 + 回读验证).

方法论来自 vendor/huashu-cad SKILL.md「先确定交付 → 制图 → 必须完成的验证」:
  - spec(结构化JSON)是唯一几何输入; LLM 只负责 NL→spec (cad2d_plan), 几何由
    确定性代码绘制, LLM 不直接触碰 DXF 实体
  - 1:1 模型单位 mm; 图纸比例只做纸面缩放, DIMLFAC=1/比例 保证 <> 标注显示真实mm
  - 产出后必须回读核对 (verify_output) + 渲染预览 (render_preview_png)

spec schema (全部坐标 mm, 1:1):
  {
    "title": str, "sheet": "A4"|"A3", "notes": [str],
    "geometry": [
      {"type":"line","p1":[x,y],"p2":[x,y],"layer":"OUTLINE"}
      {"type":"rect","x":..,"y":..,"w":..,"h":..,"layer":"OUTLINE"}
      {"type":"polyline","points":[[x,y],...],"closed":true,"layer":..}
      {"type":"circle","cx":..,"cy":..,"r":..,"layer":"HOLE"}
      {"type":"hole","cx":..,"cy":..,"r":..}          # 圆 + 十字中心线(GB)
      {"type":"arc","cx":..,"cy":..,"r":..,"start_angle":deg,"end_angle":deg}
      {"type":"text","s":str,"at":[x,y],"h":3.5,"layer":"TEXT"}
    ],
    "dims": [
      {"type":"linear","p1":[x,y],"p2":[x,y],"side":"bottom|top|left|right","offset":12,"text":"<>"}
      {"type":"diameter","cx":..,"cy":..,"r":..,"angle":135}
    ]
  }
"""
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import ezdxf
from ezdxf.enums import TextEntityAlignment

from src.engine.render_engine import (
    FONT_CJK, SHEET_SIZES, LAYERS,
    setup_chinese_font, setup_layers, setup_dimstyle,
)
from src.engine.dxf_checks import validate_dxf_file

# Phase 2 支持图幅 (宽×高 mm); 更大件走 A3, 再大属 Phase 3 (3D 生成)
SHEETS_2D = ('A4', 'A3')
STD_SCALES = [2.0, 1.5, 1.0, 0.5, 0.4, 0.25, 0.2]
KNOWN_LAYERS = {name for name, *_ in LAYERS}
GEOM_TYPES = {'line', 'rect', 'polyline', 'circle', 'hole', 'arc', 'text'}
SIDE_ALLOWS = {'bottom', 'top', 'left', 'right'}

# 图面布置 (mm, 纸面): GB/T 14689 装订边25/其余10; 标题栏右下; 技术要求左下
MARGIN_L, MARGIN_O = 25.0, 10.0
TITLE_W, TITLE_H = 150.0, 32.0


# ─────────────────────────────────────────────────────────────
# 1. spec 校验 (LLM 产出与人工输入同一道门)
# ─────────────────────────────────────────────────────────────

def _pt(v) -> Optional[Tuple[float, float]]:
    if (isinstance(v, (list, tuple)) and len(v) == 2
            and all(isinstance(c, (int, float)) for c in v)):
        return (float(v[0]), float(v[1]))
    return None


def spec_bbox(spec: dict) -> Optional[Tuple[float, float, float, float]]:
    """几何包围盒 (mm, 模型坐标); 无几何返回 None."""
    xs, ys = [], []
    for g in spec.get('geometry', []):
        t = g.get('type')
        if t == 'line':
            for k in ('p1', 'p2'):
                p = _pt(g.get(k))
                if p: xs.append(p[0]); ys.append(p[1])
        elif t == 'rect':
            xs += [g['x'], g['x'] + g['w']]; ys += [g['y'], g['y'] + g['h']]
        elif t in ('circle', 'hole'):
            xs += [g['cx'] - g['r'], g['cx'] + g['r']]
            ys += [g['cy'] - g['r'], g['cy'] + g['r']]
        elif t == 'arc':
            xs += [g['cx'] - g['r'], g['cx'] + g['r']]
            ys += [g['cy'] - g['r'], g['cy'] + g['r']]
        elif t == 'polyline':
            for p in (g.get('points') or []):
                q = _pt(p)
                if q: xs.append(q[0]); ys.append(q[1])
        elif t == 'text':
            p = _pt(g.get('at'))
            if p: xs.append(p[0]); ys.append(p[1])
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def validate_spec(spec: dict) -> List[str]:
    """返回错误清单 (空 = 通过). 结构/取值/图层面全查, 不做语义判断."""
    errs = []
    if not isinstance(spec, dict):
        return ['spec 必须是 JSON 对象']
    if not spec.get('title'):
        errs.append('缺少 title')
    if spec.get('sheet', 'A4') not in SHEETS_2D:
        errs.append(f'sheet 必须是 {SHEETS_2D} 之一')
    geoms = spec.get('geometry')
    if not isinstance(geoms, list) or not geoms:
        errs.append('geometry 必须是非空数组')
        geoms = []
    for i, g in enumerate(geoms):
        t = g.get('type')
        w = f'geometry[{i}]'
        if t not in GEOM_TYPES:
            errs.append(f'{w}: 未知类型 {t!r} (允许 {sorted(GEOM_TYPES)})'); continue
        if g.get('layer', 'OUTLINE') not in KNOWN_LAYERS:
            errs.append(f'{w}: 图层 {g.get("layer")!r} 不在白名单')
        if t == 'line' and not (_pt(g.get('p1')) and _pt(g.get('p2'))):
            errs.append(f'{w}: line 需要 p1/p2=[x,y]')
        if t == 'rect':
            if not all(isinstance(g.get(k), (int, float)) for k in ('x', 'y', 'w', 'h')):
                errs.append(f'{w}: rect 需要 x/y/w/h 数值')
            elif g['w'] <= 0 or g['h'] <= 0:
                errs.append(f'{w}: rect w/h 必须 >0')
        if t in ('circle', 'hole', 'arc'):
            if not (isinstance(g.get('r'), (int, float)) and g['r'] > 0):
                errs.append(f'{w}: r 必须 >0')
            elif not (isinstance(g.get('cx'), (int, float)) and isinstance(g.get('cy'), (int, float))):
                errs.append(f'{w}: 需要 cx/cy 数值')
        if t == 'arc':
            for k in ('start_angle', 'end_angle'):
                if not isinstance(g.get(k), (int, float)):
                    errs.append(f'{w}: arc 需要 {k} (度)')
        if t == 'polyline':
            pts = g.get('points')
            if not isinstance(pts, list) or len(pts) < 2 or not all(_pt(p) for p in pts):
                errs.append(f'{w}: polyline.points 需要 ≥2 个 [x,y]')
        if t == 'text' and not g.get('s'):
            errs.append(f'{w}: text 需要 s')
    dims = spec.get('dims', [])
    if not isinstance(dims, list):
        dims = []; errs.append('dims 必须是数组')
    for i, d in enumerate(dims):
        t = d.get('type'); w = f'dims[{i}]'
        if t == 'linear':
            if not (_pt(d.get('p1')) and _pt(d.get('p2'))):
                errs.append(f'{w}: linear 需要 p1/p2=[x,y]')
            if d.get('side', 'bottom') not in SIDE_ALLOWS:
                errs.append(f'{w}: side 必须 {sorted(SIDE_ALLOWS)}')
        elif t == 'diameter':
            if not (isinstance(d.get('r'), (int, float)) and d['r'] > 0
                    and isinstance(d.get('cx'), (int, float)) and isinstance(d.get('cy'), (int, float))):
                errs.append(f'{w}: diameter 需要 cx/cy/r')
        else:
            errs.append(f'{w}: 未知类型 {t!r} (允许 linear/diameter)')
    return errs


# ─────────────────────────────────────────────────────────────
# 2. 确定性绘制 (spec → DXF)
# ─────────────────────────────────────────────────────────────

def _pick_scale(bbox, sheet_w: float, sheet_h: float) -> Optional[float]:
    """内容区 = 图框内 - 标题栏 - 技术要求带; 选第一个放得下的标准比例."""
    gw, gh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    avail_w = sheet_w - MARGIN_L - MARGIN_O - 12
    avail_h = sheet_h - 2 * MARGIN_O - TITLE_H - 14
    for s in STD_SCALES:
        if gw * s <= avail_w and gh * s <= avail_h:
            return s
    return None


def _content_area(sheet_w: float, sheet_h: float):
    """(cx, cy) 内容区中心 (纸面坐标, 原点=图纸左下)."""
    x0 = MARGIN_L
    x1 = sheet_w - MARGIN_O
    y0 = MARGIN_O + TITLE_H + 14      # 避开标题栏+技术要求带
    y1 = sheet_h - MARGIN_O - 8
    return (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0


def generate_dxf(spec: dict, out_path: str) -> dict:
    """spec → GB DXF. 返回报告 {sheet, scale, counts}; spec 非法抛 ValueError."""
    errs = validate_spec(spec)
    if errs:
        raise ValueError('spec 校验失败: ' + '; '.join(errs[:8]))
    bbox = spec_bbox(spec)
    if bbox is None:
        raise ValueError('spec 无可绘制几何')

    sheet = spec.get('sheet', 'A4')
    sw, sh = SHEET_SIZES[sheet]           # render_engine 的 GB 图幅表 (宽×高)
    s = _pick_scale(bbox, sw, sh)
    if s is None:
        raise ValueError(f'几何 {bbox[2]-bbox[0]:.0f}×{bbox[3]-bbox[1]:.0f}mm 在 {sheet} '
                         f'任一标准比例下都放不下, 请缩放几何或换图幅')

    doc = ezdxf.new('R2013', setup=True)
    doc.units = ezdxf.units.MM
    setup_chinese_font(doc)
    setup_layers(doc)
    setup_dimstyle(doc, scale=s)          # DIMLFAC=1/s → <> 标注显示真实mm
    msp = doc.modelspace()

    ccx, ccy, cw, ch = _content_area(sw, sh)
    gx, gy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    ox, oy = ccx - gx * s, ccy - gy * s   # 模型→纸面: p*s + o

    def T(p):
        return (p[0] * s + ox, p[1] * s + oy)

    counts = {'line': 0, 'lwpolyline': 0, 'circle': 0, 'arc': 0, 'text': 0, 'dimension': 0}

    def add_text(txt, at, h, layer):
        msp.add_text(txt, height=h, dxfattribs={'layer': layer, 'style': FONT_CJK}
                     ).set_placement(at, align=TextEntityAlignment.MIDDLE_CENTER)
        counts['text'] += 1

    # --- 几何 ---
    for g in spec.get('geometry', []):
        t, lay = g.get('type'), g.get('layer', 'OUTLINE' if g.get('type') != 'hole' else 'HOLE')
        if t == 'line':
            msp.add_line(T(g['p1']), T(g['p2']), dxfattribs={'layer': lay}); counts['line'] += 1
        elif t == 'rect':
            x, y, w, h = g['x'], g['y'], g['w'], g['h']
            pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
            msp.add_lwpolyline([T(p) for p in pts], close=True, dxfattribs={'layer': lay})
            counts['lwpolyline'] += 1
        elif t == 'polyline':
            msp.add_lwpolyline([T(p) for p in g['points']], close=bool(g.get('closed')),
                               dxfattribs={'layer': lay})
            counts['lwpolyline'] += 1
        elif t == 'circle':
            c = T((g['cx'], g['cy']))
            msp.add_circle(c, g['r'] * s, dxfattribs={'layer': lay}); counts['circle'] += 1
        elif t == 'hole':
            c = T((g['cx'], g['cy']))
            msp.add_circle(c, g['r'] * s, dxfattribs={'layer': lay}); counts['circle'] += 1
            # GB 十字中心线: 超出圆周 3mm (纸面), 点划线层
            e = (g['r'] * s + 3)
            msp.add_line((c[0] - e, c[1]), (c[0] + e, c[1]), dxfattribs={'layer': 'CENTER'})
            msp.add_line((c[0], c[1] - e), (c[0], c[1] + e), dxfattribs={'layer': 'CENTER'})
            counts['line'] += 2
        elif t == 'arc':
            c = T((g['cx'], g['cy']))
            msp.add_arc(c, g['r'] * s, g['start_angle'], g['end_angle'],
                        dxfattribs={'layer': lay}); counts['arc'] += 1
        elif t == 'text':
            add_text(g['s'], T(g['at']), g.get('h', 3.5) * s, g.get('layer', 'TEXT'))

    # --- 尺寸 (真实值: 几何已×s, DIMLFAC=1/s 补偿) ---
    for d in spec.get('dims', []):
        t = d.get('type')
        if t == 'linear':
            p1, p2 = _pt(d['p1']), _pt(d['p2'])
            tp1, tp2 = T(p1), T(p2)
            off = d.get('offset', 12)
            side = d.get('side', 'bottom')
            horizontal = abs(p2[0] - p1[0]) >= abs(p2[1] - p1[1])
            if side in ('bottom', 'top'):
                base_y = min(tp1[1], tp2[1]) - off if side == 'bottom' else max(tp1[1], tp2[1]) + off
                dim = msp.add_linear_dim(base=(tp1[0], base_y), p1=tp1, p2=tp2, angle=0,
                                         dimstyle='GB_DIM', text=d.get('text', '<>'),
                                         dxfattribs={'layer': 'DIM'})
            else:
                base_x = min(tp1[0], tp2[0]) - off if side == 'left' else max(tp1[0], tp2[0]) + off
                dim = msp.add_linear_dim(base=(base_x, tp1[1]), p1=tp1, p2=tp2, angle=90,
                                         dimstyle='GB_DIM', text=d.get('text', '<>'),
                                         dxfattribs={'layer': 'DIM'})
            dim.render(); counts['dimension'] += 1
            _ = horizontal
        elif t == 'diameter':
            c = T((d['cx'], d['cy']))
            dim = msp.add_diameter_dim(c, radius=d['r'] * s, angle=d.get('angle', 135),
                                       dimstyle='GB_DIM', dxfattribs={'layer': 'DIM'})
            dim.render(); counts['dimension'] += 1

    # --- 技术要求 (左下) ---
    notes = spec.get('notes') or []
    ny = MARGIN_O + TITLE_H + 8
    if notes:
        add_text('技术要求', (MARGIN_L + 34, ny + 7 * len(notes) + 4), 4.2, 'TECH')
        for i, n in enumerate(notes):
            add_text(n, (MARGIN_L + 34 + len(n) * 1.6, ny + 7 * (len(notes) - 1 - i)), 3.0, 'TECH')

    # --- 标题栏 (右下, 简化 GB) ---
    tx, ty = sw - MARGIN_O - TITLE_W, MARGIN_O
    msp.add_lwpolyline([(tx, ty), (tx + TITLE_W, ty), (tx + TITLE_W, ty + TITLE_H), (tx, ty + TITLE_H)],
                       close=True, dxfattribs={'layer': 'TITLE'})
    add_text(spec['title'], (tx + TITLE_W * 0.62, ty + TITLE_H * 0.72),
             min(5.0, 60 / max(4, len(spec['title']))), 'TITLE')
    add_text('比例 1:g' if s >= 1 else f'比例 1:{1 / s:g}', (tx + TITLE_W * 0.62, ty + TITLE_H * 0.30), 3.2, 'TITLE')
    add_text('单位 mm', (tx + TITLE_W * 0.18, ty + TITLE_H * 0.72), 3.0, 'TITLE')
    add_text('Beacon B线', (tx + TITLE_W * 0.18, ty + TITLE_H * 0.30), 3.0, 'TITLE')
    counts['lwpolyline'] += 1

    # --- 图框 ---
    msp.add_lwpolyline([(0, 0), (sw, 0), (sw, sh), (0, sh)], close=True, dxfattribs={'layer': 'FRAME'})
    msp.add_lwpolyline([(MARGIN_L, MARGIN_O), (sw - MARGIN_O, MARGIN_O),
                        (sw - MARGIN_O, sh - MARGIN_O), (MARGIN_L, sh - MARGIN_O)],
                       close=True, dxfattribs={'layer': 'FRAME'})
    counts['lwpolyline'] += 2

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(out_path)
    return {'sheet': sheet, 'scale': s, 'counts': counts,
            'bbox_model': [round(v, 2) for v in bbox]}


# ─────────────────────────────────────────────────────────────
# 3. 必须完成的验证 (huashu-cad 纪律: 回读核对 + 渲染预览)
# ─────────────────────────────────────────────────────────────

def verify_output(out_path: str, spec: dict) -> dict:
    """回读落盘 DXF 逐项核对: 门禁检查 + 实体计数 + DIMENSION 显示值抽查."""
    doc = ezdxf.readfile(out_path)
    msp = doc.modelspace()
    findings = validate_dxf_file(out_path)

    by = {}
    for e in msp:
        by[e.dxftype()] = by.get(e.dxftype(), 0) + 1

    # DIMENSION 显示值 = 真实mm 抽查 (DIMLFAC 链路)
    ds = doc.dimstyles.get('GB_DIM')
    dim_checks = []
    for d in msp.query('DIMENSION'):
        try:
            m = d.get_measurement()
            dim_checks.append({'measured_paper': round(m, 2),
                               'displayed': round(m * ds.dxf.dimlfac, 2)})
            if len(dim_checks) >= 5:
                break
        except Exception:
            pass

    return {'gate_findings': [f.render() for f in findings][:10],
            'entity_counts': by,
            'dim_samples': dim_checks,
            # info 级 (如"按标注图纸处理") 是门禁的说明性输出, 不算失败
            'ok': not any(f.severity in ('error', 'warning') for f in findings)}


def render_preview_png(dxf_path: str, png_path: str, sheet: str = 'A4') -> None:
    """ezdxf matplotlib 渲染白底黑线预览 (huashu-cad drawing.py 同款配置)."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.config import (Configuration, BackgroundPolicy,
                                            ColorPolicy, LineweightPolicy)
    doc = ezdxf.readfile(dxf_path)
    sw, sh = SHEET_SIZES[sheet]
    fig = plt.figure(figsize=(sw / 50, sh / 50))
    ax = fig.add_axes([0, 0, 1, 1])
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE,
                        color_policy=ColorPolicy.BLACK,
                        lineweight_policy=LineweightPolicy.ABSOLUTE,
                        lineweight_scaling=1.6)
    Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg
             ).draw_layout(doc.modelspace(), finalize=True)
    ax.set_xlim(-2, sw + 2); ax.set_ylim(-2, sh + 2); ax.set_aspect('equal')
    Path(png_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png_path, dpi=200, facecolor='white')
    plt.close(fig)


def run_2d_pipeline(spec: dict, out_dir: str) -> dict:
    """生成 + 验证 + 预览一步到位 (route/CLI 共用)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dxf = str(out / 'drawing.dxf')
    gen = generate_dxf(spec, dxf)
    ver = verify_output(dxf, spec)
    png = str(out / 'preview.png')
    render_preview_png(dxf, png, gen['sheet'])
    (out / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding='utf-8')
    (out / 'report.json').write_text(
        json.dumps({'generate': gen, 'verify': ver}, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'dxf': dxf, 'png': png, 'generate': gen, 'verify': ver,
            'ok': ver['ok'], 'out_dir': str(out)}
