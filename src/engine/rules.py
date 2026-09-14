"""图纸宪法确定性规则引擎 (docs/图纸质量标准_确定性.md §2).

每条规则 = 断言函数, PASS/FAIL 二值 + 违规实体清单(可定位).
禁止概率门槛/比例评分 — 图纸对/错是确定的.

用法:
    python -m src.engine.rules output.dxf --veritas v.json --annotation a.json
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ezdxf
from ezdxf import bbox
from ezdxf.math import Vec2

TOL = 0.01          # 值精准容差 (宪法铁律4)
COVER_TOL = 0.05    # 孔坐标覆盖容差 (图纸显示精度0.1, 取其半)


def _chk(rid, ok, detail, violations=None):
    return {'rule': rid, 'ok': bool(ok), 'detail': detail,
            'violations': (violations or [])[:10],
            'violation_count': len(violations or [])}


# ---------------------------------------------------------------------------
# 数据准备
# ---------------------------------------------------------------------------

def _dim_text_bbox(dim, doc) -> tuple | None:
    """DIMENSION 关联文本的近似 bbox (text_midpoint + 按字符数估宽)."""
    mp = dim.dxf.get('text_midpoint')
    if mp is None:
        return None
    h = 3.5
    try:
        ds = doc.dimstyles.get(dim.dxf.get('dimstyle', 'Standard'))
        h = float(ds.dxf.get('dimtxt', 3.5))
    except Exception:
        pass
    t = dim.dxf.get('text', '<>')
    if t == '<>' or not t:
        try:
            m = dim.get_measurement()
            t = f'{m:.0f}' if abs(m - round(m)) < TOL else f'{m:.1f}'
        except Exception:
            t = '0'
    t = re.sub(r'[<>{}\\C\d;%%-]', '', t) or '0'
    w = max(len(t) * h * 0.62, h)
    return (mp.x - w / 2, mp.y - h / 2, mp.x + w / 2, mp.y + h / 2)


def _seg_from_dim(dim):
    """线性尺寸的尺寸线段 (base 上 p1'→p2')."""
    p1 = dim.dxf.get('defpoint2')
    p2 = dim.dxf.get('defpoint3')
    base = dim.dxf.get('defpoint')
    if p1 is None or p2 is None or base is None:
        return None
    ang = float(dim.dxf.get('angle', 0) or 0)
    rad = math.radians(ang)
    ux, uy = math.cos(rad), math.sin(rad)
    # 尺寸线过 base, 方向沿角度; p1/p2 投影到该线
    def _proj(p):
        v = (p.x - base.x, p.y - base.y)
        t = v[0] * ux + v[1] * uy
        return (base.x + t * ux, base.y + t * uy)
    a, b = _proj(p1), _proj(p2)
    return (a[0], a[1], b[0], b[1])


def _seg_intersects_seg(a, b) -> bool:
    def _o(p, q, r):
        v = (q[0]-p[0])*(r[1]-p[1]) - (q[1]-p[1])*(r[0]-p[0])
        return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)
    d1 = _o((a[0],a[1]), (a[2],a[3]), (b[0],b[1]))
    d2 = _o((a[0],a[1]), (a[2],a[3]), (b[2],b[3]))
    d3 = _o((b[0],b[1]), (b[2],b[3]), (a[0],a[1]))
    d4 = _o((b[0],b[1]), (b[2],b[3]), (a[2],a[3]))
    return d1 != d2 and d3 != d4


def _seg_intersects_circle(a, cx, cy, r) -> bool:
    """线段与圆(含弧用bbox预筛后按整圆判, 保守) 相交."""
    dx, dy = a[2]-a[0], a[3]-a[1]
    fx, fy = a[0]-cx, a[1]-cy
    A = dx*dx + dy*dy
    if A < 1e-12:
        return False
    B = 2*(fx*dx + fy*dy)
    C = fx*fx + fy*fy - r*r
    disc = B*B - 4*A*C
    if disc < 0:
        return False
    disc = math.sqrt(disc)
    for t in ((-B-disc)/(2*A), (-B+disc)/(2*A)):
        if 0 <= t <= 1:
            return True
    return False


# ---------------------------------------------------------------------------
# G 组 — 几何 (G4/G5 复用 dxf_checks)
# ---------------------------------------------------------------------------

def check_g45(dxf_path):
    from src.engine.dxf_checks import validate_dxf_file
    findings = validate_dxf_file(dxf_path)
    errs = [f.render() for f in findings if f.severity == 'error']
    dup = [e for e in errs if 'duplicate' in e]
    degen = [e for e in errs if 'zero-length' in e or 'degenerate' in e.lower()]
    return [
        _chk('G4', not dup, f'重复实体={len(dup)}', dup),
        _chk('G5', not degen, f'退化实体={len(degen)}', degen),
    ]


# ---------------------------------------------------------------------------
# D 组 — 标注 (基于 annotation.json 3D真值侧 + DXF 数量核对)
# ---------------------------------------------------------------------------

def check_d1(ann, veritas):
    """D1 逐孔坐标重建: 每孔 x,y 均可由尺寸链端点+基准精确达到."""
    g = _holes(veritas)
    if not g:
        return _chk('D1', False, 'veritas 无孔')
    dims = ann['dimensions']
    ux = {round(h['x'], 1) for h in g}
    uy = {round(h['y'], 1) for h in g}
    xs, ys = set(), set()
    for d in dims:
        if d.get('type') != 'linear' or d.get('view') != 'Top':
            continue
        (x1, y1), (x2, y2) = d['p1'], d['p2']
        if abs(y1 - y2) <= abs(x1 - x2):      # 水平: 端点携带 x 坐标
            xs.update((round(x1, 1), round(x2, 1)))
        else:                                  # 垂直: 端点携带 y 坐标
            ys.update((round(y1, 1), round(y2, 1)))
    miss_x = sorted(x for x in ux if not any(abs(x - e) <= COVER_TOL for e in xs))
    miss_y = sorted(y for y in uy if not any(abs(y - e) <= COVER_TOL for e in ys))
    ok = not miss_x and not miss_y
    v = [f'x={x}' for x in miss_x] + [f'y={y}' for y in miss_y]
    return _chk('D1', ok,
                f'孔坐标覆盖: x {len(ux)-len(miss_x)}/{len(ux)}, y {len(uy)-len(miss_y)}/{len(uy)}', v)


def check_d2(ann, veritas):
    """D2 孔径全覆盖 + 计数前缀 n=实数."""
    g = _holes(veritas)
    true_dia = Counter(round(h['d'], 2) for h in g)
    marked = {}
    for d in ann['dimensions']:
        t = d.get('text', '')
        m = re.match(r'^(\d+)-%%C([\d.]+)$', t) or re.match(r'^%%C([\d.]+)$', t)
        if m:
            n = int(m.group(1)) if m.lastindex == 2 else 1
            dia = round(float(m.group(2)), 2)
            marked[dia] = marked.get(dia, 0) + n
        elif t.startswith('R'):
            marked.setdefault(round(2 * float(t[1:]), 2), 0)
    miss = sorted(set(true_dia) - set(marked))
    bad_n = [f'Ø{k}: 标{n} 实{true_dia[k]}'
             for k, n in marked.items() if k in true_dia and n != true_dia[k]]
    ok = not miss and not bad_n
    v = [f'缺孔径Ø{k}' for k in miss] + bad_n
    return _chk('D2', ok,
                f'孔径 {len(set(true_dia)&set(marked))}/{len(true_dia)} 种已标', v)


def check_d4(ann):
    """D4 尺寸值精准: 每条线性 value = 端点几何距离 (≤0.01)."""
    bad = []
    for i, d in enumerate(ann['dimensions']):
        if d.get('type') != 'linear':
            continue
        (x1, y1), (x2, y2) = d['p1'], d['p2']
        geo = math.hypot(x2 - x1, y2 - y1)
        if abs(geo - float(d.get('value', 0))) > TOL:
            bad.append(f'#{i} value={d.get("value")} geo={geo:.3f}')
    return _chk('D4', not bad, f'线性尺寸值核验 {len(ann["dimensions"])}条', bad)


def check_d5(ann):
    """D5 无重复标注: 同两点同向同类型重复."""
    seen = {}
    dup = []
    for i, d in enumerate(ann['dimensions']):
        if d.get('type') != 'linear':
            continue
        key = (tuple(sorted([tuple(d['p1']), tuple(d['p2'])])), d.get('angle'))
        if key in seen:
            dup.append(f'#{i} 与 #{seen[key]} 重复')
        else:
            seen[key] = i
    return _chk('D5', not dup, f'线性标注 {len(seen)} 条唯一', dup)


def _holes(veritas):
    out = []
    for f in veritas.get('features', []):
        if f.get('type') != 'PIERCING':
            continue
        radii = f.get('all_radii') or [f.get('radius', 0)]
        r = min(radii) if radii else 0
        if r > 0:
            out.append({'x': f['position'][0], 'y': f['position'][1], 'r': r, 'd': 2 * r})
    return out


# ---------------------------------------------------------------------------
# A 组 — 美观 (基于 DXF 实体)
# ---------------------------------------------------------------------------

def check_a1(doc):
    """A1 尺寸文字零重叠."""
    boxes = []
    for dim in doc.modelspace().query('DIMENSION'):
        bb = _dim_text_bbox(dim, doc)
        if bb:
            boxes.append(bb)
    ov = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3]):
                ov.append(f'{boxes.index(a)}×{boxes.index(b)}')
    return _chk('A1', not ov, f'尺寸文本 {len(boxes)} 个, 重叠对={len(ov)}', ov)


def check_a2(doc):
    """A2 尺寸线不穿视图几何 (OUTLINE/HOLE 层 LINE精确 + 弧按真实张角判).

    巨半径弧(R>500, 近直线伪拟合)按弦段判 — 整圆判会把全图罩进圆内.
    """
    msp = doc.modelspace()
    geo_segs, geo_circles = [], []
    for e in msp:
        lyr = e.dxf.get('layer', '')
        if lyr not in ('OUTLINE', 'HOLE'):
            continue
        t = e.dxftype()
        if t == 'LINE':
            s, en = e.dxf.start, e.dxf.end
            geo_segs.append((s.x, s.y, en.x, en.y))
        elif t == 'CIRCLE':
            geo_circles.append((e.dxf.center.x, e.dxf.center.y, e.dxf.radius))
        elif t == 'ARC':
            r = e.dxf.radius
            if r > 500 or not math.isfinite(r):
                # 近直线: 按弦段判
                a0 = math.radians(e.dxf.start_angle)
                a1 = math.radians(e.dxf.end_angle)
                cx, cy = e.dxf.center.x, e.dxf.center.y
                geo_segs.append((cx + r * math.cos(a0), cy + r * math.sin(a0),
                                 cx + r * math.cos(a1), cy + r * math.sin(a1)))
            else:
                # 真实张角的弧: 采样成短段精确判
                a0 = math.radians(e.dxf.start_angle)
                a1 = math.radians(e.dxf.end_angle)
                if a1 <= a0:
                    a1 += 2 * math.pi
                cx, cy = e.dxf.center.x, e.dxf.center.y
                n = max(3, int((a1 - a0) / math.radians(15)))
                px = [cx + r * math.cos(a0 + (a1 - a0) * i / n) for i in range(n + 1)]
                py = [cy + r * math.sin(a0 + (a1 - a0) * i / n) for i in range(n + 1)]
                geo_segs.extend((px[i], py[i], px[i + 1], py[i + 1])
                                for i in range(n))
    bad = []
    for dim in msp.query('DIMENSION'):
        seg = _seg_from_dim(dim)
        if seg is None:
            continue
        h = dim.dxf.handle
        for gs in geo_segs:
            if _seg_intersects_seg(seg, gs):
                bad.append(f'DIM#{h}×LINE')
                break
        else:
            for gc in geo_circles:
                if _seg_intersects_circle(seg, *gc):
                    bad.append(f'DIM#{h}×CIRCLE')
                    break
    return _chk('A2', not bad, f'尺寸线穿图 {len(bad)} 处', bad)


# ---------------------------------------------------------------------------
# F 组 — 要素
# ---------------------------------------------------------------------------

def check_f2(doc):
    """F2 标题栏材料与技术要求材料一致 (SPCC默认混入Q235=FAIL)."""
    texts = [t.dxf.text for t in doc.modelspace().query('TEXT')]
    mtexts = [t.text for t in doc.modelspace().query('MTEXT')]
    title_mat = None
    for t in texts:
        m = re.search(r'材料\s*[:：]?\s*(\S+)', t)
        if m:
            title_mat = m.group(1)
    tech_mat = None
    for t in texts + mtexts:
        m = re.search(r'材料[:：]\s*([^\s,，。;；]+)', t)
        if m:
            tech_mat = m.group(1)
            break
    ok = title_mat is not None and tech_mat is not None and title_mat == tech_mat
    return _chk('F2', ok, f'标题栏材料={title_mat} 技术要求材料={tech_mat}',
                [] if ok else [f'{title_mat}≠{tech_mat}'])


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run_all(dxf_path, veritas_path=None, annotation_path=None) -> dict:
    doc = ezdxf.readfile(dxf_path)
    veritas = json.load(open(veritas_path)) if veritas_path and Path(veritas_path).exists() else {}
    ann = json.load(open(annotation_path)) if annotation_path and Path(annotation_path).exists() else None

    results = []
    results += check_g45(dxf_path)
    if ann is not None:
        results.append(check_d1(ann, veritas) if veritas else _chk('D1', False, '缺 veritas'))
        results.append(check_d2(ann, veritas) if veritas else _chk('D2', False, '缺 veritas'))
        results.append(check_d4(ann))
        results.append(check_d5(ann))
    else:
        results += [_chk('D1', False, '缺 annotation'), _chk('D2', False, '缺 annotation'),
                    _chk('D4', False, '缺 annotation'), _chk('D5', False, '缺 annotation')]
    results.append(check_a1(doc))
    results.append(check_a2(doc))
    results.append(check_f2(doc))

    passed = sum(1 for r in results if r['ok'])
    return {'file': dxf_path, 'pass': passed == len(results),
            'passed': passed, 'total': len(results), 'results': results}


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description='图纸宪法确定性规则引擎')
    p.add_argument('dxf')
    p.add_argument('--veritas', default=None)
    p.add_argument('--annotation', default=None)
    p.add_argument('--json', default=None)
    args = p.parse_args(argv)

    rep = run_all(args.dxf, args.veritas, args.annotation)
    for r in rep['results']:
        mark = 'PASS' if r['ok'] else 'FAIL'
        print(f"  [{mark}] {r['rule']}: {r['detail']}")
        for v in r['violations'][:5]:
            print(f"         - {v}")
    print(f"宪法验收: {rep['passed']}/{rep['total']} PASS" + ('' if rep['pass'] else ' → FAIL'))
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
    return 0 if rep['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
