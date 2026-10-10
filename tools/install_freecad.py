#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FreeCAD 自动安装检测与安装脚本

Beacon 3D→2D pipeline 需要 FreeCAD 进行 STP→DXF 转换。
本脚本检测 FreeCAD 是否已安装, 如未安装则尝试自动安装。

用法:
    python install_freecad.py          # 自动检测并尝试安装
    python install_freecad.py --skip   # 跳过, 仅输出安装指南
"""
import sys
import os
import platform
import subprocess
from pathlib import Path


def find_freecad() -> str | None:
    """搜索 FreeCAD 可执行文件"""
    # 检查 PATH
    for exe_name in ['freecadcmd', 'FreeCADCmd', 'FreeCAD']:
        try:
            result = subprocess.run(['where', exe_name], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip().split('\n')[0]
        except:
            pass

    # 常见安装路径 (Windows)
    common_paths = [
        r'C:\Program Files\FreeCAD 0.21\bin\FreeCADCmd.exe',
        r'C:\Program Files\FreeCAD 0.21\bin\freecadcmd.exe',
        r'C:\Program Files (x86)\FreeCAD\bin\FreeCADCmd.exe',
        r'C:\Program Files (x86)\FreeCAD\bin\freecadcmd.exe',
        r'C:\FreeCAD\bin\FreeCADCmd.exe',
    ]
    for p in common_paths:
        if os.path.exists(p):
            return p

    return None


def install_via_chocolatey() -> bool:
    """通过 Chocolatey 安装 FreeCAD"""
    try:
        result = subprocess.run(
            ['choco', 'install', 'freecad', '-y'],
            capture_output=True, text=True, timeout=300
        )
        return result.returncode == 0
    except Exception as e:
        print(f'Chocolatey 安装失败: {e}')
        return False


def install_via_winget() -> bool:
    """通过 Winget 安装 FreeCAD"""
    try:
        result = subprocess.run(
            ['winget', 'install', 'FreeCAD.FreeCAD'],
            capture_output=True, text=True, timeout=300
        )
        return result.returncode == 0
    except Exception as e:
        print(f'Winget 安装失败: {e}')
        return False


def get_install_guide() -> str:
    """返回手动安装指南"""
    guide = """
========================================
FreeCAD 安装指南 (Windows)
========================================

方法1: Chocolatey (推荐, 需管理员权限)
  以管理员身份打开 PowerShell, 运行:
    choco install freecad -y

方法2: Winget (Windows 10/11 自带)
  以管理员身份打开 PowerShell, 运行:
    winget install FreeCAD.FreeCAD

方法3: 手动下载安装
  1. 访问 https://www.freecad.org/get-started
  2. 下载 Windows Installer (.exe)
  3. 运行安装, 记住安装路径
  4. 将 FreeCAD/bin 添加到 PATH:
     系统属性 → 环境变量 → Path → 编辑
     添加: C:\\Program Files\\FreeCAD 0.21\\bin

验证安装:
  freecadcmd --version

Beacon 要求: FreeCAD >= 0.21
"""
    return guide


def main():
    print('=' * 50)
    print('Beacon FreeCAD 安装检测')
    print('=' * 50)

    freecad = find_freecad()
    if freecad:
        print(f'[OK] FreeCAD 已安装: {freecad}')
        return 0
    else:
        print('[WARN] FreeCAD 未找到')

    print()
    print('正在尝试自动安装...')

    # 尝试 Chocolatey
    if shutil.which('choco'):
        print('  检测到 Chocolatey, 尝试安装...')
        if install_via_chocolatey():
            print('[OK] Chocolatey 安装成功')
            return 0
        else:
            print('[FAIL] Chocolatey 安装失败')

    # 尝试 Winget
    if shutil.which('winget'):
        print('  检测到 Winget, 尝试安装...')
        if install_via_winget():
            print('[OK] Winget 安装成功')
            return 0
        else:
            print('[FAIL] Winget 安装失败')

    # 都需要管理员权限
    print()
    print(get_install_guide())
    return 1


if __name__ == '__main__':
    import shutil
    sys.exit(main())
