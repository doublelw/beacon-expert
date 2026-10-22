"""M-explode: 客户 STEP 装配体 → 花叔 assembly.json → 交互爆炸图 HTML + 装配工程图.

链路 (无自然语言, 确定性):
  import_step(客户STEP) 拆子件 → 逐件 tessellate (同 mech.py 参数)
  → 主轴自动判定 + 分层爆炸向量 (越靠里飞得越远, mechanical.md 纪律)
  → assembly.json (viewer 契约) → vendor viewer.py 直连出单文件 HTML
  → vendor drawing.py 出装配工程图 DXF (回转类, 全剖主视图+俯视图+明细栏)

两条腿:
  爆炸图: assembly.json + 交互HTML (双击即开, 不联网)
  装配工程图: assembly.json + 装配DXF (GB全剖, 仅适用回转类主轴=Z装配体)

环境: build123d/OCP 只支持 Python ≤3.13, 主环境 3.14 → 本模块顶层不 import
build123d; 路由在 .venv-cad 侧运行 (tools/install_cad_env.py 安装), 或直接
在 venv python 里调 build_assembly_json。主环境测试只测 venv 缺失时的降级
提示与装配体判定逻辑的纯函数部分。
"""
import base64
import json
import math
import subprocess
from pathlib import Path
from typing import Optional

from src.config import VENDOR_HUASHU

VIEWER = VENDOR_HUASHU / "scripts" / "mech" / "viewer.py"
DRAWING = VENDOR_HUASHU / "scripts" / "mech" / "drawing.py"

# 密度表与花叔 mech.MATERIALS 一致 (材质默认 steel, 客户装配体无材质信息时按钢估)
DENSITY_GMM3 = 7.85e-3
DEFAULT_MATERIAL = "steel"
DEFAULT_MATERIAL_NAME = "合金钢"


def _is_step_path(p: str) -> bool:
    return p.lower().endswith(('.step', '.stp'))


def _tessellate(part) -> dict:
    """与 mech.Assembly._tessellate 同参数 (保 viewer 渲染口径一致)."""
    import numpy as np
    bb = part.bounding_box()
    diag = math.dist((bb.min.X, bb.min.Y, bb.min.Z),
                     (bb.max.X, bb.max.Y, bb.max.Z))
    verts, tris = part.tessellate(max(0.005, diag * 4e-4), 0.15)
    v = np.array([[p.X, p.Y, p.Z] for p in verts], dtype=np.float32)
    t = np.array(tris, dtype=np.uint32)
    return dict(v=base64.b64encode(v.tobytes()).decode(),
                i=base64.b64encode(t.tobytes()).decode(),
                nv=int(len(v)), nt=int(len(t)))


def venv_python() -> Optional[Path]:
    """隔离构建环境 python (OCP 只支持 ≤3.13). 缺失返回 None."""
    p = Path(__file__).resolve().parents[2] / '.venv-cad' / 'Scripts' / 'python.exe'
    return p if p.exists() else None


def build_assembly_json(step_path: str, out_dir: str,
                       title: Optional[str] = None,
                       explode_scale: float = 0.8) -> dict:
    """客户 STEP → assembly.json + 爆炸图 HTML. 返回 {ok, stage, manifest, error}.

    需在 build123d 环境运行 (venv); 主环境调用时若 venv 缺失报 stage=env。
    """
    step_path = str(step_path)
    if not _is_step_path(step_path):
        return {'ok': False, 'stage': 'input',
                'error': f'不是 STEP 文件: {Path(step_path).name}'}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    venv_py = venv_python()
    if venv_py is None:
        return {'ok': False, 'stage': 'env',
                'error': '缺 .venv-cad 隔离环境 (先跑 tools/install_cad_env.py)'}

    try:
        from build123d import import_step, CenterOf
    except ImportError:
        # 主环境无 build123d: 走 venv 子进程 (脚本内联, 与 tools/ 隔离环境同机制)
        return _build_in_venv(venv_py, step_path, str(out), title, explode_scale)

    return _build_with_build123d(step_path, out, title, explode_scale)


def _build_with_build123d(step_path, out_dir, title, explode_scale):
    """build123d 在 sys.path (venv 直接运行) 时的主流程."""
    from pathlib import Path
    from build123d import import_step, CenterOf
    import numpy as np
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    try:
        comp = import_step(step_path)
    except Exception as ex:
        return {'ok': False, 'stage': 'read', 'error': f'STEP 读取失败: {str(ex)[:200]}'}

    children = list(comp.children)
    if not children:
        # 裸 STEP (无 compound 包裹): 直接用 solids
        children = list(comp.solids())
    if not children:
        return {'ok': False, 'stage': 'read', 'error': 'STEP 内未找到零件实体'}

    coms = []
    for c in children:
        try:
            coms.append(c.center(CenterOf.MASS))
        except Exception:
            coms.append(None)
    valid = [(c, cm) for c, cm in zip(children, coms)
             if cm is not None and c.volume > 0]
    if len(valid) < 2:
        return {'ok': False, 'stage': 'input',
                'error': f'装配体判定: 有效子件 {len(valid)} 个 (<2), 单一零件不适用爆炸图'}

    manifest = _assemble_manifest(valid, out, title, explode_scale)
    aj = out / 'assembly.json'
    aj.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    return _viewer_html(aj, manifest, out)


