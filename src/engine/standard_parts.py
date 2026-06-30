#!/usr/bin/env python3
"""标准件GB六视图标准图形库 (替代HLR几何画标准件).

每种标准件, 每视图角色(钉头面/侧面/背面/轴向), 标准GB画法.
AI识别元件 → 从库取标准图形 → 画在位置(不依赖HLR几何).

路线: 标准件用本库GB图形, HLR只画板外形/折弯/冲压.
"""
import math

# 视图角色判断: 根据孔轴向 + 视图方向 → 该视图是钉头面/侧面/背面/轴向
def get_view_role(axis_dir, view_name, head_face=None):
    """返回视图角色: head(钉头面)/tip(钉尖面)/side(侧面)/axial(轴向)/face(板面)"""
    # Z轴孔: Front/Back是板面(轴向)
    if axis_dir == 'Z':
        if view_name == 'Front':
            return 'head' if head_face == '-Z' else 'tip'
        if view_name == 'Back':
            return 'tip' if head_face == '-Z' else 'head'
        return 'side'  # Left/Right/Top/Bottom是侧面
    # X轴孔: Left/Right是轴向
    if axis_dir == 'X':
        if view_name in ('Left', 'Right'):
            return 'axial'
        return 'side'
    # Y轴孔: Top/Bottom是轴向
    if axis_dir == 'Y':
        if view_name in ('Top', 'Bottom'):
            return 'axial'
        return 'side'
    return 'side'


def draw_csink(msp, center, r, role, scale, FONT_CJK='SimSun'):
    """沉头螺钉 GB标准图形 (按视图角色画标准画法).
    GB/T 4458.4: 螺纹孔=底径圆(粗)+大径3/4弧(细)+十字中心线."""
    if role == 'head':   # 钉头面: 3圈(底孔+螺纹3/4弧+沉头)+十字
        msp.add_circle(center, r, dxfattribs={'layer': '0'})
        msp.add_arc(center, r * 1.15, 150, 60, dxfattribs={'layer': '0'})
        msp.add_circle(center, r * 1.5, dxfattribs={'layer': '0'})
        cl = r * 2.0
        msp.add_line((center[0]-cl, center[1]), (center[0]+cl, center[1]), dxfattribs={'layer': '中心线'})
        msp.add_line((center[0], center[1]-cl), (center[0], center[1]+cl), dxfattribs={'layer': '中心线'})
    elif role == 'tip':  # 背面钉尖穿出: 螺纹孔(底径圆+大径3/4弧)+十字
        msp.add_circle(center, r, dxfattribs={'layer': '0'})
        msp.add_arc(center, r * 1.15, 150, 60, dxfattribs={'layer': '0'})
        cl = r * 1.8
        msp.add_line((center[0]-cl, center[1]), (center[0]+cl, center[1]), dxfattribs={'layer': '中心线'})
        msp.add_line((center[0], center[1]-cl), (center[0], center[1]+cl), dxfattribs={'layer': '中心线'})
    elif role == 'side':  # 侧面: V形锥剖面+杆
        pass  # 由render_bend_lines的csink_holes段处理V形


def draw_thread_bso(msp, center, r, role, scale):
    """压铆螺母BSO GB标准图形 (钣金标准件).
    GB: 法兰面(轴向)=3圈花形, 螺纹面(背面/对面轴向)=螺纹孔(底径+大径弧,无法兰)."""
    if role in ('axial', 'tip'):  # 轴向视图(法兰面/螺纹面统一, GB钣金压铆两面可见)
        msp.add_circle(center, r, dxfattribs={'layer': '0'})          # 底径
        msp.add_arc(center, r * 1.1, 150, 60, dxfattribs={'layer': '0'})  # 大径3/4弧
        if role == 'axial':  # 法兰面额外画法兰圈
            msp.add_circle(center, r * 1.35, dxfattribs={'layer': '0'})  # 法兰
        cl = r * 2.0
        msp.add_line((center[0]-cl, center[1]), (center[0]+cl, center[1]), dxfattribs={'layer': '中心线'})
        msp.add_line((center[0], center[1]-cl), (center[0], center[1]+cl), dxfattribs={'layer': '中心线'})


def draw_clear(msp, center, r, role, scale):
    """过孔 GB标准图形."""
    if role in ('head', 'tip', 'axial', 'face'):
        # 板面/轴向: 圆+十字(大孔)
        msp.add_circle(center, r, dxfattribs={'layer': '0'})
        if r >= 2.5:
            cl = r * 1.8
            msp.add_line((center[0]-cl, center[1]), (center[0]+cl, center[1]), dxfattribs={'layer': '中心线'})
            msp.add_line((center[0], center[1]-cl), (center[0], center[1]+cl), dxfattribs={'layer': '中心线'})


def draw_fastener_standard(msp, center, r, hole_type, axis_dir, view_name, head_face, scale):
    """标准件GB六视图标准图形入口(AI识别→本函数→标准图形)."""
    role = get_view_role(axis_dir, view_name, head_face)
    if hole_type == 'csink':
        draw_csink(msp, center, r, role, scale)
    elif hole_type == 'thread':
        draw_thread_bso(msp, center, r, role, scale)
    elif hole_type == 'clear':
        draw_clear(msp, center, r, role, scale)
    return role
