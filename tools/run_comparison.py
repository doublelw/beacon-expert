#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Beacon 图纸确定性对比测试套件

对后壳、前壳两个测试零件进行完整的对比分析：
1. 分析参考 DWG (需要 ZWCAD 转换)
2. 分析已有 DXF 输出
3. 生成对比报告

运行:
    python run_comparison.py --all
    python run_comparison.py --part 后壳
    python run_comparison.py --part 前壳 --run-pipeline
"""
import sys
import os
import json
import math
import argparse
from pathlib import Path
from collections import Counter, defaultdict
from typing import Dict, List, Tuple, Optional

import ezdxf

# 容差设置 (mm)
TOL_POS = 0.5
TOL_DIM = 0.1
TOL_ANGLE = 1.0

# 客户固定板基准 (md5: bd9881ad)
CUSTOMER_BASELINE = {
    'dimension': 108,
    'text': 113,
    'leader': 7,
    'spline': 36,
    'arc': 142,
    'circle': 97,
    'line': 1059,
    'total': 1565,
}

# 判定阈值
THRESHOLDS = {
    'dimension_ratio': 0.7,   # >= 基准 70% (75个)
    'text_min': 50,            # >= 50
    'leader_min': 3,           # >= 3
    'spline_share_max': 0.35,  # <= 35%
}


def census_dxf(dxf_path: str) -> Dict[str, int]:
    """DXF 实体普查"""
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception as e:
        print(f'[ERROR] 读取失败: {dxf_path} - {e}', file=sys.stderr)
        return {}

    msp = doc.modelspace()
    types = Counter(e.dxftype() for e in msp)
    layers = Counter(e.dxf.get('layer', '0') for e in msp)

    text_n = types.get('TEXT', 0) + types.get('MTEXT', 0)
    dim_n = types.get('DIMENSION', 0) + types.get('ARC_DIMENSION', 0)

    return {
        'total': sum(types.values()),
        'LINE': types.get('LINE', 0),
        'CIRCLE': types.get('CIRCLE', 0),
        'ARC': types.get('ARC', 0),
        'SPLINE': types.get('SPLINE', 0),
        'DIMENSION': dim_n,
        'TEXT': types.get('TEXT', 0),
        'MTEXT': types.get('MTEXT', 0),
        'text': text_n,
        'LEADER': types.get('LEADER', 0) + types.get('MULTILEADER', 0),
        'layers': dict(layers),
    }


def analyze_dimensions(dxf_path: str) -> List[Dict]:
    """分析尺寸标注详情"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    dims = []
    for e in msp.query('DIMENSION'):
        try:
            dim_info = {
                'handle': e.dxf.handle,
                'type': str(getattr(e, 'dimension_type', 'unknown')),
                'text': e.text if hasattr(e, 'text') else '',
                'layer': e.dxf.get('layer', 'DIM'),
            }
            # 尝试获取测量值
            try:
                dim_info['measured'] = float(e.measured_distance) if hasattr(e, 'measured_distance') else None
            except:
                pass
            dims.append(dim_info)
        except Exception:
            pass

    return dims


