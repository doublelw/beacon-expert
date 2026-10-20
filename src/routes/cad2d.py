"""B线 Phase 2: 自然语言生成2D图纸 API.

POST /api/cad2d/generate  {prompt}  → 创建任务, 后台: LLM规划→确定性出图→验证→预览
GET  /api/cad2d/status/{id}                    → 状态 + 验证报告
GET  /api/cad2d/download/{id}?type=dxf|png|spec|report → 文件下载

分工纪律: LLM 只产 spec (cad2d_plan), DXF 实体由 cad2d_gen 确定性绘制。
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
from src.database import get_db, SessionLocal, Cad2dTask, User

router = APIRouter(prefix="/api/cad2d", tags=["自然语言出图"])


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=4, max_length=4000,
                        description="自然语言需求, 如: 画一张120×80安装底板,四角φ9孔距边15")


class ModifyRequest(BaseModel):
    instructions: str = Field(..., min_length=2, max_length=2000,
                              description="修改指令, 如: 宽度改成150, 中央孔改φ32")


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
    row = Cad2dTask(id=task_id, user_id=user.id, prompt=req.prompt,
                    status="queued", work_dir=work_dir)
    db.add(row)
    db.commit()
    background_tasks.add_task(_run_generation, task_id)
    return {"task_id": task_id, "status": "queued"}


@router.post("/modify/{task_id}")
async def modify(task_id: str, req: ModifyRequest,
                 background_tasks: BackgroundTasks,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """基于已完成任务的 spec 改尺寸重出图 (新任务, 保留来源+前后对比)."""
    src = db.query(Cad2dTask).filter(Cad2dTask.id == task_id).first()
    if not src or src.user_id != user.id:
        raise HTTPException(404, "来源任务不存在")
    if src.status != "done" or not (Path(src.work_dir) / 'spec.json').exists():
        raise HTTPException(400, f"来源任务无参数源 (status={src.status})")
    new_id = uuid.uuid4().hex
    work_dir = str(TASKS_DIR / new_id)
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    db.add(Cad2dTask(id=new_id, user_id=user.id,
                     prompt=f"[改图 {task_id[:8]}] {req.instructions}",
                     status="queued", work_dir=work_dir, source_task_id=task_id))
    db.commit()
    background_tasks.add_task(_run_modify, new_id, src.work_dir, req.instructions)
    return {"task_id": new_id, "source_task_id": task_id, "status": "queued"}


def _run_modify(task_id: str, source_dir: str, instructions: str):
    """后台改图: 参数源编辑→确定性重生成→前后对比."""
    db = SessionLocal()
    task = db.query(Cad2dTask).filter(Cad2dTask.id == task_id).first()
    try:
        task.status = "planning"
        db.commit()
        from src.engine.cad_modify import run_modify
        r = asyncio.run(run_modify(source_dir, instructions, task.work_dir))
        (Path(task.work_dir) / 'modify_report.json').write_text(
            json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
        task.report_path = str(Path(task.work_dir) / 'modify_report.json')
        task.dxf_path = r.get('dxf')
        task.png_path = r.get('png')
        if r.get('ok'):
            task.status = "done"
        else:
            task.status = "failed"
            task.error = f"[{r.get('stage') or 'modify'}] {str(r.get('error'))[:400]}"
        task.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as ex:
        task.status = "failed"
        task.error = f"{type(ex).__name__}: {str(ex)[:400]}"
        db.commit()
    finally:
        db.close()


def _run_generation(task_id: str):
    """后台执行 (线程池): 规划 → 出图 → 验证 → 预览. 状态全程落库."""
    db = SessionLocal()
    task = db.query(Cad2dTask).filter(Cad2dTask.id == task_id).first()
    try:
        task.status = "planning"
        db.commit()
        from src.engine.cad2d_plan import plan_from_nl
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

        from src.engine.cad2d_gen import run_2d_pipeline
        r = run_2d_pipeline(spec, task.work_dir)
        task.dxf_path, task.png_path = r['dxf'], r['png']
        task.report_path = str(Path(task.work_dir) / 'report.json')
        if not r['ok']:
            task.status = "failed"
            task.error = '出图验证未通过: ' + '; '.join(r['verify']['gate_findings'][:5])
        else:
            task.status = "done"
        db.commit()
    except Exception as ex:
        task.status = "failed"
        task.error = f"{type(ex).__name__}: {str(ex)[:400]}"
        task.updated_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


def _serialize(t: Cad2dTask) -> dict:
    return {
        "task_id": t.id, "status": t.status, "prompt": t.prompt,
        "source_task_id": t.source_task_id,
        "llm": {"provider": t.llm_provider, "model": t.llm_model,
                "attempts": t.llm_attempts},
        "dxf": t.dxf_path, "png": t.png_path, "spec": t.spec_path,
        "compare": str(Path(t.work_dir) / 'compare.png'),
        "error": t.error, "created_at": str(t.created_at),
    }


@router.get("/status/{task_id}")
async def get_status(task_id: str, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    t = db.query(Cad2dTask).filter(Cad2dTask.id == task_id).first()
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
    t = db.query(Cad2dTask).filter(Cad2dTask.id == task_id).first()
    if not t or t.user_id != user.id:
        raise HTTPException(404, "任务不存在")
    attr = {"dxf": t.dxf_path, "png": t.png_path, "spec": t.spec_path,
            "report": t.report_path,
            "compare": str(Path(t.work_dir) / 'compare.png')}.get(type)
    if not attr or not Path(attr).exists():
        raise HTTPException(404, f"产物 {type} 不存在 (status={t.status})")
    media = {"dxf": "application/dxf", "png": "image/png",
             "spec": "application/json", "report": "application/json",
             "compare": "image/png"}[type]
    return FileResponse(attr, media_type=media,
                        filename=f"{task_id}.{type}")
