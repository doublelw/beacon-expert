"""STP→DXF转换API: 上传/状态/下载."""
import os
import uuid
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from src.auth import get_current_user
from src.database import get_db, SessionLocal, ConversionTask, User
from src.config import MAX_UPLOAD_BYTES, ALLOWED_SUFFIX, TASKS_DIR
from datetime import datetime, timezone

router = APIRouter(prefix="/api/convert", tags=["转换"])


@router.post("/upload")
async def upload_stp(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    """上传STP文件→创建转换任务."""
    # 校验文件
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            400,
            f"文件超限({len(content) // 1024 // 1024}MB>{MAX_UPLOAD_BYTES // 1024 // 1024}MB)",
        )
    suffix = os.path.splitext(file.filename)[1].lower()
    if suffix not in ALLOWED_SUFFIX:
        raise HTTPException(400, f"仅支持{ALLOWED_SUFFIX}")

    task_id = uuid.uuid4().hex
    work_dir = str(TASKS_DIR / task_id)
    os.makedirs(work_dir, exist_ok=True)
    stp_path = os.path.join(work_dir, file.filename)
    with open(stp_path, "wb") as f:
        f.write(content)

    # 创建任务记录
    task = ConversionTask(
        id=task_id,
        user_id=user.id,
        stp_path=stp_path,
        status="queued",
        work_dir=work_dir,
    )
    db.add(task)
    db.commit()

    # 后台执行pipeline
    background_tasks.add_task(_run_conversion, task_id, stp_path, work_dir)
    return {"task_id": task_id, "status": "queued"}


def _run_conversion(task_id: str, stp_path: str, work_dir: str):
    """后台执行转换pipeline(同步, 在线程池中跑)."""
    db = SessionLocal()
    task = db.query(ConversionTask).filter(ConversionTask.id == task_id).first()
    try:
        # 更新状态
        task.status = "projecting"
        task.heartbeat = datetime.now(timezone.utc)
        db.commit()

        # 调用pipeline(同步版本, 用subprocess)
        import subprocess

        env = {**os.environ, "STP": stp_path, "OUT": f"{work_dir}/veritas.json"}
        saas_core = "/Users/ahs/project/Beacon/saas/core"
        fc_bin = os.environ.get(
            "FC_BIN", "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd"
        )

        # M1: veritas
        veritas_proc = subprocess.run(
            [fc_bin, f"{saas_core}/veritas.py"],
            env=env,
            capture_output=True,
            timeout=120,
            cwd=work_dir,
        )
        if veritas_proc.returncode != 0:
            task.status = "failed"
            task.error = f"veritas failed: {veritas_proc.stderr.decode()[:500]}"
            db.commit()
            return
        task.veritas_path = f"{work_dir}/veritas.json"
        task.status = "classifying"
        task.heartbeat = datetime.now(timezone.utc)
        db.commit()

        # M3: projection
        env["OUT"] = f"{work_dir}/proj_v3.json"
        proj_proc = subprocess.run(
            [fc_bin, f"{saas_core}/projection_v3.py"],
            env=env,
            capture_output=True,
            timeout=600,
            cwd=work_dir,
        )
        if proj_proc.returncode != 0:
            task.status = "failed"
            task.error = f"projection failed: {proj_proc.stderr.decode()[:500]}"
            db.commit()
            return
        task.proj_path = f"{work_dir}/proj_v3.json"
        task.status = "rendering"
        task.heartbeat = datetime.now(timezone.utc)
        db.commit()

        # M4-6: render
        dxf_path = f"{work_dir}/output.dxf"
        geom_path = f"{work_dir}/clean_geom.json"
        render_proc = subprocess.run(
            [
                "python3",
                f"{saas_core}/render_engine.py",
                "--projection",
                f"{work_dir}/proj_v3.json",
                "--geometry",
                geom_path,
                "-o",
                dxf_path,
            ],
            capture_output=True,
            timeout=60,
            cwd=work_dir,
        )
        if render_proc.returncode != 0:
            task.status = "failed"
            task.error = f"render failed: {render_proc.stderr.decode()[:500]}"
            db.commit()
            return

        task.dxf_path = dxf_path
        task.status = "done"
        task.finished_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as ex:
        task.status = "failed"
        task.error = str(ex)[:500]
        db.commit()
    finally:
        db.close()


@router.get("/status/{task_id}")
async def get_status(
    task_id: str, user: User = Depends(get_current_user), db=Depends(get_db)
):
    """查询转换状态."""
    task = db.query(ConversionTask).filter(ConversionTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "任务不存在")
    return {
        "task_id": task.id,
        "status": task.status,
        "dxf_ready": task.dxf_path and os.path.exists(task.dxf_path),
        "error": task.error,
        "degraded": task.degraded,
    }


@router.get("/download/{task_id}")
async def download_dxf(
    task_id: str, user: User = Depends(get_current_user), db=Depends(get_db)
):
    """下载DXF."""
    task = db.query(ConversionTask).filter(ConversionTask.id == task_id).first()
    if not task or not task.dxf_path or not os.path.exists(task.dxf_path):
        raise HTTPException(404, "DXF不存在")

    def gen():
        with open(task.dxf_path, "rb") as f:
            yield from f

    return StreamingResponse(
        gen(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{task_id}.dxf"'},
    )
