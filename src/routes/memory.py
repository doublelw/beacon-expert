"""Beacon专家 - 智能记忆系统路由 (Phase 6).

存储/检索/删除用户记忆, 为 LLM 提供长期上下文。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth import get_current_user
from src.database import get_db, MemoryEntry, User
from src.engine.memory_store import store, retrieve, build_context

router = APIRouter(prefix="/api/memory", tags=["记忆系统"])


class MemoryCreate(BaseModel):
    mem_type: str = Field(..., description="类型: dialog/decision/correction/preference/knowledge")
    content: dict = Field(..., description="记忆内容 (结构化 JSON)")
    context: str = Field("", description="触发场景描述")
    confidence: float = Field(0.7, ge=0.0, le=1.0, description="置信度 0-1")


def _serialize(m: MemoryEntry) -> dict:
    return {
        "id": m.id,
        "user_id": m.user_id,
        "project_id": m.project_id,
        "mem_type": m.mem_type,
        "content": m.content,
        "context": m.context,
        "confidence": m.confidence,
        "use_count": m.use_count,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "last_used_at": m.last_used_at.isoformat() if m.last_used_at else None,
    }


@router.post("")
@router.post("/")
def create_memory(
    req: MemoryCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """存储一条记忆."""
    entry = store(
        db=db,
        user_id=user.id,
        mem_type=req.mem_type,
        content=req.content,
        context=req.context,
        confidence=req.confidence,
    )
    return {"id": entry.id, "created": True}


@router.get("")
@router.get("/")
def list_memory(
    mem_type: Optional[str] = Query(None, description="按类型过滤"),
    limit: int = Query(50, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出当前用户的记忆 (可按 mem_type 过滤)."""
    entries = retrieve(db, user_id=user.id, mem_type=mem_type, limit=limit)
    return {"total": len(entries), "memories": [_serialize(m) for m in entries]}


@router.get("/context")
def get_context(
    step: str = Query(..., description="当前步骤 (e.g. classify/project/render/audit)"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """构建 LLM 上下文字符串 (供前端/agent 调用)."""
    return {"step": step, "context": build_context(db, user_id=user.id, step=step)}


@router.delete("/{memory_id}")
def delete_memory(
    memory_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除一条记忆 (仅作者本人)."""
    entry = db.query(MemoryEntry).filter(MemoryEntry.id == memory_id).first()
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="记忆不存在")
    if entry.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权删除他人记忆")
    db.delete(entry)
    db.commit()
    return {"id": memory_id, "deleted": True}