def compare_census(ours: Dict, ref: Dict, baseline: Dict = None) -> List[Dict]:
    """对比普查数据"""
    diffs = []
    baseline = baseline or CUSTOMER_BASELINE

    # 尺寸标注
    ours_dim = ours.get('DIMENSION', 0)
    ref_dim = ref.get('DIMENSION', 0)
    base_dim = baseline.get('dimension', 108)
    min_dim = max(int(base_dim * THRESHOLDS['dimension_ratio']), 1)

    diffs.append({
        'category': '尺寸标注',
        'item': '数量',
        'ours': ours_dim,
        'ref': ref_dim,
        'baseline': base_dim,
        'min_required': min_dim,
        'pass': ours_dim >= min_dim,
    })

    # 文字信息
    ours_text = ours.get('text', 0)
    ref_text = ref.get('text', 0)
    diffs.append({
        'category': '文字信息',
        'item': '数量',
        'ours': ours_text,
        'ref': ref_text,
        'baseline': baseline.get('text', 113),
        'min_required': THRESHOLDS['text_min'],
        'pass': ours_text >= THRESHOLDS['text_min'],
    })

    # 引出线
    ours_leader = ours.get('LEADER', 0)
    ref_leader = ref.get('LEADER', 0)
    diffs.append({
        'category': '引出线',
        'item': '数量',
        'ours': ours_leader,
        'ref': ref_leader,
        'baseline': baseline.get('leader', 7),
        'min_required': THRESHOLDS['leader_min'],
        'pass': ours_leader >= THRESHOLDS['leader_min'],
    })

    # 样条占比
    ours_spline = ours.get('SPLINE', 0)
    ours_curved = ours_spline + ours.get('ARC', 0) + ours.get('CIRCLE', 0)
    ref_spline = ref.get('SPLINE', 0)
    ref_curved = ref_spline + ref.get('ARC', 0) + ref.get('CIRCLE', 0)

    ours_share = ours_spline / ours_curved if ours_curved > 0 else 0
    ref_share = ref_spline / ref_curved if ref_curved > 0 else 0

    diffs.append({
        'category': '曲线保真',
        'item': '样条占比',
        'ours': f'{ours_share:.1%}',
        'ref': f'{ref_share:.1%}',
        'baseline': f'{baseline.get("spline", 36) / (baseline.get("arc", 142) + baseline.get("circle", 97) + baseline.get("spline", 36)):.1%}',
        'max_required': THRESHOLDS['spline_share_max'],
        'pass': ours_share <= THRESHOLDS['spline_share_max'],
    })

    # 总实体数
    ours_total = ours.get('total', 0)
    ref_total = ref.get('total', 0)
    diffs.append({
        'category': '实体总数',
        'item': '数量',
        'ours': ours_total,
        'ref': ref_total,
        'ratio': f'{ours_total/ref_total:.2%}' if ref_total > 0 else 'N/A',
    })

    return diffs


