"""M-gen API: 自然语言 → 结构化图纸 spec → GB 工程图 DXF.

POST /api/gen2d/generate  {description}  → 创建任务, 后台: LLM规划→确定性出图→验证→预览
GET  /api/gen2d/status/{id}                    → 状态 + 验证报告
GET  /api/gen2d/download/{id}?type=dxf|png|spec|report → 文件下载

分工纪律 (同 B线): LLM 只产 spec (engine.gen2d), DXF 实体由 engine.dxf_gen
确定性绘制 — 质量可回读校验、可复现, 不依赖 LLM 稳定性。
"""
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth import get_current_user
from src.config import TASKS_DIR
from src.database import get_db, SessionLocal, Gen2dTask, User

router = APIRouter(prefix="/api/gen2d", tags=["自然语言出图"])


class GenerateRequest(BaseModel):
    description: str = Field(..., min_length=4, max_length=4000,
                             description="自然语言图纸需求, 如: 画一张120×80安装底板, 四角φ9孔距边15, 中央φ25通孔, 材料Q235")


@router.post("/generate")
async def generate(
    req: GenerateRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """NL → spec → DXF (后台执行, 轮询 status)."""
    task_id = uuid.uuid4().hex
    work_dir = str(TASKS_DIR / task_id)
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    row = Gen2dTask(id=task_id, user_id=user.id, prompt=req.description,
                    status="queued", work_dir=work_dir)
    db.add(row)
    db.commit()
    background_tasks.add_task(_run_generation, task_id)
    return {"task_id": task_id, "status": "queued"}


def _run_generation(task_id: str):
    """后台执行 (线程池): 规划 → 出图 → 验证 → 预览. 状态全程落库."""
    db = SessionLocal()
    task = db.query(Gen2dTask).filter(Gen2dTask.id == task_id).first()
    try:
        task.status = "planning"
        db.commit()
        from src.engine.gen2d import plan_from_nl
        spec, meta = asyncio.run(plan_from_nl(task.prompt))
        task.llm_provider = (meta.get('llm') or {}).get('provider')
        task.llm_model = (meta.get('llm') or {}).get('model')
        task.llm_attempts = meta.get('attempts', 0)
        if spec is None:
            task.status = "failed"
            task.error = '; '.join(meta.get('errors', [])[:6]) or '规划失败'
            db.commit()
            return
        (Path(task.work_dir) / 'spec.json').write_text(
            json.dumps(spec, ensure_ascii=False, indent=1), encoding='utf-8')
        task.spec_path = str(Path(task.work_dir) / 'spec.json')
        task.status = "drawing"
        db.commit()

        from src.engine.dxf_gen import generate, render_preview_png
        r = generate(spec, task.work_dir)
        task.dxf_path = r['dxf_path']
        task.report_path = str(Path(task.work_dir) / 'gen_report.json')
        png_path = str(Path(task.work_dir) / 'preview.png')
        try:
            render_preview_png(r['dxf_path'], png_path, r['sheet'])
            task.png_path = png_path
        except Exception:
            pass
        rep = {'generate': r, 'assumptions': spec.get('assumptions', []),
               'llm': meta.get('llm')}
        Path(task.report_path).write_text(
            json.dumps(rep, ensure_ascii=False, indent=1, default=str),
            encoding='utf-8')
        if r['verify']['ok']:
            task.status = "done"
        else:
            task.status = "failed"
            task.error = '出图验证未通过: ' + json.dumps(r['verify'], ensure_ascii=False)
        task.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as ex:
        task.status = "failed"
        task.error = f"{type(ex).__name__}: {str(ex)[:400]}"
        task.updated_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


def _serialize(t: Gen2dTask) -> dict:
    return {
        "task_id": t.id, "status": t.status, "prompt": t.prompt,
        "llm": {"provider": t.llm_provider, "model": t.llm_model,
                "attempts": t.llm_attempts},
        "dxf": t.dxf_path, "png": t.png_path, "spec": t.spec_path,
        "error": t.error, "created_at": str(t.created_at),
    }


@router.get("/status/{task_id}")
async def get_status(task_id: str, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    t = db.query(Gen2dTask).filter(Gen2dTask.id == task_id).first()
    if not t or t.user_id != user.id:
        raise HTTPException(404, "任务不存在")
    out = _serialize(t)
    if t.report_path and Path(t.report_path).exists():
        out["report"] = json.loads(Path(t.report_path).read_text(encoding='utf-8'))
    return out


@router.get("/download/{task_id}")
async def download(task_id: str, type: str = "dxf",
                   user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    t = db.query(Gen2dTask).filter(Gen2dTask.id == task_id).first()
    if not t or t.user_id != user.id:
        raise HTTPException(404, "任务不存在")
    attr = {"dxf": t.dxf_path, "png": t.png_path,
            "spec": t.spec_path, "report": t.report_path}.get(type)
    if not attr or not Path(attr).exists():
        raise HTTPException(404, f"产物 {type} 不存在 (status={t.status})")
    media = {"dxf": "application/dxf", "png": "image/png",
             "spec": "application/json", "report": "application/json"}[type]
    return FileResponse(attr, media_type=media, filename=f"{task_id}.{type}")
