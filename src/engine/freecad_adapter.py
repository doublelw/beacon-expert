"""FreeCAD子进程封装: 跨平台 + 隔离 + 超时.

通过 freecadcmd 执行独立脚本, 每次任务分配独立 HOME 目录避免全局状态污染,
支持 timeout 秒级超时与异步并发. 返回统一的 {stdout, stderr, returncode} 字典.
"""
import asyncio
import os
import shutil
import uuid

from src.config import FC_BIN, TASKS_DIR


async def run_freecad(script_path: str, env_extras: dict = None, timeout: int = 600) -> dict:
    """异步执行 freecadcmd 脚本.

    Args:
        script_path: 要执行的 .py 脚本绝对路径 (freecadcmd 解释执行).
        env_extras: 注入子进程的额外环境变量 (如 STP / OUT).
        timeout: 超时秒数, 超时后 kill 子进程.

    Returns:
        {stdout, stderr, returncode}; 超时 returncode=-1, stderr 提示超时.
    """
    env_extras = env_extras or {}
    task_home = TASKS_DIR / f"fc_home_{uuid.uuid4().hex[:8]}"
    task_home.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HOME": str(task_home), **env_extras}

    proc = await asyncio.create_subprocess_exec(
        FC_BIN, script_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env, cwd=str(task_home),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
            "returncode": proc.returncode,
        }
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"stdout": "", "stderr": f"Timeout after {timeout}s", "returncode": -1}
    finally:
        shutil.rmtree(task_home, ignore_errors=True)
