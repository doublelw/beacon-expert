"""ZWCAD COM 远程适配器 — 通过SSH在S5上用ZWCAD COM渲染STEP→DXF

架构:
  Mac(本地) → SSH → S5(Windows+ZWCAD 2026) → COM → DXF输出 → SCP回Mac

用法:
  from engine.zwcad_adapter import run_zwcad_render
  result = await run_zwcad_render(
      stp_path="/path/to/part.stp",
      output_dir="/tmp/output",
      ssh_host="frp-c2061",
      zwcad_path=r"D:\Program Files\zwsoft\ZWCAD 2026",
      s5_workdir=r"D:\beacon_render"
  )
"""
import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# S5 SSH别名(从~/.ssh/config)
DEFAULT_SSH_HOST = "frp-c2061"
# ZWCAD在S5上的安装路径
DEFAULT_ZWCAD_PATH = r"D:\Program Files\zwsoft\ZWCAD 2026"
# S5上的工作目录
DEFAULT_S5_WORKDIR = r"D:\beacon_render"
# 超时(秒)
DEFAULT_TIMEOUT = 120


# S5上执行的Python COM脚本模板
_ZWCAD_RENDER_SCRIPT = r'''
import sys, os, time
import win32com.client as wc

stp_path = sys.argv[1]
dxf_path = sys.argv[2]

print(f"ZWCAD render: {stp_path} -> {dxf_path}")

# 连接ZWCAD(优先已有实例,否则启动新的)
try:
    app = wc.GetActiveObject("ZWCAD.Application")
    print("Connected to existing ZWCAD")
except:
    app = wc.Dispatch("ZWCAD.Application")
    print("Started new ZWCAD instance")

app.Visible = 0  # 无头模式

try:
    # 方法1: 直接打开STEP(ZWCAD支持3D格式导入)
    doc = app.Documents.Open(stp_path)
    print(f"Opened: {doc.Name}")

    # 等待导入完成
    time.sleep(3)

    # 获取模型空间
    ms = doc.ModelSpace
    print(f"ModelSpace entities: {ms.Count}")

    # 方法A: 用FLATSHOT创建2D投影(等效于HLR)
    # 设置当前视图为前视
    app.ActiveDocument.SendCommand("_-VIEW\n_FRONT\n")
    time.sleep(1)

    # FLATSHOT: 将3D投影为2D块
    app.ActiveDocument.SendCommand(
        "FLATSHOT\n"
        f"{dxf_path}.dwg\n"  # 输出文件
        "N\n"  # 不替换
        "\n"  # 默认
    )
    time.sleep(2)

    # 方法B: 用VIEWBASE创建标准三视图
    # app.ActiveDocument.SendCommand("VIEWBASE\n")
    # time.sleep(3)

    # 保存为DXF
    doc.SaveAs(dxf_path, "acDXF")
    print(f"Saved DXF: {dxf_path}")

    # 验证DXF文件
    if os.path.exists(dxf_path):
        size = os.path.getsize(dxf_path)
        print(f"DXF size: {size} bytes")
        if size < 1000:
            print("WARNING: DXF too small, may be empty")
    else:
        print("ERROR: DXF not created")
        sys.exit(1)

    doc.Close(False)

except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
finally:
    app.Visible = 1
    # 不退出ZWCAD(保持实例供下次使用)
'''


def _ssh_run(host: str, command: str, timeout: int = DEFAULT_TIMEOUT) -> tuple:
    """SSH执行命令,返回(stdout, returncode)"""
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", host, command],
        capture_output=True, text=True, timeout=timeout
    )
    return proc.stdout.strip(), proc.returncode


def _scp_upload(host: str, local: str, remote: str) -> bool:
    """SCP上传文件"""
    proc = subprocess.run(
        ["scp", "-o", "ConnectTimeout=10", local, f"{host}:{remote}"],
        capture_output=True, timeout=60
    )
    return proc.returncode == 0


def _scp_download(host: str, remote: str, local: str) -> bool:
    """SCP下载文件"""
    proc = subprocess.run(
        ["scp", "-o", "ConnectTimeout=10", f"{host}:{remote}", local],
        capture_output=True, timeout=60
    )
    return proc.returncode == 0


async def run_zwcad_render(
    stp_path: str,
    output_dir: str,
    ssh_host: str = DEFAULT_SSH_HOST,
    s5_workdir: str = DEFAULT_S5_WORKDIR,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """远程调用S5上ZWCAD COM渲染STEP→DXF

    Returns: {"success": bool, "dxf_path": str, "error": str|None}
    """
    stp_name = Path(stp_path).stem
    remote_stp = f"{s5_workdir}\\{stp_name}.stp"
    remote_dxf = f"{s5_workdir}\\{stp_name}.dxf"
    remote_script = f"{s5_workdir}\\zwcad_render.py"
    local_dxf = os.path.join(output_dir, f"{stp_name}_zwcad.dxf"

    try:
        # 1. 确保S5工作目录存在
        _ssh_run(ssh_host, f'powershell -Command "New-Item -ItemType Directory -Force -Path {s5_workdir} | Out-Null"')

        # 2. 上传STEP文件
        if not _scp_upload(ssh_host, stp_path, remote_stp):
            return {"success": False, "dxf_path": None, "error": "SCP upload STEP failed"}

        # 3. 上传COM渲染脚本
        script_local = os.path.join(tempfile.gettempdir(), "zwcad_render.py")
        with open(script_local, "w") as f:
            f.write(_ZWCAD_RENDER_SCRIPT)
        _scp_upload(ssh_host, script_local, remote_script)

        # 4. 执行ZWCAD COM渲染
        run_cmd = f'python {remote_script} "{remote_stp}" "{remote_dxf}"'
        stdout, rc = _ssh_run(ssh_host, run_cmd, timeout=timeout)
        logger.info(f"ZWCAD render output: {stdout}")

        if rc != 0:
            return {"success": False, "dxf_path": None, "error": f"Render failed: {stdout}"}

        # 5. 下载DXF
        if not _scp_download(ssh_host, remote_dxf, local_dxf):
            return {"success": False, "dxf_path": None, "error": "SCP download DXF failed"}

        # 6. 验证DXF
        if not os.path.exists(local_dxf) or os.path.getsize(local_dxf) < 1000:
            return {"success": False, "dxf_path": local_dxf, "error": "DXF too small or empty"}

        return {"success": True, "dxf_path": local_dxf, "error": None}

    except Exception as e:
        logger.exception("ZWCAD render failed")
        return {"success": False, "dxf_path": None, "error": str(e)}


def test_zwcad_connection(ssh_host: str = DEFAULT_SSH_HOST) -> bool:
    """测试S5上ZWCAD COM是否可用(同步)
    在S5上运行: python -c "import win32com.client; app=wc.Dispatch('ZWCAD.Application'); print(app.Version)"
    """
    cmd = (
        'python -c '
        '"import win32com.client as wc; app=wc.Dispatch(\'ZWCAD.Application\'); '
        'print(\'ZWCAD_VERSION:\', app.Version); app.Quit()"'
    )
    stdout, rc = _ssh_run(ssh_host, cmd, timeout=30)
    if rc == 0 and 'ZWCAD_VERSION' in stdout:
        logger.info(f"ZWCAD COM OK: {stdout}")
        return True
    logger.error(f"ZWCAD COM test failed: rc={rc} stdout={stdout}")
    return False
