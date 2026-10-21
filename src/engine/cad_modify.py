"""C线: 已知图纸修改尺寸 → 新图纸 (参数源驱动改图引擎).

前提 (诚实边界): 被改图纸须有参数源 — 本平台生成的任务目录里有
  spec.json (2D) 或 build_code.py+params.json (3D)。任意来源 DXF 无参数
  源时不做语义改尺寸 (只改尺寸文字假装改了几何是造假), 走 derive 整图缩放。

流程 (huashu-cad SKILL.md 改图纪律):
  1. LLM 把修改指令翻译成 参数源编辑 (只改指令涉及的值, 其余原样保留)
  2. 校验/构建失败带真实报错回炉 (≤2轮)
  3. 确定性重生成新图
  4. 前后对比: 参数diff + 未受影响对象清单 (作用范围外对象保全核对) + 并排预览图
"""
import json
from pathlib import Path
from typing import Optional, Tuple

from src.engine.llm_call import call_llm
from src.engine.cad2d_gen import validate_spec
from src.engine.cad3d_plan import lint_build_code, extract_code

MAX_REPAIR = 2

SPEC_EDIT_SYSTEM = """你是工程图规格编辑器。输入: 当前出图规格JSON + 用户修改指令。
输出: 修改后的完整规格JSON (只输出JSON, 无其他文字)。

规则:
1. 保持schema与图层规则不变, 只改指令涉及的数值/几何/标注
2. 改尺寸必须同步关联坐标 (如板宽改大, 右侧孔位随边距右移; 孔径改动同步直径标注)
3. 指令没提到的内容一字不动 (作用范围外对象必须保全)
4. 指令含糊时按字面最小改动, 并在 notes 末尾加一条"修改: <说明>"
5. 修改后几何仍须适配原图幅
"""

CODE_EDIT_SYSTEM = """你是参数化CAD代码编辑器。输入: build123d构建代码 + 用户修改指令。
输出: 修改后的完整代码 (只输出代码, 保持契约: 第一行 PARAMS 字典 + part 定义)。

规则:
1. 尺寸改动优先只改 PARAMS 数值; 结构性改动(加孔/改形状)才动构建表达式
2. 改尺寸必须同步关联参数 (如板长改大, 依赖边距定位的孔表达式要能自适应)
3. 指令没提到的部分原样保留
4. 只允许 from build123d import / import math, 禁一切IO
"""


# ── LLM 编辑 (spec / 构建代码) ────────────────────────────────

async def modify_spec_llm(spec: dict, instructions: str) -> Tuple[Optional[dict], dict]:
    """修改指令 → 新spec (校验+修复循环)."""
    from src.engine.cad2d_plan import extract_json
    meta = {'attempts': 0, 'errors': []}
    user_msg = json.dumps(spec, ensure_ascii=False) + '\n\n修改指令: ' + instructions
    for _ in range(MAX_REPAIR + 1):
        meta['attempts'] += 1
        r = await call_llm(user_msg, system=SPEC_EDIT_SYSTEM, temperature=0)
        if r.get('error') or not r.get('text'):
            meta['errors'].append(f"LLM调用失败: {r.get('error') or '空回复'}")
            return None, meta
        new = extract_json(r['text'])
        if new is None:
            meta['errors'].append('回复中找不到合法JSON')
        else:
            errs = validate_spec(new)
            if not errs:
                return new, meta
            meta['errors'] = errs
        user_msg = (json.dumps(spec, ensure_ascii=False) + '\n\n修改指令: ' + instructions
                    + '\n\n上次输出问题, 修正后重新输出完整JSON:\n'
                    + '\n'.join(f'- {e}' for e in meta['errors'][:8]))
    return None, meta


async def modify_code_llm(code: str, instructions: str) -> Tuple[Optional[str], dict]:
    """修改指令 → 新构建代码 (lint围栏)."""
    meta = {'attempts': 0, 'errors': []}
    user_msg = code + '\n\n修改指令: ' + instructions
    for _ in range(MAX_REPAIR + 1):
        meta['attempts'] += 1
        r = await call_llm(user_msg, system=CODE_EDIT_SYSTEM, max_tokens=4096, temperature=0)
        if r.get('error') or not r.get('text'):
            meta['errors'].append(f"LLM调用失败: {r.get('error') or '空回复'}")
            return None, meta
        new = extract_code(r['text'])
        if new is None:
            meta['errors'].append('回复中缺少 PARAMS+part 代码结构')
        else:
            errs = lint_build_code(new)
            if not errs:
                return new, meta
            meta['errors'] = errs
        user_msg = (code + '\n\n修改指令: ' + instructions
                    + '\n\n上次代码问题, 修正后重新输出完整代码:\n'
                    + '\n'.join(f'- {e}' for e in meta['errors'][:8]))
    return None, meta


# ── 确定性对比 (作用范围核对) ─────────────────────────────────

def _geom_key(g: dict) -> str:
    return json.dumps({k: v for k, v in sorted(g.items()) if k != 'layer'},
                      ensure_ascii=False, sort_keys=True)


