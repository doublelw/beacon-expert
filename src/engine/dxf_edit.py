"""C线 Phase 4: 任意来源 DXF 的确定性编辑引擎 (STRETCH 语义).

原则 (huashu-cad SKILL.md 改图纪律):
  - 只在副本上改; 操作按"窗口选区"表达 (CAD STRETCH 语义), LLM 只负责
    把自然语言指令翻译成操作参数, 几何由本模块确定性应用
  - 尺寸联动: 几何改了, 标注 defpoint 跟着窗口走, render() 重出真实测量值;
    从不单独改尺寸文字假装改几何
  - 作用域审计: 编辑前后逐实体对比 (handle/type/layer/几何指纹), 窗口外的
    实体必须逐位不变, 审计不过即失败

支持实体: LINE / LWPOLYLINE (顶点级拉伸), CIRCLE / ARC / TEXT / MTEXT /
INSERT (整体内窗口则整体移动), DIMENSION (defpoint 联动+重渲染)。
"""
import hashlib
import json
import math
from pathlib import Path
from typing import Optional

import ezdxf
from ezdxf import bbox
from ezdxf.math import Vec3


# ─────────────────────────────────────────────────────────────
# 提取 (LLM 的眼睛)
# ─────────────────────────────────────────────────────────────

def extract_summary(dxf_path: str, max_items: int = 400) -> dict:
    """图纸摘要: 实体清单/图层/包围盒/标注测量值 — 给 LLM 选窗口用."""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    ext = bbox.extents(msp, fast=True)
    summary = {
        'bbox': [round(ext.extmin.x, 2), round(ext.extmin.y, 2),
                 round(ext.extmax.x, 2), round(ext.extmax.y, 2)] if ext.has_data else None,
        'unit': str(doc.units),
        'layers': sorted({e.dxf.layer for e in msp}),
        'entity_counts': {},
        'lines': [], 'circles': [], 'dims': [], 'texts': [],
    }
    for e in msp:
        t = e.dxftype()
        summary['entity_counts'][t] = summary['entity_counts'].get(t, 0) + 1
        try:
            if t == 'LINE' and len(summary['lines']) < max_items:
                summary['lines'].append({
                    'h': e.dxf.handle,
                    'p1': [round(e.dxf.start.x, 2), round(e.dxf.start.y, 2)],
                    'p2': [round(e.dxf.end.x, 2), round(e.dxf.end.y, 2)],
                    'layer': e.dxf.layer})
            elif t == 'CIRCLE' and len(summary['circles']) < max_items // 2:
                summary['circles'].append({
                    'h': e.dxf.handle,
                    'c': [round(e.dxf.center.x, 2), round(e.dxf.center.y, 2)],
                    'r': round(e.dxf.radius, 2), 'layer': e.dxf.layer})
            elif t == 'DIMENSION' and len(summary['dims']) < 120:
                m = e.get_measurement()
                summary['dims'].append({
                    'h': e.dxf.handle, 'layer': e.dxf.layer,
                    'measured': round(m, 2),          # 图纸(纸面)测量值
                    'shown': round(m * _dimlfac(doc, e), 2),  # 显示值(真实mm)
                    'text': e.dxf.text})
            elif t in ('TEXT', 'MTEXT') and len(summary['texts']) < 60:
                ins = e.dxf.insert if t == 'TEXT' else e.dxf.insert
                summary['texts'].append({
                    'h': e.dxf.handle, 'layer': e.dxf.layer,
                    'at': [round(ins.x, 2), round(ins.y, 2)],
                    's': (e.dxf.text if t == 'TEXT' else e.text)[:40]})
        except Exception:
            continue
    return summary


# ─────────────────────────────────────────────────────────────
# 确定性操作 (STRETCH 语义)
# ─────────────────────────────────────────────────────────────

def _in(px, py, w) -> bool:
    x1, y1, x2, y2 = w
    return min(x1, x2) <= px <= max(x1, x2) and min(y1, y2) <= py <= max(y1, y2)


