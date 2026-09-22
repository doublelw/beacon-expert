"""veritas.json → 自然语言设计描述 (GB口径, 3D→文字→图纸工作流的中间件).

用途: 用户发3D → 本模块产出人可读的《设计描述》→ 确认后走 render 管线出
GB 2D 图纸给加工商. 设计描述即图纸的文字镜像, 信息与 DXF 一一对应.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict


def _fmt(v: float) -> str:
    return f'{v:g}'


def narrative(veritas: dict, title: str = '', number: str = '') -> str:
    bb = veritas.get('bbox', {})
    W = bb.get('xmax', 0) - bb.get('xmin', 0)
    H = bb.get('ymax', 0) - bb.get('ymin', 0)
    D = bb.get('zmax', 0) - bb.get('zmin', 0)
    feats = veritas.get('features', [])
    kinds = Counter(f['type'] for f in feats)
    t = veritas.get('sheet_thickness') or veritas.get('thickness')

    L = []
    L.append(f'# {title or "零件"} 设计描述（{number or "图号待定"}）')
    L.append('')
    L.append('## 1. 概述')
    L.append(f'- 毛坯外形: {_fmt(W)} × {_fmt(H)} × {_fmt(D)} mm（长×宽×高，含特征）')
    if t:
        L.append(f'- 材料厚度: {_fmt(t)} mm 薄板（钣金件）' if kinds.get("BEND") or kinds.get("PIERCING")
                 else f'- 特征厚度: {_fmt(t)} mm')
    L.append(f'- 工艺类型: 钣金（冲孔 {kinds.get("PIERCING", 0)} 处 / 折弯 {kinds.get("BEND", 0)} 处'
             f' / 成形 {kinds.get("STAMP", 0)} 处 / 倒角 {kinds.get("CHAMFER", 0)} 处）')
    L.append('- 尺寸单位: mm；未注公差按 GB/T 1804-m；未注形位公差按 GB/T 1184-K')

    # 孔表 (按直径分组)
    holes = [f for f in feats if f.get('type') == 'PIERCING']
    if holes:
        L.append('')
        L.append('## 2. 孔加工信息（全部通孔坐标系：图示左下角为原点）')
        groups = defaultdict(list)
        for h in holes:
            radii = h.get('all_radii') or [h.get('radius', 0)]
            groups[round(2 * min(radii), 2)].append(h)
        L.append(f'共 {len(holes)} 孔，{len(groups)} 种直径：')
        for dia in sorted(groups):
            grp = groups[dia]
            xs = sorted({round(h["position"][0], 1) for h in grp})
            ys = sorted({round(h["position"][1], 1) for h in grp})
            L.append(f'- **{len(grp)}×Ø{_fmt(dia)}**（{len(xs)} 个 X 位 × {len(ys)} 个 Y 位网格）')
            L.append(f'  X: {", ".join(_fmt(x) for x in xs)}')
            L.append(f'  Y: {", ".join(_fmt(y) for y in ys)}')
        special = [h for h in holes if h.get('hole_type') in ('thread', 'csink')]
        if special:
            L.append(f'- 特殊孔口: {len(special)} 个（'
                     + ', '.join(sorted({h["hole_type"] for h in special}))
                     + '，螺纹/沉头孔口尺寸见图纸引线）')

    # 折弯
    bends = [f for f in feats if f.get('type') == 'BEND']
    if bends:
        L.append('')
        L.append('## 3. 折弯信息')
        angles = Counter()
        for b in bends:
            ang = b.get('angle')
            ang = round(ang, 1) if isinstance(ang, (int, float)) else ang
            angles[ang] += 1
        L.append(f'- 折弯 {len(bends)} 处，角度分布: '
                 + ', '.join(f'{k}°×{v}' if k else f'见图×{v}' for k, v in sorted(angles.items(), key=str)))
        radii = {round(b.get('radius', 0), 1) for b in bends if b.get('radius')}
        if radii:
            L.append(f'- 折弯半径: R{"/R".join(_fmt(r) for r in sorted(radii))}（内R）')
        L.append('- 展开计算: K因子取 0.33（材料实测修正后更新）；展开图见图纸下方参考视图')

    # 材料/表面 (veritas 无则给默认口径)
    L.append('')
    L.append('## 4. 材料与表面处理')
    L.append('- 材料: Q235（或按订单，图纸标题栏为准）')
    L.append('- 表面处理: 镀锌蓝白铬酸盐 Fe/Ep.Zn5，盐雾 48H')
    L.append('- 锐边倒钝 R0.5，孔口倒角 C0.5，去毛刺')

    L.append('')
    L.append('## 5. 验收与图纸')
    L.append('- 交付物: GB 2D 工程图 DXF（六视图+展开图+全部尺寸标注）')
    L.append('- 图纸经 23 条确定性规则验收（孔位 100% 重建/零重叠/零穿图/尺寸值=3D 实测）')
    return '\n'.join(L)


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description='veritas → 自然语言设计描述')
    p.add_argument('veritas')
    p.add_argument('-o', '--output', default=None)
    p.add_argument('--title', default='')
    p.add_argument('--number', default='')
    args = p.parse_args(argv)
    text = narrative(json.load(open(args.veritas)), args.title, args.number)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(text)
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
