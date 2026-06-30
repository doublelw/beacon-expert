#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一渲染引擎 (M4 布局 + M5 标注 + M6 加工说明 + 中文字体 + 线宽规范)
====================================================================

职责(对应系统级设计 M4-M6 + M8):
  - M4 布局: 第一角六视图 SMART 布局(长对正 / 高平齐 / 间距=板高×0.3)
  - M5 标注: 按 drawing_plan 标注(若 plan 缺失则降级用 annotation.json 并标记)
  - M6 加工说明: GB 技术要求模板(GB/T1804-m / 锐边倒钝 / 材料 / 粗糙度)
  - 中文字体: ezdxf SimSun(styles.add font_name=simsun.ttc)
  - 线宽规范: 粗实线轮廓 / 细实线尺寸 / 虚线隐藏 / 点划线中心

输入:
  - projection.json (M3 六视图几何, core/projection.py 产物)
  - drawing_plan.json (M2 规划, 对照基准; 可选)
  - annotation.json (M5 标注, core/annotator.py 产物; plan 缺失时用)
  - geometry.json (bbox / holes, 备用)

输出:
  - DXF (R2013, GB 工程图, 中文字体)

第一角六视图布局 (GB/T 17452, SMART 量化):
  ┌─────────────────────────────────────┐
  │  图框 (A1: 841×594)                  │
  │                                      │
  │   ┌──────┐    ┌──────┐              │
  │   │ Top  │    │ Left │   ← 高平齐   │  Top=长边侧视(W×D)
  │   └──────┘    └──────┘     (Y对齐)  │  Left=短边侧视(H×D)
  │   ┌──────┐    ┌──────┐              │
  │   │Front │    │Right│              │  Front=板面(W×H, 孔正圆, 主视图)
  │   │(板面)│    ├──────┤              │
  │   └──────┘    │ Back │              │
  │   ┌──────┐    └──────┘              │
  │   │Bottom│                          │
  │   └──────┘                          │
  │   ↑长对正(X对齐,误差<1mm)            │
  │                                      │
  │  [标题栏]  [技术要求]  [明细栏]      │
  └─────────────────────────────────────┘

  SMART 规则:
    - Top 在 Front 正上方 (长对正, X 中心对齐, 间距=板厚D×2.5)
    - Bottom 在 Front 正下方 (同 Top)
    - Left 在 Front 右侧 (第一角: 左视图放右边, 高平齐)
    - Right 在 Left 下方, Back 在最右
    - 长对正: Top/Front/Bottom 中心 X 对齐, 误差 < 1mm
    - 高平齐: 侧列整体中心 Y = Front 中心 Y, 误差 < 1mm
    - 视图不拥挤: 间距 ≥ 板最大尺寸 × 0.2

中文字体 (GB):
  - ezdxf styles.add('SimSun') font_name='simsun.ttc'
  - 标注字号 3.5mm, 标题 7mm, 技术要求 5mm
  - DXF TEXT 中文不乱码(供应商 AutoCAD / 中望 / 浩辰 CAD 直接识别 simsun.ttc)

线宽规范 (GB/T 17450 / 4457.4):
  - 粗实线 0.5mm: 轮廓边 (OUTLINE 层)
  - 细实线 0.18mm: 尺寸线 / 延伸线 (DIM 层)
  - 虚线 0.25mm: 隐藏边 (HIDDEN 层, 本管线 HLR 已去隐藏, 保留层位)
  - 点划线 0.18mm: 中心线 / 对称线 (CENTER 层)
  - 细实线 0.13mm: 图框 / 标题栏 / 注释 (FRAME / TEXT 层)

运行:
  python core/render_engine.py \\
    --projection output/six_proj.json \\
    --annotation  output/six_ann.json \\
    --plan        output/drawing_plan.json \\
    --geometry    output/six_geom.json \\
    -o            output/fixed_board_gb.dxf
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import ezdxf
from ezdxf.enums import TextEntityAlignment
from .standard_parts import draw_fastener_standard, get_view_role

# ─────────────────────────────────────────────────────────────
# GB 规范常量 (SMART 量化)
# ─────────────────────────────────────────────────────────────

# 字体
FONT_CJK = 'SimSun'            # GB 工程图标准中文字体
FONT_CJK_FILE = 'simsun.ttc'   # ezdxf font_name (供应商 CAD 识别)
FONT_LATIN = 'txt'             # GB 西文 (与 SimSun 配套, AutoCAD gdt.shx)

# 字号 (GB/T 14689, 单位 mm)
FONT_HEIGHT_DIM = 3.5          # 标注
FONT_HEIGHT_TEXT = 5.0         # 一般文字
FONT_HEIGHT_TITLE = 7.0        # 标题
FONT_HEIGHT_TECH = 5.0         # 技术要求
FONT_HEIGHT_SMALL = 2.5        # 角注

# 线宽 (GB/T 17450, mm)
LW_THICK = 0.50                # 粗实线 (轮廓)
LW_THIN = 0.18                 # 细实线 (尺寸 / 延伸线)
LW_CENTER = 0.18               # 点划线 (中心)
LW_HIDDEN = 0.25               # 虚线 (隐藏)
LW_FRAME = 0.13                # 图框 / 标题栏

# 图层 (GB 工程图图层规范)
# (name, color, linetype, lineweight)
# color: 1=红 2=黄 3=绿 4=青 5=蓝 6=洋红 7=白/黑 8=灰
LAYERS = [
    ('OUTLINE',   7, 'CONTINUOUS',   LW_THICK),   # 可见轮廓 粗实线
    ('HOLE',      7, 'CONTINUOUS',   LW_THICK),   # 孔轮廓 (粗实线, 与板轮廓同级)
    ('CENTER',    1, 'CENTER',       LW_CENTER),  # 中心线 点划线 红
    ('HIDDEN',    4, 'HIDDEN',       LW_HIDDEN),  # 隐藏边 虚线 (预留)
    ('DIM',       2, 'CONTINUOUS',   LW_THIN),    # 尺寸线 / 延伸线 细实线
    ('LEADER',    2, 'CONTINUOUS',   LW_THIN),    # 引出线 细实线
    ('FRAME',     7, 'CONTINUOUS',   LW_FRAME),   # 图框
    ('TITLE',     7, 'CONTINUOUS',   LW_FRAME),   # 标题栏
    ('TEXT',      7, 'CONTINUOUS',   LW_FRAME),   # 一般文字
    ('TECH',      5, 'CONTINUOUS',   LW_FRAME),   # 技术要求
    ('BOM',       7, 'CONTINUOUS',   LW_FRAME),   # 明细栏
    ('SECTION',   1, 'CONTINUOUS',   LW_THIN),    # 剖面线 (预留)
]

# 图幅 (GB/T 14689, mm) — 宽×高
SHEET_SIZES = {
    'A0': (1189, 841), 'A1': (841, 594), 'A2': (594, 420),
    'A3': (420, 297),  'A4': (297, 210),
}

# 布局间距系数 (SMART)
SPACING_H_FACTOR = 0.3   # 上下间距 = 板高 H × 0.3
SPACING_W_FACTOR = 0.3   # 左右间距 = 板宽 W × 0.3
MIN_CROWD_FACTOR = 0.2   # 最小间距 = 板最大尺寸 × 0.2 (防拥挤)


# ─────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────

@dataclass
class ViewLayout:
    """单视图布局参数 (第一角)."""
    name: str
    origin: Tuple[float, float]   # 视图原点在图纸的坐标
    # 投影几何 bbox (2D, 该视图平面内)
    geom_xmin: float = 0.0
    geom_ymin: float = 0.0
    geom_xmax: float = 0.0
    geom_ymax: float = 0.0
    scale: float = 1.0

    @property
    def width(self) -> float:
        return self.geom_xmax - self.geom_xmin

    @property
    def height(self) -> float:
        return self.geom_ymax - self.geom_ymin

    @property
    def center_x(self) -> float:
        """视图几何中心(图纸坐标)."""
        # origin 已是几何左下角的图纸位置, 中心 = origin + 宽/2
        return self.origin[0] + (self.geom_xmax - self.geom_xmin) * self.scale / 2

    @property
    def bottom_y(self) -> float:
        # 几何最低点(geom_ymin)对应的图纸Y
        return self.origin[1]

    @property
    def top_y(self) -> float:
        return self.origin[1] + (self.geom_ymax - self.geom_ymin) * self.scale

    def to_abs(self, x: float, y: float) -> Tuple[float, float]:
        """视图局部 2D 坐标 → 图纸绝对坐标.

        关键: 几何局部坐标可能有任意偏移 (xmin/ymin), 需先归零再缩放.
        图纸坐标 = origin + (局部x - xmin, 局部y - ymin) * scale
        """
        ax = self.origin[0] + (x - self.geom_xmin) * self.scale
        ay = self.origin[1] + (y - self.geom_ymin) * self.scale
        return (ax, ay)


@dataclass
class LayoutResult:
    """六视图布局结果."""
    origins: Dict[str, Tuple[float, float]]
    views: Dict[str, ViewLayout] = field(default_factory=dict)
    sheet: str = 'A1'
    sheet_size: Tuple[float, float] = (841, 594)
    scale: float = 1.0
    # SMART 校验
    align_error_x: float = 0.0   # 长对正误差 (mm)
    align_error_y: float = 0.0   # 高平齐误差 (mm)