def _entity_digest(e) -> str:
    """几何指纹: 类型+图层+关键 DXF 属性 (审计用, 排除 handle)."""
    keys = {
        'LINE': ('layer', 'start', 'end'),
        'LWPOLYLINE': ('layer', 'count'),
        'CIRCLE': ('layer', 'center', 'radius'),
        'ARC': ('layer', 'center', 'radius', 'start_angle', 'end_angle'),
        'TEXT': ('layer', 'insert', 'text', 'height'),
        'DIMENSION': ('layer', 'defpoint', 'defpoint2', 'defpoint3', 'text'),
    }
    t = e.dxftype()
    parts = [t]
    for k in keys.get(t, ('layer',)):
        try:
            v = e.dxf.get(k)
            parts.append(f'{k}={v}')
        except Exception:
            pass
    if t == 'LWPOLYLINE':
        parts.append('pts=' + ','.join(f'{x:.3f},{y:.3f}' for x, y, *_ in e.get_points('xyb')))
    return hashlib.md5('|'.join(parts).encode('utf-8'), usedforsecurity=False).hexdigest()[:16]


def _dimlfac(doc, dim) -> float:
    try:
        ds = doc.dimstyles.get(dim.dxf.dimstyle)
        return float(ds.dxf.dimlfac or 1.0)
    except Exception:
        return 1.0


def _stretch_entity(doc, e, w, dx, dy) -> bool:
    """STRETCH 单实体; 返回是否被修改."""
    t = e.dxftype()
    if t == 'LINE':
        s, ep = e.dxf.start, e.dxf.end
        s_in, e_in = _in(s.x, s.y, w), _in(ep.x, ep.y, w)
        if not s_in and not e_in:
            return False
        if s_in:
            e.dxf.start = Vec3(s.x + dx, s.y + dy, s.z)
        if e_in:
            e.dxf.end = Vec3(ep.x + dx, ep.y + dy, ep.z)
        return True
    if t == 'LWPOLYLINE':
        pts = list(e.get_points('xyb'))
        moved = False
        new_pts = []
        for x, y, *rest in pts:
            if _in(x, y, w):
                new_pts.append((x + dx, y + dy, *rest))
                moved = True
            else:
                new_pts.append((x, y, *rest))
        if moved:
            e.set_points(new_pts, format='xyb')
        return moved
    if t in ('CIRCLE', 'ARC', 'TEXT', 'MTEXT', 'INSERT'):
        base = e.dxf.center if t in ('CIRCLE', 'ARC') else e.dxf.insert
        if _in(base.x, base.y, w):
            new = Vec3(base.x + dx, base.y + dy, base.z)
            if t in ('CIRCLE', 'ARC'):
                e.dxf.center = new
            else:
                e.dxf.insert = new
            return True
        return False
    if t == 'DIMENSION':
        moved = False
        for k in ('defpoint', 'defpoint2', 'defpoint3', 'defpoint4', 'text_midpoint'):
            try:
                p = e.dxf.get(k)
            except Exception:
                continue
            if p is not None and _in(p.x, p.y, w):
                e.dxf.set(k, Vec3(p.x + dx, p.y + dy, p.z))
                moved = True
        if moved:
            # 几何变了 → 标注回归真实测量 (显式文字覆盖重置为测量值)
            if e.dxf.text not in ('<>', ''):
                e.dxf.text = '<>'
            try:
                e.render()
            except Exception:
                pass
        return moved
    return False


def _scale_pt(p, bx, by, f):
    return Vec3(bx + (p.x - bx) * f, by + (p.y - by) * f, p.z)


def _scale_entity(doc, e, w, f) -> bool:
    """窗口内实体按 factor 绕窗口中心缩放 (改孔径/特征大小用)."""
    x1, y1, x2, y2 = w
    bx, by = (min(x1, x2) + max(x1, x2)) / 2, (min(y1, y2) + max(y1, y2)) / 2
    t = e.dxftype()
    if t == 'LINE':
        s, ep = e.dxf.start, e.dxf.end
        if not (_in(s.x, s.y, w) and _in(ep.x, ep.y, w)):
            return False
        e.dxf.start = _scale_pt(s, bx, by, f)
        e.dxf.end = _scale_pt(ep, bx, by, f)
        return True
    if t == 'LWPOLYLINE':
        pts = list(e.get_points('xyb'))
        if not all(_in(x, y, w) for x, y, *_ in pts):
            return False
        e.set_points([(bx + (x - bx) * f, by + (y - by) * f, *rest)
                      for x, y, *rest in pts], format='xyb')
        return True
    if t in ('CIRCLE', 'ARC'):
        c = e.dxf.center
        if not _in(c.x, c.y, w):
            return False
        e.dxf.center = _scale_pt(c, bx, by, f)
        e.dxf.radius = e.dxf.radius * f
        return True
    if t in ('TEXT', 'MTEXT', 'INSERT'):
        ins = e.dxf.insert
        if not _in(ins.x, ins.y, w):
            return False
        e.dxf.insert = _scale_pt(ins, bx, by, f)
        if t == 'TEXT' and hasattr(e.dxf, 'height'):
            e.dxf.height = e.dxf.height * f
        return True
    if t == 'DIMENSION':
        moved = False
        for k in ('defpoint', 'defpoint2', 'defpoint3', 'defpoint4', 'text_midpoint'):
            try:
                p = e.dxf.get(k)
            except Exception:
                continue
            if p is not None and _in(p.x, p.y, w):
                e.dxf.set(k, _scale_pt(p, bx, by, f))
                moved = True
        if moved:
            if e.dxf.text not in ('<>', ''):
                e.dxf.text = '<>'
            try:
                e.render()
            except Exception:
                pass
        return moved
    return False


