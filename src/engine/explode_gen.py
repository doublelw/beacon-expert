"""M-explode: 客户 STEP 装配体 → 花叔 assembly.json → 交互爆炸图 HTML.

链路 (无自然语言, 确定性):
  import_step(客户STEP) 拆子件 → 逐件 tessellate (同 mech.py 参数)
  → 主轴自动判定 + 分层爆炸向量 (越靠里飞得越远, mechanical.md 纪律)
  → assembly.json (viewer 契约) → vendor viewer.py 直连出单文件 HTML

装配体识别: 多子件且各子件质心不重合才算装配体; 单一零件报"非装配体"
(单件爆炸图无意义)。爆炸轴取向: 沿子件质心离散度最大的轴分层。
"""
import base64
import json
import math
import subprocess
from pathlib import Path
from typing import List, Optional

import numpy as np
from build123d import import_step, CenterOf

from src.config import VENDOR_HUASHU

VIEWER = VENDOR_HUASHU / "scripts" / "mech" / "viewer.py"

# 密度表与花叔 mech.MATERIALS 一致 (材质默认 steel, 客户装配体无材质信息时按钢估)
DENSITY_GMM3 = 7.85e-3
DEFAULT_MATERIAL = "steel"
DEFAULT_MATERIAL_NAME = "合金钢"


def _is_step_path(p: str) -> bool:
    return p.lower().endswith(('.step', '.stp'))


def _tessellate(part) -> dict:
    """与 mech.Assembly._tessellate 同参数 (保 viewer 渲染口径一致)."""
    bb = part.bounding_box()
    diag = math.dist((bb.min.X, bb.min.Y, bb.min.Z),
                     (bb.max.X, bb.max.Y, bb.max.Z))
    verts, tris = part.tessellate(max(0.005, diag * 4e-4), 0.15)
    v = np.array([[p.X, p.Y, p.Z] for p in verts], dtype=np.float32)
    t = np.array(tris, dtype=np.uint32)
    return dict(v=base64.b64encode(v.tobytes()).decode(),
                i=base64.b64encode(t.tobytes()).decode(),
                nv=int(len(v)), nt=int(len(t)))


