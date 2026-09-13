"""veritas.json → geometry.json 适配 (P2 标注引擎的输入桥).

veritas 特征 (PIERCING/BEND/STAMP...) → SaaS annotator 需要的
  {width, height, depth, bbox{xmin..zmax}, holes_2d: [{x, y, d, r}]}

坐标: 零件系原样传递 (render 的 vl.to_abs 负责布局), 孔位取 (x, y) 投影
(Top 视图), 孔径 = 2×min(all_radii) (贯通孔径, 同径标一次的数据基础).
"""
from __future__ import annotations

from typing import Optional


def veritas_to_geometry(veritas: dict) -> Optional[dict]:
    """从 veritas.json dict 构造 annotator.annotate(geometry) 输入.

    无 bbox 或无 PIERCING 特征时返回 None (调用方降级).
    """
    bb = veritas.get('bbox')
    if not bb:
        return None
    holes = []
    for f in veritas.get('features', []):
        if f.get('type') != 'PIERCING':
            continue
        pos = f.get('position')
        if not pos or len(pos) < 2:
            continue
        radii = f.get('all_radii') or [f.get('radius', 0)]
        r = min(radii) if radii else 0
        if r <= 0:
            continue
        holes.append({'x': float(pos[0]), 'y': float(pos[1]),
                      'r': float(r), 'd': 2.0 * float(r)})

    xmin, xmax = bb['xmin'], bb['xmax']
    ymin, ymax = bb['ymin'], bb['ymax']
    zmin, zmax = bb['zmin'], bb['zmax']
    return {
        'width': xmax - xmin,
        'height': ymax - ymin,
        'depth': zmax - zmin,
        'bbox': {'xmin': xmin, 'ymin': ymin, 'zmin': zmin,
                 'xmax': xmax, 'ymax': ymax, 'zmax': zmax},
        'holes': holes,
        'holes_2d': holes,
    }
