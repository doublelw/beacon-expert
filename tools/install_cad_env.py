#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""huashu-cad 隔离环境安装与验证脚本 (Phase 1)

B线「自然语言生成图纸」的生成侧栈 (build123d/OCP) 只支持 Python ≤3.13,
主环境 3.14 → 按 vendor/huashu-cad/references/environment.md 用 uv 建
Python 3.12 隔离环境, 版本 pin 沿用上游验证组合。

用法:
    python tools/install_cad_env.py             # 建环境 + doctor + build123d 冒烟验证
    python tools/install_cad_env.py --chromium  # 额外安装 playwright chromium (Phase 5 出片用, 下载量大)

Windows 运行 vendor 脚本 (mech.py / viewer.py 等) 需加 PYTHONUTF8=1,
见 vendor/huashu-cad/VENDORED.md 环境约束一节。
"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENV_DIR = REPO / '.venv-cad'
VENV_PY = VENV_DIR / 'Scripts' / 'python.exe'    # Windows
CAD_IO = REPO / 'vendor' / 'huashu-cad' / 'scripts' / 'cad_io.py'

# 上游 environment.md 的验证组合 (ezdxf/matplotlib 为 macOS 验证值, Windows 3.12 兼容)
PINS = [
    'ezdxf==1.4.4',
    'matplotlib==3.11.2',
    'build123d==0.13.0',
    'py-gearworks==0.0.24',
    'bd_warehouse==0.4.0',
    'playwright',
]

# build123d 冒烟: 30×20×5 板打 φ8 中心通孔 + φ4 偏心孔(Pos 平移验证) → STEP → 读回核对
# (照搬上游 cad_io.py 的 STEP 检查口径: solids/volume/bbox/is_valid)
# 注意 align=BASE = (CENTER, CENTER, MIN): x/y 居中非从零起, 板跨 [-15,15]×[-10,10]
SMOKE_SRC = '''
import sys, math, json
from pathlib import Path
from build123d import Box, Cylinder, Pos, Align, export_step, import_step
BASE = (Align.CENTER, Align.CENTER, Align.MIN)
part = (Box(30, 20, 5, align=BASE)
        - Cylinder(4, 10, align=BASE)                      # 中心 φ8 通孔
        - Pos(-10, -5, 0) * Cylinder(2, 10, align=BASE))   # 偏心 φ4 通孔 (Pos 定位)
out = Path(sys.argv[1])
export_step(part, str(out))
s = import_step(str(out))
bb = s.bounding_box()
expect_vol = 30 * 20 * 5 - math.pi * 16 * 5 - math.pi * 4 * 5
report = {
    'solids': len(s.solids()),
    'volume': round(s.volume, 2),
    'expect_volume': round(expect_vol, 2),
    'bbox': [round(bb.size.X, 2), round(bb.size.Y, 2), round(bb.size.Z, 2)],
    'valid': s.is_valid,
}
ok = (report['solids'] == 1 and report['valid']
      and abs(report['volume'] - expect_vol) < 1.0
      and report['bbox'] == [30.0, 20.0, 5.0])
print(json.dumps({**report, 'ok': ok}, ensure_ascii=False))
sys.exit(0 if ok else 1)
'''


def run(cmd, timeout=600, cwd=REPO):
    print('  $', ' '.join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       timeout=timeout, cwd=str(cwd), encoding='utf-8', errors='replace')
    if r.stdout.strip():
        print('   ', r.stdout.strip().replace('\n', '\n    '))
    if r.returncode != 0:
        print('   STDERR:', r.stderr.strip()[-2000:])
    return r.returncode == 0


def create_venv() -> bool:
    if VENV_PY.exists():
        print(f'[2/4] 隔离环境已存在: {VENV_DIR}')
        return True
    print(f'[1/4] 创建 uv 隔离环境 (Python 3.12): {VENV_DIR}')
    return run(['uv', 'venv', VENV_DIR, '--python', '3.12'])


def install_pkgs() -> bool:
    print(f'[2/4] 安装依赖 (上游验证组合): {" ".join(PINS)}')
    # OCP 轮子 ~200MB, 首次安装较慢
    return run(['uv', 'pip', 'install', '--python', VENV_PY] + PINS, timeout=1200)


def run_doctor() -> bool:
    print('[3/4] 上游 doctor 探测:')
    return run([VENV_PY, CAD_IO, 'doctor'], timeout=120)


def smoke_test() -> bool:
    out_dir = REPO / 'output' / 'cad_smoke'
    out_dir.mkdir(parents=True, exist_ok=True)
    step = out_dir / 'plate_phi8.step'
    smoke_py = out_dir / 'smoke_gen.py'
    smoke_py.write_text(SMOKE_SRC, encoding='utf-8')
    print(f'[4/4] build123d 冒烟验证 (首次 import 需 20-30s): {step}')
    ok = run([VENV_PY, smoke_py, step], timeout=300)
    if ok:
        print(f'    STEP 产物: {step}')
    return ok


def install_chromium() -> bool:
    print('[+] 安装 playwright chromium (render_media 出片用):')
    return run([VENV_PY, '-m', 'playwright', 'install', 'chromium'], timeout=1200)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--chromium', action='store_true',
                    help='额外安装 playwright chromium (下载量大, Phase 5 才需要)')
    a = ap.parse_args()

    results = {}
    steps = [create_venv, install_pkgs, run_doctor, smoke_test]
    if a.chromium:
        steps.append(install_chromium)
    for fn in steps:
        results[fn.__name__] = fn()

    print('\n===== Phase 1 结果 =====')
    for name, ok in results.items():
        print(f'  {name}: {"OK" if ok else "FAIL"}')
    if not all(results.values()):
        sys.exit(1)
    print(f'\n后续使用: {VENV_PY}  (vendor/huashu-cad/scripts/ 下各脚本均以它运行)')


if __name__ == '__main__':
    main()