# ─────────────────────────────────────────────────────────────
# M4 布局: 第一角六视图 SMART
# ─────────────────────────────────────────────────────────────

def _view_geom_bbox(view_data: dict) -> Tuple[float, float, float, float]:
    """计算单视图 2D 几何 bbox (视图局部坐标系, 原点未平移)."""
    xs, ys = [], []
    for ln in view_data.get('lines', []):
        xs += [ln['p1'][0], ln['p2'][0]]
        ys += [ln['p1'][1], ln['p2'][1]]
    for a in view_data.get('arcs', []):
        xs += [a['cx'] - a['r'], a['cx'] + a['r']]
        ys += [a['cy'] - a['r'], a['cy'] + a['r']]
    for sp in view_data.get('splines', []):
        for p in sp.get('points', []):
            xs.append(p[0]); ys.append(p[1])
    for ci in view_data.get('circles', []):
        xs += [ci['cx'] - ci['r'], ci['cx'] + ci['r']]
        ys += [ci['cy'] - ci['r'], ci['cy'] + ci['r']]
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def _view_size(view_data: dict) -> Tuple[float, float]:
    """视图几何尺寸 (宽, 高), 用于布局排布 (不依赖坐标系偏移)."""
    b = _view_geom_bbox(view_data)
    return (b[2] - b[0], b[3] - b[1])


def _normalize_view_origin(view_data: dict) -> Tuple[float, float]:
    """视图几何的左下角 (用于把几何平移到图纸位置时减去偏移).

    每个视图是独立 2D 平面, 几何坐标可能有任意偏移 (如 zmin=-34.65).
    布局时把几何左下角对齐到图纸 origin, 故返回 (xmin, ymin).
    """
    b = _view_geom_bbox(view_data)
    return (b[0], b[1])


def _pick_sheet(W: float, H: float, D: float) -> Tuple[str, Tuple[float, float], float]:
    """按零件尺寸选图幅 + 比例 (GB/T 14689, GB/T 14690).

    板类零件第一角六视图布局 (GB/T 17452):
      主视图(Front)= 板面 W×H, 俯视(Top)=W×D 在上, 仰视(Bottom)=W×D 在下,
      左视(Left)=H×D 在右, 右视(Right)=H×D 在左(镜像), 后视(Back)=W×H 在最右.

    主列(纵向): Top(W×D) + Front(W×H) + Bottom(W×D)
      总高 = D + gap + H + gap + D = H + 2D + 2gap
      总宽 = W
    侧列(横向叠放 在 Front 右侧): Left/Right/Back (各 H×D 或 W×H)
      侧列总高 ≈ 主列 Front 区域高 (H), 单视图高 D
      总宽 = W + gap + max(H, W)

    策略: 候选图幅中找能放下且填充率 55-80% 的最小标准比例.
    """
    # 估算所需绘图区 (1:1)
    # 视图间距 (布局实际用的 spacing_v/h 公式同 layout_six_views)
    spacing_v_1to1 = max(D * 2.5, H * 0.25)
    spacing_h_1to1 = max(H * 0.4, W * 0.20)
    # 主列: Top + Front + Bottom (垂直), 宽=W, 高=2*spacing_v + 2D + H
    col1_w = W
    col1_h = 2 * spacing_v_1to1 + 2 * D + H
    # 侧列宽: Left/Right 短端面 = H (板宽方向)
    col2_w = H
    # 列3: Back 单独 = W (与 Front 同尺寸)
    col3_w = W
    draw_w_1to1 = col1_w + spacing_h_1to1 + col2_w + spacing_h_1to1 + col3_w
    draw_h_1to1 = col1_h

    std_scales = [1.0, 1 / 1.5, 1 / 2, 1 / 2.5, 1 / 3,
                  1 / 4, 1 / 5, 1 / 8, 1 / 10]
    # 工程图惯例: 优先缩小比例 (≤1:1), 放大比例仅用于微小件
    if min(W, H, D) < 20:
        std_scales = [2.0, 1.5, 1.0] + std_scales

    best = None   # (score, sheet_name, sheet_size, scale)
    # 六视图板类零件: A1(841×594) 首选, A0 次选 (GB 工程图惯例, 大图幅留标注空间)
    for name in ('A1', 'A0', 'A2'):
        sw, sh = SHEET_SIZES[name]
        avail_w = sw - 270    # 右侧标题栏
        avail_h = sh - 240    # 上下边距 + 技术要求
        # 对每个标准比例都打分 (不只取第一个 <=0.85)
        for sc in std_scales:
            vw = draw_w_1to1 * sc
            vh = draw_h_1to1 * sc
            fill_w = vw / avail_w if avail_w > 0 else 1
            fill_h = vh / avail_h if avail_h > 0 else 1
            fill = max(fill_w, fill_h)
            if fill > 0.85:
                continue   # 放不下
            if fill < 0.30:
                continue   # 填充过低 (图幅过大或比例过小)
            # 偏好: 填充率 0.70-0.85 (六视图紧凑但留标注空间), 标准比例 (1:2 最优)
            # 六视图信息量大, 接受较高填充率 (最高 0.85)
            target_dist = abs(fill - 0.72)
            sheet_pref = {'A4': 0, 'A3': 1, 'A2': 2, 'A1': 3, 'A0': 4}[name]
            # 标准比例奖励: 1:2 最优 (GB 工程图最常用)
            std_bonus = 0.0
            inv_sc = 1.0 / sc
            if abs(inv_sc - 2.0) < 0.01:
                std_bonus = -1.5     # 1:2 强烈偏好
            elif abs(inv_sc - 1.0) < 0.01:
                std_bonus = -0.8
            elif abs(inv_sc - 5.0) < 0.01 or abs(inv_sc - 10.0) < 0.01:
                std_bonus = -0.5
            inv_scale = 1.0 / sc
            scale_penalty = (inv_scale ** 1.5) * 0.10
            # 图幅偏好: 六视图大图幅好 (A1 > A2), 但避免 A0 过稀疏
            # A1 (pref=3) 轻微奖励, A0 (pref=4) 中性 (可能过大)
            sheet_bonus = {0: 0.6, 1: 0.3, 2: 0.1, 3: -0.2, 4: -0.1}[sheet_pref]
            score = target_dist * 4 + scale_penalty + std_bonus + sheet_bonus
            if best is None or score < best[0]:
                best = (score, name, (sw, sh), sc)

    if best is None:
        # 兜底: 板类零件六视图典型 = A1 1:2
        return 'A1', SHEET_SIZES['A1'], 1 / 2
    return best[1], best[2], round(best[3], 4)


