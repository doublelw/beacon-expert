"""图纸验收门 (P1): 按客户样例普查表硬门禁, 替代 dxf_checks 的完整性检查.

教训 (2026-09-13): dxf_checks 只查文件完整性 (重复线/零长度),
4个尺寸的废图照样 0错误通过. 工程图的核心价值是尺寸标注密度 —
本门禁以客户样例 固定板.dxf (108 DIMENSION/113 TEXT/7 LEADER/36 SPLINE)
为基准做普查对比, 任何一项不达标即 FAIL.

用法:
    python -m src.engine.acceptance <dxf> [--baseline <dxf>] [--min-dim N]
    或 from src.engine.acceptance import accept, census
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import ezdxf

# 客户样例固定板.dxf 普查基准 (md5 bd9881ad, 2026-06 微信原件)
CUSTOMER_BASELINE = {
    'dimension': 108, 'text': 113, 'leader': 7,
    'spline': 36, 'arc': 142, 'circle': 97,
}

# 达标线 (相对基准的比例 / 绝对下限)
THRESHOLDS = {
    'dimension_ratio': 0.7,   # DIMENSION ≥ 基准70% (75个)
    'text_min': 50,           # TEXT/MTEXT ≥ 50
    'leader_min': 3,          # LEADER ≥ 3
    'spline_share_max': 0.35, # 弯曲实体中样条占比 ≤35% (客户13%, 采样膨胀期51%;
                              # 绝对计数跨CAD风格不可比 — 客户把自由曲线打成短线段)
}


def census(dxf_path: str) -> dict:
    """DXF 实体普查 (类型/图层/标注密度)."""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    types = Counter(e.dxftype() for e in msp)
    layers = Counter(e.dxf.get('layer', '0') for e in msp)
    text_n = types.get('TEXT', 0) + types.get('MTEXT', 0)
    return {
        'total': sum(types.values()),
        'types': dict(types),
        'layers': dict(layers),
        'dimension': types.get('DIMENSION', 0) + types.get('ARC_DIMENSION', 0),
        'text': text_n,
        'leader': types.get('LEADER', 0) + types.get('MULTILEADER', 0),
        'spline': types.get('SPLINE', 0),
        'arc': types.get('ARC', 0),
        'circle': types.get('CIRCLE', 0),
    }


def accept(dxf_path: str, baseline: dict = None,
           thresholds: dict = None) -> dict:
    """验收判定: 逐项对比基准, 返回 {pass, metrics, verdicts}."""
    baseline = baseline or CUSTOMER_BASELINE
    th = {**THRESHOLDS, **(thresholds or {})}
    c = census(dxf_path)

    checks = []

    def _chk(name, value, ok, detail):
        checks.append({'metric': name, 'value': value, 'ok': bool(ok),
                       'detail': detail})

    dim_target = max(int(baseline['dimension'] * th['dimension_ratio']), 1)
    _chk('dimension', c['dimension'], c['dimension'] >= dim_target,
         f"{c['dimension']} >= {dim_target} (基准{baseline['dimension']}×{th['dimension_ratio']:.0%})")
    _chk('text', c['text'], c['text'] >= th['text_min'],
         f"{c['text']} >= {th['text_min']} (基准{baseline['text']})")
    _chk('leader', c['leader'], c['leader'] >= th['leader_min'],
         f"{c['leader']} >= {th['leader_min']} (基准{baseline['leader']})")
    curved = c['spline'] + c['arc'] + c['circle']
    share = (c['spline'] / curved) if curved else 0.0
    _chk('spline_share', round(share, 3), share <= th['spline_share_max'],
         f"{share:.1%} <= {th['spline_share_max']:.0%} (样条{c['spline']}/弯曲{curved}; 客户13%)")

    passed = all(k['ok'] for k in checks)
    return {'pass': passed, 'file': dxf_path, 'census': c,
            'baseline': baseline, 'checks': checks}


def compare_report(dxf_path: str, baseline_dxf: str) -> dict:
    """逐项对比客户样例 (P2 报告用), 不做判定."""
    ours = census(dxf_path)
    theirs = census(baseline_dxf)
    keys = ['dimension', 'text', 'leader', 'spline', 'arc', 'circle', 'total']
    rows = []
    for k in keys:
        ratio = (ours[k] / theirs[k]) if theirs[k] else None
        rows.append({'metric': k, 'ours': ours[k], 'sample': theirs[k],
                     'ratio': round(ratio, 3) if ratio is not None else None})
    return {'ours': dxf_path, 'sample': baseline_dxf, 'rows': rows}


def main(argv: list | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description='图纸验收门 (客户样例普查基准)')
    p.add_argument('dxf', help='待验收 DXF')
    p.add_argument('--baseline', default=None, help='客户样例 DXF (默认内置固定板基准)')
    p.add_argument('--min-dim', type=int, default=None, help='覆盖 DIMENSION 下限')
    p.add_argument('--json', default=None, help='结果写 JSON')
    p.add_argument('--compare', action='store_true', help='仅输出对比表(不判定)')
    args = p.parse_args(argv)

    if args.compare and args.baseline:
        rep = compare_report(args.dxf, args.baseline)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    th = {'dimension_ratio': 1.0} if args.min_dim is None else {}
    if args.min_dim is not None:
        result = accept(args.dxf, thresholds={'dimension_ratio': 0,
                                              'text_min': 50, 'leader_min': 3})
        # 直接用绝对值覆盖
        result['checks'][0]['value'] = result['census']['dimension']
        result['checks'][0]['ok'] = result['census']['dimension'] >= args.min_dim
        result['checks'][0]['detail'] = f"{result['census']['dimension']} >= {args.min_dim}"
        result['pass'] = all(k['ok'] for k in result['checks'])
    else:
        baseline = None
        if args.baseline and Path(args.baseline).exists():
            baseline = {k: v for k, v in census(args.baseline).items()
                        if k in CUSTOMER_BASELINE}
        result = accept(args.dxf, baseline=baseline)

    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    for k in result['checks']:
        mark = 'PASS' if k['ok'] else 'FAIL'
        print(f"  [{mark}] {k['metric']}: {k['detail']}")
    print(f"验收: {'PASS' if result['pass'] else 'FAIL'}  {args.dxf}")
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