def diff_spec(old: dict, new: dict) -> dict:
    """前后对比: 变更清单 + 未受影响对象计数 (范围外保全证明)."""
    changes, unchanged = [], 0
    og, ng = old.get('geometry', []), new.get('geometry', [])
    if len(og) != len(ng):
        changes.append(f'几何数量: {len(og)} → {len(ng)}')
    for i in range(min(len(og), len(ng))):
        if _geom_key(og[i]) != _geom_key(ng[i]):
            o, n = og[i], ng[i]
            fields = [k for k in set(o) | set(n) if o.get(k) != n.get(k)]
            changes.append(f"几何[{i}] {o.get('type')}: {'/'.join(fields)} "
                           f"{ {k: o.get(k) for k in fields} } → { {k: n.get(k) for k in fields} }")
        else:
            unchanged += 1
    od, nd = old.get('dims', []), new.get('dims', [])
    if len(od) != len(nd):
        changes.append(f'标注数量: {len(od)} → {len(nd)}')
    for i in range(min(len(od), len(nd))):
        if json.dumps(od[i], sort_keys=True, ensure_ascii=False) != \
                json.dumps(nd[i], sort_keys=True, ensure_ascii=False):
            changes.append(f'标注[{i}]: {od[i].get("type")} 值变更 '
                           f'{json.dumps(od[i], ensure_ascii=False)} → {json.dumps(nd[i], ensure_ascii=False)}')
    if old.get('title') != new.get('title'):
        changes.append(f"标题: {old.get('title')} → {new.get('title')}")
    if old.get('notes') != new.get('notes'):
        added = [n for n in (new.get('notes') or []) if n not in (old.get('notes') or [])]
        if added:
            changes.append('新增注记: ' + ' | '.join(added))
    return {'changes': changes, 'unchanged_geometry': unchanged,
            'total_geometry': len(og)}


def diff_params(old: dict, new: dict) -> dict:
    changes = []
    for k in sorted(set(old) | set(new)):
        if old.get(k) != new.get(k):
            changes.append(f'{k}: {old.get(k)} → {new.get(k)}')
    return {'changes': changes, 'unchanged_params':
            len([k for k in old if old.get(k) == new.get(k)])}


