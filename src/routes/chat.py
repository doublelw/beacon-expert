"""Phase 4: AI会话API."""
import uuid
import json
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from src.auth import get_current_user
from src.database import get_db, User, Conversation, init_db, SessionLocal
from src.engine.chat_workflow import (
    start_classify, confirm_classify, correct_classify,
    handle_user_message, confirm_and_advance, add_message,
)
from src.config import TASKS_DIR, MAX_UPLOAD_BYTES, ALLOWED_SUFFIX, SAAS_CORE, FC_BIN
import os
import subprocess
import asyncio

router = APIRouter(prefix="/api/chat", tags=["AI会话"])


class MessageRequest(BaseModel):
    text: str


class ConfirmRequest(BaseModel):
    process: str = None  # M0确认时可指定正确工艺


class CorrectRequest(BaseModel):
    process: str
    reason: str = ""


@router.post("/start")
async def start_conversation(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传STP → 创建会话 → 后台异步跑M0(前端轮询 /chat/{id} 看实时进度)."""
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "文件超限")
    suffix = os.path.splitext(file.filename)[1].lower()
    if suffix not in ALLOWED_SUFFIX:
        raise HTTPException(400, "仅支持STP/STEP")

    conv_id = uuid.uuid4().hex
    work_dir = str(TASKS_DIR / conv_id)
    os.makedirs(work_dir, exist_ok=True)
    stp_path = os.path.join(work_dir, file.filename)
    with open(stp_path, "wb") as f:
        f.write(content)

    conv = Conversation(id=conv_id, user_id=user.id, stage="init")
    db.add(conv)
    add_message(conv, "ai", f"📁 已接收文件：{file.filename}", "init")
    add_message(conv, "ai", "🔧 正在用 FreeCAD 提取 3D 特征（冷启动需数十秒，请稍候）...", "init")
    ctx = conv.context or {}
    ctx["stp_path"] = stp_path
    ctx["work_dir"] = work_dir
    conv.context = ctx
    db.commit()

    # 后台异步: veritas特征提取 → AI工艺判断. 不阻塞响应, 每步写进度消息供轮询.
    asyncio.create_task(_run_classify_bg(conv_id, stp_path, work_dir))
    return {"conversation_id": conv_id, "stage": "init", "status": "running"}


async def _run_classify_bg(conv_id: str, stp_path: str, work_dir: str):
    """后台任务: veritas(线程池) → classify(LLM). 每步 add_message 写进度, 失败也写."""
    db = SessionLocal()
    try:
        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        if not conv:
            return
        out = f"{work_dir}/veritas.json"

        def _veritas():
            env = {**os.environ, "STP": stp_path, "OUT": out}
            subprocess.run(
                [FC_BIN, str(SAAS_CORE / "veritas.py")],
                env=env, capture_output=True, timeout=180, cwd=work_dir,
            )
        try:
            await asyncio.to_thread(_veritas)
            features_json = open(out).read() if os.path.exists(out) else "{}"
        except Exception as ex:
            features_json = "{}"
            add_message(conv, "ai", f"⚠️ 特征提取异常（改用默认特征）：{str(ex)[:120]}", "init")
            db.commit()

        add_message(conv, "ai", "🧠 特征就绪，正在 AI 工艺判断...", "classify")
        db.commit()
        await start_classify(conv, db, features_json)
        db.commit()
    except Exception as e:
        try:
            conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
            if conv:
                add_message(conv, "ai", f"❌ 分析失败：{str(e)[:200]}", "failed")
                conv.stage = "failed"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/{conv_id}/message")
async def send_message(
    conv_id: str,
    req: MessageRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """用户发送消息 → AI响应."""
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        raise HTTPException(404, "会话不存在")
    response = await handle_user_message(conv, db, req.text)
    return {"stage": conv.stage, "message": response}


@router.post("/{conv_id}/confirm")
async def confirm_stage(
    conv_id: str,
    req: ConfirmRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """确认当前stage → 推进."""
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        raise HTTPException(404, "会话不存在")
    if conv.stage == "classify":
        await confirm_classify(conv, db, req.process)
        return {"stage": conv.stage, "message": conv.messages[-1]["content"]}
    elif conv.stage in ("plan", "audit"):
        await confirm_and_advance(conv, db)
        return {"stage": conv.stage, "message": conv.messages[-1]["content"]}
    return {"stage": conv.stage, "message": "当前阶段无需确认"}


@router.post("/{conv_id}/correct")
async def correct_stage(
    conv_id: str,
    req: CorrectRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """纠正当前stage → 存储+重跑."""
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        raise HTTPException(404, "会话不存在")
    if conv.stage == "classify":
        await correct_classify(conv, db, req.process, req.reason)
        return {"stage": conv.stage, "message": conv.messages[-1]["content"]}
    return {"stage": conv.stage, "message": "当前阶段不支持纠正"}


@router.get("/{conv_id}")
async def get_conversation(
    conv_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取会话历史."""
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        raise HTTPException(404, "会话不存在")
    return {
        "id": conv.id,
        "stage": conv.stage,
        "messages": conv.messages,
        "context": conv.context,
    }