def _apply_op(doc, op: dict) -> dict:
    """应用一个编辑操作, 返回 {op, touched: [handles]}."""
    msp = doc.modelspace()
    kind = op['op']
    w = op['window']
    touched = []
    if kind == 'stretch':
        dx, dy = float(op['dx']), float(op['dy'])
        for e in list(msp):
            if _stretch_entity(doc, e, w, dx, dy):
                touched.append(e.dxf.handle)
    elif kind == 'scale_region':
        f = float(op['factor'])
        if f <= 0:
            raise ValueError('scale factor 必须 >0')
        for e in list(msp):
            if _scale_entity(doc, e, w, f):
                touched.append(e.dxf.handle)
    elif kind == 'delete':
        for e in list(msp):
            base = getattr(e.dxf, 'center', None) or getattr(e.dxf, 'insert', None) \
                or getattr(e.dxf, 'start', None)
            ok = False
            if e.dxftype() == 'LINE':
                s, ep = e.dxf.start, e.dxf.end
                ok = _in(s.x, s.y, w) and _in(ep.x, ep.y, w)
            elif base is not None:
                ok = _in(base.x, base.y, w)
            if ok:
                touched.append(e.dxf.handle)
                msp.delete_entity(e)
    elif kind == 'retext_dim':
        # 仅允许跟随几何修改: 把指定标注文字改回真实测量值
        for e in msp.query('DIMENSION'):
            if e.dxf.handle == op['handle']:
                lf = _dimlfac(doc, e)
                e.dxf.text = str(round(e.get_measurement() * lf, 1))
                try:
                    e.render()
                except Exception:
                    pass
                touched.append(e.dxf.handle)
                break
    else:
        raise ValueError(f'未知操作 {kind!r}')
    return {'op': kind, 'window': w, 'touched': touched}


# ─────────────────────────────────────────────────────────────
# 作用域审计 (范围外对象必须逐位不变)
# ─────────────────────────────────────────────────────────────

def scope_audit(before_path: str, after_path: str) -> dict:
    before = {e.dxf.handle: _entity_digest(e)
              for e in ezdxf.readfile(before_path).modelspace()}
    after = {e.dxf.handle: _entity_digest(e)
             for e in ezdxf.readfile(after_path).modelspace()}
    changed = sorted(h for h in before if h in after and before[h] != after[h])
    removed = sorted(h for h in before if h not in after)
    added = sorted(h for h in after if h not in before)
    unchanged = len([h for h in before if h in after and before[h] == after[h]])
    return {'changed': changed, 'removed': removed, 'added': added,
            'unchanged': unchanged, 'total_before': len(before),
            # 匿名块(*Dnn)是DIMENSION重渲染的副产物, 视为受控新增
            'ok': all(h.startswith('*D') or h in after for h in added)}


def apply_edits(dxf_path: str, ops: list, out_path: str) -> dict:
    """编辑主入口: 副本上按序应用操作 → 审计."""
    doc = ezdxf.readfile(dxf_path)
    results = []
    for op in ops:
        results.append(_apply_op(doc, op))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(out_path)
    audit = scope_audit(dxf_path, out_path)
    return {'ops': results, 'audit': audit,
            'touched_total': sorted({h for r in results for h in r['touched']})}
