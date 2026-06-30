"""Phase 4: AI会话API."""
import uuid
import json
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from src.auth import get_current_user
from src.database import get_db, User, Conversation, init_db
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
    """上传STP → 创建会话 → 启动M0工艺判断."""
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

    # 创建会话
    conv = Conversation(id=conv_id, user_id=user.id, stage="init")
    db.add(conv)
    db.commit()
    add_message(conv, "ai", f"📁 已接收文件：{file.filename}\n正在分析3D模型...", "init")
    db.commit()

    # M0: 先用freecadcmd提取特征(veritas)
    try:
        env = {**os.environ, "STP": stp_path, "OUT": f"{work_dir}/veritas.json"}
        subprocess.run(
            [FC_BIN, str(SAAS_CORE / "veritas.py")],
            env=env, capture_output=True, timeout=120, cwd=work_dir,
        )
        features_json = (
            open(f"{work_dir}/veritas.json").read()
            if os.path.exists(f"{work_dir}/veritas.json")
            else "{}"
        )
    except Exception:
        features_json = "{}"

    # 启动AI工艺判断
    ctx = conv.context or {}
    ctx["stp_path"] = stp_path
    ctx["work_dir"] = work_dir
    conv.context = ctx
    db.commit()

    msg = await start_classify(conv, db, features_json)
    return {"conversation_id": conv_id, "stage": "classify", "message": msg}


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
