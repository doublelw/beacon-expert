"""Phase 4: AI会话工作流状态机.

编排 M0→M1→M2→M3-M6→M7, 每步AI输出→用户确认/纠正→下一步.
记忆自动注入(build_context) + 用户交互自动存储(MemoryEntry).
"""
import json
import os
import asyncio
import subprocess
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from src.database import Conversation, MemoryEntry, ConversionTask, SessionLocal
from src.engine.classify_ai import classify, refine_process
from src.engine.understand_ai import understand
from src.engine.plan_ai import plan, make_drawing_plan, save_plan
from src.engine.audit_ai import audit
from src.engine.memory_store import build_context, store

STAGE_ORDER = ["init", "classify", "understand", "plan", "convert", "audit", "done"]


def add_message(conv: Conversation, role: str, content: str, stage: str = None):
    """添加对话消息."""
    msgs = list(conv.messages or [])
    msgs.append({
        "role": role,
        "content": content,
        "stage": stage or conv.stage,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    conv.messages = msgs
    conv.updated_at = datetime.now(timezone.utc)


async def start_classify(conv: Conversation, db: Session, features_json: str):
    """M0: 工艺判断 + 列出可行工艺 + 顾问式提问. 进入 consultation=pending 等用户回答."""
    memory_ctx = build_context(db, conv.user_id, "classify")
    result = await classify(features_json)
    ctx = dict(conv.context or {})
    ctx["features_json"] = features_json
    ctx["classify_result"] = result
    ctx["viable_processes"] = result.get("viable_processes", [])
    ctx["consultation"] = "pending"
    conv.context = ctx
    confidence = result.get("confidence", 0)
    process = result.get("process", "unknown")
    reasoning = result.get("reasoning", "")
    viable = result.get("viable_processes", [])
    msg = f"📊 初步工艺判断：{process}（置信度{confidence:.0%}）\n理由：{reasoning}"
    if viable:
        msg += "\n\n🔧 可行工艺对比："
        for v in viable[:5]:
            msg += f"\n• {v.get('process','?')}（成本:{v.get('cost','?')}）— {v.get('note','')}"
    msg += ("\n\n💡 请在下方确认【零件性质(工艺)】(第一行, 你的选择优先于我的判断) "
            "+ 材料 / 公差 / 量级 / 优先 / 特殊——这些也是加工商理解产品的关键信息。\n"
            "请回答下方几个问题")
    add_message(conv, "ai", msg, "classify")
    conv.stage = "classify"
    db.commit()
    return msg


async def confirm_classify(conv: Conversation, db: Session, confirmed_process: str = None):
    """用户确认M0 → 存储+推进到M1."""
    ctx = dict(conv.context or {})
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
    ctx = dict(conv.context or {})
    ctx["process"] = correct_process
    conv.context = ctx
    add_message(conv, "user", f"纠正：这是{correct_process}。{reason}", "classify")
    add_message(conv, "ai", f"已记忆，后续同类型零件将自动判断为{correct_process}。", "classify")
    db.commit()


async def start_understand(conv: Conversation, db: Session):
    """M1: AI理解. 读veritas→描述零件."""
    ctx = dict(conv.context or {})
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
    ctx = dict(conv.context or {})
    # 对话流: classify + consultation=pending → 用户自然语言回答 → AI 提取工艺+材料+要求
    if conv.stage == "classify" and ctx.get("consultation") == "pending":
        refined = await refine_process(ctx.get("features_json", "{}"), ctx.get("viable_processes", []), message)
        # AI 从自然语言中提取工艺(不再用 "零件性质:X" 模式匹配)
        process = refined.get("process") or ctx.get("classify_result", {}).get("process", "unknown")
        # 检查用户是否明确说了工艺关键词(自然语言覆盖)
        msg_lower = message.lower()
        for kw, proc in [("钣金","钣金"),("机加","机加"),("机加工","机加"),("注塑","注塑"),("铸造","铸造"),("焊接","焊接"),("3d打印","3D打印"),("3d","3D打印")]:
            if kw in msg_lower:
                process = proc
                ctx["process_source"] = "user"
                break
        else:
            ctx["process_source"] = "refine"
        ctx["process"] = process
        ctx["refined"] = refined
        ctx["consultation_answer"] = message
        ctx["consultation"] = "done"
        conv.context = ctx
        store(db, conv.user_id, "dialog", {"stage": "classify", "consultation_answer": message}, context=conv.id, confidence=0.9)
        # 对话式回复
        rmsg = f"✅ 好的，工艺确认为{process}。"
        if refined.get("reasoning"):
            rmsg += f"\n{refined['reasoning']}"
        if refined.get("cost_note"):
            rmsg += f"\n💰 {refined['cost_note']}"
        rmsg += "\n\n确认就开始转换，或者告诉我需要调整什么。"
        add_message(conv, "ai", rmsg, "classify")
        db.commit()
        return conv.messages[-1]["content"]
    # 存用户输入为dialog记忆
    store(
        db, conv.user_id, "dialog",
        {"stage": conv.stage, "message": message},
        context=conv.id, confidence=0.7,
    )
    # 根据stage决定AI如何响应
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
        # 真正触发转换(后台): projection→render→DXF, 创建 ConversionTask(id=conv_id) 供前端 pollConvert 跟踪
        asyncio.create_task(_run_convert_bg(conv.id))
    elif conv.stage == "audit":
        add_message(conv, "user", "确认接受", "audit")
        conv.stage = "done"
        add_message(conv, "ai", "✅ 转换完成！DXF已可下载。", "done")
        db.commit()


SAAS_CORE = "/Users/ahs/project/Beacon/saas/core"
GEOM_PATH = "/Users/ahs/project/Beacon/saas/output/clean_geom.json"


def _build_audit_context(features_json: str, dxf_path: str) -> str:
    """构造审计上下文: 几何应有特征(孔/外形) vs DXF实际标注数, 让 LLM 能判断标注是否充分(GB合规)."""
    import json as _j
    holes_n, diams, overall = 0, [], ""
    try:
        v = _j.loads(features_json) if features_json else {}
        hs = v.get("holes_2d") or v.get("holes") or []
        if not hs and isinstance(v.get("features"), list):
            hs = [f for f in v["features"] if f.get("type") == "PIERCING" or f.get("diameter")]
        holes_n = len(hs)
        diams = sorted({round(h.get("d", h.get("diameter", (h.get("r", 0) or 0) * 2)), 2) for h in hs})
        htypes = {}
        for h in hs:
            t = h.get("hole_type") or ("thread" if "M" in str(h.get("text", "")) else "plain")
            htypes[t] = htypes.get(t, 0) + 1
        bb = v.get("bbox", {})
        if bb:
            overall = f"{bb.get('xmax',0)-bb.get('xmin',0):.0f} x {bb.get('ymax',0)-bb.get('ymin',0):.0f} x {bb.get('zmax',0)-bb.get('zmin',0):.0f}mm"
    except Exception:
        pass
    dim_count, dim_vals = 0, []
    try:
        import ezdxf
        doc = ezdxf.readfile(dxf_path)
        for e in doc.modelspace().query("DIMENSION"):
            dim_count += 1
            t = getattr(getattr(e, "dxf", None), "text", "") or ""
            if t and t != "<>":
                dim_vals.append(t)
    except Exception:
        try:
            dim_count = open(dxf_path, encoding="utf-8", errors="ignore").read().count("DIMENSION")
        except Exception:
            pass
    return (f"【几何应有特征(来自veritas识别)】孔数={holes_n}  孔类型={htypes}  独特孔径φ={diams}  外形W×H×D={overall}\n"
            f"【DXF实际标注】尺寸标注实体数={dim_count}  标注值样本={dim_vals[:25]}\n"
            f"【审计任务】按 system 的 GB 合规清单判断: 1)标注充分否(孔径/孔距/外形/厚度/倒角是否覆盖所有特征)? "
            f"2)加工商必需信息齐全否? 3)重点: 几何识别到 {holes_n} 个孔(类型{htypes}), DXF孔径标注须覆盖全部独特孔径; "
            f"沉头孔(csink)须标沉头直径×角度, 螺纹孔(thread)须标M规格+公差。缺则明确指出'标注严重不足'并列出缺失项。")


def _veritas_to_geometry(veritas_path: str, out_path: str) -> tuple:
    """识别(veritas) → annotator 期望的 geometry.json: bbox/W,H,D/holes_2d.
    把 Z 轴 PIERCING 孔投影到俯视图(XY)成 {x,y,r,d}, 保留沉头/螺纹/通孔元信息。"""
    import json as _j
    v = _j.load(open(veritas_path, encoding="utf-8"))
    bb = v["bbox"]
    geom = {
        "bbox": bb,
        "width": round(bb["xmax"] - bb["xmin"], 3),
        "height": round(bb["ymax"] - bb["ymin"], 3),
        "depth": round(bb["zmax"] - bb["zmin"], 3),
        "thickness": v.get("thickness"),
        "thickness_axis": v.get("thickness_axis"),
        "holes_2d": [],
        "holes_meta": [],
        "chamfers": sum(1 for f in v.get("features", []) if f.get("type") == "CHAMFER"),
    }
    for f in v.get("features", []):
        if f.get("type") != "PIERCING":
            continue
        pos = f.get("position", [0, 0, 0])
        h = {"x": round(pos[0], 3), "y": round(pos[1], 3),
             "r": round(f.get("radius", 0), 3), "d": round(f.get("diameter", 0), 3)}
        geom["holes_meta"].append({**h, "axis": f.get("axis_dir"), "through": f.get("through"),
                                    "hole_type": f.get("hole_type"), "all_radii": f.get("all_radii")})
        # 俯视图(Top, XY面)只放 Z 轴孔(从顶面可见为圆); X/Y 轴孔在主/侧视图才可见
        if f.get("axis_dir") == "Z":
            geom["holes_2d"].append(h)
    _j.dump(geom, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return geom, len(geom["holes_2d"])


async def _run_convert_bg(conv_id: str):
    """后台: veritas(已存在)→projection→render→DXF. 写 ConversionTask(id=conv_id) 供前端轮询, 完成后跑 audit."""
    db = SessionLocal()
    try:
        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        if not conv:
            return
        ctx = dict(conv.context or {})
        stp_path = ctx.get("stp_path")
        work_dir = ctx.get("work_dir")
        if not stp_path or not work_dir:
            add_message(conv, "ai", "❌ 缺少STP路径，无法转换", "failed")
            conv.stage = "failed"; db.commit(); return
        task = db.query(ConversionTask).filter(ConversionTask.id == conv_id).first()
        if not task:
            task = ConversionTask(id=conv_id, user_id=conv.user_id, stp_path=stp_path, work_dir=work_dir, status="projecting")
            db.add(task)
        else:
            task.status = "projecting"
        task.heartbeat = datetime.now(timezone.utc); db.commit()

        fc_bin = os.environ.get("FC_BIN", "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd")
        proj_out = f"{work_dir}/proj_v3.json"
        geom_path = f"{work_dir}/geometry.json"
        ann_path = f"{work_dir}/annotation.json"
        dxf_path = f"{work_dir}/output.dxf"

        # === AI 绘图规划 (to_thread 前异步调 LLM): veritas + 顾问 → drawing_plan.json ===
        plan_path = f"{work_dir}/drawing_plan.json"
        plan_obj = None
        features_json = ""
        try:
            features_json = open(f"{work_dir}/veritas.json", encoding="utf-8").read()
            consultation = ctx.get("consultation_answer", "") or ""
            process = ctx.get("process", "") or (ctx.get("classify_result") or {}).get("process", "")
            plan_obj = await make_drawing_plan(features_json, consultation, process)
            save_plan(plan_obj, plan_path)
            ctx["plan_path"] = plan_path; conv.context = ctx; db.commit()
        except Exception as pe:
            add_message(conv, "ai", f"⚠️ AI绘图规划未完成(降级规则标注)：{str(pe)[:100]}", "convert"); db.commit()
            plan_path = None

        def _pipe():
            env = {**os.environ, "STP": stp_path, "OUT": proj_out}
            subprocess.run([fc_bin, f"{SAAS_CORE}/projection_v3.py"], env=env, capture_output=True, timeout=600, cwd=work_dir)
            _veritas_to_geometry(f"{work_dir}/veritas.json", geom_path)
            ann_cmd = ["python3", f"{SAAS_CORE}/annotator.py", geom_path, "--projection", proj_out, "-o", ann_path]
            if plan_path: ann_cmd += ["--plan", plan_path]
            subprocess.run(ann_cmd, capture_output=True, timeout=60, cwd=SAAS_CORE)
            rcmd = ["python3", f"{SAAS_CORE}/render_engine.py", "--projection", proj_out,
                    "--geometry", geom_path, "--annotation", ann_path, "--veritas", f"{work_dir}/veritas.json"]
            if plan_path: rcmd += ["--plan", plan_path]
            rcmd += ["-o", dxf_path]
            subprocess.run(rcmd, capture_output=True, timeout=60, cwd=SAAS_CORE)
            return dxf_path
        await asyncio.to_thread(_pipe)

        if os.path.exists(dxf_path):
            # 修复 DXF header ($EXTMIN/$EXTMAX 空白根因) + 平移到原点
            try:
                subprocess.run(["python3", f"{SAAS_CORE}/fix_dxf_extents.py", dxf_path],
                               capture_output=True, timeout=30, cwd=SAAS_CORE)
            except Exception:
                pass
            task.dxf_path = dxf_path; task.status = "done"; task.finished_at = datetime.now(timezone.utc); db.commit()
            ctx["dxf_path"] = dxf_path; conv.context = ctx; db.commit()
            # AI 逐特征审计: veritas × drawing_plan × DXF 实体 + GB 合规清单
            try:
                await start_audit(conv, db, features_json or "{}", plan_obj or {}, dxf_path)
                db.commit()
            except Exception as ae:
                add_message(conv, "ai", f"⚠️ AI审查未完成：{str(ae)[:120]}", "convert"); db.commit()
        else:
            task.status = "failed"; task.error = "render未输出DXF"; db.commit()
            add_message(conv, "ai", "❌ 转换失败：未生成DXF", "failed"); conv.stage = "failed"; db.commit()
    except Exception as e:
        try:
            t = db.query(ConversionTask).filter(ConversionTask.id == conv_id).first()
            if t: t.status = "failed"; t.error = str(e)[:300]
            c = db.query(Conversation).filter(Conversation.id == conv_id).first()
            if c: add_message(c, "ai", f"❌ 转换异常：{str(e)[:200]}", "failed"); c.stage = "failed"
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


async def start_audit(conv: Conversation, db: Session, features_json: str, plan: dict, dxf_path: str):
    """M7: AI逐特征审计(转换完成后调用). 确定性 coverage(veritas×plan×DXF) + LLM GB 合规裁决."""
    result = await audit(features_json, plan, dxf_path)
    ctx = dict(conv.context or {})
    ctx["audit_result"] = result
    conv.context = ctx
    score = result.get("readiness_score", 0)
    cov = result.get("feature_coverage", {}) or {}
    holes = cov.get("holes", {}); csink = cov.get("csink", {}); thread = cov.get("thread", {})
    msg = f"🔍 AI审查结果：\n加工就绪度：{score}/100\n"
    msg += (f"特征覆盖：孔 {holes.get('covered',0)}/{holes.get('total',0)}、"
            f"沉头 {csink.get('covered',0)}/{csink.get('total',0)}、"
            f"螺纹 {thread.get('covered',0)}/{thread.get('total',0)}\n")
    missing = result.get("missing", [])
    if missing:
        msg += f"缺失项：{', '.join(str(m) for m in missing[:6])}\n"
    msg += "\n✅ 确认接受？或【纠正】补充？"
    add_message(conv, "ai", msg, "audit")
    conv.stage = "audit"
    db.commit()