def _assemble_manifest(valid, out: Path, title, explode_scale):
    """子件 → assembly.json manifest (viewer 契约)."""
    import numpy as np
    n = len(valid)
    # 主轴 = 各件包围盒"最薄"轴 (装配叠层轴, 不用质心离散度 — 行星件径向分布会误判)
    bb_min = np.full((n, 3), math.inf)
    bb_max = np.full((n, 3), -math.inf)
    for i, (c, cm) in enumerate(valid):
        bb = c.bounding_box()
        for k in range(3):
            bb_min[i, k] = float(bb.min.__getattribute__(['X', 'Y', 'Z'][k]))
            bb_max[i, k] = float(bb.max.__getattribute__(['X', 'Y', 'Z'][k]))
    thickness = bb_max - bb_min
    mean_thick = thickness.mean(axis=0)
    cand = [k for k in range(3) if mean_thick[k] > 1e-6]
    main_axis = min(cand, key=lambda k: mean_thick[k]) if cand else 2

    # 分层: 沿主轴按质心, 越靠里飞得越远; 同层件加径向散开 (mechanical.md 纪律)
    axis_vals = np.array([float(cm.__getattribute__(['X', 'Y', 'Z'][main_axis]))
                          for _, cm in valid])
    mid = float((axis_vals.max() + axis_vals.min()) / 2)
    com_arr = np.array([[float(cm.X), float(cm.Y), float(cm.Z)] for _, cm in valid])
    radial_center = com_arr.mean(axis=0)
    radial_center[main_axis] = mid

    part_rows = []
    total_mass = 0.0
    for i, (c, cm) in enumerate(valid):
        code = f"P{i + 1:02d}"
        mass_g = c.volume * DENSITY_GMM3
        total_mass += mass_g
        axial_off = float(round((float(axis_vals[i]) - mid) * explode_scale, 3))
        rv = com_arr[i] - radial_center
        rv[main_axis] = 0.0
        radial_dist = float(np.linalg.norm(rv))
        radial_scale = 0.8 * radial_dist * explode_scale
        ex = [0.0, 0.0, 0.0]
        if radial_dist > 0.5:
            rdir = rv / radial_dist
            ex = [rdir[k] * radial_scale for k in range(3)]
        ex[main_axis] = axial_off
        ex = [float(round(v, 3)) for v in ex]
        part_rows.append({
            'name': c.label or code, 'code': code,
            'material': DEFAULT_MATERIAL, 'material_name': DEFAULT_MATERIAL_NAME,
            'qty': 1, 'mass_g': round(mass_g, 1),
            'explode': ex,
            'spin_axis': [0, 0, 1] if main_axis == 2 else [0, 1, 0] if main_axis == 1 else [1, 0, 0],
            'spin_center': [float(cm.X), float(cm.Y), float(cm.Z)],
            'color': None, 'gear': None, 'note': '',
            'motion': {'spin': 0.0, 'orbit': 0.0, 'orbit_center': [0.0, 0.0, 0.0]},
            'mesh': _tessellate(c),
        })
    return {
        'title': title or 'assembly',
        'units': 'mm',
        'bbox': {'min': [round(v, 3) for v in bb_min.min(axis=0)],
                 'max': [round(v, 3) for v in bb_max.max(axis=0)]},
        'mass_total_g': round(total_mass, 1),
        'checks_ok': True,
        'planetary': None,
        'parts': part_rows,
        '_beacon': {'source': 'customer_step', 'main_axis': main_axis,
                    'explode_scale': explode_scale, 'n_parts': len(part_rows)},
    }


def _viewer_html(aj: Path, manifest, out: Path) -> dict:
    """花叔 viewer.py 直连出单文件 HTML (双击即开, 不联网)."""
    html_out = out / f'{manifest["title"]}-交互爆炸图.html'
    venv_py = venv_python()
    if venv_py is None:
        return {'ok': False, 'stage': 'env', 'manifest': manifest,
                'assembly_json': str(aj), 'html': None,
                'n_parts': len(manifest['parts']),
                'main_axis': manifest['_beacon']['main_axis'],
                'error': '缺 .venv-cad (先跑 tools/install_cad_env.py)'}
    r = subprocess.run([str(venv_py), str(VIEWER), str(aj), '--out', str(html_out)],
                       capture_output=True, text=True, timeout=120,
                       encoding='utf-8', errors='replace',
                       env={**__import__('os').environ, 'PYTHONUTF8': '1'})
    ok = r.returncode == 0 and html_out.exists()
    return {'ok': ok, 'stage': 'viewer', 'manifest': manifest,
            'assembly_json': str(aj),
            'html': str(html_out) if ok else None,
            'n_parts': len(manifest['parts']),
            'main_axis': manifest['_beacon']['main_axis'],
            'error': None if ok else r.stderr[-300:]}