def layout_six_views(projection: dict, plan: Optional[dict] = None) -> LayoutResult:
    """M4 第一角六视图 SMART 布局 (GB/T 17452).

    板类零件约定 (projection.py VIEW_DEFS, 板面在 XY, 厚度沿 Z):
      Front/Back  axes=(x,y): 板面 W×H (孔正圆, 主视图)
      Top/Bottom  axes=(x,z): 长边侧视 W×D
      Left/Right  axes=(y,z): 短边侧视 H×D

    客户固定板.dxf 实测布局 (CAD 实测反馈, 2026-06-22):
      ┌──────┬──────────┬──────┬──────┐
      │      │   Top    │      │      │   ← Top 在 Front 正上方 (长对正 X)
      ├──────┼──────────┼──────┼──────┤
      │ Left │  Front   │Right │ Back │   ← Front 居中, Left 左, Right 右, Back 最右
      │      │  (板面)  │      │      │      (高平齐 Y)
      ├──────┼──────────┼──────┴──────┘
      │      │  Bottom  │            ← Bottom 在 Front 正下方 (长对正 X)
      └──────┴──────────┘

    即: Front 居中, Top/Bottom 在 Front 正上/正下 (主列, 长对正),
        Left 在 Front 左, Right 在 Front 右 (与 Front 同行, 高平齐),
        Back 在最右侧 (远离 Front, 第一角投影惯例).

    SMART 规则(量化):
      * 长对正: Top/Front/Bottom 中心 X 对齐 (误差 <1mm)
      * 高平齐: Front/Left/Right/Back 中心 Y 对齐 (误差 <1mm)
      * 间距: 上下 = 板厚 D × 2.5 (保证 D 小时仍清晰); 左右 = 板高 H × 0.4
      * 图幅利用率 55-75% (不挤不散)
    """
    bbox = projection.get('bbox', {})
    W = bbox.get('width', 300.0)
    H = bbox.get('height', 200.0)
    D = bbox.get('depth', 25.0)
    views = projection.get('views', {})

    # plan 可指定图幅/比例
    if plan and 'frame' in plan and plan['frame'].get('size'):
        sheet_name = plan['frame']['size']
        sheet_size = SHEET_SIZES.get(sheet_name, SHEET_SIZES['A1'])
        scale = plan['frame'].get('scale_val', 0.5)
    else:
        sheet_name, sheet_size, scale = _pick_sheet(W, H, D)

    sw, sh = sheet_size
    s = scale

    # 各视图几何尺寸 (W, H) + 左下角偏移 (xmin, ymin)
    view_info: Dict[str, dict] = {}
    for vn in ('Front', 'Back', 'Top', 'Bottom', 'Left', 'Right'):
        if vn in views:
            bb = _view_geom_bbox(views[vn])
            view_info[vn] = {
                'w': bb[2] - bb[0],
                'h': bb[3] - bb[1],
                'xmin': bb[0],
                'ymin': bb[1],
            }

    def _w(vn):
        return view_info.get(vn, {}).get('w', 0.0)
    def _h(vn):
        return view_info.get(vn, {}).get('h', 0.0)

    # SMART 间距 (图纸单位, 已乘比例)
    # 上下间距: 板厚 D 小 (26mm), 用 max(D*2.5, H*0.25) 保证侧视图不挤
    spacing_v = max(D * 2.5, H * 0.25) * s      # 上下 (垂直)
    spacing_h = max(H * 0.4, W * 0.20) * s      # 左右 (水平)
    min_gap = max(W, H) * MIN_CROWD_FACTOR * s
    spacing_v = max(spacing_v, min_gap)
    spacing_h = max(spacing_h, min_gap)

    # === 主列 (Front 中心列): Top + Front + Bottom, 长对正 ===
    main_col_views = [v for v in ('Top', 'Front', 'Bottom') if v in view_info]
    main_col_w = max((_w(v) for v in main_col_views), default=0) * s
    main_col_h = (sum(_h(v) * s for v in main_col_views)
                  + spacing_v * max(0, len(main_col_views) - 1))

    # === 左列 (Left): 单视图, 与 Front 高平齐 ===
    has_left = 'Left' in view_info
    left_col_w = _w('Left') * s if has_left else 0

    # === 右列 (Right): 单视图, 与 Front 高平齐 ===
    has_right = 'Right' in view_info
    right_col_w = _w('Right') * s if has_right else 0

    # === Back 列 (最右): 单视图, 与 Front 高平齐 ===
    has_back = 'Back' in view_info
    back_col_w = _w('Back') * s if has_back else 0

    # 图纸可用区 (边距 + 标题栏 + 技术要求)
    margin_l = 60.0
    margin_r = 210.0     # 右侧标题栏
    margin_t = 40.0
    margin_b = 200.0     # 底部技术要求
    avail_w = sw - margin_l - margin_r
    avail_h = sh - margin_t - margin_b

    # 总宽 = left + gap + main + gap + right + gap + back
    total_w = main_col_w
    if has_left:
        total_w += spacing_h + left_col_w
    if has_right:
        total_w += spacing_h + right_col_w
    if has_back:
        total_w += spacing_h + back_col_w
    # 行高 = 主列高 (Top+Front+Bottom 垂直堆叠)
    total_h = main_col_h

    # 自适应比例: 若超出可用区, 缩小比例
    if total_w > avail_w * 0.95 or total_h > avail_h * 0.95:
        s_new = min(avail_w * 0.92 / max(total_w / s, 1),
                    avail_h * 0.92 / max(total_h / s, 1))
        s = round(max(s_new, 0.05), 4)
        spacing_v = max(D * 2.5, H * 0.25) * s
        spacing_h = max(H * 0.4, W * 0.20) * s
        min_gap = max(W, H) * MIN_CROWD_FACTOR * s
        spacing_v = max(spacing_v, min_gap)
        spacing_h = max(spacing_h, min_gap)
        main_col_w = max((_w(v) for v in main_col_views), default=0) * s
        main_col_h = (sum(_h(v) * s for v in main_col_views)
                      + spacing_v * max(0, len(main_col_views) - 1))
        left_col_w = _w('Left') * s if has_left else 0
        right_col_w = _w('Right') * s if has_right else 0
        back_col_w = _w('Back') * s if has_back else 0
        total_w = main_col_w
        if has_left:
            total_w += spacing_h + left_col_w
        if has_right:
            total_w += spacing_h + right_col_w
        if has_back:
            total_w += spacing_h + back_col_w
        total_h = main_col_h

    # 整体居中 (在可用区内)
    x_start = margin_l + max(0, (avail_w - total_w) / 2)
    y_start = margin_b + max(0, (avail_h - total_h) / 2)

    origins: Dict[str, Tuple[float, float]] = {}

    # === 主列布局 (Top → Front → Bottom, 自顶向下, 长对正 X 中心对齐) ===
    # Left 在 Front 左 → 主列右移让出左侧 Left 空间
    main_col_left_x = x_start + (left_col_w + spacing_h if has_left else 0)
    cur_top_y = y_start + main_col_h
    front_origin_x = None
    front_origin_y = None
    for vn in main_col_views:   # ['Top', 'Front', 'Bottom']
        vw = _w(vn) * s
        vh = _h(vn) * s
        # 长对正: 中心 X 对齐
        left_x = main_col_left_x + (main_col_w - vw) / 2
        bottom_y = cur_top_y - vh
        origins[vn] = (round(left_x, 3), round(bottom_y, 3))
        if vn == 'Front':
            front_origin_x = left_x
            front_origin_y = bottom_y
        cur_top_y = bottom_y - spacing_v   # 下一个视图在下方

    # Front 中心 Y (用于侧视图/Back 高平齐)
    if front_origin_y is not None:
        front_cy = front_origin_y + _h('Front') * s / 2
    elif 'Front' in origins:
        front_cy = origins['Front'][1] + _h('Front') * s / 2
    else:
        front_cy = y_start + total_h / 2

    # === Left/Right 在 Front 左右两侧 (视图竖向), 与 Front 高平齐; Back 最右 ===
    if has_left:
        left_vh = _h('Left') * s
        origins['Left'] = (round(x_start, 3), round(front_cy - left_vh / 2, 3))
    if has_right:
        right_vh = _h('Right') * s
        origins['Right'] = (round(main_col_left_x + main_col_w + spacing_h, 3),
                            round(front_cy - right_vh / 2, 3))
    if has_back:
        back_vh = _h('Back') * s
        back_x = main_col_left_x + main_col_w + spacing_h
        if has_right:
            back_x += right_col_w + spacing_h
        origins['Back'] = (round(back_x, 3), round(front_cy - back_vh / 2, 3))

    # === SMART 校验: 长对正 (Top/Front/Bottom 中心X) + 高平齐 (Front/Left/Right/Back 中心Y) ===
    align_err_x = 0.0
    main_centers = []
    for vn in ('Top', 'Front', 'Bottom'):
        if vn in view_info and vn in origins:
            cx = origins[vn][0] + _w(vn) * s / 2
            main_centers.append(cx)
    if len(main_centers) >= 2:
        align_err_x = max(main_centers) - min(main_centers)

    align_err_y = 0.0
    main_cy = (origins['Front'][1] + _h('Front') * s / 2
               if 'Front' in origins and 'Front' in view_info else None)
    side_cys = []
    for vn in ('Left', 'Right', 'Back'):
        if vn in origins and vn in view_info:
            side_cys.append(origins[vn][1] + _h(vn) * s / 2)
    if main_cy is not None and side_cys:
        align_err_y = max(abs(cy - main_cy) for cy in side_cys)

    view_layouts: Dict[str, ViewLayout] = {}
    for vn, (ox, oy) in origins.items():
        if vn in view_info:
            info = view_info[vn]
            view_layouts[vn] = ViewLayout(
                name=vn, origin=(ox, oy),
                geom_xmin=info['xmin'], geom_ymin=info['ymin'],
                geom_xmax=info['xmin'] + info['w'],
                geom_ymax=info['ymin'] + info['h'],
                scale=s,
            )

    return LayoutResult(
        origins=origins, views=view_layouts,
        sheet=sheet_name, sheet_size=sheet_size, scale=s,
        align_error_x=round(align_err_x, 4),
        align_error_y=round(align_err_y, 4),
    )


# ─────────────────────────────────────────────────────────────
# 字体 / 图层 / 样式初始化
# ─────────────────────────────────────────────────────────────

def setup_chinese_font(doc: 'ezdxf.document.Drawing') -> None:
    """配置中文字体 SimSun (GB 工程图标准).

    ezdxf 1.4: doc.styles.add(name).font_name = 'simsun.ttc'
    供应商 AutoCAD / 中望 / 浩辰 CAD 自动用系统 simsun.ttc 渲染中文.
    macOS 无 SimSun 时, DXF 内仍写 simsun.ttc (跨平台标准), 本地 ezdxf
    预览可降级到 Songti SC (由 audit 脚本处理).
    """
    existing_styles = {s.dxf.name for s in doc.styles}
    # 主中文字体样式 (ezdxf 1.4: styles.add(name, font=...) 必填 font)
    if FONT_CJK not in existing_styles:
        try:
            style = doc.styles.add(FONT_CJK, font=FONT_CJK_FILE)
        except Exception:
            # 兼容旧签名: 先 add 再设 font_name
            try:
                style = doc.styles.add(FONT_CJK, font='arial.ttf')
                style.font_name = FONT_CJK_FILE
                style.dxf.font = FONT_CJK_FILE
            except Exception:
                pass

    # 西文配套
    if FONT_LATIN not in existing_styles:
        try:
            doc.styles.add(FONT_LATIN, font='txt.shx')
        except Exception:
            pass


