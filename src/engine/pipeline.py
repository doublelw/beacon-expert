"""async pipeline: M0→M8 编排, work_dir 隔离, 超时分级, 断点续跑.

串联 veritas → ai_plan → projection → render → verify → eval 六阶段,
每阶段独立的 work_dir 隔离, 失败立即返回已完成阶段结果.
Semaphore 限制单机并发 (默认 2), 避免同时跑爆 FreeCAD.
"""
import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from src.config import TASKS_DIR, SAAS_CORE
from src.engine.freecad_adapter import run_freecad

# 单机并发上限 (FreeCAD 重量级, 2-4 合理)
_semaphore = asyncio.Semaphore(2)

# 阶段元信息 (脚本相对 SAAS_CORE, 超时秒)
STAGES = {
    "veritas":    {"script": "veritas.py",     "env_key": "OUT", "timeout": 120},
    "ai_plan":    {"script": "gen_ai_plan.py", "env_key": "OUT", "timeout": 30},
    "projection": {"script": "projection_v3.py", "env_key": "OUT", "timeout": 600},
    "render":     {"mode": "python", "timeout": 60},
    "verify":     {"script": "verify.py", "env_key": None, "timeout": 30},
    "eval":       {"script": "eval.py", "env_key": None, "timeout": 30},
}


def _truncate(s: str, n: int = 200) -> str:
    return s if len(s) <= n else s[:n]


async def _run_python(script_path: str, args: list, cwd: str, timeout: int) -> dict:
    """同步 python 脚本丢到默认 executor 跑, 截断输出."""
    try:
        r = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["python3", script_path, *args],
                capture_output=True, timeout=timeout, cwd=cwd,
                env={**os.environ, "PYTHONPATH": str(SAAS_CORE.parent)},
            ),
        )
        return {
            "returncode": r.returncode,
            "stdout": r.stdout.decode(errors="replace"),
            "stderr": r.stderr.decode(errors="replace"),
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": f"Timeout after {timeout}s"}


async def run_pipeline(stp_path: str, work_dir: str = None) -> dict:
    """端到端转换: STP → DXF.

    Args:
        stp_path: 输入 .stp/.step 绝对路径.
        work_dir: 工作目录 (None 则自动生成); 各阶段产物写在此处.

    Returns:
        {status, dxf_path, results, work_dir, error?}
        status: done (DXF 生成) / partial (有产物但无 DXF) / failed (前序阶段失败).
    """
    async with _semaphore:
        work_dir = work_dir or str(TASKS_DIR / uuid.uuid4().hex)
        os.makedirs(work_dir, exist_ok=True)
        # gen_ai_plan.py 用硬编码 output/ 相对路径, 必须建好
        out_subdir = Path(work_dir) / "output"
        out_subdir.mkdir(parents=True, exist_ok=True)

        results: dict = {}

        # === Stage 1: veritas (freecadcmd) ===
        veritas_out = f"{work_dir}/veritas.json"
        r = await run_freecad(
            str(SAAS_CORE / "veritas.py"),
            {"STP": stp_path, "OUT": veritas_out},
            timeout=STAGES["veritas"]["timeout"],
        )
        results["veritas"] = r
        if r["returncode"] != 0 or not os.path.exists(veritas_out):
            return {
                "status": "failed", "stage": "veritas",
                "error": _truncate(r.get("stderr", "")), "results": results,
                "work_dir": work_dir,
            }

        # === Stage 2: ai_plan (python, 硬编码 output/ 相对路径) ===
        # gen_ai_plan.py 读 output/veritas.json, 写 output/ai_plan.json
        ai_plan_out = f"{work_dir}/output/ai_plan.json"
        # 复制 veritas.json → output/veritas.json 供 ai_plan 读取
        import shutil as _sh
        _sh.copy(veritas_out, f"{work_dir}/output/veritas.json")
        r2 = await _run_python(
            str(SAAS_CORE / "gen_ai_plan.py"), [], cwd=work_dir,
            timeout=STAGES["ai_plan"]["timeout"],
        )
        results["ai_plan"] = r2
        # ai_plan 失败不致命, render 可降级

        # === Stage 3: projection (freecadcmd) ===
        proj_out = f"{work_dir}/proj_v3.json"
        r3 = await run_freecad(
            str(SAAS_CORE / "projection_v3.py"),
            {"STP": stp_path, "OUT": proj_out},
            timeout=STAGES["projection"]["timeout"],
        )
        results["projection"] = r3
        if r3["returncode"] != 0 or not os.path.exists(proj_out):
            return {
                "status": "failed", "stage": "projection",
                "error": _truncate(r3.get("stderr", "")), "results": results,
                "work_dir": work_dir,
            }

        # === Stage 4: render (python, 直接函数调用) ===
        dxf_path = f"{work_dir}/output.dxf"
        try:
            if str(SAAS_CORE.parent) not in sys.path:
                sys.path.insert(0, str(SAAS_CORE.parent))
            from src.engine.render_engine import render
            proj = json.load(open(proj_out))
            plan_data = json.load(open(ai_plan_out)) if os.path.exists(ai_plan_out) else None
            geom_path = f"{work_dir}/clean_geom.json"
            geom = json.load(open(geom_path)) if os.path.exists(geom_path) else None
            report = render(proj, plan_data, None, geom, dxf_path)
            results["render"] = {"status": "ok", "report": report}
        except Exception as ex:  # noqa: BLE001
            results["render"] = {"status": "error", "error": _truncate(str(ex))}

        # === Stage 5: verify (python, sys.argv: proj veritas out) ===
        try:
            vout = f"{work_dir}/verify_report.json"
            vr = await _run_python(
                str(SAAS_CORE / "verify.py"),
                [proj_out, veritas_out, vout], cwd=work_dir,
                timeout=STAGES["verify"]["timeout"],
            )
            results["verify"] = json.load(open(vout)) if os.path.exists(vout) else {
                "returncode": vr["returncode"], "stderr": _truncate(vr["stderr"]),
            }
        except Exception as ex:  # noqa: BLE001
            results["verify"] = {"error": _truncate(str(ex))}

        # === Stage 6: eval (python, sys.argv: dxf veritas plan — 无 json 输出) ===
        try:
            er = await _run_python(
                str(SAAS_CORE / "eval.py"),
                [dxf_path, veritas_out, ai_plan_out], cwd=work_dir,
                timeout=STAGES["eval"]["timeout"],
            )
            # eval.py 仅 print 到 stdout, 不写 json; 保留 stdout 摘要
            results["eval"] = {
                "returncode": er["returncode"],
                "stdout": _truncate(er["stdout"], 800),
                "stderr": _truncate(er["stderr"]),
            }
        except Exception as ex:  # noqa: BLE001
            results["eval"] = {"error": _truncate(str(ex))}

        dxf_exists = os.path.exists(dxf_path)
        return {
            "status": "done" if dxf_exists else "partial",
            "dxf_path": dxf_path if dxf_exists else None,
            "results": results,
            "work_dir": work_dir,
        }