def _build_in_venv(venv_py: Path, step_path: str, out_dir: str,
                   title, explode_scale) -> dict:
    """主环境无 build123d → 把构建+mesh 丢给 venv 子进程, 主环境收 assembly.json."""
    import os
    driver = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})\n"
        "from src.engine.explode_gen import _build_with_build123d\n"
        f"r = _build_with_build123d({step_path!r}, {out_dir!r}, {title!r}, {float(explode_scale)})\n"
        f"json.dump(r, open({json.dumps(out_dir + '/venv_build.json')},'w',encoding='utf-8'), ensure_ascii=False)\n"
        "sys.exit(0 if r.get('ok') else 1)\n"
    )
    drv = Path(out_dir) / '_venv_driver.py'
    drv.write_text(driver, encoding='utf-8')
    r = subprocess.run([str(venv_py), str(drv)], capture_output=True, text=True,
                       timeout=600, encoding='utf-8', errors='replace',
                       env={**os.environ, 'PYTHONUTF8': '1'})
    out_dir_p = Path(out_dir)
    rep = out_dir_p / 'venv_build.json'
    if rep.exists():
        body = json.loads(rep.read_text(encoding='utf-8'))
        # 装配体判定: 把 stage/error 传回主环境
        if body.get('stage') == 'input':
            body['ok'] = False
            body['error'] = body.get('error', '')
            return body
        body['ok'] = r.returncode == 0 and body.get('ok', False)
        if not body['ok']:
            body['error'] = (r.stderr or r.stdout)[-400:]
        # manifest 由 driver 一并落盘 → 主环境能拿到 main_axis 做装配工程图降级判定
        return body
    return {'ok': False, 'stage': 'build', 'error': (r.stderr or r.stdout)[-400:]}


def run_explode(step_path: str, out_dir: str, title: str = None,
                explode_scale: float = 0.8) -> dict:
    """路由/CLI 统一入口: 客户STEP → 爆炸图HTML + 装配工程图DXF."""
    r = build_assembly_json(step_path, out_dir, title, explode_scale)
    # 装配工程图 (回转类装配体, 花叔 drawing.py; 失败不阻断爆炸图交付)
    if r.get('ok') and r.get('assembly_json'):
        drawing = _assembly_drawing(step_path, out_dir, title, r)
        r['assembly_dxf'] = drawing.get('dxf')
        r['assembly_dxf_png'] = drawing.get('png')
        r['drawing_note'] = drawing.get('note') or drawing.get('error')
    return r


def _assembly_drawing(step_path, out_dir, title, r):
    """花叔 drawing.py 出装配工程图 (仅回转类主轴Z装配体; 其他如实降级)."""
    out = Path(out_dir)
    manifest = r.get('manifest') or {}
    main_axis = manifest.get('_beacon', {}).get('main_axis', 2)
    if main_axis != 2:
        return {'dxf': None, 'png': None,
                'note': f'非回转类主轴 (主轴={["X", "Y", "Z"][main_axis]}), '
                        '装配工程图仅支持主轴=Z 的回转类装配体, 已降级为爆炸图'}
    venv_py = venv_python()
    if venv_py is None:
        return {'dxf': None, 'png': None, 'note': '缺 .venv-cad, 无法出装配工程图'}
    aj = out / 'assembly.json'
    if not aj.exists():
        return {'dxf': None, 'png': None, 'note': '缺 assembly.json'}
    # ASCII 文件名 (Windows 编码坑): 中文标题写进 assembly.json, 不在文件名里
    dxf = out / 'assembly_drawing.dxf'
    png = out / 'assembly_drawing.png'
    env = {**__import__('os').environ, 'PYTHONUTF8': '1'}
    cmd = [str(venv_py), str(DRAWING), str(aj), str(step_path),
           '--out', str(dxf), '--png', str(png)]
    cr = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                        encoding='utf-8', errors='replace', env=env)
    if cr.returncode == 0 and dxf.exists():
        return {'dxf': str(dxf),
                'png': str(png) if png.exists() else None,
                'note': '装配工程图: 全剖主视图+俯视图, 齿轮GB/T 4459.2简化画法'}
    return {'dxf': None, 'png': None,
            'note': f'装配工程图失败: {(cr.stderr or cr.stdout)[-300:]}'}