def setup_dimstyle(doc: 'ezdxf.document.Drawing') -> None:
    """创建 GB 标注样式 (DIMSTYLE), 用 SimSun 字体.

    DIMENSION 的文字样式由 DIMSTYLE 控制 (非 dimension 本身).
    GB/T 4458.4 标注: 字高 3.5mm, 箭头 2.5mm, 延伸线间隙 1mm.

    关键修复: ezdxf 的 EZDXF 模板默认 dimlfac=100 (cm 显示), 导致标注文字
    被放大 100 倍 (例: 150mm 显示成 "15000")。GB 工程图必须 dimlfac=1.0 (mm).
    """
    existing = [ds.dxf.name for ds in doc.dimstyles]
    if 'GB_DIM' in existing:
        # 已存在则覆盖关键参数 (避免旧文件 dimlfac=100 残留)
        ds = doc.dimstyles.get('GB_DIM')
    else:
        try:
            ds = doc.dimstyles.duplicate_entry('EZDXF', 'GB_DIM')
        except Exception:
            try:
                ds = doc.dimstyles.add('GB_DIM')
            except Exception:
                return
    try:
        ds.dxf.dimtxsty = FONT_CJK        # 标注文字样式 = SimSun
        ds.dxf.dimtxt = FONT_HEIGHT_DIM   # 文字高度 3.5
        ds.dxf.dimasz = 2.5               # 箭头尺寸
        ds.dxf.dimgap = 1.0               # 文字与尺寸线间隙
        ds.dxf.dimexe = 2.0               # 延伸线超出尺寸线
        ds.dxf.dimexo = 1.0               # 延伸线偏移原点
        ds.dxf.dimclrt = 7                # 文字颜色 白
        ds.dxf.dimclrd = 2                # 尺寸线颜色 黄
        ds.dxf.dimclre = 2                # 延伸线颜色 黄
        ds.dxf.dimlfac = 1.0              # 长度比例因子 = 1 (mm 显示, 不放大)
        ds.dxf.dimtfac = 1.0              # 公差文字高度比例
        ds.dxf.dimlunit = 2               # 单位: 十进制
        ds.dxf.dimdec = 1                 # 小数位 1 (例: 150.0)
        ds.dxf.dimadec = 0                # 角度小数位
        ds.dxf.dimaunit = 0               # 角度单位: 十进制
        ds.dxf.dimpost = '<>'             # 文字前缀后缀 (空)
    except Exception:
        pass


def setup_layers(doc: 'ezdxf.document.Drawing') -> None:
    """配置 GB 图层 (颜色 / 线型 / 线宽)."""
    # 线型: 确保 CONTINUOUS / CENTER / HIDDEN 存在
    linetypes_needed = {'CONTINUOUS', 'CENTER', 'HIDDEN', 'DASHED', 'PHANTOM'}
    existing_lt = {lt.dxf.name for lt in doc.linetypes}
    if 'CENTER' not in existing_lt:
        try:
            doc.linetypes.add('CENTER', pattern='A 12.7,-5.08,2.54,-5.08',
                              description='___ . ___ . ___')
        except Exception:
            pass
    if 'HIDDEN' not in existing_lt:
        try:
            doc.linetypes.add('HIDDEN', pattern='A 6.35,-3.18,6.35,-3.18',
                              description='___ ___ ___')
        except Exception:
            pass

    # 线宽常量 (ezdxf Lineweight)
    # 0=默认 5=0.05mm 13=0.13mm 18=0.18mm 25=0.25mm 50=0.50mm
    lw_map = {0.13: 13, 0.18: 18, 0.25: 25, 0.50: 50}

    existing_layers = {l.dxf.name for l in doc.layers}
    for name, color, linetype, lw in LAYERS:
        if name in existing_layers:
            continue
        lw_ezdxf = lw_map.get(lw, 0)
        try:
            doc.layers.add(name=name, color=color, linetype=linetype)
            layer = doc.layers.get(name)
            layer.dxf.lineweight = lw_ezdxf
        except Exception:
            doc.layers.add(name=name, color=color)


# ─────────────────────────────────────────────────────────────
# M3 几何渲染 (六视图)
# ─────────────────────────────────────────────────────────────

def render_projection(msp, projection: dict, layout: LayoutResult, geometry: Optional[dict] = None) -> Dict[str, int]:
    """渲染六视图几何到 modelspace.

    每条几何 (line/arc/spline/circle) 按 ViewLayout.origin 平移到图纸坐标.
    轮廓/孔 → OUTLINE/HOLE 层 (粗实线).
    """
    counts = {'line': 0, 'arc': 0, 'spline': 0, 'circle': 0}
    views = projection.get('views', {})
    _std_drawn = set()  # 已画standard_parts的位置(vn,cx,cy,r) 防止circles+arcs重复

    # 自动孔类型符号 (GB螺纹画法): veritas 识别的 csink沉头/thread螺纹孔 → 加3/4弧(大径)
    symbol_pos = set()
    csink_holes = []
    fastener_callout = {}
    spec_radii = set()         # csink+thread半径(GB符号, 半径匹配替代位置匹配)
    spec_thread_radii = set()  # thread半径(压铆法兰)
    try:
        if os.path.exists('output/veritas.json'):
            _vdata = json.load(open('output/veritas.json'))
            for _h in _vdata.get('features', []):
                if _h.get('type') == 'PIERCING':
                    if _h.get('hole_type') in ('csink', 'thread'):
                        symbol_pos.add((round(abs(_h['position'][0]), 0), round(abs(_h['position'][1]), 0)))
                        for _r in _h.get('all_radii', [_h['radius']]):
                            spec_radii.add(round(_r, 1))
                    if _h.get('hole_type') == 'csink':
                        csink_holes.append(_h)
                    if _h.get('hole_type') == 'thread':
                        for _r in _h.get('all_radii', [_h['radius']]):
                            spec_thread_radii.add(round(_r, 1))
        if os.path.exists('output/ai_plan.json'):
            _plan = json.load(open('output/ai_plan.json'))
            for _f in _plan.get('fasteners', []):
                _co = _f.get('callout', '')
                _d = _f.get('spec_diameter_mm', 0)
                _part = _f.get('part', '')
                for _pos in _f.get('positions', []):
                    fastener_callout[(round(abs(_pos[0]), 0), round(abs(_pos[1]), 0))] = \
                        f'{_co} {_part} φ{_d:g}'
    except Exception:
        pass

    for vn, view_data in views.items():
        if vn not in layout.views:
            continue
        vl = layout.views[vn]

        for ln in view_data.get('lines', []):
            p1 = vl.to_abs(ln['p1'][0], ln['p1'][1])
            p2 = vl.to_abs(ln['p2'][0], ln['p2'][1])
            msp.add_line(p1, p2, dxfattribs={'layer': 'OUTLINE'})
            counts['line'] += 1

        for a in view_data.get('arcs', []):
            a1 = math.degrees(a.get('start_angle', 0))
            a2 = math.degrees(a.get('end_angle', 6.28318))
            center = vl.to_abs(a['cx'], a['cy'])
            r = a['r'] * vl.scale
            _aht = a.get('hole_type', '')
            _aaxis = a.get('axis_dir', 'Z')
            _ahead = a.get('head_face')
            # 标准件: arcs也用standard_parts库(统一, thread弧段在Top/Left/Right)
            if _aht in ('csink', 'thread', 'clear'):
                _std_key = (vn, round(a['cx'], 0), round(a['cy'], 0), round(a['r'], 1))
                if _std_key not in _std_drawn:  # 去重(circles已画则arcs不重复)
                    _std_drawn.add(_std_key)
                    try:
                        draw_fastener_standard(msp, center, r, _aht, _aaxis, vn, _ahead, vl.scale)
                    except Exception:
                        msp.add_arc(center, r, a1, a2, dxfattribs={'layer': '0'})
                else:
                    pass  # circles循环已画standard_parts, arcs不重复
            else:
                msp.add_arc(center, r, a1, a2, dxfattribs={'layer': 'OUTLINE'})
            counts['arc'] += 1

        for sp in view_data.get('splines', []):
            pts = sp.get('points', sp.get('ctrl_points', []))
            if len(pts) < 2:
                continue
            abs_pts = [vl.to_abs(p[0], p[1]) for p in pts]
            if all(math.hypot(abs_pts[i][0]-abs_pts[0][0],
                              abs_pts[i][1]-abs_pts[0][1]) < 1e-6
                   for i in range(1, len(abs_pts))):
                continue
            try:
                if len(abs_pts) == 2:
                    msp.add_line(abs_pts[0], abs_pts[1],
                                 dxfattribs={'layer': 'OUTLINE'})
                    counts['line'] += 1
                else:
                    msp.add_spline(abs_pts, dxfattribs={'layer': 'OUTLINE'})
                    counts['spline'] += 1
            except Exception:
                try:
                    msp.add_lwpolyline(abs_pts, dxfattribs={'layer': 'OUTLINE'})
                    counts['line'] += 1
                except Exception:
                    pass

        # 真孔径集合(geometry holes_2d),区分真孔vs seam/圆角
        true_radii = set(round(h['r'], 1) for h in (geometry or {}).get('holes_2d', [])) if geometry else set()
        for ci in view_data.get('circles', []):
            center = vl.to_abs(ci['cx'], ci['cy'])
            r = ci['r'] * vl.scale
            r_local = round(ci['r'], 1)
            _ht = ci.get('hole_type', '')
            _axis = ci.get('axis_dir', 'Z')
            _head = ci.get('head_face')
            # 标准件: 用标准件库GB图形(不画HLR圆, 直接标准图形)
            if _ht in ('csink', 'thread', 'clear'):
                _std_key = (vn, round(ci['cx'], 0), round(ci['cy'], 0), round(ci['r'], 1))
                if _std_key not in _std_drawn:  # 去重(circles+arcs同位置只画一次)
                    _std_drawn.add(_std_key)
                    try:
                        draw_fastener_standard(msp, center, r, _ht, _axis, vn, _head, vl.scale)
                    except Exception:
                        msp.add_circle(center, r, dxfattribs={'layer': '0'})
                counts['circle'] += 1
            else:
                # 非标准件(轮廓圆/其它): HLR几何
                is_hole = (not true_radii) or (r_local in true_radii)
                msp.add_circle(center, r, dxfattribs={'layer': 'HOLE' if is_hole else 'OUTLINE'})
                counts['circle'] += 1
            # 视图标注(代号+尺寸+引线)
            _pos_key = (round(abs(ci['cx']), 0), round(abs(ci['cy']), 0))
            _co = fastener_callout.get(_pos_key, '')
            if _co and r_local >= 2.5:
                _tx = center[0] + r + 10 * vl.scale
                _ty = center[1] + r + 8 * vl.scale
                msp.add_text(_co, dxfattribs={'height': 4 * vl.scale, 'insert': (_tx, _ty),
                                              'layer': 'DIM', 'style': FONT_CJK})
                msp.add_line((center[0] + r * 0.7, center[1] + r * 0.7), (_tx - 1, _ty - 1),
                             dxfattribs={'layer': 'DIM'})

        # 沉头钉侧面V形锥剖面 (仅Left/Right画, Top/Bottom易出孤立线暂禁)
        if vn in ('Left', 'Right') and csink_holes:
            _bzmin = projection['bbox']['zmin']
            _bzmax = projection['bbox']['zmax']
            for _h in csink_holes:
                _x, _y, _z = _h['position']
                _hf = _h.get('head_face', '-Z')
                _allr = sorted(_h.get('all_radii', [_h['radius']]))
                _R = max(_allr)      # 沉头大径
                _r = min(_allr)      # 底孔
                _hz = _bzmin if _hf == '-Z' else _bzmax
                _depth = max((_R - _r) * 1.5, 1.0)
                _sign = 1 if _hf == '-Z' else -1
                _hz2 = _hz + _sign * _depth
                _backz = _bzmax if _hf == '-Z' else _bzmin
                if vn in ('Left', 'Right'):
                    # Left/Right: cx=Z(板厚), cy=Y(板高). 钉贯穿cx(Z), V形在cx=_hz端
                    _p1 = vl.to_abs(_hz, _y + _R);   _p2 = vl.to_abs(_hz2, _y + _r)
                    _p3 = vl.to_abs(_hz2, _y - _r);  _p4 = vl.to_abs(_hz, _y - _R)
                    _pb1 = vl.to_abs(_backz, _y + _r); _pb2 = vl.to_abs(_backz, _y - _r)
                else:
                    # Top/Bottom: cx=X(板宽), cy=Z(板厚). 钉贯穿cy(Z), V形在cy=_hz端
                    _p1 = vl.to_abs(_x + _R, _hz);   _p2 = vl.to_abs(_x + _r, _hz2)
                    _p3 = vl.to_abs(_x - _r, _hz2);  _p4 = vl.to_abs(_x - _R, _hz)
                    _pb1 = vl.to_abs(_x + _r, _backz); _pb2 = vl.to_abs(_x - _r, _backz)
                msp.add_line(_p1, _p2, dxfattribs={'layer': 'OUTLINE'})   # 锥面斜线
                msp.add_line(_p4, _p3, dxfattribs={'layer': 'OUTLINE'})
                msp.add_line(_p2, _pb1, dxfattribs={'layer': 'OUTLINE'})  # 钉杆
                msp.add_line(_p3, _pb2, dxfattribs={'layer': 'OUTLINE'})

    return counts


