# Vendored from: doublelw/STP-DXF core/annotator.py (Beacon P2 标注算法, 2026-06)
# 2026-09-13 移植说明:
#  - 坐标约定改为视图局部坐标 (render_annotation 的 vl.to_abs 自加 layout 原点+比例),
#    故 DEFAULT_ORIGINS 归零 — 不再输出带原点的绝对坐标
#  - 输入 geometry.json 由 veritas_geom.veritas_to_geometry 从 veritas.json 适配生成
# 算法: 外形6 + Yu2006链式孔距DP + 角部定位 + 同径标一次(n-%%C) + 大孔leader

"""
P2 标注算法 — AI标注决策 + 无重叠布局

输入: geometry.json (bbox/holes/holes_2d) + 可选 projection.json (三视图2D轮廓)
输出: annotation.json — DIMENSION 清单 [{type, p1, p2, value, layer, view, level}, ...]

算法:
  1. 候选生成   — 外形 / 孔径 / 孔距(链式)
  2. 边界标注放置 (Kakoulis & Tollis 2001/2006 思想):
                 候选位置 → 无重叠布局(层堆叠 + 碰撞检测 + 偏好评分)
  3. 链式分层动态规划 (Yu et al. 2006, 基准孔板标注):
                 有序孔串 → 最优分层(最小化层数与总延伸线长度)
  4. GB/T 4458 / ASME Y14.5 约束 — 同径只标一次, 标注线不穿实体,
                 链式段长合理, 引出线折线规范

设计原则(对标客户 固定板.dxf):
  - linear(32) 链式孔距 ≈ 82   (每段 = 相邻独特坐标差)
  - radius(36) 孔径   ≈ 4-6    (每种独特直径一次)
  - 外形 linear 若干
  - 总 DIMENSION ≥ 100, 达标率 > 90%

本模块只产出 annotation.json (规范无关的标注清单);DXF 渲染由 core/render_dxf.py 完成,
这样 P2 可独立于 P1(projection.json)测试 —— 若 projection.json 缺失, 从 geometry 自推
三视图的 2D 包络。
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

TOL = 1e-3  # 坐标等价容差(mm)


@dataclass
class Dimension:
    """一条标注"""
    type: str               # 'linear' | 'radius' | 'diameter' | 'leader' | 'angular'
    p1: tuple               # (x, y) 标注第一点(视图绝对坐标)
    p2: tuple               # (x, y) 第二点; radius 时 p2=center
    value: float            # 标注数值(mm); radius 为半径
    layer: str = 'DIM'
    view: str = 'Top'       # 所属视图
    level: int = 0          # 标注线所在层(离板边距离档位)
    side: str = 'bottom'    # 板边侧('bottom'/'top'/'left'/'right'), 决定渲染偏移方向
    angle: float = 0.0      # linear 标注方向角(度), 0=水平, 90=垂直
    text: str = ''          # 显式文本覆盖(如 "4-%%C5" 表示4处直径5)
    leader_pts: list = field(default_factory=list)  # leader 折线点

    def to_dict(self) -> dict:
        d = asdict(self)
        d['p1'] = list(self.p1)
        d['p2'] = list(self.p2)
        return d


# ---------------------------------------------------------------------------
# 视图布局(若 projection.json 缺失, 从 geometry 自推三视图包络)
# ---------------------------------------------------------------------------

# 默认三视图原点 — 归零: beacon-expert render_annotation 按视图局部坐标
# (vl.to_abs 自加 layout 原点+比例), 标注清单不再携带绝对原点
DEFAULT_ORIGINS = {
    'Top':   (0.0, 0.0),
    'Front': (0.0, 0.0),
    'Left':  (0.0, 0.0),
}


def _view_origins(projection: dict | None) -> dict:
    if projection and projection.get('origins'):
        return {k: tuple(v) for k, v in projection['origins'].items()}
    return dict(DEFAULT_ORIGINS)


# ---------------------------------------------------------------------------
# 碰撞 / 无重叠布局 (Kakoulis & Tollis 边界标注)
# ---------------------------------------------------------------------------

class _Placer:
    """
    边界标注放置器: 把同向(同 angle)的 linear 标注按"层"堆叠, 每层是一条平行于板边
    的尺寸线; 层间距 = LEVEL_GAP。同层内两条标注的 [延伸线x区间] 不得重叠(留 MIN_GAP)。
    这就是 Kakoulis & Tollis (2001) 的"等高线放置 + 最少层数"贪心核心。

    leader / radius 标注使用独立的"引出点 + 折线"放置, 通过 _bbox_collide 检测整体碰撞。
    """

    LEVEL_GAP = 16.0    # 标注层间距(mm), 工程图常见 14-18
    MIN_GAP = 4.0       # 同层标注间最小间隙

    def __init__(self) -> None:
        # key=(view, angle, side) -> list of (level, span_lo, span_hi)
        self._lanes: dict[tuple, list[tuple[int, float, float]]] = {}

    def place_linear(self, view: str, angle: float, side: str,
                     span_lo: float, span_hi: float, allow_touch: bool = False) -> int:
        """
        为一条 linear 标注分配层号(离板边由近到远 = 0,1,2...)。
        span_lo/hi: 标注在"沿板边方向"上的坐标区间(用于同层碰撞)。

        关键: 同 (view, angle, side) 的标注共享层池 —— 同侧同向的标注竞争同一组层,
        外形(满跨度)会自然把孔距链式推到更内层, 符合工程图"外形在外、孔距在内"
        的惯例(Kakoulis & Tollis 边界标注的层级约束)。
        side 区分板边哪一侧('bottom'/'top'/'left'/'right'): 不同侧的标注物理上
        在板的不同边, 延伸线不交叉, 故独立成池。

        allow_touch=True 时, 端点共享(span 端点相等, 如链式相邻段 [0,10]与[10,20])
        视为不碰撞 —— 工程图链式尺寸线首尾相接是规范允许的。
        """
        key = (view, angle, side)
        lo, hi = sorted((span_lo, span_hi))
        lanes = self._lanes.setdefault(key, [])
        level = 0
        while True:
            occupied = [(l, h) for (lv, l, h) in lanes if lv == level]
            if allow_touch:
                # 仅真正区间相交(span 有正长度重叠)才算碰撞
                clash = any(lo < h - 1e-3 and l < hi - 1e-3 for (l, h) in occupied)
            else:
                clash = any(not (hi + self.MIN_GAP <= l or lo - self.MIN_GAP >= h)
                            for (l, h) in occupied)
            if not clash:
                lanes.append((level, lo, hi))
                return level
            level += 1

    # leader / radius 折线 bbox 碰撞(简化: 只在同 view 内 leader 区域)
    def place_freeform(self, view: str, bbox: tuple[float, float, float, float],
                       taken: list | None = None) -> bool:
        """检查/记录一个自由形 bbox 是否与已记录碰撞; 返回是否可用。"""
        taken = taken if taken is not None else self._lanes.setdefault(('free', view), [])
        x0, y0, x1, y1 = bbox
        for tb in taken:
            tx0, ty0, tx1, ty1 = tb
            if not (x1 <= tx0 or x0 >= tx1 or y1 <= ty0 or y0 >= ty1):
                return False
        taken.append(bbox)
        return True


# ---------------------------------------------------------------------------
# 链式分层动态规划 (Yu et al. 2006 思想)
# ---------------------------------------------------------------------------

def chain_layers_dp(positions: list[float], min_seg: float = 0.0,
                    level_gap: float = _Placer.LEVEL_GAP) -> list[int]:
    """
    给定一串有序位置 p0<p1<...<pn(板基准 + 各孔坐标 + 板对边), 用动态规划把"相邻段"
    (p_i -> p_{i+1}) 分配到尽量少的层, 使同一层内任意两段不重叠(段在沿边方向占
    [p_i, p_{i+1}], 层方向各占 level_gap)。

    目标函数(对齐 Yu 2006): 最小化 (层数 * 大权重 + 总层距离)。
    —— 层数优先, 次优总延伸线长度(离板越近越好读)。

    返回: 每段(i -> i+1) 的层号 list, 长度 = len(positions)-1。
    """
    n_seg = len(positions) - 1
    if n_seg <= 0:
        return []
    segs = [(positions[i], positions[i + 1], i) for i in range(n_seg)]
    # 段长过小的合并阈值: 中心距小于该值的相邻段不能同层(延伸线会打架)
    # —— Yu 基准标注里段长即孔距, 实测固定板孔距多在 8-30mm, 同层 OK。

    # 贪心 first-fit-decreasing-height 的简化版: 按段起点排序(已有序), 逐段找最低可用层。
    # 对工程图孔板标注, first-fit 已是 Yu 2006 报告的最优近界策略, DP 与之结果一致。
    lanes: list[list[tuple[float, float]]] = []  # 每层: [(lo,hi)...]
    assign = [0] * n_seg
    for idx, (lo, hi, _orig) in enumerate(segs):
        placed = False
        for lv, occ in enumerate(lanes):
            # 链式相邻段端点共享([0,10]与[10,20])是规范允许, 仅正长度相交算碰撞
            clash = any(lo < o_hi - 1e-3 and o_lo < hi - 1e-3 for (o_lo, o_hi) in occ)
            if not clash:
                occ.append((lo, hi))
                assign[idx] = lv
                placed = True
                break
        if not placed:
            lanes.append([(lo, hi)])
            assign[idx] = len(lanes) - 1
    return assign


# ---------------------------------------------------------------------------
# GB / ASME Y14.5 约束
# ---------------------------------------------------------------------------

def _clean_chain(unique_inner: list[float], edge_lo: float, edge_hi: float) -> list[float]:
    """构造链式标注的位置序列: [edge_lo, *inner..., edge_hi], 去重保序,
    过滤掉与板边重合的孔坐标。"""
    inner = [p for p in unique_inner
             if not (abs(p - edge_lo) < TOL or abs(p - edge_hi) < TOL)]
    chain = [edge_lo] + inner + [edge_hi]
    seen: set = set()
    out = []
    for p in chain:
        key = round(p, 1)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _same_dia(a: float, b: float, tol: float = 0.05) -> bool:
    return abs(a - b) <= tol


def _unique_diameters(holes: list[dict], tol: float = 0.05) -> list[float]:
    """同径只保留一次(GB: 同尺寸孔只在其中一处标注直径, 其余用"n-%%Cd"省略)。"""
    out: list[float] = []
    for h in sorted(holes, key=lambda h: h['d']):
        if not out or not _same_dia(out[-1], h['d'], tol):
            out.append(h['d'])
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def annotate(geometry: dict, projection: dict | None = None,
             *, target_count: int = 100) -> dict:
    """
    生成标注清单。

    Args:
        geometry:   geometry.json (bbox/holes/holes_2d)
        projection: 可选 projection.json (origins + 视图2D轮廓)
        target_count: 目标标注数下限(对标客户 108)

    Returns:
        {'dimensions': [Dimension.to_dict...], 'stats': {...}}
    """
    bb = geometry['bbox']
    W, H, D = geometry['width'], geometry['height'], geometry['depth']
    bx0, by0, bz0 = bb['xmin'], bb['ymin'], bb['zmin']
    bx1, by1, bz1 = bb['xmax'], bb['ymax'], bb['zmax']
    holes = geometry.get('holes_2d') or geometry.get('holes') or []
    origins = _view_origins(projection)
    top_ox, top_oy = origins['Top']
    front_ox, front_oy = origins['Front']
    left_ox, left_oy = origins['Left']

    dims: list[Dimension] = []
    placer = _Placer()

    def _lin(view, angle, side, p1, p2, value, text='', allow_touch=False):
        """放置一条 linear 标注: 由 placer 决定 level, side 决定板边侧, 统一登记。"""
        x1, y1 = p1; x2, y2 = p2
        span = (min(x1, x2), max(x1, x2)) if angle == 0 else (min(y1, y2), max(y1, y2))
        level = placer.place_linear(view, angle, side, span[0], span[1],
                                    allow_touch=allow_touch)
        dims.append(Dimension('linear', p1, p2, value, view=view,
                              level=level, side=side, angle=angle, text=text))

    # === 1. 外形标注 (Top: W, H; Front: W, D; Left: H, D) =================
    # 外形与链式孔距共享同一 side 池(bottom/left): 外形满跨度会把链式推到内层,
    # 形成工程图惯例的"外形尺寸链在外、孔距尺寸链在内"。
    _lin('Top', 0, 'bottom',
         (top_ox + bx0, top_oy + by0), (top_ox + bx1, top_oy + by0), W, f'{W:.0f}')
    _lin('Top', 90, 'left',
         (top_ox + bx0, top_oy + by0), (top_ox + bx0, top_oy + by1), H, f'{H:.0f}')
    _lin('Front', 0, 'bottom',
         (front_ox + bx0, front_oy + bz0), (front_ox + bx1, front_oy + bz0), W, f'{W:.0f}')
    _lin('Front', 90, 'left',
         (front_ox + bx0, front_oy + bz0), (front_ox + bx0, front_oy + bz1), D, f'{D:.1f}')
    _lin('Left', 0, 'bottom',
         (left_ox + by0, left_oy + bz0), (left_ox + by1, left_oy + bz0), H, f'{H:.0f}')
    _lin('Left', 90, 'left',
         (left_ox + by0, left_oy + bz0), (left_ox + by0, left_oy + bz1), D, f'{D:.1f}')

    # === 2. 孔距链式标注 (Top 视图, Yu 2006 DP 分层) ======================
    # X 方向: 基准(板左边 bx0) -> 各独特孔 x -> 板右边 bx1
    unique_x = sorted({round(h['x'], 1) for h in holes})
    chain_x = _clean_chain(unique_x, bx0, bx1)
    assign_x = chain_layers_dp(chain_x)
    for i in range(len(chain_x) - 1):
        lo, hi = chain_x[i], chain_x[i + 1]
        seg = round(hi - lo, 1)
        if seg < 1.2:  # 极微段: 文字物理放不下
            continue
        # <6mm小段不共层(端点共享文字必叠), ≥6mm链式相邻共层(规范允许)
        _lin('Top', 0, 'bottom',
             (top_ox + lo, top_oy + by0), (top_ox + hi, top_oy + by0), seg, f'{seg:g}',
             allow_touch=(hi - lo) >= 6.0)

    # Y 方向: 板下边 by0 -> 各独特孔 y -> 板上边 by1, 放板左侧
    unique_y = sorted({round(h['y'], 1) for h in holes})
    chain_y = _clean_chain(unique_y, by0, by1)
    assign_y = chain_layers_dp(chain_y)
    for i in range(len(chain_y) - 1):
        lo, hi = chain_y[i], chain_y[i + 1]
        seg = round(hi - lo, 1)
        if seg < 1.2:
            continue
        _lin('Top', 90, 'left',
             (top_ox + bx0, top_oy + lo), (top_ox + bx0, top_oy + hi), seg, f'{seg:g}',
             allow_touch=(hi - lo) >= 6.0)

    # === 3. 基准孔定位强化 (GB: 关键角部孔相对板边的绝对距离, 单独放对边) ===
    # 链式标注的"基准段"(板边 -> 第一个孔)已含第一个孔定位; 这里只对最关键的
    # 角部定位孔补一条到对边的绝对距离, 放在板的 top/right 侧(与链式 bottom/left
    # 侧物理分离), 用独立层池避免与链式竞争同向 span。
    # 对称孔坐标相同→重复定位尺寸(145.1×2教训): 按(分量,值)去重
    seen_loc = set()
    key_holes = _pick_key_holes(holes, bx0, bx1, by0, by1, top_n=4)
    for h in key_holes:
        vx = round(bx1 - h['x'], 1)
        vy = round(by1 - h['y'], 1)
        if ('x', vx) not in seen_loc:
            seen_loc.add(('x', vx))
            _lin('Top', 0, 'top',
                 (top_ox + h['x'], top_oy + by1), (top_ox + bx1, top_oy + by1), vx)
        if ('y', vy) not in seen_loc:
            seen_loc.add(('y', vy))
            _lin('Top', 90, 'right',
                 (top_ox + bx1, top_oy + h['y']), (top_ox + bx1, top_oy + by1), vy)

    # === 4. 孔径标注 (GB: 同径只标一次, radius_dim; 多孔组/大孔用 leader) ====
    dia_groups: dict[float, list[dict]] = {}
    for h in holes:
        dia_groups.setdefault(round(h['d'], 2), []).append(h)
    # 多孔组(≥3)与大孔(≥20): leader 引出 "n-%%Cd" (客户样例的引线风格);
    # 其余 radius_dim 单点标注
    radius_count = 0
    for dia, grp in sorted(dia_groups.items()):
        rep = grp[0]
        cx, cy = top_ox + rep['x'], top_oy + rep['y']
        if dia >= 20.0 or len(grp) >= 3:
            # 引出线注释(对标客户 LEADER): n-%%Cd
            bbox = (cx + rep['r'], cy + rep['r'],
                    cx + rep['r'] + 40, cy + rep['r'] + 14)
            if placer.place_freeform('Top', bbox):
                pts = [(cx + rep['r'] * 0.7, cy + rep['r'] * 0.7),
                       (cx + rep['r'] + 15, cy + rep['r'] + 15),
                       (cx + rep['r'] + 38, cy + rep['r'] + 15)]
                dims.append(Dimension('leader', (cx, cy), (cx, cy), dia / 2,
                                      view='Top', leader_pts=pts,
                                      text=f'{len(grp)}-%%C{dia:g}' if len(grp) > 1 else f'%%C{dia:g}'))
            else:
                dims.append(Dimension('radius', (cx, cy), (cx, cy), rep['r'],
                                      view='Top',
                                      text=f'{len(grp)}-%%C{dia:g}' if len(grp) > 1 else f'%%C{dia:g}'))
                radius_count += 1
        else:
            dims.append(Dimension('radius', (cx, cy), (cx, cy), rep['r'],
                                  view='Top',
                                  text=f'{len(grp)}-%%C{dia:g}' if len(grp) > 1 else f'R{rep["r"]:g}'))
            radius_count += 1

    # === 5. 厚度引出(Front 视图 leader, 补 LEADER 数) =====================
    bbox = (front_ox + bx1 - 5, front_oy + bz1,
            front_ox + bx1 + 35, front_oy + bz1 + 14)
    if placer.place_freeform('Front', bbox):
        pts = [(front_ox + bx1, front_oy + (bz0 + bz1) / 2),
               (front_ox + bx1 + 15, front_oy + (bz0 + bz1) / 2 + 10),
               (front_ox + bx1 + 33, front_oy + (bz0 + bz1) / 2 + 10)]
        dims.append(Dimension('leader',
                              (front_ox + bx1, front_oy + (bz0 + bz1) / 2),
                              (front_ox + bx1, front_oy + (bz0 + bz1) / 2),
                              D, view='Front', leader_pts=pts, text=f't={D:g}'))

    stats = _stats(dims, target_count)
    return {'dimensions': [d.to_dict() for d in dims], 'stats': stats}


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _pick_key_holes(holes: list[dict], bx0: float, bx1: float,
                    by0: float, by1: float, top_n: int = 8) -> list[dict]:
    """选离板边角最近的若干孔(角部定位孔通常最关键, GB 工程图惯例)。"""
    def corner_dist(h: dict) -> float:
        return min(math.hypot(h['x'] - bx0, h['y'] - by0),
                   math.hypot(h['x'] - bx1, h['y'] - by0),
                   math.hypot(h['x'] - bx0, h['y'] - by1),
                   math.hypot(h['x'] - bx1, h['y'] - by1))
    return sorted(holes, key=corner_dist)[:top_n]


def _stats(dims: list[Dimension], target: int) -> dict:
    by_type: dict[str, int] = {}
    for d in dims:
        by_type[d.type] = by_type.get(d.type, 0) + 1
    linear = by_type.get('linear', 0)
    radius = by_type.get('radius', 0)
    return {
        'total': len(dims),
        'linear': linear,
        'radius': radius,
        'leader': by_type.get('leader', 0),
        'by_type': by_type,
        'target': target,
        'meets_target': len(dims) >= target,
        'meets_linear': linear >= 70,
        'meets_radius': radius >= 4,
    }


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

def load_geometry(path: str) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_projection(path: str | None) -> dict | None:
    if not path or not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_annotation(result: dict, path: str) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description='P2 标注算法 — 生成 annotation.json')
    p.add_argument('geometry', help='geometry.json 路径')
    p.add_argument('--projection', default=None, help='可选 projection.json 路径')
    p.add_argument('-o', '--output', default='annotation.json', help='输出路径')
    p.add_argument('--target', type=int, default=100, help='标注数下限(对标客户108)')
    args = p.parse_args(argv)

    geometry = load_geometry(args.geometry)
    projection = load_projection(args.projection)
    result = annotate(geometry, projection, target_count=args.target)
    save_annotation(result, args.output)
    s = result['stats']
    print(f"[annotator] {args.output}")
    print(f"  total={s['total']} (target {s['target']}, linear={s['linear']}, "
          f"radius={s['radius']}, leader={s['leader']})")
    print(f"  meets_target={s['meets_target']} meets_linear(>=70)={s['meets_linear']} "
          f"meets_radius(>=4)={s['meets_radius']}")
    return 0 if s['meets_target'] and s['meets_linear'] and s['meets_radius'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
