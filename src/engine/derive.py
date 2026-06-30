"""Beacon专家 - 变体派生引擎 (Phase 5).

decide_method: 决策派生方式 (scale=坐标缩放秒级 / rerun=重跑投影)
derive_dxf_scale: 直接缩放 DXF 所有坐标, 生成新文件。
"""
from pathlib import Path
from typing import Dict, List, Optional

import ezdxf
from ezdxf.math import Vec3

# 缩放方法适用的最大缩放因子阈值 (超出则需 rerun 保证几何正确性)
SCALE_MAX_FACTOR = 5.0


def decide_method(params: Dict) -> str:
    """决策派生方法.

    规则:
      1. 仅 scale_factor 且在合理范围 → 'scale' (坐标缩放, 秒级)
      2. add_holes / material 等几何变更 → 'rerun' (必须重跑投影)
      3. scale_factor 超大 (>=5x) → 'rerun' (避免几何失真)

    Args:
        params: 已 exclude_none 的参数 dict (scale_factor / add_holes / material / ...)

    Returns:
        'scale' 或 'rerun'
    """
    has_geom_change = any(k in params for k in ("add_holes", "material"))
    if has_geom_change:
        return "rerun"

    sf = params.get("scale_factor")
    if sf is None:
        return "rerun"
    # 仅缩放, 但缩放过大需重跑保证圆弧/标注正确
    if sf <= 0 or sf >= SCALE_MAX_FACTOR:
        return "rerun"
    return "scale"


def derive_dxf_scale(source_dxf_path: str, scale_factor: float) -> str:
    """缩放 DXF 所有坐标, 输出新 DXF.

    对 source 中的所有实体 (LINE/CIRCLE/ARC/LWPOLYLINE/POLYLINE/INSERT/TEXT/...):
      - LINE: 缩放起点终点
      - CIRCLE: 缩放圆心 + 半径
      - ARC: 缩放圆心 + 半径 (角度不变)
      - LWPOLYLINE: 缩放所有顶点
      - POLYLINE (3D): 缩放所有顶点
      - INSERT: 缩放插入点 + xscale/yscale/zscale
      - TEXT/MTEXT: 缩放插入点 + 字高
      - DIMENSION/其它: 跳过 (标注重跑更准确)
      - HATCH: 跳过 (图案密度需重算)

    Args:
        source_dxf_path: 源 DXF 路径
        scale_factor: 缩放因子 (>0)

    Returns:
        新 DXF 路径 (同目录, 文件名加 _scale{n})

    Raises:
        FileNotFoundError: 源文件不存在
        ValueError: scale_factor 非法
    """
    src = Path(source_dxf_path)
    if not src.is_file():
        raise FileNotFoundError(f"源 DXF 不存在: {source_dxf_path}")
    if scale_factor <= 0:
        raise ValueError(f"scale_factor 必须 >0, 收到 {scale_factor}")

    doc = ezdxf.readfile(str(src))
    msp = doc.modelspace()

    def _scale_point(x: float, y: float, z: float = 0.0):
        return x * scale_factor, y * scale_factor, z * scale_factor

    skipped = []
    for entity in list(msp):
        dxftype = entity.dxftype()

        if dxftype == "LINE":
            entity.dxf.start = Vec3(*_scale_point(*entity.dxf.start.xyz))
            entity.dxf.end = Vec3(*_scale_point(*entity.dxf.end.xyz))

        elif dxftype == "CIRCLE":
            cx, cy, cz = _scale_point(*entity.dxf.center.xyz)
            entity.dxf.center = Vec3(cx, cy, cz)
            entity.dxf.radius = entity.dxf.radius * scale_factor

        elif dxftype == "ARC":
            cx, cy, cz = _scale_point(*entity.dxf.center.xyz)
            entity.dxf.center = Vec3(cx, cy, cz)
            entity.dxf.radius = entity.dxf.radius * scale_factor
            # start_angle/end_angle 不变

        elif dxftype == "LWPOLYLINE":
            # lwpolyline.dxf.elevation 是 z 基准高度, 同步缩放
            with entity.points("xyseb") as pts:  # x,y,start_width,end_width,bulge
                for p in pts:
                    p.x = p.x * scale_factor
                    p.y = p.y * scale_factor
            entity.dxf.elevation = entity.dxf.elevation * scale_factor

        elif dxftype == "POLYLINE":
            # 3D POLYLINE: 遍历顶点
            for v in entity.vertices:
                vx, vy, vz = _scale_point(*v.dxf.location.xyz)
                v.dxf.location = Vec3(vx, vy, vz)

        elif dxftype == "POINT":
            px, py, pz = _scale_point(*entity.dxf.location.xyz)
            entity.dxf.location = Vec3(px, py, pz)

        elif dxftype == "INSERT":
            ix, iy, iz = _scale_point(*entity.dxf.insert.xyz)
            entity.dxf.insert = Vec3(ix, iy, iz)
            # 块引用缩放叠加
            entity.dxf.xscale = entity.dxf.get("xscale", 1.0) * scale_factor
            entity.dxf.yscale = entity.dxf.get("yscale", 1.0) * scale_factor
            entity.dxf.zscale = entity.dxf.get("zscale", 1.0) * scale_factor

        elif dxftype in ("TEXT", "MTEXT"):
            ix, iy, iz = _scale_point(*entity.dxf.insert.xyz)
            entity.dxf.insert = Vec3(ix, iy, iz)
            # 字高缩放
            if entity.dxf.hasattr("height"):
                entity.dxf.height = entity.dxf.height * scale_factor
            # MTEXT char_height
            if dxftype == "MTEXT" and entity.dxf.hasattr("char_height"):
                entity.dxf.char_height = entity.dxf.char_height * scale_factor

        else:
            # DIMENSION/HATCH/SPLINE(控制点缩放复杂) 等跳过
            skipped.append(dxftype)

    # 输出路径: 同目录, 加 _scale{sf}
    sf_str = f"{scale_factor:.3f}".rstrip("0").rstrip(".")
    out_name = f"{src.stem}_scale{sf_str}{src.suffix}"
    out_path = src.parent / out_name
    doc.saveas(str(out_path))

    # 记录跳过的实体类型到 meta (调试用, 写入 .meta.json)
    if skipped:
        import json
        meta_path = out_path.with_suffix(".meta.json")
        meta_path.write_text(json.dumps({
            "source": str(src),
            "scale_factor": scale_factor,
            "skipped_types": sorted(set(skipped)),
            "skipped_count": len(skipped),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    return str(out_path)