# ─────────────────────────────────────────────────────────────
# M5 标注渲染 (drawing_plan 驱动)
# ─────────────────────────────────────────────────────────────

def _resolve_dims(plan: Optional[dict], annotation: Optional[dict]) -> Tuple[List[dict], str]:
    """标注来源解析 (M5 关键: 按 plan 标注, 非自己拍脑袋).

    返回 (dimensions, source).
      source='drawing_plan':      M2 规划驱动 (理想), 坐标=零件坐标
      source='annotation_fallback': M2 缺失降级用 annotator, 坐标需反推

    坐标统一为【零件视图局部坐标】(与 projection.json 一致):
      - plan: 假设已是零件坐标 (planner 应输出零件坐标)
      - annotator: 标注坐标 = DEFAULT_ORIGINS[view] + 零件坐标, 需减去 origin

    重要修复 (2026-06-22): 若 plan.annotations 缺 p1/p2 (只有 value/view/angle
    的高级规划), 不能用 [0,0] 默认值 (会产生 meas=0 的 DIMENSION, CAD 不显示).
    必须降级到 annotation (annotator 已生成具体坐标).
    """
    if plan and 'annotations' in plan:
        plan_anns = plan['annotations']
        # 检查 plan 是否提供具体坐标 (p1/p2)
        has_coords = any('p1' in a and 'p2' in a for a in plan_anns)
        if has_coords:
            dims = []
            for a in plan_anns:
                if 'p1' not in a or 'p2' not in a:
                    continue   # 跳过无坐标的标注
                dims.append({
                    'type': a.get('type', 'linear'),
                    'p1': a.get('p1', [0, 0]),
                    'p2': a.get('p2', [0, 0]),
                    'value': a.get('value', 0),
                    'view': a.get('view', 'Top'),
                    'level': a.get('level', 0),
                    'side': a.get('side', 'bottom'),
                    'angle': a.get('angle', 0),
                    'text': a.get('text', ''),
                    'target': a.get('target', ''),
                })
            if dims:
                return dims, 'drawing_plan'
        # plan 没有具体坐标 → 降级到 annotation
    if annotation and 'dimensions' in annotation:
        # annotator 坐标反推: 减去 DEFAULT_ORIGINS 得零件坐标
        # DEFAULT_ORIGINS 同 annotator.py: Top(250,380) Front(250,40) Left(760,380)
        ANN_ORIGINS = {'Top': (250.0, 380.0), 'Front': (250.0, 40.0),
                       'Left': (760.0, 380.0)}
        dims = []
        for d in annotation['dimensions']:
            vn = d.get('view', 'Top')
            ox, oy = ANN_ORIGINS.get(vn, (0.0, 0.0))
            nd = dict(d)
            p1 = d.get('p1', [0, 0])
            p2 = d.get('p2', [0, 0])
            nd['p1'] = [p1[0] - ox, p1[1] - oy]
            nd['p2'] = [p2[0] - ox, p2[1] - oy]
            if d.get('leader_pts'):
                nd['leader_pts'] = [[pp[0] - ox, pp[1] - oy] for pp in d['leader_pts']]
            dims.append(nd)
        return dims, 'annotation_fallback'
    return [], 'none'


