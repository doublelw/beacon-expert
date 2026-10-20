"""B线 Phase 3 + C线改图: 自然语言→3D参数件→GB工程图 API.

POST /api/cad3d/generate  {prompt} → LLM写build123d构建体 → STEP(回读验证)
  → veritas→标注→六视图HLR投影→GB渲染 全管线
POST /api/cad3d/modify/{task_id}  {instructions} → 改参数源重出图+前后对比
GET  /api/cad3d/status/{id} / download/{id}?type=dxf|png|step|params|report|compare
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
from src.database import get_db, SessionLocal, Cad3dTask, User

router = APIRouter(prefix="/api/cad3d", tags=["自然语言3D出图"])


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=4, max_length=4000)


class ModifyRequest(BaseModel):
    instructions: str = Field(..., min_length=2, max_length=2000,
                              description="修改指令, 如: 宽度改成150, 中央孔改φ32")


@router.post("/generate")
async def generate(req: GenerateRequest, background_tasks: BackgroundTasks,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task_id = uuid.uuid4().hex
    work_dir = str(TASKS_DIR / task_id)
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    db.add(Cad3dTask(id=task_id, user_id=user.id, prompt=req.prompt,
                     status="queued", work_dir=work_dir))
    db.commit()
    background_tasks.add_task(_run_generation, task_id)
    return {"task_id": task_id, "status": "queued"}


def _set(db, task, **kw):
    for k, v in kw.items():
        setattr(task, k, v)
    task.updated_at = datetime.now(timezone.utc)
    db.commit()


def _run_generation(task_id: str):
    """后台: LLM构建体 → STEP验证 → 3D→2D全管线 (线程池, 事件循环内跑async规划)."""
    db = SessionLocal()
    task = db.query(Cad3dTask).filter(Cad3dTask.id == task_id).first()
    try:
        from src.engine.cad3d_gen import venv_available, run_3d_pipeline
        if not venv_available():
            _set(db, task, status="failed",
                 error="构建环境缺失: 先运行 python tools/install_cad_env.py")
            return
        _set(db, task, status="planning")
        from src.engine.cad3d_plan import plan_3d, repair_3d
        code, meta = asyncio.run(plan_3d(task.prompt))
        _set(db, task, llm_provider=(meta.get('llm') or {}).get('provider'),
             llm_model=(meta.get('llm') or {}).get('model'),
             llm_attempts=meta.get('attempts', 0))
        if code is None:
            _set(db, task, status="failed", error='; '.join(meta['errors'][:5]))
            return

        _set(db, task, status="building")
        r = run_3d_pipeline(code, task.work_dir)
        if not r.get('ok') and r.get('stage') in ('build', 'verify'):
            code2, _m = asyncio.run(repair_3d(task.prompt, code, r.get('error', '')))
            if code2:
                r = run_3d_pipeline(code2, task.work_dir)
        (Path(task.work_dir) / 'report.json').write_text(
            json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
        task.report_path = str(Path(task.work_dir) / 'report.json')
        task.step_path = r.get('step')
        task.params_path = str(Path(task.work_dir) / 'params.json') \
            if (Path(task.work_dir) / 'params.json').exists() else None
        task.dxf_path = r.get('dxf')
        task.png_path = r.get('png')
        if r.get('ok'):
            task.status = "done"
        else:
            task.status = "failed"
            task.error = f"[{r.get('stage')}] {str(r.get('error'))[:400]}"
        _set(db, task)
    except Exception as ex:
        _set(db, task, status="failed", error=f"{type(ex).__name__}: {str(ex)[:400]}")
    finally:
        db.close()


@router.post("/modify/{task_id}")
async def modify(task_id: str, req: ModifyRequest,
                 background_tasks: BackgroundTasks,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """基于已完成任务的参数源改尺寸重出图 (新任务, 保留来源)."""
    src = db.query(Cad3dTask).filter(Cad3dTask.id == task_id).first()
    if not src or src.user_id != user.id:
        raise HTTPException(404, "来源任务不存在")
    if src.status != "done" or not (Path(src.work_dir) / 'build_code.py').exists():
        raise HTTPException(400, f"来源任务无参数源 (status={src.status})")
    new_id = uuid.uuid4().hex
    work_dir = str(TASKS_DIR / new_id)
    db.add(Cad3dTask(id=new_id, user_id=user.id,
                     prompt=f"[改图 {task_id[:8]}] {req.instructions}",
                     status="queued", work_dir=work_dir, source_task_id=task_id))
    db.commit()
    background_tasks.add_task(_run_modify, new_id, src.work_dir, req.instructions)
    return {"task_id": new_id, "source_task_id": task_id, "status": "queued"}


def _run_modify(task_id: str, source_dir: str, instructions: str):
    db = SessionLocal()
    task = db.query(Cad3dTask).filter(Cad3dTask.id == task_id).first()
    try:
        _set(db, task, status="planning")
        from src.engine.cad_modify import run_modify
        r = asyncio.run(run_modify(source_dir, instructions, task.work_dir))
        (Path(task.work_dir) / 'modify_report.json').write_text(
            json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
        task.report_path = str(Path(task.work_dir) / 'modify_report.json')
        task.dxf_path = r.get('dxf')
        task.png_path = r.get('png')
        task.compare_path = r.get('compare_png')
        if r.get('ok'):
            task.status = "done"
        else:
            task.status = "failed"
            task.error = f"[{r.get('stage') or 'modify'}] {str(r.get('error'))[:400]}"
        _set(db, task)
    except Exception as ex:
        _set(db, task, status="failed", error=f"{type(ex).__name__}: {str(ex)[:400]}")
    finally:
        db.close()


@router.get("/status/{task_id}")
async def get_status(task_id: str, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    t = db.query(Cad3dTask).filter(Cad3dTask.id == task_id).first()
    if not t or t.user_id != user.id:
        raise HTTPException(404, "任务不存在")
    out = {"task_id": t.id, "status": t.status, "prompt": t.prompt,
           "source_task_id": t.source_task_id,
           "llm": {"provider": t.llm_provider, "model": t.llm_model,
                   "attempts": t.llm_attempts},
           "dxf": t.dxf_path, "png": t.png_path, "step": t.step_path,
           "compare": t.compare_path, "error": t.error,
           "created_at": str(t.created_at)}
    if t.report_path and Path(t.report_path).exists():
        out["report"] = json.loads(Path(t.report_path).read_text(encoding='utf-8'))
    return out


@router.get("/download/{task_id}")
async def download(task_id: str, type: str = "dxf",
                   user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    t = db.query(Cad3dTask).filter(Cad3dTask.id == task_id).first()
    if not t or t.user_id != user.id:
        raise HTTPException(404, "任务不存在")
    attr = {"dxf": t.dxf_path, "png": t.png_path, "step": t.step_path,
            "params": t.params_path, "report": t.report_path,
            "compare": t.compare_path}.get(type)
    if not attr or not Path(attr).exists():
        raise HTTPException(404, f"产物 {type} 不存在 (status={t.status})")
    media = {"dxf": "application/dxf", "png": "image/png", "step": "application/step",
             "params": "application/json", "report": "application/json",
             "compare": "image/png"}[type]
    return FileResponse(attr, media_type=media, filename=f"{task_id}.{type}")