def side_by_side_png(dxf_a: str, dxf_b: str, out_png: str,
                     label_a: str = '修改前', label_b: str = '修改后') -> None:
    """并排对比图 (改图证据): 左原图 右新图, 各按自身包围盒取景等比."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import ezdxf
    from ezdxf import bbox
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.config import (Configuration, BackgroundPolicy,
                                             ColorPolicy, LineweightPolicy)

    def _draw(ax, path, label):
        doc = ezdxf.readfile(path)
        msp = doc.modelspace()
        ext = bbox.extents(msp, fast=True)
        cfg = Configuration(background_policy=BackgroundPolicy.WHITE,
                            color_policy=ColorPolicy.BLACK,
                            lineweight_policy=LineweightPolicy.ABSOLUTE,
                            lineweight_scaling=1.0)
        Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg
                 ).draw_layout(msp, finalize=True)
        ax.set_aspect('equal')
        if ext.has_data:
            m = max(ext.size.x, ext.size.y) * 0.03
            ax.set_xlim(ext.extmin.x - m, ext.extmax.x + m)
            ax.set_ylim(ext.extmin.y - m, ext.extmax.y + m)
        ax.set_title(label, fontsize=11)

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(20, 8))
    _draw(a1, dxf_a, label_a)
    _draw(a2, dxf_b, label_b)
    fig.tight_layout()
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, facecolor='white')
    plt.close(fig)


# ── 改图执行 (源任务目录 → 新任务目录) ────────────────────────

def load_source(source_task_dir: str) -> Optional[dict]:
    """识别参数源类型: {'kind':'2d','spec':...} | {'kind':'3d','code':...,'params':...} | None."""
    d = Path(source_task_dir)
    if (d / 'spec.json').exists():
        return {'kind': '2d', 'spec': json.loads((d / 'spec.json').read_text(encoding='utf-8'))}
    if (d / 'build_code.py').exists():
        params = {}
        if (d / 'params.json').exists():
            params = json.loads((d / 'params.json').read_text(encoding='utf-8'))
        return {'kind': '3d', 'code': (d / 'build_code.py').read_text(encoding='utf-8'),
                'params': params}
    return None


async def run_modify(source_task_dir: str, instructions: str, out_dir: str) -> dict:
    """入口: 参数源+指令 → 新图纸+对比证据. 运行于事件循环 (route async 直接调)."""
    src = load_source(source_task_dir)
    if src is None:
        return {'ok': False,
                'error': '源任务无参数源 (spec.json / build_code.py)。'
                         '任意来源DXF的语义改尺寸需特征识别, v1 仅支持本平台生成的图纸;'
                         '整图缩放请用 /api/drawings/{id}/variant。'}
    wd = Path(out_dir)
    wd.mkdir(parents=True, exist_ok=True)
    (wd / 'modify_instructions.txt').write_text(instructions, encoding='utf-8')

    if src['kind'] == '2d':
        new_spec, meta = await modify_spec_llm(src['spec'], instructions)
        if new_spec is None:
            return {'ok': False, 'stage': 'edit_spec', 'error': '; '.join(meta['errors'][:5])}
        diff = diff_spec(src['spec'], new_spec)
        (wd / 'spec_new.json').write_text(json.dumps(new_spec, ensure_ascii=False, indent=1),
                                          encoding='utf-8')
        from src.engine.cad2d_gen import run_2d_pipeline
        r = run_2d_pipeline(new_spec, str(wd))
        old_dxf = str(Path(source_task_dir) / 'drawing.dxf')
        cmp_png = str(wd / 'compare.png')
        if r['ok'] and Path(old_dxf).exists():
            side_by_side_png(old_dxf, r['dxf'], cmp_png)
        return {'ok': r['ok'], 'kind': '2d', 'diff': diff, 'llm_attempts': meta['attempts'],
                'dxf': r['dxf'], 'png': r['png'], 'compare_png': cmp_png,
                'out_dir': str(wd)}

    # 3D: 代码编辑 → 重建 → 全管线
    new_code, meta = await modify_code_llm(src['code'], instructions)
    if new_code is None:
        return {'ok': False, 'stage': 'edit_code', 'error': '; '.join(meta['errors'][:5])}
    from src.engine.cad3d_gen import run_3d_pipeline, run_build
    r = run_3d_pipeline(new_code, str(wd))
    if not r.get('ok') and r.get('stage') in ('build', 'verify'):
        # 一轮回炉: 带真实报错修代码 (huashu-cad 纪律)
        from src.engine.cad3d_plan import repair_3d
        code2, _ = await repair_3d(instructions, new_code, r.get('error', ''))
        if code2:
            r = run_3d_pipeline(code2, str(wd))
            new_code = code2
    diff = diff_params(src.get('params', {}), r.get('params', {}))
    old_dxf = str(Path(source_task_dir) / 'drawing.dxf')
    cmp_png = str(wd / 'compare.png')
    if r.get('ok') and Path(old_dxf).exists():
        try:
            side_by_side_png(old_dxf, r['dxf'], cmp_png)
        except Exception:
            cmp_png = None
    return {'ok': bool(r.get('ok')), 'kind': '3d', 'diff': diff,
            'llm_attempts': meta['attempts'], 'stages': r.get('stages'),
            'dxf': r.get('dxf'), 'png': r.get('png'), 'compare_png': cmp_png,
            'params': r.get('params'), 'out_dir': str(wd),
            'error': r.get('error')}


async def run_dxf_modify(dxf_path: str, instructions: str, out_dir: str) -> dict:
    """任意来源 DXF + 修改指令 → 新图纸 (STRETCH编辑 + 作用域审计 + 证据图).

    与 run_modify 的区别: 无参数源, 编辑直接作用于 DXF 实体 (LLM 只翻译
    成窗口操作); 作用域外对象逐实体审计保全。
    """
    wd = Path(out_dir)
    wd.mkdir(parents=True, exist_ok=True)
    src = Path(dxf_path)
    local = wd / f'source{src.suffix}'
    local.write_bytes(src.read_bytes())          # 只在副本上改
    (wd / 'modify_instructions.txt').write_text(instructions, encoding='utf-8')

    from src.engine.dxf_edit_plan import plan_dxf_edit
    plan, meta = await plan_dxf_edit(str(local), instructions)
    if plan is None:
        return {'ok': False, 'stage': 'plan', 'error': '; '.join(meta['errors'][:5])}
    (wd / 'edit_plan.json').write_text(
        json.dumps(plan, ensure_ascii=False, indent=1), encoding='utf-8')

    from src.engine.dxf_edit import apply_edits
    out_dxf = str(wd / 'drawing_modified.dxf')
    r = apply_edits(str(local), plan['ops'], out_dxf)
    audit = r['audit']
    cmp_png = str(wd / 'compare.png')
    try:
        side_by_side_png(str(local), out_dxf, cmp_png, '修改前', '修改后')
    except Exception:
        cmp_png = None
    ok = bool(audit['ok']) and bool(r['touched_total'] or not plan['ops'])
    return {'ok': ok, 'kind': 'dxf', 'note': plan.get('note', ''),
            'ops': [{'op': o['op'], 'window': o.get('window'),
                     'touched': len(o['touched'])} for o in r['ops']],
            'touched': r['touched_total'],
            'audit': {'changed': len(audit['changed']),
                      'removed': len(audit['removed']),
                      'added': len(audit['added']),
                      'unchanged': audit['unchanged'],
                      'total_before': audit['total_before'], 'ok': audit['ok']},
            'dxf': out_dxf, 'compare_png': cmp_png if ok else None,
            'llm_attempts': meta['attempts'], 'out_dir': str(wd),
            'error': None if ok else '作用域审计未通过或无实体被修改'}