def render_annotation(msp, dims: List[dict], layout: LayoutResult,
                      source: str = 'annotation_fallback') -> Dict[str, int]:
    """渲染标注 (GB/T 4458.4).

    linear:  外形 / 孔距 (DIMLINEAR)
    radius:  孔径 (RADIUS / %%C 直径)
    leader:  引出线 (厚度 / 大孔 / 技术要求指向)

    标注文字 style=SimSun, height=3.5mm.
    每条标注带 target/source 字段 (可追溯, M7 审计用).
    """
    stats = {'linear': 0, 'radius': 0, 'leader': 0, 'failed': 0}

    for d in dims:
        t = d.get('type', 'linear')
        vn = d.get('view', 'Top')
        if vn not in layout.views:
            stats['failed'] += 1
            continue
        vl = layout.views[vn]

        try:
            p1_local = d.get('p1', [0, 0])
            p2_local = d.get('p2', [0, 0])
            # 局部零件坐标 → 图纸坐标 (via to_abs, 含 scale + origin)
            p1 = vl.to_abs(p1_local[0], p1_local[1])
            p2 = vl.to_abs(p2_local[0], p2_local[1])
            text = d.get('text', '')
            level = d.get('level', 0)
            side = d.get('side', 'bottom')
            angle = d.get('angle', 0)

            if t == 'linear':
                # 标注层偏移 (图纸单位, 已含比例)
                offset = (25 + level * 18) * vl.scale
                if angle == 0:
                    if side == 'bottom':
                        base = (p1[0], p1[1] - offset)
                    else:
                        base = (p1[0], p1[1] + offset)
                else:
                    if side == 'left':
                        base = (p1[0] - offset, p1[1])
                    else:
                        base = (p1[0] + offset, p1[1])

                # 重要: dimstyle 必须作为独立参数传 (非 dxfattribs).
                # ezdxf add_linear_dim 解析 dimstyle 参数, dxfattribs 里的 dimstyle
                # 不被识别, 导致 dimlfac 等 DIMSTYLE 设置失效 (文字放大 100 倍).
                # 客户反馈 "DIMENSION 不显示" 的根因之一: 文字值=14990 而非 150,
                # CAD 用户看不到正常尺寸.
                dim = msp.add_linear_dim(
                    base=base, p1=p1, p2=p2, angle=angle,
                    dimstyle='GB_DIM',
                    dxfattribs={'layer': 'DIM'})
                dim.render()
                stats['linear'] += 1

            elif t == 'radius':
                r_val = d.get('value', 0)
                if r_val <= 0 and 'r' in d:
                    r_val = d['r']
                center = p1
                r_scaled = r_val * vl.scale
                # 同上: dimstyle 必须作为独立参数
                dim = msp.add_radius_dim(
                    center=center, radius=r_scaled, angle=45,
                    dimstyle='GB_DIM',
                    dxfattribs={'layer': 'DIM'})
                dim.render()
                stats['radius'] += 1

            elif t == 'leader':
                pts_local = d.get('leader_pts', [list(p1_local), list(p2_local)])
                pts_abs = [vl.to_abs(pp[0], pp[1]) for pp in pts_local]
                msp.add_leader(pts_abs, dxfattribs={'layer': 'LEADER'})
                if pts_abs and text:
                    end = pts_abs[-1]
                    msp.add_text(
                        text,
                        dxfattribs={
                            'height': FONT_HEIGHT_DIM * vl.scale,
                            'insert': (end[0] + 2, end[1] + 2),
                            'layer': 'TEXT', 'style': FONT_CJK,
                        })
                stats['leader'] += 1

        except Exception:
            stats['failed'] += 1

    return stats


# ─────────────────────────────────────────────────────────────
# M6 加工说明 (GB 技术要求)
# ─────────────────────────────────────────────────────────────

def _build_tech_requirements(plan: Optional[dict], geometry: Optional[dict]) -> List[str]:
    """技术要求模板 (GB).

    按零件类型 + plan.tech_req 组合. 默认模板:
      1. 未注公差按 GB/T 1804-m
      2. 锐边倒钝 R0.5
      3. 表面去毛刺、清洁
      4. 材料 Q235 (或 plan 指定)
      5. 关键面粗糙度 Ra 3.2 (或 plan 指定)
      6. 热处理: 调质 HB220-250 (若 plan 要求)
    """
    tech = []
    if plan and 'tech_req' in plan:
        # plan 已规划, 优先用
        return list(plan['tech_req'])

    # 默认 GB 模板
    material = 'SPCC 冷轧钢板'
    roughness = 'Ra 3.2'
    if geometry:
        # 板类零件默认粗糙度
        D = geometry.get('depth', 25)
        if D < 10:
            roughness = 'Ra 1.6'
        elif D > 40:
            roughness = 'Ra 6.3'

    tech = [
        '1. 未注尺寸公差按 GB/T 1804-m。',
        '2. 未注形位公差按 GB/T 1184-K。',
        '3. 锐边倒钝 R0.5。',
        f'4. 材料: {material}。钢板 GB/T 709。板厚 ' + str(D) + 'mm。',
        f'5. 关键加工面粗糙度 {roughness}, 其余 {roughness}。',
        '6. 表面去毛刺、清洁, 不得有划伤、锈蚀。',
        '7. 孔口倒角 C0.5。',
        '8. 表面处理: 镀锌蓝白铬酸盐(Fe/Ep.Zn5), 盐雾测试48H。',
        '9. 关键面形位公差: 平面度0.2, 平行度0.1 (基准A)。',
    ]
    return tech


def render_tech_requirements(msp, tech_list: List[str], layout: LayoutResult) -> None:
    """渲染技术要求 (MTEXT, 多行, GB 格式)."""
    sw, sh = layout.sheet_size
    # 技术要求放在图纸左下角
    x = 60
    y = 50

    # 标题
    msp.add_text(
        '技术要求',
        dxfattribs={
            'height': FONT_HEIGHT_TECH,
            'insert': (x, y + 5),
            'layer': 'TECH', 'style': FONT_CJK,
        })
    y -= 8

    # 条目 (MTEXT 多行)
    if tech_list:
        content = '\\P'.join(tech_list)  # \\P = ezdxf MTEXT 换行
        msp.add_mtext(
            content,
            dxfattribs={
                'layer': 'TECH',
                'insert': (x, y),
                'char_height': FONT_HEIGHT_DIM,
                'style': FONT_CJK,
            })


# ─────────────────────────────────────────────────────────────
# 图框 / 标题栏 / 明细栏
# ─────────────────────────────────────────────────────────────

def render_frame(msp, layout: LayoutResult, plan: Optional[dict],
                 geometry: Optional[dict]) -> None:
    """图框 + 标题栏 + 明细栏 (GB/T 10609.1)."""
    sw, sh = layout.sheet_size

    # 外框 (粗)
    msp.add_lwpolyline(
        [(0, 0), (sw, 0), (sw, sh), (0, sh)],
        close=True, dxfattribs={'layer': 'FRAME'})
    # 内框 (留装订边 25mm 左, 10mm 其余)
    ml, mr, mt, mb = 25.0, 10.0, 10.0, 10.0
    msp.add_lwpolyline(
        [(ml, mb), (sw - mr, mb), (sw - mr, sh - mt), (ml, sh - mt)],
        close=True, dxfattribs={'layer': 'FRAME'})

    # 标题栏 (右下角, GB 标准尺寸 180×56)
    tb_w, tb_h = 180.0, 56.0
    tb_x0 = sw - mr - tb_w
    tb_y0 = mb
    msp.add_lwpolyline(
        [(tb_x0, tb_y0), (tb_x0 + tb_w, tb_y0),
         (tb_x0 + tb_w, tb_y0 + tb_h), (tb_x0, tb_y0 + tb_h)],
        close=True, dxfattribs={'layer': 'TITLE'})

    # 标题栏内分隔线
    # 横线
    mid_y = tb_y0 + tb_h / 2
    msp.add_line((tb_x0, mid_y), (tb_x0 + tb_w, mid_y), dxfattribs={'layer': 'TITLE'})
    # 竖线 (三栏)
    x1 = tb_x0 + tb_w * 0.4
    x2 = tb_x0 + tb_w * 0.7
    msp.add_line((x1, tb_y0), (x1, tb_y0 + tb_h), dxfattribs={'layer': 'TITLE'})
    msp.add_line((x2, tb_y0), (x2, tb_y0 + tb_h), dxfattribs={'layer': 'TITLE'})

    # 标题栏文字
    title_block = plan.get('title_block', {}) if plan else {}
    name = title_block.get('name', '固定板')
    number = title_block.get('number', 'BEACON-001')
    scale_str = f"1:{1/layout.scale:g}" if layout.scale < 1 else f"{layout.scale:g}:1"
    material = title_block.get('material', 'Q235')

    def _add_text(txt, x, y, h, layer='TITLE'):
        msp.add_text(txt, dxfattribs={
            'height': h, 'insert': (x, y), 'layer': layer, 'style': FONT_CJK})

    # 名称 (大字)
    _add_text(name, tb_x0 + 5, tb_y0 + tb_h - 14, FONT_HEIGHT_TITLE)
    # 图号
    _add_text(number, tb_x0 + 5, tb_y0 + 5, FONT_HEIGHT_TEXT)
    # 比例
    _add_text(f'比例 {scale_str}', x1 + 5, tb_y0 + tb_h - 12, FONT_HEIGHT_TEXT)
    _add_text(f'图幅 {layout.sheet}', x1 + 5, tb_y0 + 5, FONT_HEIGHT_TEXT)
    # 材料
    _add_text(f'材料 {material}', x2 + 5, tb_y0 + tb_h - 12, FONT_HEIGHT_TEXT)
    _add_text(f'单位: mm', x2 + 5, tb_y0 + 5, FONT_HEIGHT_TEXT)

    # 明细栏 (标题栏上方, 单件)
    bom_y0 = tb_y0 + tb_h
    bom_h = 20.0
    msp.add_lwpolyline(
        [(tb_x0, bom_y0), (tb_x0 + tb_w, bom_y0),
         (tb_x0 + tb_w, bom_y0 + bom_h), (tb_x0, bom_y0 + bom_h)],
        close=True, dxfattribs={'layer': 'BOM'})
    # 序号 名称 图号 数量
    _add_text('1', tb_x0 + 3, bom_y0 + 6, FONT_HEIGHT_SMALL, 'BOM')
    _add_text(name, tb_x0 + 12, bom_y0 + 6, FONT_HEIGHT_SMALL, 'BOM')
    _add_text(number, tb_x0 + tb_w * 0.5, bom_y0 + 6, FONT_HEIGHT_SMALL, 'BOM')
    _add_text('1', tb_x0 + tb_w - 12, bom_y0 + 6, FONT_HEIGHT_SMALL, 'BOM')


