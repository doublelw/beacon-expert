"""Beacon专家 - 部署运维监控 (Phase 7).

health_check: 健康检查 (freecadcmd/磁盘/LLM)
log_conversion: 转换任务日志 (JSONL)
cleanup_work_dir: 清理过期任务工作目录
"""
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from src.config import FC_BIN, TASKS_DIR, DATA_DIR, LLM_PROVIDERS


def health_check() -> dict:
    """综合健康检查.

    Returns:
        dict with keys:
            - status: ok/degraded
            - freecadcmd_ok: bool
            - disk_free_mb: int
            - llm_ok: bool (是否配置了至少一个 LLM provider key)
            - tasks_count: 当前任务目录数
            - timestamp: ISO8601
    """
    # FreeCAD
    fc_ok = (
        shutil.which("freecadcmd") is not None
        or (FC_BIN and os.path.isfile(FC_BIN))
    )

    # 磁盘
    try:
        disk_free_mb = shutil.disk_usage("/").free // (1024 * 1024)
    except OSError:
        disk_free_mb = -1

    # LLM: 检查是否有 API key 环境变量 (沿用 config 的 provider 名)
    llm_env_keys = [
        "ZHIPU_API_KEY", "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
    ]
    llm_ok = any(os.getenv(k) for k in llm_env_keys) or bool(LLM_PROVIDERS)

    # 任务目录数
    try:
        tasks_count = sum(
            1 for p in TASKS_DIR.iterdir() if p.is_dir()
        ) if TASKS_DIR.exists() else 0
    except OSError:
        tasks_count = -1

    degraded = (not fc_ok) or disk_free_mb < 1024  # <1GB 警戒
    return {
        "status": "degraded" if degraded else "ok",
        "freecadcmd_ok": fc_ok,
        "freecadcmd_path": FC_BIN,
        "disk_free_mb": disk_free_mb,
        "llm_ok": llm_ok,
        "tasks_count": tasks_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def log_conversion(task_id: str, status: str, error: str = "") -> str:
    """记录转换任务日志 (追加 JSONL).

    日志路径: data/logs/conversion.jsonl
    每行一条 JSON: {task_id, status, error, ts}

    Args:
        task_id: 任务 UUID
        status: queued/classifying/projecting/rendering/auditing/done/failed/timeout/degraded
        error: 错误信息 (可选)

    Returns:
        日志文件路径
    """
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "conversion.jsonl"

    record = {
        "task_id": task_id,
        "status": status,
        "error": error or "",
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return str(log_path)


def cleanup_work_dir(tasks_dir: Optional[Path] = None, max_age_days: int = 7) -> dict:
    """清理过期任务工作目录.

    扫描 tasks_dir 下所有子目录, 删除 mtime 早于 max_age_days 的目录。

    Args:
        tasks_dir: 任务目录 (默认 config.TASKS_DIR)
        max_age_days: 最大保留天数

    Returns:
        dict: {deleted: [...], skipped: int, freed_mb: float}
    """
    tasks_dir = Path(tasks_dir) if tasks_dir else TASKS_DIR
    if not tasks_dir.exists():
        return {"deleted": [], "skipped": 0, "freed_mb": 0.0}

    cutoff = time.time() - max_age_days * 86400
    deleted = []
    freed_bytes = 0
    skipped = 0

    for entry in tasks_dir.iterdir():
        if not entry.is_dir():
            skipped += 1
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            skipped += 1
            continue
        if mtime < cutoff:
            # 计算目录总大小
            dir_size = sum(
                f.stat().st_size for f in entry.rglob("*") if f.is_file()
            )
            try:
                shutil.rmtree(entry)
                deleted.append(str(entry))
                freed_bytes += dir_size
            except OSError:
                skipped += 1
        else:
            skipped += 1

    return {
        "deleted": deleted,
        "deleted_count": len(deleted),
        "skipped": skipped,
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "cutoff_days": max_age_days,
    }
