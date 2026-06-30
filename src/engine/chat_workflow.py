"""Phase 4: AI会话工作流状态机.

编排 M0→M1→M2→M3-M6→M7, 每步AI输出→用户确认/纠正→下一步.
记忆自动注入(build_context) + 用户交互自动存储(MemoryEntry).
"""
import json
import os
import asyncio
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from src.database import Conversation, MemoryEntry, ConversionTask
from src.engine.classify_ai import classify
from src.engine.understand_ai import understand
from src.engine.plan_ai import plan
from src.engine.audit_ai import audit
from src.engine.memory_store import build_context, store

STAGE_ORDER = ["init", "classify", "understand", "plan", "convert", "audit", "done"]


def add_message(conv: Conversation, role: str, content: str, stage: str = None):
    """添加对话消息."""
    msgs = conv.messages or []
    msgs.append({
        "role": role,
        "content": content,
        "stage": stage or conv.stage,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    conv.messages = msgs
    conv.updated_at = datetime.now(timezone.utc)


async def start_classify(conv: Conversation, db: Session, features_json: str):
    """M0: 工艺判断. 返回AI消息供用户确认."""
    # 记忆注入
    memory_ctx = build_context(db, conv.user_id, "classify")
    # AI判断
    result = await classify(features_json)
    # 存上下文
    ctx = conv.context or {}
    ctx["features_json"] = features_json
    ctx["classify_result"] = result
    conv.context = ctx
    # 构建AI消息
    confidence = result.get("confidence", 0)
    process = result.get("process", "unknown")
    reasoning = result.get("reasoning", "")
    msg = f"📊 工艺判断：{process}（置信度{confidence:.0%}）\n理由：{reasoning}"
    if result.get("ask_user"):
        msg += "\n\n⚠️ 置信度较低，请确认工艺类型。"
    add_message(conv, "ai", msg, "classify")
    conv.stage = "classify"
    db.commit()
    return msg


async def confirm_classify(conv: Conversation, db: Session, confirmed_process: str = None):
    """用户确认M0 → 存储+推进到M1."""
    ctx = conv.context or {}
    process = confirmed_process or ctx.get("classify_result", {}).get("process", "unknown")
    # 存记忆(decision)
    store(
        db, conv.user_id, "decision",
        {"stage": "classify", "process": process},
        context=conv.id, confidence=0.9,
    )
    ctx["process"] = process
    conv.context = ctx
    add_message(conv, "user", f"确认工艺：{process}", "classify")
    db.commit()
    # 自动推进到M1
    await start_understand(conv, db)


async def correct_classify(conv: Conversation, db: Session, correct_process: str, reason: str = ""):
    """用户纠正M0 → 存储(高置信度)+用正确工艺重跑."""
    store(
        db, conv.user_id, "correction",
        {
            "stage": "classify",
            "original": conv.context.get("classify_result", {}).get("process"),
            "corrected": correct_process,
            "reason": reason,
        },
        context=conv.id, confidence=1.0,  # 用户纠正=绝对信任
    )
    ctx = conv.context or {}
    ctx["process"] = correct_process
    conv.context = ctx
    add_message(conv, "user", f"纠正：这是{correct_process}。{reason}", "classify")
    add_message(conv, "ai", f"已记忆，后续同类型零件将自动判断为{correct_process}。", "classify")
    db.commit()


async def start_understand(conv: Conversation, db: Session):
    """M1: AI理解. 读veritas→描述零件."""
    ctx = conv.context or {}
    veritas_json = ctx.get("features_json", "{}")
    memory_ctx = build_context(db, conv.user_id, "understand")
    description = await understand(veritas_json, memory_context=memory_ctx)
    ctx["description"] = description
    conv.context = ctx
    add_message(conv, "ai", f"🔍 零件理解：\n{description}", "understand")
    conv.stage = "understand"
    db.commit()


async def handle_user_message(conv: Conversation, db: Session, message: str) -> str:
    """用户在任意stage发送消息. AI响应+记忆存储."""
    add_message(conv, "user", message, conv.stage)
    # 存用户输入为dialog记忆
    store(
        db, conv.user_id, "dialog",
        {"stage": conv.stage, "message": message},
        context=conv.id, confidence=0.7,
    )
    # 根据stage决定AI如何响应
    ctx = conv.context or {}
    if conv.stage == "understand":
        # 用户补充信息→记住→推进M2
        memory_ctx = build_context(db, conv.user_id, "plan")
        process = ctx.get("process", "sheet_metal")
        description = ctx.get("description", "")
        # 把用户补充加入描述
        full_desc = description + f"\n用户补充：{message}"
        plan_result = await plan(full_desc, process, memory_context=memory_ctx)
        ctx["plan"] = plan_result
        conv.context = ctx
        add_message(conv, "ai", f"📋 标注规划：\n{plan_result}\n\n✅ 确认执行转换？", "plan")
        conv.stage = "plan"
    elif conv.stage == "plan":
        # 用户调整→记住→等待确认
        store(
            db, conv.user_id, "preference",
            {"stage": "plan", "adjustment": message},
            context=conv.id, confidence=0.8,
        )
        add_message(conv, "ai", f"已记住您的调整：{message}\n✅ 确认执行转换？", "plan")
    elif conv.stage == "audit":
        # 用户补充→记住
        store(
            db, conv.user_id, "preference",
            {"stage": "audit", "feedback": message},
            context=conv.id, confidence=0.8,
        )
        add_message(conv, "ai", f"已记住反馈：{message}", "audit")
    else:
        add_message(conv, "ai", f"收到。当前阶段：{conv.stage}", conv.stage)
    db.commit()
    return conv.messages[-1]["content"]


async def confirm_and_advance(conv: Conversation, db: Session):
    """用户确认当前stage → 推进到下一stage."""
    if conv.stage == "plan":
        add_message(conv, "user", "确认执行转换", "plan")
        conv.stage = "convert"
        add_message(conv, "ai", "⚙️ 正在转换... 转换完成后将自动进行AI审查。", "convert")
        db.commit()
        # 转换由convert路由的后台任务执行
    elif conv.stage == "audit":
        add_message(conv, "user", "确认接受", "audit")
        conv.stage = "done"
        add_message(conv, "ai", "✅ 转换完成！DXF已可下载。", "done")
        db.commit()


async def start_audit(conv: Conversation, db: Session, audit_report: str, dxf_summary: str):
    """M7: AI审查(转换完成后调用)."""
    result = await audit(audit_report, dxf_summary)
    ctx = conv.context or {}
    ctx["audit_result"] = result
    conv.context = ctx
    score = result.get("readiness_score", 0)
    missing = result.get("missing", [])
    msg = f"🔍 AI审查结果：\n加工就绪度：{score}/100\n"
    if missing:
        msg += f"缺失项：{', '.join(missing)}\n"
    msg += "\n✅ 确认接受？或补充信息？"
    add_message(conv, "ai", msg, "audit")
    conv.stage = "audit"
    db.commit()