def render_fastener_bom(msp, layout, plan_path='output/ai_plan.json'):
    """紧固件明细栏 (AI识别结果直接体现, 对标样例 Y1:9-S-M3-1 + 明细栏).
    AI 识别的钉类型 → 代号+规格+数量+位置, 画在图纸左侧."""
    if not os.path.exists(plan_path):
        return 0
    plan = json.load(open(plan_path))
    fasteners = plan.get('fasteners', [])
    if not fasteners:
        return 0
    x0 = 60
    y = 135
    msp.add_text('AI识别 · 紧固件明细', dxfattribs={
        'height': FONT_HEIGHT_TEXT, 'insert': (x0, y), 'layer': 'TEXT', 'style': FONT_CJK})
    y -= 8
    for f in fasteners:
        spec = f.get('spec', '')
        d = f.get('spec_diameter_mm', 0)
        through = '贯穿' if f.get('through') else '盲'
        pos = f.get('positions', [[]])
        pos_str = f'({pos[0][0]:.0f},{pos[0][1]:.0f})' if pos and pos[0] else ''
        txt = f'{spec}  φ{d:g}{through}  示例{pos_str}'
        msp.add_text(txt, dxfattribs={
            'height': FONT_HEIGHT_DIM, 'insert': (x0, y), 'layer': 'TEXT', 'style': FONT_CJK})
        y -= 5
    return len(fasteners)


def render_outline_dims(msp, projection, layout):
    """M5 外形尺寸标注: 用Front视图实际几何(lines最外端点)作标注端点, 不用全局bbox.
    端点准确 = 工程师能看懂标的是哪段."""
    vl = layout.views.get('Front')
    if not vl:
        return 0
    vd = projection.get('views', {}).get('Front', {})
    # 从实际几何算外轮廓端点(lines + circles 的最外点)
    xs, ys = [], []
    for ln in vd.get('lines', []):
        xs += [ln['p1'][0], ln['p2'][0]]
        ys += [ln['p1'][1], ln['p2'][1]]
    for c in vd.get('circles', []):
        xs += [c['cx'] - c['r'], c['cx'] + c['r']]
        ys += [c['cy'] - c['r'], c['cy'] + c['r']]
    for a in vd.get('arcs', []):
        xs += [a['cx'] - a['r'], a['cx'] + a['r']]
        ys += [a['cy'] - a['r'], a['cy'] + a['r']]
    if not xs:
        return 0
    xmin, ymin, xmax, ymax = min(xs), min(ys), max(xs), max(ys)
    n = 0
    try:
        # 底部宽标注(实际左下→右下)
        p1 = vl.to_abs(xmin, ymin)
        p2 = vl.to_abs(xmax, ymin)
        dim = msp.add_linear_dim(base=(p1[0], p1[1] - 35 * layout.scale),
                                 p1=p1, p2=p2, angle=0, dimstyle='GB_DIM',
                                 dxfattribs={'layer': 'DIM'})
        dim.render(); n += 1
        # 左侧高标注(实际左下→左上)
        p3 = vl.to_abs(xmin, ymin)
        p4 = vl.to_abs(xmin, ymax)
        dim2 = msp.add_linear_dim(base=(p3[0] - 35 * layout.scale, p3[1]),
                                  p1=p3, p2=p4, angle=90, dimstyle='GB_DIM',
                                  dxfattribs={'layer': 'DIM'})
        dim2.render(); n += 1
    except Exception:
        pass
    return n


def render_tolerance_table(msp, layout):
    """公差表 (GB/T 1804, 对标样例公差表)."""
    sw, sh = layout.sheet_size
    tx = sw - 240; ty = sh - 70
    rows = [
        ('公差等级 GB/T1804', '线性', '角度'),
        ('精密 F 级', '±0.05', '±0.5°'),
        ('中等 M 级', '±0.1', '±0.5°'),
    ]
    for i, (g, l, a) in enumerate(rows):
        y = ty - i * 6
        msp.add_text(g, dxfattribs={'height': 3, 'insert': (tx, y), 'layer': 'TEXT', 'style': FONT_CJK})
        msp.add_text(l, dxfattribs={'height': 3, 'insert': (tx + 115, y), 'layer': 'TEXT', 'style': FONT_CJK})
        msp.add_text(a, dxfattribs={'height': 3, 'insert': (tx + 160, y), 'layer': 'TEXT', 'style': FONT_CJK})
    return 1


def render_bend_lines(msp, projection, layout, veritas_path='output/veritas.json'):
    """钣金折弯线标注 (成形Front视图, 对标样例折弯中心线)."""
    if not os.path.exists(veritas_path):
        return 0
    v = json.load(open(veritas_path))
    vl = layout.views.get('Front')
    if not vl:
        return 0
    n = 0
    for f in v.get('features', []):
        if f['type'] != 'BEND':
            continue
        ax = f['axis']; x, y, z = f['center']
        half = f.get('span', 50) / 2
        if abs(ax[0]) > 0.5:        # X轴折弯 → 折弯线沿X(水平)
            p1 = vl.to_abs(x - half, y); p2 = vl.to_abs(x + half, y)
        elif abs(ax[1]) > 0.5:      # Y轴折弯 → 沿Y(竖直)
            p1 = vl.to_abs(x, y - half); p2 = vl.to_abs(x, y + half)
        else:
            continue
        msp.add_line(p1, p2, dxfattribs={'layer': 'CENTER'})   # 折弯中心线(点划线)
        # 折弯标注: 角度+长度(span)+内圆角R (按drawing_requirements清单, 当前漏标的)
        _mx = (p1[0] + p2[0]) / 2; _my = (p1[1] + p2[1]) / 2
        _span = f.get('span', 0); _r = f.get('radius', 0)
        msp.add_text(f'90° L{_span:.0f} R{_r:.1f}', dxfattribs={
            'height': 3 * layout.scale,
            'insert': (_mx + 3, _my + 3), 'layer': 'DIM', 'style': FONT_CJK})
        n += 1
    return n


def render_hole_position_dims(msp, projection, layout, plan_path='output/ai_plan.json'):
    """M5 孔位置标注: 每类钉代表孔距基准边X/Y (工程师定位打孔用). 分层偏移避重叠."""
    if not os.path.exists(plan_path):
        return 0
    plan = json.load(open(plan_path))
    vl = layout.views.get('Front')
    if not vl:
        return 0
    vd = projection.get('views', {}).get('Front', {})
    xs, ys = [], []
    for ln in vd.get('lines', []):
        xs += [ln['p1'][0], ln['p2'][0]]; ys += [ln['p1'][1], ln['p2'][1]]
    for c in vd.get('circles', []):
        xs += [c['cx']-c['r'], c['cx']+c['r']]; ys += [c['cy']-c['r'], c['cy']+c['r']]
    if not xs:
        return 0
    xmin, ymin = min(xs), min(ys)
    # shapely标注避让(贪心找无碰撞base位置, 对标样例标注躲开)
    try:
        from shapely.geometry import box as sbox
        placed_x, placed_y = [], []
        def _find_clear(placed, cx, cy, w, h, axis='y'):
            """贪心找无碰撞位置(沿axis偏移)"""
            for d in range(0, 180, 10):
                for sign in (-1, 1):
                    v = cy + sign * d * layout.scale
                    bb = sbox(cx - w, (v if axis == 'y' else cy) - h,
                              cx + w, (v if axis == 'y' else cy) + h) if axis == 'y' \
                        else sbox(v - w, cy - h, v + w, cy + h)
                    if not any(bb.intersects(p) for p in placed):
                        placed.append(bb)
                        return v
            placed.append(sbox(cx - w, cy - h, cx + w, cy + h))
            return cy
    except Exception:
        def _find_clear(placed, cx, cy, w, h, axis='y'):
            return cy + (len(placed) % 5) * 18 * layout.scale

    n = 0
    for i, f in enumerate(plan.get('fasteners', [])):
        d = f.get('spec_diameter_mm', 0)
        is_csink = '沉头' in f.get('part', '')
        is_key = d >= 5 or is_csink    # 关键孔(大孔/沉头)标X+Y
        pos = f['positions'][0] if f.get('positions') else None
        if not pos:
            continue
        pa = vl.to_abs(pos[0], pos[1])
        try:
            if is_key:
                # X标注(距左边) — shapely避让base_y
                p1 = vl.to_abs(xmin, pos[1]); p2 = vl.to_abs(pos[0], pos[1])
                by = _find_clear(placed_x, (p1[0]+p2[0])/2, p1[1]-25*layout.scale,
                                 abs(p2[0]-p1[0])/2+8, 4, 'y')
                dim = msp.add_linear_dim(base=(p1[0], by), p1=p1, p2=p2,
                                         angle=0, dimstyle='GB_DIM', dxfattribs={'layer': 'DIM'})
                dim.render(); n += 1
                # Y标注(距下边) — shapely避让base_x
                p3 = vl.to_abs(pos[0], ymin); p4 = vl.to_abs(pos[0], pos[1])
                bx = _find_clear(placed_y, p3[1]+(p4[1]-p3[1])/2, p3[0]-25*layout.scale,
                                 4, abs(p4[1]-p3[1])/2+4, 'x')
                dim2 = msp.add_linear_dim(base=(bx, p3[1]), p1=p3, p2=p4,
                                          angle=90, dimstyle='GB_DIM', dxfattribs={'layer': 'DIM'})
                dim2.render(); n += 1
            # 阵列间距文字(同规格钉定位: N个×间距)
            sp = f.get('spacing')
            co = f.get('callout', '')
            if sp and sp.get('typical_mm') and f.get('count', 0) > 1:
                txt = f"{co}: {f['count']}个 间距{sp['typical_mm']}"
                msp.add_text(txt, dxfattribs={'height': 3 * layout.scale,
                             'insert': (pa[0] + 6, pa[1] + 6 + (n % 8) * 4),
                             'layer': 'DIM', 'style': FONT_CJK})
                n += 1
        except Exception:
            pass
    return n


