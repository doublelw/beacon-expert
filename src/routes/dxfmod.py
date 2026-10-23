"""C线改图 API: 两条路径.

路径A (精确, 平台生成的图纸): POST /api/dxfmod/modify-task/{task_id}
  在**投影参数源**上改 (proj_v3/veritas/annotation) → 重跑 render_engine
  → 标注按几何重新派生、多视图自动一致、门禁自动复跑 (proj_edit.run_proj_modify)

路径B (通用, 第三方 DXF): POST /api/dxfmod/modify
  multipart 上传 DXF + 指令 → LLM 翻译成 STRETCH 窗口操作 → 确定性应用
  → 作用域审计 → 前后对比图 (cad_modify.run_dxf_modify)

GET /api/dxfmod/status/{id} / download/{id}?type=dxf|compare|plan|report
"""
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth import get_current_user
from src.config import TASKS_DIR, MAX_UPLOAD_BYTES
from src.database import get_db, SessionLocal, Cad3dTask, ConversionTask, User

router = APIRouter(prefix="/api/dxfmod", tags=["DXF改图"])


class TaskModifyRequest(BaseModel):
    instructions: str = Field(..., min_length=2, max_length=2000,
                              description="改图指令, 如: 把四个角孔从φ2.2改成φ3.2 / 宽度从120改成150")


@router.post("/modify-task/{task_id}")
async def modify_task(task_id: str, req: TaskModifyRequest,
                      background_tasks: BackgroundTasks,
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """平台生成的图纸: 按任务 id 自然语言改尺寸 (投影源头精确改 + 重渲染)."""
    conv = db.query(ConversionTask).filter(ConversionTask.id == task_id).first()
    work_dir = conv.work_dir if (conv and conv.user_id == user.id) else str(TASKS_DIR / task_id)
    if not (Path(work_dir) / 'proj_v3.json').exists():
        raise HTTPException(
            400, "该任务无投影参数源 (非本平台生成或已清理) — "
                 "第三方 DXF 请用 POST /api/dxfmod/modify 上传文件走 STRETCH 路径")
    new_id = uuid.uuid4().hex
    out_dir = str(TASKS_DIR / new_id)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    db.add(Cad3dTask(id=new_id, user_id=user.id,
                     prompt=f"[改图-参数源 {task_id[:8]}] {req.instructions}",
                     status="queued", work_dir=out_dir, source_task_id=task_id,
                     step_path=str(Path(work_dir) / 'proj_v3.json')))
    db.commit()
    background_tasks.add_task(_run_proj_modify, new_id, work_dir, req.instructions)
    return {"task_id": new_id, "source_task_id": task_id, "status": "queued",
            "path": "proj_source"}


def _run_proj_modify(task_id: str, source_dir: str, instructions: str):
    """后台: 参数源改尺寸 → 重渲染 → 验证 (proj_edit 全链)."""
    db = SessionLocal()
    task = db.query(Cad3dTask).filter(Cad3dTask.id == task_id).first()
    try:
        task.status = "planning"
        db.commit()
        from src.engine.proj_edit import run_proj_modify
        r = asyncio.run(run_proj_modify(source_dir, instructions, task.work_dir))
        (Path(task.work_dir) / 'modify_report.json').write_text(
            json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
        task.report_path = str(Path(task.work_dir) / 'modify_report.json')
        task.dxf_path = r.get('dxf')
        task.compare_path = r.get('compare_png')
        task.llm_provider = (r.get('llm') or {}).get('provider')
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


@router.post("/modify")
async def modify(background_tasks: BackgroundTasks,
                 file: UploadFile = File(...),
                 instructions: str = Form(...),
                 user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """上传任意 DXF + 修改指令 → 改尺寸生成新图纸."""
    if not file.filename or not file.filename.lower().endswith('.dxf'):
        raise HTTPException(400, "仅支持 .dxf")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"文件超限({len(content) // 1024 // 1024}MB)")
    task_id = uuid.uuid4().hex
    work_dir = str(TASKS_DIR / task_id)
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    src_path = Path(work_dir) / 'source.dxf'
    src_path.write_bytes(content)
    # 可解析性预检 (立即反馈, 不进后台; 本机 ezdxf 的 read() 不吃 BytesIO, 用 readfile)
    import ezdxf
    try:
        ezdxf.readfile(str(src_path))
    except Exception as ex:
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(400, f"DXF 解析失败: {str(ex)[:150]}")
    # 复用 Cad3dTask 表 (prompt=指令, step_path 存源图, source_task_id 空)
    db.add(Cad3dTask(id=task_id, user_id=user.id,
                     prompt=f"[DXF改图] {instructions[:200]}",
                     status="queued", work_dir=work_dir,
                     step_path=str(src_path)))
    db.commit()
    background_tasks.add_task(_run_modify, task_id, instructions)
    return {"task_id": task_id, "status": "queued"}


def _run_modify(task_id: str, instructions: str):
    db = SessionLocal()
    task = db.query(Cad3dTask).filter(Cad3dTask.id == task_id).first()
    try:
        task.status = "planning"
        db.commit()
        from src.engine.cad_modify import run_dxf_modify
        src = str(Path(task.work_dir) / 'source.dxf')
        r = asyncio.run(run_dxf_modify(src, instructions, task.work_dir))
        (Path(task.work_dir) / 'modify_report.json').write_text(
            json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
        task.report_path = str(Path(task.work_dir) / 'modify_report.json')
        task.dxf_path = r.get('dxf')
        task.compare_path = r.get('compare_png')
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


@router.get("/status/{task_id}")
async def get_status(task_id: str, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    t = db.query(Cad3dTask).filter(Cad3dTask.id == task_id).first()
    if not t or t.user_id != user.id:
        raise HTTPException(404, "任务不存在")
    out = {"task_id": t.id, "status": t.status, "prompt": t.prompt,
           "dxf": t.dxf_path, "compare": t.compare_path, "error": t.error,
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
    # 计划文件: 路径A=modify_plan.json (参数源), 路径B=edit_plan.json (窗口操作)
    plan_path = None
    for fn in ('modify_plan.json', 'edit_plan.json'):
        p = Path(t.work_dir) / fn
        if p.exists():
            plan_path = str(p)
            break
    attr = {"dxf": t.dxf_path, "compare": t.compare_path,
            "plan": plan_path, "report": t.report_path}.get(type)
    if not attr or not Path(attr).exists():
        raise HTTPException(404, f"产物 {type} 不存在 (status={t.status})")
    media = {"dxf": "application/dxf", "compare": "image/png",
             "plan": "application/json", "report": "application/json"}[type]
    return FileResponse(attr, media_type=media, filename=f"{task_id}.{type}")