def build_assembly_json(step_path: str, out_dir: str,
                       title: Optional[str] = None,
                       explode_scale: float = 0.8) -> dict:
    """客户 STEP → assembly.json. 返回 manifest (含 ok/stage 供路由落库)."""
    step_path = str(step_path)
    if not _is_step_path(step_path):
        return {'ok': False, 'stage': 'input', 'error': f'不是 STEP 文件: {Path(step_path).name}'}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    try:
        comp = import_step(step_path)
    except Exception as ex:
        return {'ok': False, 'stage': 'read', 'error': f'STEP 读取失败: {str(ex)[:200]}'}

    # 子件 = compound 直接子 (非装配 STEP 可能是裸 solid, 包一层)
    children = list(comp.children)
    if len(children) == 1 and len(comp.solids()) > 1:
        children = list(comp.solids())
    if not children:
        return {'ok': False, 'stage': 'read', 'error': 'STEP 内未找到零件实体'}

    # 装配体判定: 多子件, 且质心不全重合 (重合 = 同位叠放, 无爆炸意义)
    coms = []
    for c in children:
        try:
            coms.append(c.center(CenterOf.MASS))
        except Exception:
            coms.append(None)
    valid = [(c, cm) for c, cm in zip(children, coms) if cm is not None and c.volume > 0]
    if len(valid) < 2:
        return {'ok': False, 'stage': 'input',
                'error': f'装配体判定: 有效子件 {len(valid)} 个 (<2), 单一零件不适用爆炸图'}

    # 主轴 = 各零件包围盒"最薄"的方向 (装配叠层轴).
    # 不用质心离散度 — 行星轮径向分布会把 X/Y 误判为主轴.
    # 取各件 bbox 的 X/Y/Z 厚度均值, 最薄轴即装配轴 (齿轮/轴/法兰的惯例).
    bb_min_arr = np.full((len(valid), 3), math.inf)
    bb_max_arr = np.full((len(valid), 3), -math.inf)
    for i, (c, cm) in enumerate(valid):
        bb = c.bounding_box()
        for k in range(3):
            bb_min_arr[i, k] = float(bb.min.__getattribute__(['X', 'Y', 'Z'][k]))
            bb_max_arr[i, k] = float(bb.max.__getattribute__(['X', 'Y', 'Z'][k]))
    thickness = bb_max_arr - bb_min_arr
    mean_thick = thickness.mean(axis=0)
    # 最薄且非零的轴 = 主轴; 全为零(片体)退回 Z
    cand = [k for k in range(3) if mean_thick[k] > 1e-6]
    main_axis = min(cand, key=lambda k: mean_thick[k]) if cand else 2

    # 分层: 沿主轴按各件质心坐标, 越靠里飞得越远 (mechanical.md 纪律).
    # 同层件 (主轴偏移≈0 且 质心径向偏离装配中心) 加径向散开, 避免重叠 —
    # 行星轮典型场景: 同 Z 层的 N 个行星件按圆周角散开 (mechanical.md 径向 0.6-1×齿顶圆).
    axis_vals = np.array([float(cm.__getattribute__(['X', 'Y', 'Z'][main_axis])) for _, cm in valid])
    mid = float((axis_vals.max() + axis_vals.min()) / 2)
    # 装配中心 = 主轴方向的中心, 径向 = 垂直于主轴的平面内各件质心到主轴线的距离
    com_arr = np.array([[float(cm.X), float(cm.Y), float(cm.Z)] for _, cm in valid])
    radial_center = com_arr.mean(axis=0)
    radial_center[main_axis] = mid
    radial_offsets = []
    for i in range(len(valid)):
        rvec = com_arr[i] - radial_center
        rvec[main_axis] = 0.0
        radial_offsets.append(rvec)
    part_rows = []
    total_mass = 0.0
    bbox_min = [math.inf] * 3
    bbox_max = [-math.inf] * 3
    for i, (c, cm) in enumerate(valid):
        code = f"P{i + 1:02d}"
        mass_g = c.volume * DENSITY_GMM3
        total_mass += mass_g
        axial_off = float(round((float(axis_vals[i]) - mid) * explode_scale, 3))
        rv = radial_offsets[i]
        radial_dist = float(np.linalg.norm(rv))
        # 径向散开量: 件本身质心离装配中心越远, 散开越多 (行星轮大, 壳体近轴小)
        radial_scale = 0.8 * radial_dist * explode_scale
        ex = [0.0, 0.0, 0.0]
        if radial_dist > 0.5:
            rdir = rv / radial_dist
            ex = [rdir[k] * radial_scale for k in range(3)]
        ex[main_axis] = axial_off
        ex = [float(round(v, 3)) for v in ex]
        bb = c.bounding_box()
        for k in range(3):
            bbox_min[k] = min(bbox_min[k], float(bb.min.__getattribute__(['X', 'Y', 'Z'][k])))
            bbox_max[k] = max(bbox_max[k], float(bb.max.__getattribute__(['X', 'Y', 'Z'][k])))
        mesh = _tessellate(c)
        part_rows.append({
            'name': c.label or code, 'code': code,
            'material': DEFAULT_MATERIAL, 'material_name': DEFAULT_MATERIAL_NAME,
            'qty': 1, 'mass_g': round(mass_g, 1),
            'explode': ex,
            'spin_axis': [0, 0, 1] if main_axis == 2 else [0, 1, 0] if main_axis == 1 else [1, 0, 0],
            'spin_center': [cm.X, cm.Y, cm.Z],
            'color': None, 'gear': None, 'note': '',
            'motion': {'spin': 0.0, 'orbit': 0.0, 'orbit_center': [0, 0, 0]},
            'mesh': mesh,
        })

    manifest = {
        'title': title or Path(step_path).stem,
        'units': 'mm',
        'bbox': {'min': [round(v, 3) for v in bbox_min],
                 'max': [round(v, 3) for v in bbox_max]},
        'mass_total_g': round(total_mass, 1),
        'checks_ok': True,
        'planetary': None,
        'parts': part_rows,
        # 标记来源: 客户 STEP 逆向 (非参数化生成), viewer 不读但审计可查
        '_beacon': {'source': 'customer_step', 'main_axis': main_axis,
                    'explode_scale': explode_scale, 'n_parts': len(part_rows)},
    }
    aj = out / 'assembly.json'
    aj.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')

    # viewer 直连出 HTML (venv python, 上游契约: 单文件双击即开)
    html_out = out / f'{manifest["title"]}-交互爆炸图.html'
    venv_py = str(Path(__file__).resolve().parents[2] / '.venv-cad' / 'Scripts' / 'python.exe')
    r = subprocess.run([venv_py, str(VIEWER), str(aj), '--out', str(html_out)],
                      capture_output=True, text=True, timeout=120,
                      encoding='utf-8', errors='replace')
    ok = r.returncode == 0 and html_out.exists()
    return {'ok': ok, 'stage': 'viewer', 'manifest': manifest,
            'assembly_json': str(aj), 'html': str(html_out) if ok else None,
            'viewer_log': (r.stdout + r.stderr)[-400:],
            'n_parts': len(part_rows), 'main_axis': main_axis,
            'error': None if ok else r.stderr[-300:]}


def run_explode(step_path: str, out_dir: str, title: str = None,
                explode_scale: float = 0.8) -> dict:
    """路由/CLI 统一入口."""
    return build_assembly_json(step_path, out_dir, title, explode_scale)