def render_diameter_dims(msp, projection, layout, plan_path='output/ai_plan.json'):
    """M5 孔径标注: 每类钉标直径φ (对标样例直径DIMENSION)."""
    if not os.path.exists(plan_path):
        return 0
    plan = json.load(open(plan_path))
    vl = layout.views.get('Front')
    if not vl:
        return 0
    n = 0
    for f in plan.get('fasteners', []):
        d = f.get('spec_diameter_mm', 0)
        pos = f['positions'][0] if f.get('positions') else None
        if not pos or d < 3:
            continue
        center = vl.to_abs(pos[0], pos[1])
        r = d / 2 * vl.scale
        try:
            dim = msp.add_diameter_dim(center, radius=r, angle=45,
                                       dimstyle='GB_DIM', dxfattribs={'layer': 'DIM'})
            dim.render(); n += 1
        except Exception:
            try:
                msp.add_text(f'φ{d:g}', dxfattribs={'height': 3.5 * vl.scale,
                             'insert': (center[0] + r + 3, center[1] + r + 3),
                             'layer': 'DIM', 'style': FONT_CJK})
                msp.add_line((center[0], center[1]),
                             (center[0] + r + 3, center[1] + r + 3),
                             dxfattribs={'layer': 'DIM'})
                n += 1
            except Exception:
                pass
    return n


def render_side_dims(msp, projection, layout):
    """M5 侧视图标注: Left/Right/Top/Bottom标板厚+侧向特征."""
    bb = projection['bbox']
    D = bb['zmax'] - bb['zmin']
    n = 0
    for vn in ('Left', 'Right'):
        vl = layout.views.get(vn)
        if not vl:
            continue
        try:
            # 板厚标注(侧视)
            p1 = vl.to_abs(bb['zmin'], bb['ymin'])
            p2 = vl.to_abs(bb['zmax'], bb['ymin'])
            dim = msp.add_linear_dim(base=(p1[0], p1[1] - 25 * layout.scale),
                                     p1=p1, p2=p2, angle=0, dimstyle='GB_DIM',
                                     dxfattribs={'layer': 'DIM'})
            dim.render(); n += 1
        except Exception:
            pass
    return n


# ─────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────

def render(projection: dict,
           plan: Optional[dict] = None,
           annotation: Optional[dict] = None,
           geometry: Optional[dict] = None,
           output_dxf: str = 'output.dxf') -> dict:
    """统一渲染主入口 (M4 布局 + M3 几何 + M5 标注 + M6 加工 + 图框).

    Args:
        projection: projection.json (M3 六视图几何, 必需)
        plan:       drawing_plan.json (M2 规划, 可选, M5 标注基准)
        annotation: annotation.json (M5 标注, plan 缺失时降级用)
        geometry:   geometry.json (bbox/holes, 备用)
        output_dxf: 输出 DXF 路径

    Returns:
        渲染报告 (counts / SMART 校验 / 标注来源 / 技术要求条数)
    """
    # === M4 布局 ===
    layout = layout_six_views(projection, plan)

    # === ezdxf 文档 ===
    doc = ezdxf.new('R2013', setup=True)
    setup_chinese_font(doc)
    setup_layers(doc)
    setup_dimstyle(doc)
    msp = doc.modelspace()

    # === M3 几何 ===
    geom_counts = render_projection(msp, projection, layout, geometry)

    # === M5 外形尺寸标注 (对标样例线性标注) ===
    dim_count = render_outline_dims(msp, projection, layout)

    # === 公差表 (GB/T 1804, 对标样例) ===
    render_tolerance_table(msp, layout)

    # === 钣金折弯线标注 (钣金特有, 对标样例) ===
    bend_count = render_bend_lines(msp, projection, layout)

    # === M5 孔位置标注 (每类钉距基准X/Y, 工程师定位用) ===
    hole_dim_count = render_hole_position_dims(msp, projection, layout)

    # === M5 孔径标注 (每类钉标直径φ, 对标样例直径DIMENSION) ===
    dia_count = render_diameter_dims(msp, projection, layout)

    # === M5 侧视图标注 (板厚+侧向特征, 多视图标注) ===
    side_count = render_side_dims(msp, projection, layout)

    # === M5 标注 ===
    dims, dim_source = _resolve_dims(plan, annotation)
    # 若 plan 提供标注, 需把 plan 的坐标(可能是零件坐标)映射到视图;
    # annotator 已用视图局部坐标 (p1=origin+offset), 这里一致处理.
    ann_stats = render_annotation(msp, dims, layout, source=dim_source)

    # === M6 加工说明 ===
    tech_list = _build_tech_requirements(plan, geometry)
    render_tech_requirements(msp, tech_list, layout)

    # === 图框 / 标题栏 ===
    render_frame(msp, layout, plan, geometry)

    # === 紧固件明细栏 (AI识别结果直接体现, 对标样例 Y1:9-S-M3-1+明细栏) ===
    fastener_count = render_fastener_bom(msp, layout)

    # === 保存 ===
    os.makedirs(os.path.dirname(os.path.abspath(output_dxf)), exist_ok=True)
    doc.saveas(output_dxf)

    # === 渲染报告 (M7 审计输入) ===
    report = {
        'output': output_dxf,
        'sheet': layout.sheet,
        'sheet_size': list(layout.sheet_size),
        'scale': layout.scale,
        'views_rendered': list(layout.origins.keys()),
        'view_origins': {k: list(v) for k, v in layout.origins.items()},
        'geometry_counts': geom_counts,
        'annotation_source': dim_source,
        'annotation_stats': ann_stats,
        'tech_req_count': len(tech_list),
        'tech_req': tech_list,
        'smart_check': {
            'align_error_x_mm': layout.align_error_x,
            'align_x_pass': layout.align_error_x < 1.0,   # 长对正误差 <1mm
            'align_error_y_mm': layout.align_error_y,
            'align_y_pass': layout.align_error_y < 1.0,   # 高平齐误差 <1mm
            'spacing_v_mm': max(
                (projection.get('bbox', {}).get('depth', 25)) * 2.5,
                (projection.get('bbox', {}).get('height', 200)) * 0.25),
            'spacing_h_mm': max(
                (projection.get('bbox', {}).get('height', 200)) * 0.4,
                (projection.get('bbox', {}).get('width', 300)) * 0.20),
            'font_cjk': FONT_CJK,
            'font_cjk_file': FONT_CJK_FILE,
        },
        'layers': [{'name': n, 'color': c, 'linetype': lt, 'lineweight_mm': lw}
                   for n, c, lt, lw in LAYERS],
    }
    return report


# ─────────────────────────────────────────────────────────────
# IO
# ─────────────────────────────────────────────────────────────

def _load(path: Optional[str]) -> Optional[dict]:
    if not path or not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        description='统一渲染引擎 (M4 布局 + M3 几何 + M5 标注 + M6 加工 + 中文字体)')
    p.add_argument('--projection', required=True, help='projection.json (M3)')
    p.add_argument('--plan', default=None, help='drawing_plan.json (M2, 可选)')
    p.add_argument('--annotation', default=None, help='annotation.json (M5 降级)')
    p.add_argument('--geometry', default=None, help='geometry.json (备用)')
    p.add_argument('-o', '--output', default='output/fixed_board_gb.dxf',
                   help='输出 DXF')
    args = p.parse_args(argv)

    proj = _load(args.projection)
    plan = _load(args.plan)
    ann = _load(args.annotation)
    geom = _load(args.geometry)

    if proj is None:
        print(f'[render_engine] ERROR: projection not found: {args.projection}',
              file=sys.stderr)
        return 1

    report = render(proj, plan, ann, geom, args.output)

    # 输出报告
    print(f"[render_engine] {report['output']}")
    print(f"  图幅 {report['sheet']} ({report['sheet_size'][0]}×{report['sheet_size'][1]}mm) "
          f"比例 1:{1/report['scale']:g}")
    print(f"  视图: {report['views_rendered']}")
    print(f"  几何: {report['geometry_counts']}")
    print(f"  标注: source={report['annotation_source']} "
          f"stats={report['annotation_stats']}")
    print(f"  技术要求: {report['tech_req_count']} 条")
    sc = report['smart_check']
    print(f"  SMART: 长对正误差 {sc['align_error_x_mm']}mm "
          f"(pass={sc['align_x_pass']}) 高平齐误差 {sc.get('align_error_y_mm',0)}mm "
          f"(pass={sc.get('align_y_pass',True)}) 字体 {sc['font_cjk']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