def generate_report(part_name: str, our_path: str, ref_path: str = None,
                   verbose: bool = False) -> str:
    """生成对比报告"""
    lines = []

    lines.append('=' * 70)
    lines.append(f'Beacon 图纸确定性对比报告 - {part_name}')
    lines.append('=' * 70)
    lines.append('')

    # 分析生成图纸
    lines.append(f'[生成图纸] {our_path}')
    our_census = census_dxf(our_path)
    if not our_census:
        lines.append('  [ERROR] 无法读取生成图纸')
        return '\n'.join(lines)

    lines.append(f'  实体总数: {our_census.get("total", 0)}')
    lines.append(f'  尺寸标注: {our_census.get("DIMENSION", 0)}')
    lines.append(f'  文字信息: {our_census.get("text", 0)}')
    lines.append(f'  引出线:   {our_census.get("LEADER", 0)}')
    lines.append('')

    # 分析参考图纸 (如果提供)
    if ref_path and os.path.exists(ref_path):
        lines.append(f'[参考图纸] {ref_path}')
        ref_census = census_dxf(ref_path)
        if ref_census:
            lines.append(f'  实体总数: {ref_census.get("total", 0)}')
            lines.append(f'  尺寸标注: {ref_census.get("DIMENSION", 0)}')
            lines.append(f'  文字信息: {ref_census.get("text", 0)}')
            lines.append(f'  引出线:   {ref_census.get("LEADER", 0)}')
        else:
            ref_census = {}
        lines.append('')

        # 对比 (仅当参考图纸可读取时)
        lines.append('[对比分析]')
        if ref_census:
            diffs = compare_census(our_census, ref_census)
            for d in diffs:
                status = '[PASS]' if d.get('pass', True) else '[FAIL]'
                lines.append(f'  {status} [{d["category"]}] {d["item"]}: 我们={d["ours"]}, 参考={d.get("ref", "N/A")}')
        else:
            lines.append('  [SKIP] 参考 DWG 无法直接读取 (需 ZWCAD/ODA 转 DXF)')
            # 仅与固定板基准对比
            diffs = compare_census(our_census, {})
            for d in diffs:
                status = '[PASS]' if d.get('pass', True) else '[FAIL]'
                lines.append(f'  {status} [{d["category"]}] {d["item"]}: 我们={d["ours"]}, 基准={d.get("baseline", "N/A")}')
        lines.append('')

        # 与基准对比
        lines.append('[与固定板基准对比]')
        baseline = CUSTOMER_BASELINE
        min_dim = max(int(baseline['dimension'] * THRESHOLDS['dimension_ratio']), 1)
        ours_dim = our_census.get('DIMENSION', 0)
        if ours_dim >= min_dim:
            lines.append(f'  [PASS] 尺寸标注: {ours_dim} >= {min_dim} (基准{baseline["dimension"]})')
        else:
            lines.append(f'  [FAIL] 尺寸标注: {ours_dim} < {min_dim} (基准{baseline["dimension"]})')

        ours_text = our_census.get('text', 0)
        if ours_text >= THRESHOLDS['text_min']:
            lines.append(f'  [PASS] 文字信息: {ours_text} >= {THRESHOLDS["text_min"]}')
        else:
            lines.append(f'  [FAIL] 文字信息: {ours_text} < {THRESHOLDS["text_min"]}')

        ours_leader = our_census.get('LEADER', 0)
        if ours_leader >= THRESHOLDS['leader_min']:
            lines.append(f'  [PASS] 引出线: {ours_leader} >= {THRESHOLDS["leader_min"]}')
        else:
            lines.append(f'  [FAIL] 引出线: {ours_leader} < {THRESHOLDS["leader_min"]}')

        ours_spline = our_census.get('SPLINE', 0)
        ours_curved = ours_spline + our_census.get('ARC', 0) + our_census.get('CIRCLE', 0)
        share = ours_spline / ours_curved if ours_curved > 0 else 0
        if share <= THRESHOLDS['spline_share_max']:
            lines.append(f'  [PASS] 样条占比: {share:.1%} <= {THRESHOLDS["spline_share_max"]:.0%}')
        else:
            lines.append(f'  [FAIL] 样条占比: {share:.1%} > {THRESHOLDS["spline_share_max"]:.0%}')
        lines.append('')

    # 图层分析
    lines.append('[图层分析]')
    layers = our_census.get('layers', {})
    for layer, count in sorted(layers.items(), key=lambda x: -x[1]):
        lines.append(f'  {layer}: {count}')
    lines.append('')

    # 最终结论
    all_pass = (our_census.get('DIMENSION', 0) >= min_dim and
                our_census.get('text', 0) >= THRESHOLDS['text_min'] and
                our_census.get('LEADER', 0) >= THRESHOLDS['leader_min'] and
                (ours_spline / ours_curved if ours_curved > 0 else 0) <= THRESHOLDS['spline_share_max'])

    lines.append('[最终结论]')
    if all_pass:
        lines.append('  [OK] 图纸通过所有确定性判定，可作为加工依据')
    else:
        lines.append('  [FAIL] 图纸未通过全部判定，需修复后重新验证')
    lines.append('=' * 70)

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Beacon 图纸确定性对比测试')
    parser.add_argument('--part', '-p', choices=['后壳', '前壳', 'all'], default='all',
                       help='选择测试零件')
    parser.add_argument('--run-pipeline', action='store_true',
                       help='运行完整 pipeline (需要 FreeCAD)')
    parser.add_argument('--output', '-o', help='输出报告路径')
    parser.add_argument('--json', '-j', action='store_true', help='输出 JSON 格式')

    args = parser.parse_args()

    base_dir = Path(__file__).parent.parent
    samples_dir = base_dir / 'docs' / 'samples'
    part_dirs = {
        '后壳': base_dir / '后壳',
        '前壳': base_dir / '前壳',
    }

    results = {}

    parts_to_run = ['后壳', '前壳'] if args.part == 'all' else [args.part]

    for part in parts_to_run:
        print(f'\n处理 {part}...')

        # 查找参考 DWG
        ref_dwg = part_dirs[part] / f'{part}.dwg'
        ref_stp = part_dirs[part] / f'{part}.stp'

        # 查找已有 DXF 输出
        dxf_files = list(samples_dir.glob(f'*{part}*.dxf'))
        dxf_files.sort(key=lambda x: x.stat().st_size, reverse=True)

        if not dxf_files:
            print(f'  [WARN] 未找到 {part} 的 DXF 输出文件')
            continue

        # 使用最大的 DXF 作为主要输出 (通常是 GB_rich 或 最终)
        best_dxf = dxf_files[0]
        print(f'  最佳输出: {best_dxf.name} ({best_dxf.stat().st_size} bytes)')

        # 生成报告
        report = generate_report(part, str(best_dxf), str(ref_dwg), verbose=True)
        results[part] = {
            'best_dxf': str(best_dxf),
            'ref_dwg': str(ref_dwg),
            'ref_stp': str(ref_stp),
            'report': report,
        }

        if args.output:
            out_path = Path(args.output) / f'{part}_comparison.md'
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(report, encoding='utf-8')
            print(f'  报告已保存: {out_path}')
        else:
            print(report)

    return 0


if __name__ == '__main__':
    sys.exit(main())
