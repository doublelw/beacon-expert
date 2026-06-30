"""Beacon专家 - 记忆存储引擎 (Phase 6).

CRUD + LLM 上下文构建。按类型/置信度/最近使用检索, 拼接成上下文字符串。
"""
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from src.database import MemoryEntry

# step → mem_type 映射: 不同步骤需要哪些类型的记忆
STEP_TYPE_MAP = {
    "classify": ["preference", "knowledge", "decision"],
    "project": ["decision", "preference", "knowledge"],
    "render": ["preference", "decision"],
    "audit": ["correction", "decision", "preference"],
    "chat": ["dialog", "preference", "knowledge"],
}
DEFAULT_TYPES = ["preference", "knowledge", "decision"]

# 单步最大记忆条数 (避免上下文爆炸)
MAX_CONTEXT_ENTRIES = 15


def store(
    db: Session,
    user_id: int,
    mem_type: str,
    content: dict,
    context: str = "",
    confidence: float = 0.7,
) -> MemoryEntry:
    """存储一条记忆.

    Args:
        db: Session
        user_id: 用户 id
        mem_type: dialog/decision/correction/preference/knowledge
        content: 结构化 JSON 内容
        context: 触发场景描述
        confidence: 置信度 0-1

    Returns:
        已存入的 MemoryEntry (含 id)
    """
    entry = MemoryEntry(
        user_id=user_id,
        mem_type=mem_type,
        content=content,
        context=context,
        confidence=max(0.0, min(1.0, confidence)),
        use_count=0,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def retrieve(
    db: Session,
    user_id: int,
    mem_type: Optional[str] = None,
    limit: int = 10,
) -> List[MemoryEntry]:
    """检索用户记忆.

    排序规则: 置信度降序 + 最近使用降序 + 创建时间降序。

    Args:
        user_id: 用户 id
        mem_type: 可选类型过滤
        limit: 最多返回条数

    Returns:
        List[MemoryEntry]
    """
    q = db.query(MemoryEntry).filter(MemoryEntry.user_id == user_id)
    if mem_type:
        q = q.filter(MemoryEntry.mem_type == mem_type)
    entries = (
        q.order_by(
            MemoryEntry.confidence.desc(),
            MemoryEntry.last_used_at.desc().nullslast(),
            MemoryEntry.created_at.desc(),
        )
        .limit(limit)
        .all()
    )
    # 触摸: 累计 use_count + 更新 last_used_at
    now = datetime.now(timezone.utc)
    for e in entries:
        e.use_count = (e.use_count or 0) + 1
        e.last_used_at = now
    db.commit()
    return entries


def build_context(db: Session, user_id: int, step: str) -> str:
    """构建 LLM 上下文字符串.

    根据 step 拉取相关类型记忆, 拼成可注入 prompt 的纯文本。
    高置信度 (>=0.85) 的 correction/preference 优先; 过期无用记忆自动沉底。

    Args:
        user_id: 用户 id
        step: 当前流程步骤 (classify/project/render/audit/chat/...)

    Returns:
        上下文字符串 (空记忆返回 "[无历史记忆]")
    """
    types = STEP_TYPE_MAP.get(step, DEFAULT_TYPES)
    q = db.query(MemoryEntry).filter(
        MemoryEntry.user_id == user_id,
        MemoryEntry.mem_type.in_(types),
    )
    entries = (
        q.order_by(
            MemoryEntry.confidence.desc(),
            MemoryEntry.last_used_at.desc().nullslast(),
            MemoryEntry.created_at.desc(),
        )
        .limit(MAX_CONTEXT_ENTRIES)
        .all()
    )

    if not entries:
        return "[无历史记忆]"

    lines = [f"# 用户记忆上下文 (step={step})"]
    for e in entries:
        # content 是 dict, 转成 k=v 简洁形式
        content_str = _flatten_content(e.content)
        conf_mark = "*" if e.confidence >= 0.85 else " "
        ctx = f" [{e.context}]" if e.context else ""
        lines.append(
            f"-{conf_mark} [{e.mem_type}] (置信度{e.confidence:.2f}, 用{e.use_count}次){ctx}: {content_str}"
        )

    # 触摸更新
    now = datetime.now(timezone.utc)
    for e in entries:
        e.use_count = (e.use_count or 0) + 1
        e.last_used_at = now
    db.commit()

    return "\n".join(lines)


def _flatten_content(content) -> str:
    """把 content (dict/list/scalar) 压平为单行字符串."""
    if isinstance(content, dict):
        parts = [f"{k}={v}" for k, v in content.items()]
        return ", ".join(parts)
    if isinstance(content, list):
        return ", ".join(str(x) for x in content)
    return str(content)
