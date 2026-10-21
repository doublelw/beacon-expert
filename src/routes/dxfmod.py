"""C线 Phase 4: 任意来源 DXF 修改 API.

POST /api/dxfmod/modify  multipart: file=xxx.dxf + instructions=文字
  → 任务: 提取摘要→LLM编辑计划(STRETCH窗口操作)→确定性应用→作用域审计→前后对比图
GET  /api/dxfmod/status/{id} / download/{id}?type=dxf|compare|plan|report
"""
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from src.auth import get_current_user
from src.config import TASKS_DIR, MAX_UPLOAD_BYTES
from src.database import get_db, SessionLocal, Cad3dTask, User

router = APIRouter(prefix="/api/dxfmod", tags=["DXF改图"])


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
    attr = {"dxf": t.dxf_path, "compare": t.compare_path,
            "plan": str(Path(t.work_dir) / 'edit_plan.json'),
            "report": t.report_path}.get(type)
    if not attr or not Path(attr).exists():
        raise HTTPException(404, f"产物 {type} 不存在 (status={t.status})")
    media = {"dxf": "application/dxf", "compare": "image/png",
             "plan": "application/json", "report": "application/json"}[type]
    return FileResponse(attr, media_type=media, filename=f"{task_id}.{type}")
