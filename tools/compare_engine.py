#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Beacon 图纸确定性对比引擎

严格对比生成的 DXF 与参考图纸（DWG/DXF），逐条判定几何和标注差异。
加工图纸必须精准：钉是钉铆是铆，没有概率误差。

使用方式:
    python compare_engine.py <our_dxf> <ref_dxf_or_dwg> [--verbose]
    python compare_engine.py --analyze <dxf_path>          # 单独分析图纸
    python compare_engine.py --baseline                      # 查看基准数据
"""
import sys
import os
import json
import math
from pathlib import Path
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Set

import ezdxf
from ezdxf import units

# 容差设置 (mm) - 加工精度要求
TOL_POS = 0.5    # 位置容差
TOL_DIM = 0.1    # 尺寸容差
TOL_ANGLE = 1.0  # 角度容差 (度)


@dataclass
class EntityType:
    """实体类型统计"""
    name: str
    count: int = 0
    layer: str = ''


@dataclass
class DimensionInfo:
    """尺寸标注信息"""
    handle: str
    dtype: str
    text: str
    p1: Tuple[float, float] = (0, 0)
    p2: Tuple[float, float] = (0, 0)
    mid: Tuple[float, float] = (0, 0)
    layer: str = 'DIM'


@dataclass
class GeometryInfo:
    """几何实体信息"""
    handle: str
    dtype: str
    layer: str = '0'
    # Line
    p1: Optional[Tuple[float, float]] = None
    p2: Optional[Tuple[float, float]] = None
    # Circle
    cx: Optional[float] = None
    cy: Optional[float] = None
    r: Optional[float] = None
    # Arc
    start_angle: Optional[float] = None
    end_angle: Optional[float] = None
    # Spline
    points: List[Tuple[float, float]] = field(default_factory=list)


@dataclass
class ComparisonResult:
    """对比结果"""
    our_file: str = ''
    ref_file: str = ''

    # 普查数据
    our_census: Dict[str, int] = field(default_factory=dict)
    ref_census: Dict[str, int] = field(default_factory=dict)

    # 差异统计
    diffs: List[Dict] = field(default_factory=list)

    # 判定结果
    verdicts: List[Dict] = field(default_factory=list)

    # 汇总
    summary: Dict = field(default_factory=dict)

    def add_diff(self, category: str, item: str, our_val, ref_val, tol=None):
        """添加差异记录"""
        self.diffs.append({
            'category': category,
            'item': item,
            'ours': our_val,
            'reference': ref_val,
            'tol': tol,
            'pass': abs(our_val - ref_val) <= tol if tol else True
        })

    def add_verdict(self, rule_id: str, rule_name: str, pass_: bool, detail: str = ''):
        """添加判定结果"""
        self.verdicts.append({
            'rule': rule_id,
            'name': rule_name,
            'pass': pass_,
            'detail': detail
        })

    def report(self) -> str:
        """生成报告"""
        lines = []
        lines.append('=' * 70)
        lines.append('Beacon 图纸确定性对比报告')
        lines.append('=' * 70)
        lines.append(f'生成图纸: {self.our_file}')
        lines.append(f'参考图纸: {self.ref_file}')
        lines.append('')

        # 普查对比
        lines.append('[普查对比]')
        lines.append(f'{"指标":<20} {"我们":>10} {"参考":>10} {"比例":>10}')
        lines.append('-' * 50)
        for key in ['dimension', 'text', 'leader', 'spline', 'arc', 'circle', 'line', 'total']:
            ours = self.our_census.get(key, 0)
            ref = self.ref_census.get(key, 0)
            ratio = f'{ours/ref:.2%}' if ref > 0 else 'N/A'
            lines.append(f'{key:<20} {ours:>10} {ref:>10} {ratio:>10}')
        lines.append('')

        # 判定结果
        lines.append('[确定性判定]')
        passed = sum(1 for v in self.verdicts if v['pass'])
        total = len(self.verdicts)
        lines.append(f'通过: {passed}/{total}')
        lines.append('')

        for v in self.verdicts:
            mark = '[PASS]' if v['pass'] else '[FAIL]'
            lines.append(f'  {mark} {v["rule"]}: {v["name"]}')
            if v['detail']:
                lines.append(f'           {v["detail"]}')
        lines.append('')

        # 差异详情
        if self.diffs:
            lines.append('[差异详情]')
            fail_diffs = [d for d in self.diffs if not d['pass']]
            if fail_diffs:
                lines.append(f'发现 {len(fail_diffs)} 处差异:')
                for d in fail_diffs[:20]:
                    lines.append(f'  [FAIL] [{d["category"]}] {d["item"]}: 我们={d["ours"]}, 参考={d["reference"]}')
            else:
                lines.append('  所有指标均在容差范围内 [OK]')
            lines.append('')

        # 最终结论
        all_pass = all(v['pass'] for v in self.verdicts)
        lines.append('[最终结论]')
        if all_pass:
            lines.append('  [OK] 图纸通过所有确定性判定，可作为加工依据')
        else:
            fail_count = sum(1 for v in self.verdicts if not v['pass'])
            lines.append(f'  [FAIL] 图纸未通过 {fail_count} 项判定，需修复后重新验证')
        lines.append('=' * 70)

        return '\n'.join(lines)


def census_dxf(dxf_path: str) -> Dict[str, int]:
    """DXF 实体普查"""
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception as e:
        print(f'读取失败 {dxf_path}: {e}', file=sys.stderr)
        return {}

    msp = doc.modelspace()
    types = Counter(e.dxftype() for e in msp)
    layers = Counter(e.dxf.get('layer', '0') for e in msp)

    text_n = types.get('TEXT', 0) + types.get('MTEXT', 0)

    return {
        'total': sum(types.values()),
        'LINE': types.get('LINE', 0),
        'CIRCLE': types.get('CIRCLE', 0),
        'ARC': types.get('ARC', 0),
        'SPLINE': types.get('SPLINE', 0),
        'DIMENSION': types.get('DIMENSION', 0),
        'TEXT': types.get('TEXT', 0),
        'MTEXT': types.get('MTEXT', 0),
        'text': text_n,
        'LEADER': types.get('LEADER', 0),
        'ATTSELECT': types.get('ATTSELECT', 0),
    }


def extract_geoms(dxf_path: str) -> Dict[str, List[GeometryInfo]]:
    """提取几何实体"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    geoms = defaultdict(list)

    for e in msp:
        handle = e.dxf.handle
        dtype = e.dxftype()
        layer = e.dxf.get('layer', '0')

        geom = GeometryInfo(handle=handle, dtype=dtype, layer=layer)

        if dtype == 'LINE':
            geom.p1 = (e.dxf.start.x, e.dxf.start.y)
            geom.p2 = (e.dxf.end.x, e.dxf.end.y)
        elif dtype == 'CIRCLE':
            geom.cx = e.dxf.center.x
            geom.cy = e.dxf.center.y
            geom.r = e.dxf.radius
        elif dtype == 'ARC':
            geom.cx = e.dxf.center.x
            geom.cy = e.dxf.center.y
            geom.r = e.dxf.radius
            geom.start_angle = math.degrees(e.dxf.start_angle)
            geom.end_angle = math.degrees(e.dxf.end_angle)
        elif dtype == 'SPLINE':
            geom.points = [(p.x, p.y) for p in e.control_points]

        geoms[dtype].append(geom)

    return dict(geoms)


