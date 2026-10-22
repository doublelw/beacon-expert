"""M-assembly: 客户装配体STEP → 交互爆炸图 + 装配工程图 API.

POST /api/assembly/upload   上传客户装配体STEP (.step/.stp, 多子件) → 拆件
  → 逐件tessellate → assembly.json → 花叔viewer.py出交互爆炸图HTML
GET  /api/assembly/status/{id}         状态 + 验证报告
GET  /api/assembly/download/{id}?type  exploded_html|assembly_json|step

链路 (无自然语言, 确定性):
  客户STEP ─import_step─> 子件 ─tessellate─> assembly.json ─viewer.py─> 单文件HTML
  单文件HTML双击即开: 爆炸滑杆/剖切/边线/零件标签/BOM悬停高亮/视角预设 (不联网)。
装配体识别: 多子件且各件质心不重合才算装配体; 单一零件拒绝 (爆炸图无意义)。
"""
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from src.auth import get_current_user
from src.database import get_db, SessionLocal, CadAssemblyTask, User
from src.config import MAX_UPLOAD_BYTES, TASKS_DIR

router = APIRouter(prefix="/api/assembly", tags=["装配爆炸图"])


def _is_step(filename: str) -> bool:
    return filename.lower().endswith(('.step', '.stp'))


@router.post("/upload")
async def upload_asm(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传客户装配体STEP → 拆件+爆炸图任务 (后台)."""
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"文件超限({len(content) // 1024 // 1024}MB)")
    if not _is_step(file.filename):
        raise HTTPException(400, "仅支持 .step / .stp")

    task_id = uuid.uuid4().hex
    work_dir = str(TASKS_DIR / task_id)
    os.makedirs(work_dir, exist_ok=True)
    step_path = os.path.join(work_dir, "source.step")
    with open(step_path, "wb") as f:
        f.write(content)

    task = CadAssemblyTask(
        id=task_id, user_id=user.id,
        prompt=f"assembly:{file.filename}",
        status="queued", work_dir=work_dir, step_path=step_path,
    )
    db.add(task)
    db.commit()
    background_tasks.add_task(_run_assembly, task_id)
    return {"task_id": task_id, "status": "queued", "n_parts": None}


def _run_assembly(task_id: str):
    """后台: 拆件 → assembly.json → 爆炸图HTML + 装配工程图DXF (venv 侧全链)."""
    db = SessionLocal()
    task = db.query(CadAssemblyTask).filter(CadAssemblyTask.id == task_id).first()
    try:
        task.status = "tearing"
        db.commit()
        from src.engine.explode_gen import run_explode
        r = run_explode(task.step_path, task.work_dir,
                        title=os.path.basename(task.step_path))
        task.assembly_json = r.get('assembly_json')
        task.exploded_html = r.get('html')
        task.assembly_dxf_path = r.get('assembly_dxf')
        task.assembly_dxf_png = r.get('assembly_dxf_png')
        task.n_parts = r.get('n_parts') or 0
        if r.get('ok'):
            task.status = "done"
        else:
            task.status = "failed"
            task.error = f"[{r.get('stage')}] {str(r.get('error'))[:400]}"
        task.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as ex:
        task.status = "failed"
        task.error = f"{type(ex).__name__}: {str(ex)[:400]}"
        task.updated_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


@router.get("/status/{task_id}")
async def get_status(task_id: str, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    t = db.query(CadAssemblyTask).filter(CadAssemblyTask.id == task_id).first()
    if not t or t.user_id != user.id:
        raise HTTPException(404, "任务不存在")
    return {
        "task_id": t.id, "status": t.status, "n_parts": t.n_parts,
        "step": t.step_path, "assembly_json": t.assembly_json,
        "exploded_html": t.exploded_html,
        "assembly_dxf": t.assembly_dxf_path,
        "assembly_dxf_png": t.assembly_dxf_png,
        "error": t.error, "created_at": str(t.created_at),
    }


@router.get("/download/{task_id}")
async def download(task_id: str, type: str = "exploded_html",
                   user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    t = db.query(CadAssemblyTask).filter(CadAssemblyTask.id == task_id).first()
    if not t or t.user_id != user.id:
        raise HTTPException(404, "任务不存在")
    attr = {"exploded_html": t.exploded_html, "assembly_json": t.assembly_json,
            "assembly_dxf": t.assembly_dxf_path,
            "assembly_dxf_png": t.assembly_dxf_png,
            "step": t.step_path}.get(type)
    if not attr or not Path(attr).exists():
        raise HTTPException(404, f"产物 {type} 不存在 (status={t.status})")
    media = {"exploded_html": "text/html", "assembly_json": "application/json",
             "assembly_dxf": "application/dxf", "assembly_dxf_png": "image/png",
             "step": "application/step"}[type]
    return FileResponse(attr, media_type=media,
                        filename=os.path.basename(attr))
