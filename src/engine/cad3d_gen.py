"""B线 Phase 3: NL→build123d→STEP→现有3D→2D管线 编排器.

串三个执行环境 (全部同步, 供路由线程池调用):
  .venv-cad (Py3.12)  : build123d 构建 + STEP 回读验证
  FreeCADCmd          : veritas 特征提取 / projection_v3 三路HLR六视图
  主环境 (Py3.14)     : annotator 确定性标注 + render_engine GB出图 + 预览

纪律 (huashu-cad geometry.md): STEP 导出后重新导入核对实体数/有效性/包围盒/
体积; 构建代码即参数源 (PARAMS 字典), 改图功能在其上做尺寸修改再生。
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from src.config import FC_BIN, SAAS_CORE

REPO = Path(__file__).resolve().parents[2]
VENV_PY = REPO / '.venv-cad' / 'Scripts' / 'python.exe'

# venv 侧: 沙盒执行构建体 (代码只许定义 PARAMS+part, 文件导出由 runner 控制)
BUILD_RUNNER = '''
import os, json
code = open(os.environ["BUILD_CODE"], encoding="utf-8").read()
env = {}
exec(compile(code, "build_code.py", "exec"), env)   # 围栏内代码, lint 已禁 IO/网络
part = env["part"]
from build123d import export_step
export_step(part, os.environ["OUT_STEP"])
with open(os.environ["OUT_PARAMS"], "w", encoding="utf-8") as f:
    json.dump(env.get("PARAMS", {}), f, ensure_ascii=False, indent=1)
print("BUILD_OK")
'''

# venv 侧: STEP 回读验证 (上游 cad_io.py 同口径)
VERIFY_RUNNER = '''
import os, json
from build123d import import_step
s = import_step(os.environ["STEP"])
bb = s.bounding_box()
print(json.dumps({
    "valid": bool(s.is_valid),
    "solids": len(s.solids()),
    "volume": round(s.volume, 2),
    "bbox": [round(bb.size.X, 2), round(bb.size.Y, 2), round(bb.size.Z, 2)],
}, ensure_ascii=False))
'''


def _run(cmd, env_extra: dict, timeout: int, cwd: str) -> dict:
    env = {**os.environ, 'PYTHONUTF8': '1', **env_extra}
    try:
        r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                           timeout=timeout, cwd=cwd, env=env,
                           encoding='utf-8', errors='replace')
        return {'rc': r.returncode, 'stdout': r.stdout[-4000:], 'stderr': r.stderr[-4000:]}
    except subprocess.TimeoutExpired:
        return {'rc': -1, 'stdout': '', 'stderr': f'Timeout after {timeout}s'}


def venv_available() -> bool:
    return VENV_PY.exists()


# ── 1. 构建与验证 (venv) ──────────────────────────────────────

def run_build(code: str, work_dir: str, timeout: int = 180) -> dict:
    """构建体 → STEP + 参数源. 成功返回 {step, params, build_log}."""
    wd = Path(work_dir)
    (wd / 'build_code.py').write_text(code, encoding='utf-8')
    runner = wd / '_build_runner.py'
    runner.write_text(BUILD_RUNNER, encoding='utf-8')
    step = wd / 'part.step'
    params = wd / 'params.json'
    r = _run([VENV_PY, runner],
             {'BUILD_CODE': str(wd / 'build_code.py'),
              'OUT_STEP': str(step), 'OUT_PARAMS': str(params)},
             timeout, str(wd))
    out = {'rc': r['rc'], 'stdout': r['stdout'], 'stderr': r['stderr']}
    if r['rc'] == 0 and step.exists() and params.exists():
        out['step'] = str(step)
        out['params'] = json.loads(params.read_text(encoding='utf-8'))
    return out


def run_verify(step_path: str, work_dir: str) -> dict:
    """STEP 回读核对 (geometry.md 纪律): 有效/实体数/体积/包围盒."""
    wd = Path(work_dir)
    runner = wd / '_verify_runner.py'
    if not runner.exists():
        runner.write_text(VERIFY_RUNNER, encoding='utf-8')
    r = _run([VENV_PY, runner], {'STEP': step_path}, 120, str(wd))
    if r['rc'] != 0:
        return {'ok': False, 'error': r['stderr'][-300:]}
    try:
        v = json.loads(r['stdout'].strip().splitlines()[-1])
    except Exception:
        return {'ok': False, 'error': 'verify 输出解析失败: ' + r['stdout'][-200:]}
    v['ok'] = v.get('valid') and v.get('solids', 0) >= 1 and v.get('volume', 0) > 0
    if v['ok']:
        (wd / 'verify.json').write_text(json.dumps(v, ensure_ascii=False, indent=1),
                                        encoding='utf-8')
    return v


# ── 2. 3D→2D 管线 (FreeCAD + 主环境) ──────────────────────────

def _freecad_stage(script: str, stp: str, out: str, work_dir: str,
                   timeout: int) -> dict:
    script_path = SAAS_CORE / script
    r = _run([FC_BIN, script_path], {'STP': stp, 'OUT': out}, timeout, work_dir)
    r['ok'] = r['rc'] == 0 and Path(out).exists()
    return r


def run_projection_pipeline(step_path: str, work_dir: str) -> dict:
    """STEP → veritas → 标注 → 六视图投影 → GB渲染 → 预览. 全确定性无LLM."""
    wd = Path(work_dir)
    stages = {}

    # veritas (FreeCAD)
    vrt_out = str(wd / 'veritas.json')
    r1 = _freecad_stage('veritas.py', step_path, vrt_out, str(wd), 120)
    stages['veritas'] = {'ok': r1['ok'], 'stderr': r1['stderr'][-300:] if not r1['ok'] else ''}
    if not r1['ok']:
        return {'ok': False, 'stage': 'veritas', 'stages': stages,
                'error': r1['stderr'][-400:] or r1['stdout'][-400:]}

    # annotation (主环境, 确定性链式标注)
    ann = None
    try:
        from src.engine.annotator import annotate
        from src.engine.veritas_geom import veritas_to_geometry
        vrt = json.loads(Path(vrt_out).read_text(encoding='utf-8'))
        geom = veritas_to_geometry(vrt)
        if geom is not None:
            ann = annotate(geom, None, target_count=100)
            (wd / 'annotation.json').write_text(
                json.dumps(ann, ensure_ascii=False, indent=1), encoding='utf-8')
            stages['annotate'] = {'ok': True, 'stats': ann.get('stats', {})}
        else:
            stages['annotate'] = {'ok': True, 'skipped': 'no geometry features'}
    except Exception as ex:  # 标注失败不致命, render 降级
        stages['annotate'] = {'ok': True, 'degraded': str(ex)[:200]}
    vrt = json.loads(Path(vrt_out).read_text(encoding='utf-8'))

    # projection_v3 (FreeCAD, 三路HLR六视图)
    proj_out = str(wd / 'proj_v3.json')
    r3 = _freecad_stage('projection_v3.py', step_path, proj_out, str(wd), 600)
    stages['projection'] = {'ok': r3['ok'], 'stderr': r3['stderr'][-300:] if not r3['ok'] else ''}
    if not r3['ok']:
        return {'ok': False, 'stage': 'projection', 'stages': stages,
                'error': r3['stderr'][-400:] or r3['stdout'][-400:]}

    # render (主环境, GB 六视图)
    try:
        from src.engine.render_engine import render
        proj = json.loads(Path(proj_out).read_text(encoding='utf-8'))
        dxf = str(wd / 'drawing.dxf')
        report = render(proj, None, ann, None, dxf, veritas=vrt)
        stages['render'] = {'ok': True, 'counts': report.get('counts', {})}
    except Exception as ex:
        return {'ok': False, 'stage': 'render', 'stages': stages, 'error': str(ex)[:400]}

    # 门禁 + 预览
    from src.engine.dxf_checks import validate_dxf_file
    findings = [f for f in validate_dxf_file(dxf) if f.severity in ('error', 'warning')]
    stages['gate'] = {'ok': not findings, 'findings': [f.render() for f in findings[:6]]}

    png = str(wd / 'preview.png')
    try:
        render_preview_auto(dxf, png)
    except Exception as ex:
        stages['preview'] = {'ok': False, 'error': str(ex)[:200]}

    return {'ok': not findings, 'dxf': dxf, 'png': png if Path(png).exists() else None,
            'stages': stages, 'out_dir': str(wd)}


def render_preview_auto(dxf_path: str, png_path: str) -> None:
    """通用预览: 按实体包围盒自适应取景 (不预设图幅)."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import ezdxf
    from ezdxf import bbox
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.config import (Configuration, BackgroundPolicy,
                                             ColorPolicy, LineweightPolicy)
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    ext = bbox.extents(msp, fast=True)
    if not ext.has_data:
        raise ValueError('空图幅')
    w, h = ext.size.x, ext.size.y
    fig = plt.figure(figsize=(max(8, w / 50), max(6, h / 50)))
    ax = fig.add_axes([0, 0, 1, 1])
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE,
                        color_policy=ColorPolicy.BLACK,
                        lineweight_policy=LineweightPolicy.ABSOLUTE,
                        lineweight_scaling=1.2)
    Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg
             ).draw_layout(msp, finalize=True)
    ax.set_aspect('equal')
    m = max(w, h) * 0.02
    ax.set_xlim(ext.extmin.x - m, ext.extmax.x + m)
    ax.set_ylim(ext.extmin.y - m, ext.extmax.y + m)
    Path(png_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png_path, dpi=160, facecolor='white')
    plt.close(fig)


# ── 3. 全链 ──────────────────────────────────────────────────

def run_3d_pipeline(code: str, work_dir: str) -> dict:
    """构建代码 → STEP(验证) → 3D→2D 全管线. 返回汇总报告."""
    wd = Path(work_dir)
    wd.mkdir(parents=True, exist_ok=True)
    b = run_build(code, str(wd))
    if 'step' not in b:
        return {'ok': False, 'stage': 'build', 'error': (b['stderr'] or b['stdout'])[-500:]}
    v = run_verify(b['step'], str(wd))
    if not v.get('ok'):
        return {'ok': False, 'stage': 'verify', 'error': json.dumps(v, ensure_ascii=False)[:400]}
    p = run_projection_pipeline(b['step'], str(wd))
    p['step'] = b['step']
    p['params'] = b['params']
    p['verify'] = {k: v[k] for k in ('valid', 'solids', 'volume', 'bbox')}
    if not p['ok']:
        return p
    (wd / 'report.json').write_text(
        json.dumps(p, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    return p