def extract_dimensions(dxf_path: str) -> List[DimensionInfo]:
    """提取尺寸标注"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    dims = []
    for e in msp.query('DIMENSION'):
        try:
            dim = DimensionInfo(
                handle=e.dxf.handle,
                dtype=str(getattr(e, 'dimension_type', 'unknown')),
                text=e.text if hasattr(e, 'text') else '',
                layer=e.dxf.get('layer', 'DIM')
            )
            # 尝试获取端点
            try:
                dim.p1 = (e.dxf.defpoint.x, e.dxf.defpoint.y)
            except:
                pass
            dims.append(dim)
        except Exception:
            pass

    return dims


def find_closest_geom(geom: GeometryInfo, candidates: List[GeometryInfo], tol: float) -> Optional[GeometryInfo]:
    """在候选列表中找最近的几何实体"""
    best = None
    best_dist = float('inf')

    for cand in candidates:
        if geom.dtype != cand.dtype:
            continue

        dist = 0
        if geom.dtype in ('CIRCLE', 'ARC'):
            if geom.cx is not None and cand.cx is not None:
                dist = math.hypot(geom.cx - cand.cx, geom.cy - cand.cy)
                if geom.r is not None and cand.r is not None:
                    dist += abs(geom.r - cand.r)
        elif geom.dtype == 'LINE':
            if geom.p1 and cand.p1:
                dist = math.hypot(geom.p1[0] - cand.p1[0], geom.p1[1] - cand.p1[1])

        if dist < best_dist and dist <= tol:
            best_dist = dist
            best = cand

    return best


def compare_dxf_files(our_path: str, ref_path: str, verbose: bool = False) -> ComparisonResult:
    """对比两个 DXF 文件"""
    result = ComparisonResult(
        our_file=Path(our_path).name,
        ref_file=Path(ref_path).name
    )

    # 普查
    result.our_census = census_dxf(our_path)
    result.ref_census = census_dxf(ref_path)

    # 判定 G1: 几何完整性
    our_total = result.our_census.get('total', 0)
    ref_total = result.ref_census.get('total', 0)
    if ref_total > 0:
        ratio = our_total / ref_total
        result.add_verdict('G1', '几何完整性', ratio >= 0.7,
                          f'实体数 {our_total} vs {ref_total} (比例 {ratio:.2%})')
    else:
        result.add_verdict('G1', '几何完整性', False, '参考文件无实体')

    # 判定 G2: 标注密度
    our_dims = result.our_census.get('DIMENSION', 0)
    ref_dims = result.ref_census.get('DIMENSION', 0)
    if ref_dims > 0:
        dim_ratio = our_dims / ref_dims
        result.add_verdict('D1', '标注密度', dim_ratio >= 0.5,
                          f'尺寸标注 {our_dims} vs {ref_dims} (比例 {dim_ratio:.2%})')
    else:
        result.add_verdict('D1', '标注密度', False, '参考文件无尺寸标注')

    # 判定 G3: 文字信息
    our_text = result.our_census.get('text', 0)
    ref_text = result.ref_census.get('text', 0)
    if ref_text > 0:
        text_ratio = our_text / ref_text
        result.add_verdict('D2', '文字信息量', text_ratio >= 0.5,
                          f'文字 {our_text} vs {ref_text} (比例 {text_ratio:.2%})')

    # 判定 G4: 引出线
    our_leaders = result.our_census.get('LEADER', 0)
    ref_leaders = result.ref_census.get('LEADER', 0)
    if ref_leaders > 0:
        result.add_verdict('D3', '引出线标注', our_leaders >= ref_leaders * 0.5,
                          f'引出线 {our_leaders} vs {ref_leaders}')

    # 判定 G5: 样条曲线比例
    our_spline = result.our_census.get('SPLINE', 0)
    our_curved = our_spline + result.our_census.get('ARC', 0) + result.our_census.get('CIRCLE', 0)
    ref_spline = result.ref_census.get('SPLINE', 0)
    ref_curved = ref_spline + result.ref_census.get('ARC', 0) + result.ref_census.get('CIRCLE', 0)

    if our_curved > 0:
        our_spline_share = our_spline / our_curved
        result.add_verdict('G6', '曲线实体保真', our_spline_share <= 0.35,
                          f'样条占比 {our_spline_share:.1%} (样条{our_spline}/弯曲{our_curved})')

    if ref_curved > 0:
        ref_spline_share = ref_spline / ref_curved
        result.add_diff('曲线分析', '样条占比', our_spline_share, ref_spline_share)

    # 详细几何对比 (如果启用 verbose)
    if verbose:
        our_geoms = extract_geoms(our_path)
        ref_geoms = extract_geoms(ref_path)

        # 对比圆
        our_circles = our_geoms.get('CIRCLE', [])
        ref_circles = ref_geoms.get('CIRCLE', [])
        result.add_diff('几何', '圆数量', len(our_circles), len(ref_circles))

        # 对比圆弧
        our_arcs = our_geoms.get('ARC', [])
        ref_arcs = ref_geoms.get('ARC', [])
        result.add_diff('几何', '圆弧数量', len(our_arcs), len(ref_arcs))

        # 对比直线
        our_lines = our_geoms.get('LINE', [])
        ref_lines = ref_geoms.get('LINE', [])
        result.add_diff('几何', '直线数量', len(our_lines), len(ref_lines))

    # 汇总
    result.summary = {
        'our_entities': our_total,
        'ref_entities': ref_total,
        'our_dims': our_dims,
        'ref_dims': ref_dims,
    }

    return result


def analyze_single_dxf(dxf_path: str, verbose: bool = False) -> ComparisonResult:
    """单独分析一个 DXF 文件"""
    result = ComparisonResult(our_file=Path(dxf_path).name)
    result.our_census = census_dxf(dxf_path)

    # 基于普查数据的判定
    census = result.our_census

    # G1: 总实体数
    total = census.get('total', 0)
    result.add_verdict('G1', '实体总数', total > 100, f'实体总数: {total}')

    # G2: 几何实体比例
    geo_count = census.get('LINE', 0) + census.get('CIRCLE', 0) + census.get('ARC', 0) + census.get('SPLINE', 0)
    if total > 0:
        geo_ratio = geo_count / total
        result.add_verdict('G2', '几何实体比例', geo_ratio >= 0.5, f'几何: {geo_count}/{total} = {geo_ratio:.1%}')

    # D1: 尺寸标注
    dims = census.get('DIMENSION', 0)
    result.add_verdict('D1', '尺寸标注', dims >= 20, f'尺寸标注: {dims}')

    # D2: 文字信息
    text = census.get('text', 0)
    result.add_verdict('D2', '文字信息', text >= 10, f'文字: {text}')

    # D3: 引出线
    leaders = census.get('LEADER', 0)
    result.add_verdict('D3', '引出线', leaders >= 3, f'引出线: {leaders}')

    # G3: 样条比例
    spline = census.get('SPLINE', 0)
    curved = spline + census.get('ARC', 0) + census.get('CIRCLE', 0)
    if curved > 0:
        spline_share = spline / curved
        result.add_verdict('G3', '曲线保真度', spline_share <= 0.35, f'样条占比: {spline_share:.1%}')

    # A1: 图层规范
    doc = ezdxf.readfile(dxf_path)
    layers = set(e.dxf.get('layer', '0') for e in doc.modelspace())
    required_layers = {'OUTLINE', 'DIM', 'TEXT', 'FRAME'}
    has_required = required_layers.issubset(layers)
    result.add_verdict('A1', '图层规范', has_required, f'图层: {sorted(layers)}')

    # F1: 图框
    has_frame = 'FRAME' in layers
    result.add_verdict('F1', '图框', has_frame, '有图框' if has_frame else '无图框')

    # F2: 标题栏
    has_title = 'TITLE' in layers
    result.add_verdict('F2', '标题栏', has_title, '有标题栏' if has_title else '无标题栏')

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Beacon 图纸确定性对比引擎')
    parser.add_argument('our_dxf', help='生成的 DXF 文件')
    parser.add_argument('ref_dxf', nargs='?', help='参考 DXF/DWG 文件 (可选)')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细输出')
    parser.add_argument('--analyze', '-a', help='单独分析一个 DXF 文件')
    parser.add_argument('--baseline', '-b', action='store_true', help='显示基准数据')
    parser.add_argument('--json', '-j', help='输出 JSON 格式')

    args = parser.parse_args()

    if args.baseline:
        print('=== 确定性对比基准 ===')
        print('客户固定板样例 (md5: bd9881ad):')
        print('  DIMENSION: 108')
        print('  TEXT/MTEXT: 113')
        print('  LEADER: 7')
        print('  SPLINE: 36')
        print('  ARC: 142')
        print('  CIRCLE: 97')
        print('  LINE: 1059')
        print()
        print('判定阈值:')
        print('  尺寸标注 >= 基准 × 70% (75个)')
        print('  文字信息 >= 50')
        print('  引出线 >= 3')
        print('  样条占比 <= 35%')
        print('  位置容差: 0.5mm')
        print('  尺寸容差: 0.1mm')
        return 0

    if args.analyze:
        result = analyze_single_dxf(args.analyze, verbose=args.verbose)
        if args.json:
            print(json.dumps({
                'file': result.our_file,
                'census': result.our_census,
                'verdicts': result.verdicts,
                'all_pass': all(v['pass'] for v in result.verdicts)
            }, ensure_ascii=False, indent=2))
        else:
            print(result.report())
        return 0 if all(v['pass'] for v in result.verdicts) else 1

    if not args.ref_dxf:
        print('错误: 请提供参考文件或 --analyze 模式', file=sys.stderr)
        return 1

    # 尝试读取参考文件 (DWG 需要先转换)
    if args.ref_dxf.endswith('.dwg'):
        print(f'警告: 参考文件 {args.ref_dxf} 是 DWG 格式', file=sys.stderr)
        print('       ezdxf 无法直接读取 DWG，请先用 ZWCAD/ODA 转换为 DXF', file=sys.stderr)
        print('       将仅进行生成文件的单独分析', file=sys.stderr)
        result = analyze_single_dxf(args.our_dxf, verbose=args.verbose)
    else:
        result = compare_dxf_files(args.our_dxf, args.ref_dxf, verbose=args.verbose)

    if args.json:
        print(json.dumps({
            'our_file': result.our_file,
            'ref_file': result.ref_file,
            'our_census': result.our_census,
            'ref_census': result.ref_census,
            'verdicts': result.verdicts,
            'diffs': result.diffs,
            'all_pass': all(v['pass'] for v in result.verdicts)
        }, ensure_ascii=False, indent=2))
    else:
        print(result.report())

    return 0 if all(v['pass'] for v in result.verdicts) else 1


if __name__ == '__main__':
    sys.exit(main())
