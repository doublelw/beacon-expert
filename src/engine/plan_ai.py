"""M2: AI 绘图规划器 — 工艺感知版: LLM 读 veritas + 顾问 + process → drawing_plan.json.

架构位置: 识别(veritas) → **plan_ai(AI 决策, 工艺感知)** → 规则执行(annotator/render) → AI 审计.
判据 = GB/T 国标 + 清晰交代零件全部细节 + 工艺专属信息(钣金折弯/焊接, 机加螺纹/沉孔, 注塑拔模...).

工艺分支 (process 不再只当标签):
- 钣金(含钣金+焊接): 6 标准 + 折弯 section + 展开图 auxiliary + 翻边/压铆 detail;
  annotations 含 bend/weld; 板厚 t={sheet_thickness}(薄板料厚, 非 bbox 跨度);
  tech_req 含 GB/T 1804-m / GB/T 324 / 去毛刺钝化.
- 机加(默认): 6 标准 + 沉头/盲孔 section + 密集孔 detail; thread/csink/diameter/chamfer/thickness. (原逻辑保留)
- 注塑/铸造/3D打印/焊接: 各自默认视图集与标注规则(简化但区分).

实现:
- PLAN_PROMPT 按工艺注入规则段 (sentinel __PROCESS_RULES__).
- _rule_fallback_plan 按 _process_kind(process) 分支.
- _validate_and_fix: 钣金件确保至少有 bend/weld 样本 + 展开图/折弯 section (供 render/审计).
- plan_meta 新增 process_confirmed_by_user 标记.
"""
import json
import re
from src.engine.llm_call import call_llm
from src.engine.gb_knowledge import gb_dimensioning_context, gb_consultation_context

MODEL = "glm-5-turbo"
MAX_TOKENS = 2000

# 6 个标准视图名(GB/T 17452-1998 视图命名惯例).
STANDARD_VIEWS = ["Front", "Back", "Top", "Bottom", "Left", "Right"]


# ========================= 工艺规则段 (注入 PLAN_PROMPT) =========================

PROCESS_RULES_SHEETMETAL = """【工艺 = 钣金 (含钣金+焊接) — 按钣金行业惯例 + GB/T 4458.4 + GB/T 324 规划】
**此工艺规则优先级高于下方通用机加规则**, 必须产出:
- views 额外包含:
  • 折弯剖面 section: type="section", plane={"axis":"Y","offset":0}, source="Front", label="A-A", purpose="垂直折弯线剖切, 显示折弯角/折弯R/减薄".
  • 展开图 auxiliary: type="auxiliary", view="Flat-Pattern", source="Front", label="展开图", purpose="标注展开长度 + 孔位展开坐标".
  • 翻边/压铆区 detail: type="detail", source="Top", zone={"cx":0,"cy":0,"r":25}, scale=3, label="A(3:1)".
- annotations 钣金专属:
  • 折弯 type="bend": value="折弯 90° R1.0 ↑", spec={"angle":90,"radius":1.0,"direction":"up","k_factor":0.3}. 至少 1~2 条样例.
  • 焊缝 type="weld": value="角焊缝 3♭", spec={"weld_type":"fillet","size":3,"symbol":"♭","standard":"GB/T 324"}. 钣金件必给 (供 render/审计锚点).
  • 板厚 type="thickness": value="t{sheet_thickness}", 注意 sheet_thickness=薄板料厚 (非 bbox 跨度); 若顾问/工艺文本含 "板厚X/t=X" 取 X, 否则默认 1.5.
  • 翻边孔/压铆螺纹孔 仍用 csink/thread (按 veritas hole_type), 普孔用 diameter, 孔位坐标用展开图系.
- roughness: 折弯变形面 Ra3.2 / 焊接面 Ra6.3 / 外形 Ra6.3.
- tech_req 追加: "折弯未注尺寸按 GB/T 1804-m" / "焊缝按 GB/T 324" / "去毛刺、钝化"."""

PROCESS_RULES_MACHINING = """【工艺 = 机加工 (车/铣/钻) — 按 GB/T 4458.4 + GB/T 4459.1 规划 (默认)】
- views 额外包含:
  • 沉头孔(csink)/盲孔(through=false/span 大) section: plane={"axis","offset"}, source=源视图, label="A-A"/"B-B".
  • 密集孔群 detail: source=源视图, zone={"cx","cy","r"}, scale=2~5, label="X(N:1)".
  • 轴向孔(axis_dir=X/Y)若主视图看不清 auxiliary.
- annotations:
  • thread 螺纹 → "M{d}-6H"(粗牙默认), spec={"d","pitch","class":"6H"}.
  • csink 沉头 → "%%C{big_d}x90%%D", big_d=all_radii 最大值×2.
  • clear/通孔 → "%%C{d}"; 盲孔加深度 "↓{span}".
  • CHAMFER → "C{c}", c 取 radius(<1)或 C2/C3.
  • thickness → "t{thickness}", thickness=veritas 壁厚/板厚(bbox 跨度合理值).
- roughness: 配合面 Ra1.6 / 非配合 Ra6.3 / 不去除材料 Ra25.
- tech_req: 未注公差 GB/T 1804-m / 未注形位 GB/T 1184-K / 锐边倒钝 R0.5 / 去毛刺."""

PROCESS_RULES_INJECTION = """【工艺 = 注塑 — 关注拔模角/壁厚/熔接痕】
- views 额外: 壁厚剖面 section (显示壁厚均匀性) + 拔模方向 auxiliary (type="auxiliary", 标拔模方向).
- annotations: draft (拔模角 "拔模角 1°") / wall (壁厚 "壁厚 2.0") / radius (圆角 "R0.5") / 倒角仍用 chamfer.
- roughness: 外观面 Ra0.8 (抛光 Ra0.4) / 非外观 Ra1.6.
- tech_req: 拔模斜度 1° / 未注壁厚均匀 / 浇口位置 / 合模线去毛刺."""

PROCESS_RULES_CASTING = """【工艺 = 铸造/压铸 — 关注分型面/圆角/壁厚】
- views 额外: 分型面 section (显示分型线) + 圆角/壁厚 detail.
- annotations: fillet (铸造圆角 "R3-R5") / draft (拔模角 "拔模角 2°") / wall (壁厚).
- roughness: 铸造面 Ra12.5 / 机加面 Ra1.6.
- tech_req: 铸造圆角 R3-R5 / 分型面错移 ≤0.3 / 拔模斜度 1°-3° / 未注壁厚."""

PROCESS_RULES_WELDING = """【工艺 = 焊接 (组焊件) — 关注焊缝符号/焊接顺序】
- views 额外: 焊缝 section (剖切显示焊深) + 焊缝区 detail.
- annotations: weld (GB/T 324 符号: 角焊 ♭/对接 ∇/点焊 〇), 标注焊缝尺寸.
- roughness: 焊缝面 Ra6.3 / 机加面 Ra1.6.
- tech_req: 焊缝按 GB/T 324 / 焊后去应力退火 / 焊缝检测等级 II."""

PROCESS_RULES_3DPRINT = """【工艺 = 3D 打印/增材 — 关注打印方向/最小壁厚/纹理】
- views 额外: 最小壁厚 section + 打印方向 auxiliary (标 0° 基准与层纹方向).
- annotations: wall (最小壁厚 "最小壁厚 0.8") / texture (层纹方向 "层纹方向 Z").
- roughness: X-Y 平面 Ra6.3 / Z 层纹 Ra12.5.
- tech_req: 打印方向 / 层厚 0.1-0.3mm / 最小壁厚 0.8mm / 后处理(去支撑/打磨)."""


def _process_kind(process: str) -> str:
    """工艺归类: sheetmetal/machining/injection/casting/welding/3dprint. 未识别默认 machining."""
    p = (process or "").strip()
    pl = p.lower()
    if "钣金" in p or "sheet" in pl or "折弯" in p:
        return "sheetmetal"
    if "机加" in p or "machin" in pl or "cnc" in pl or "铣" in p or "车削" in p or "钻" in p:
        return "machining"
    if "注塑" in p or "inject" in pl or "塑料" in p:
        return "injection"
    if "铸造" in p or "压铸" in p or "cast" in pl:
        return "casting"
    if "焊接" in p or "weld" in pl:
        return "welding"
    if "3d" in pl or "打印" in p or "print" in pl or "增材" in p:
        return "3dprint"
    return "machining"


def _process_rules(process: str) -> str:
    """根据 process 选工艺规则段, 注入 PLAN_PROMPT."""
    return {
        "sheetmetal": PROCESS_RULES_SHEETMETAL,
        "machining": PROCESS_RULES_MACHINING,
        "injection": PROCESS_RULES_INJECTION,
        "casting": PROCESS_RULES_CASTING,
        "welding": PROCESS_RULES_WELDING,
        "3dprint": PROCESS_RULES_3DPRINT,
    }.get(_process_kind(process), PROCESS_RULES_MACHINING)

PLAN_PROMPT = """你是机械制图专家(精通 GB/T 4458.4 尺寸注法 / 4458.5 / 131 / 1182 / 10609.1 / 324 焊缝 / 钣金制图).
任务: 读 3D 零件的识别特征(veritas) + 顾问信息 + **工艺(process)**, 规划这个零件的 2D 工程图画法, 产出 drawing_plan JSON.
**核心: 必须按工艺分支决策视图与标注 — 钣金件要有折弯剖面/展开图/bend/weld, 机加件要有沉头/盲孔剖视/thread/csink, 注塑件要有拔模/壁厚, 等等. 工艺规则段优先级最高.**

========== 零件识别特征 (veritas.json) ==========
__FEATURES__

========== 顾问/工艺信息 ==========
工艺: __PROCESS__
顾问答复(材料/公差/量级/优先/特殊): __CONSULTATION__

========== 特征摘要(辅助决策, 勿遗漏特征) ==========
__SUMMARY__

========== 工艺专属规则 (优先级最高, 必须遵守) ==========
__PROCESS_RULES__

========== 通用决策要求 (在工艺规则框架下细化) ==========
1. **视图集 views[]**: 至少含 Front/Back/Top/Bottom/Left/Right 中的若干 standard 视图(Front+Top+Left 起步).
   工艺专属规则要求的 section/auxiliary/detail 必须加进去. 判据: 每个识别特征(每个孔/倒角)在 ≥1 视图清晰可见.
   type 取值: standard / section(剖视,带 plane+label) / detail(局部放大,带 zone+scale) / auxiliary(向视/展开图等).
2. **标注 annotations[]** — 给出**标注规则 + 每种类型 1~2 个代表样例**即可(系统会按规则对每个 feature 自动展开, 勿逐个穷举所有孔以免输出截断):
   - view 选该特征可见的视图(Top 优先 Z 轴孔, Front/Left 优先 X/Y 轴孔; 钣金孔位可放展开图).
   - feature_id 必须严格等于 veritas 的 feature id(audit 核对主键).
   - 每种类型只放 1~2 条代表样例(真实 feature_id), 倒角只需 1 条样例. 不必穷举.
3. **粗糙度 roughness[]** (GB/T 131): 按工艺规则.
4. **形位公差 gdt[]** (GB/T 1182) + 基准 A/B/C: 至少 1 条.
5. **标题栏 title_block** (GB/T 10609.1): name=零件名(从工艺/文件推断), material=顾问给定或推断, scale 按 bbox 尺寸定(>200mm 用 1:2, 100~200 用 1:1, <100 用 2:1), sheet 按 A1/A2/A3/A4 估, drawing_no="BEACON-{seq}", company="Beacon", date=今日.
6. **技术要求 tech_req[]**: 工艺规则要求的标准引用 + 通用项(材料/去毛刺).

========== 输出格式(只输出 JSON, 无其他文字) ==========
{
  "title_block": {"name":"","drawing_no":"","material":"","scale":"","sheet":"","designer":"","checker":"","reviewer":"","date":"","company":"Beacon"},
  "views": [{"view":"Front","type":"standard"}, {"view":"Section-A","type":"section","plane":{"axis":"Y","offset":0.0},"source":"Top","label":"A-A"}],
  "annotations": [{"feature_id":"F00xx","view":"Top","type":"diameter","value":"%%C5"}],
  "roughness": [{"face":"","ra":"","placement":""}],
  "gdt": [{"char":"⊥","name":"垂直度","value":0.05,"face":"","datum":"A"}],
  "tech_req": ["未注尺寸公差按 GB/T 1804-m"],
  "plan_meta": {"part_name":"","process":"","confidence":0.0}
}
"""


def _build_summary(veritas: dict) -> str:
    """从 veritas 构造给 LLM 看的特征摘要(避免漏特征, 同时压缩 token)."""
    feats = veritas.get("features", [])
    bbox = veritas.get("bbox") or {}
    summary = veritas.get("summary") or {}
    by_hole_type = {"csink": 0, "clear": 0, "thread": 0}
    by_axis = {"X": 0, "Y": 0, "Z": 0}
    blind = 0
    for f in feats:
        if f.get("type") == "PIERCING":
            ht = f.get("hole_type", "clear")
            by_hole_type[ht] = by_hole_type.get(ht, 0) + 1
            ax = f.get("axis_dir", "?")
            by_axis[ax] = by_axis.get(ax, 0) + 1
            if not f.get("through", True):
                blind += 1
    chamfers = sum(1 for f in feats if f.get("type") == "CHAMFER")
    lines = [
        f"外形 bbox: {bbox}",
        f"壁厚 thickness: {veritas.get('thickness')} (轴 {veritas.get('thickness_axis')})",
        f"孔统计: 沉头(csink)={by_hole_type['csink']} 过孔(clear)={by_hole_type['clear']} 螺纹(thread)={by_hole_type['thread']}",
        f"孔轴向分布: X={by_axis['X']} Y={by_axis['Y']} Z={by_axis['Z']}",
        f"盲孔数(through=false): {blind}",
        f"倒角(CHAMFER)数: {chamfers}",
        f"summary: {summary}",
        f"特征总数: {len(feats)}",
    ]
    return "\n".join(lines)


async def make_drawing_plan(features_json: str, consultation: str = "", process: str = "") -> dict:
    """读 veritas 全特征 + 顾问信息 + 工艺 → 调 LLM → 返回 drawing_plan dict.

    Args:
        features_json: veritas.json 序列化字符串(或已是 dict 亦可, 内部兼容).
        consultation: 顾问阶段得到的材料/公差/量级/优先/特殊等文本.
        process: 工艺(机加工/钣金/注塑...), 来自 classify_ai. 决定视图/标注/tech_req 分支.

    Returns:
        符合 drawing_plan_schema 的 dict. LLM 失败/解析失败 → _rule_fallback_plan 兜底(也按工艺分支).
    """
    # veritas 既可能是 str 也可能是 dict, 统一成 dict.
    try:
        veritas = json.loads(features_json) if isinstance(features_json, str) else features_json
    except Exception:
        veritas = {"features": [], "raw": features_json}

    summary_str = _build_summary(veritas)
    # 给 LLM 的 features 用原 veritas(features 完整, 但截断超大文本以防超 token).
    veritas_for_llm = json.dumps(veritas, ensure_ascii=False)
    if len(veritas_for_llm) > 6000:
        # 太长则只保留 PIERCING/OUTLINE + 头部信息(倒角通常重复模板, 摘要已涵盖数量).
        feats = veritas.get("features", [])
        slim_feats = [f for f in feats if f.get("type") != "CHAMFER"][:60]
        veritas_slim = {k: v for k, v in veritas.items() if k != "features"}
        veritas_slim["features"] = slim_feats
        veritas_slim["_note"] = f"CHAMFER 已在摘要中给出数量, 此处省略 {len(feats)-len(slim_feats)} 条"
        veritas_for_llm = json.dumps(veritas_slim, ensure_ascii=False)

    system = gb_dimensioning_context() + "\n\n" + gb_consultation_context()
    # 用 sentinel 替换而非 .format(), 避免 prompt 内 JSON 示例的花括号被误当占位符.
    prompt = (PLAN_PROMPT
              .replace("__FEATURES__", veritas_for_llm)
              .replace("__PROCESS__", process or "(未指定)")
              .replace("__CONSULTATION__", consultation or "(用户未明确回答)")
              .replace("__SUMMARY__", summary_str)
              .replace("__PROCESS_RULES__", _process_rules(process)))

    result = await call_llm(prompt, model=MODEL, system=system, max_tokens=MAX_TOKENS, temperature=0)
    text = result.get("text", "") if isinstance(result, dict) else ""
    if text:
        plan = _parse_plan_json(text)
        if plan is not None:
            # 校验 & 补全: 确保所有顶层 key 存在, feature_id 覆盖所有 PIERCING.
            return _validate_and_fix(plan, veritas, process)
    # 兜底
    return _rule_fallback_plan(veritas, consultation=consultation, process=process)


def _parse_plan_json(text: str) -> dict | None:
    """从 LLM 文本中解析 drawing_plan JSON.

    容错: 跳过 ```json 代码块围栏; 若被 max_tokens 截断(末尾不完整),
    逐步回退到最后一个完整闭合点并补全外层括号, 尽力复原一个可解析的对象.
    """
    if not text:
        return None
    s = text.find("{")
    e = text.rfind("}") + 1
    if s < 0 or e <= s:
        return None
    chunk = text[s:e]
    # 1) 直接解析(最理想).
    try:
        return json.loads(chunk)
    except Exception:
        pass
    # 2) 截断修复: 从末尾向前找最后一个完整的 '}' (栈平衡), 然后闭合未关的 '[' 和 '{'.
    repaired = _repair_truncated(chunk)
    if repaired:
        try:
            return json.loads(repaired)
        except Exception:
            pass
    return None


def _repair_truncated(chunk: str) -> str:
    """对被截断的 JSON 文本, 截到栈平衡点再补上闭合括号."""
    depth_sq = depth_cu = 0
    in_str = False
    esc = False
    last_balanced = -1
    for i, ch in enumerate(chunk):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth_sq += 1
        elif ch == "]":
            depth_sq -= 1
        elif ch == "{":
            depth_cu += 1
        elif ch == "}":
            depth_cu -= 1
        if depth_sq == 0 and depth_cu == 0:
            last_balanced = i
    if last_balanced >= 0 and last_balanced < len(chunk) - 1:
        # 在最后一个完整闭合对象处截断(可能丢掉部分数组尾部元素, 但保住已完成的顶层).
        return chunk[: last_balanced + 1]
    # 栈未平衡: 截到最后一个完整 value/元素, 再补闭合.
    # 找最后一个 ',' 或 '{'/'[' 之后的位置, 去掉半截元素.
    cut = chunk.rstrip().rstrip(",")
    # 去掉末尾半截键值对/元素(到倒数第一个完整逗号).
    last_comma = max(cut.rfind(","), cut.rfind("{"), cut.rfind("["))
    if last_comma > 0:
        cut = cut[: last_comma + 1].rstrip(",").rstrip()
    # 补齐未闭合的括号.
    opens = []
    in_str2 = False
    esc2 = False
    for ch in cut:
        if in_str2:
            if esc2:
                esc2 = False
            elif ch == "\\":
                esc2 = True
            elif ch == '"':
                in_str2 = False
            continue
        if ch == '"':
            in_str2 = True
        elif ch in "[{":
            opens.append(ch)
        elif ch in "]}":
            if opens and ((opens[-1] == "[" and ch == "]") or (opens[-1] == "{" and ch == "}")):
                opens.pop()
    closing = "".join("]" if o == "[" else "}" for o in reversed(opens))
    return cut + closing


def _validate_and_fix(plan: dict, veritas: dict, process: str = "") -> dict:
    """轻校验: 顶层 key 齐全; annotations 覆盖每个 PIERCING; 钣金件确保 bend/weld 样本 + 展开图/折弯 section."""
    for k in ("title_block", "views", "annotations", "roughness", "gdt", "tech_req", "plan_meta"):
        if k not in plan or not isinstance(plan[k], (list, dict)):
            plan[k] = [] if k in ("views", "annotations", "roughness", "gdt", "tech_req") else ({} if k in ("title_block", "plan_meta") else [])
    # 补漏: veritas 每个 PIERCING 若 LLM 没标, 补一条 diameter 兜底, 保证 audit 覆盖率.
    covered = {a.get("feature_id") for a in plan["annotations"] if isinstance(a, dict)}
    for f in veritas.get("features", []):
        fid = f.get("id")
        if f.get("type") == "PIERCING" and fid and fid not in covered:
            d = f.get("diameter")
            ht = f.get("hole_type", "clear")
            ax = f.get("axis_dir", "Z")
            view = "Top" if ax == "Z" else ("Front" if ax == "X" else "Left")
            if ht == "thread":
                plan["annotations"].append({"feature_id": fid, "view": view, "type": "thread",
                                            "value": f"M{d}-6H", "spec": {"d": d, "pitch": 0.8, "class": "6H"}})
            elif ht == "csink":
                big_d = round(max(f.get("all_radii", [d / 2]) or [d / 2]) * 2, 1)
                plan["annotations"].append({"feature_id": fid, "view": view, "type": "csink",
                                            "value": f"%%C{big_d}x90%%D", "spec": {"big_d": big_d, "angle": 90}})
            else:
                plan["annotations"].append({"feature_id": fid, "view": view, "type": "diameter", "value": f"%%C{d}"})

    # ===== 工艺感知补全: 钣金件确保至少有 bend/weld 样本 + 展开图/折弯剖面 (即使 veritas 没 bends, 也放样本让 render/审计有据) =====
    if _process_kind(process) == "sheetmetal":
        types_present = {a.get("type") for a in plan["annotations"] if isinstance(a, dict)}
        if "bend" not in types_present:
            plan["annotations"].append({"feature_id": None, "view": "Front", "type": "bend",
                                        "value": "折弯 90° R1.0 ↑",
                                        "spec": {"angle": 90, "radius": 1.0, "direction": "up", "k_factor": 0.3}})
        if "weld" not in types_present:
            plan["annotations"].append({"feature_id": None, "view": "Front", "type": "weld",
                                        "value": "角焊缝 3♭",
                                        "spec": {"weld_type": "fillet", "size": 3, "symbol": "♭", "standard": "GB/T 324"}})
        # 视图补全: 展开图 auxiliary + 折弯剖面 section
        view_tuples = [(v.get("view"), v.get("type"), v.get("label"), v.get("purpose", ""))
                       for v in plan["views"] if isinstance(v, dict)]
        has_flat_aux = any(
            t == "auxiliary" and ("展开" in str(l or "") or "展开" in str(p or "") or "Flat" in str(n or ""))
            for n, t, l, p in view_tuples
        )
        has_bend_sec = any(t == "section" and "折弯" in str(p) for n, t, l, p in view_tuples)
        if not has_flat_aux:
            plan["views"].append({"view": "Flat-Pattern", "type": "auxiliary", "source": "Front",
                                  "label": "展开图", "purpose": "钣金展开图: 标注展开长度与孔位展开坐标"})
        if not has_bend_sec:
            plan["views"].append({"view": "Section-Bend-A", "type": "section",
                                  "plane": {"axis": "Y", "offset": 0.0}, "source": "Front", "label": "A-A",
                                  "purpose": "折弯剖面: 垂直折弯线剖切, 显示折弯角/折弯R/减薄"})

    # plan_meta
    pm = plan.get("plan_meta") or {}
    pm.setdefault("process", process or "")
    pm["process_confirmed_by_user"] = bool((process or "").strip()) and "(未指定)" not in (process or "")
    pm.setdefault("confidence", 0.85)
    pm.setdefault("part_name", (plan.get("title_block") or {}).get("name", ""))
    plan["plan_meta"] = pm
    return plan


def _rule_fallback_plan(veritas: dict, consultation: str = "", process: str = "") -> dict:
    """LLM 不可用时的规则兜底. 按 process 分支:
    - sheetmetal → _sheetmetal_fallback (折弯 section + 展开图 auxiliary + bend/weld)
    - injection/casting/welding/3dprint → _simple_process_fallback
    - machining/default → _machining_fallback (原逻辑: 6 标准 + 沉头/盲孔 section + thread/csink/diameter)
    """
    kind = _process_kind(process)
    if kind == "sheetmetal":
        return _sheetmetal_fallback(veritas, consultation, process)
    if kind in ("injection", "casting", "welding", "3dprint"):
        return _simple_process_fallback(veritas, consultation, process, kind)
    return _machining_fallback(veritas, consultation, process)


def _machining_fallback(veritas: dict, consultation: str = "", process: str = "") -> dict:
    """机加规则兜底: 6 标准视图 + 每孔一条标注 + 配合面 Ra1.6 + 标题栏默认. (原 _rule_fallback_plan 逻辑, 保留)"""
    feats = veritas.get("features", []) if isinstance(veritas, dict) else []
    bbox = veritas.get("bbox") or {}
    thickness = veritas.get("thickness", 0)
    w = max(abs(bbox.get("xmax", 0) - bbox.get("xmin", 0)), abs(bbox.get("width") or 0))
    # scale 估算
    if w > 300:
        scale, sheet = "1:3", "A1"
    elif w > 200:
        scale, sheet = "1:2", "A2"
    elif w > 100:
        scale, sheet = "1:1", "A3"
    else:
        scale, sheet = "2:1", "A4"

    views = [{"view": v, "type": "standard"} for v in STANDARD_VIEWS]
    # Z 轴孔多的零件默认 Front+Top+Left 已够, 但沉头/盲孔需剖视, 加一个 A-A 剖.
    has_csink_or_blind = any(
        f.get("type") == "PIERCING" and (f.get("hole_type") == "csink" or not f.get("through", True))
        for f in feats
    )
    if has_csink_or_blind:
        views.append({"view": "Section-A", "type": "section",
                      "plane": {"axis": "Y", "offset": 0.0}, "source": "Top", "label": "A-A"})

    annotations = []
    for f in feats:
        if f.get("type") != "PIERCING":
            if f.get("type") == "CHAMFER":
                c = round(f.get("radius", 2.5), 1)
                annotations.append({"feature_id": f.get("id"), "view": "Front",
                                    "type": "chamfer", "value": f"C{c}"})
            continue
        fid = f.get("id")
        d = f.get("diameter")
        ht = f.get("hole_type", "clear")
        ax = f.get("axis_dir", "Z")
        view = "Top" if ax == "Z" else ("Front" if ax == "X" else "Left")
        if ht == "thread":
            annotations.append({"feature_id": fid, "view": view, "type": "thread",
                                "value": f"M{d}-6H", "spec": {"d": d, "pitch": 0.8, "class": "6H"}})
        elif ht == "csink":
            big_d = round(max(f.get("all_radii", [d / 2]) or [d / 2]) * 2, 1)
            annotations.append({"feature_id": fid, "view": view, "type": "csink",
                                "value": f"%%C{big_d}x90%%D", "spec": {"big_d": big_d, "angle": 90}})
        else:
            v = f"%%C{d}"
            if not f.get("through", True):
                v += f" ↓{f.get('span', '')}"
            annotations.append({"feature_id": fid, "view": view, "type": "diameter", "value": v})
    # 板厚标注
    if thickness:
        annotations.append({"feature_id": None, "view": "Front", "type": "thickness", "value": f"t{thickness}"})

    roughness = [
        {"face": "配合孔/配合面", "ra": "Ra1.6", "placement": "孔周围"},
        {"face": "非配合面/外形", "ra": "Ra6.3", "placement": "外形附近"},
        {"face": "不去除材料", "ra": "Ra25", "placement": "右上"},
    ]
    gdt = [{"char": "⊥", "name": "垂直度", "value": 0.05, "face": "配合孔", "datum": "A"}]
    tech_req = [
        "未注尺寸公差按 GB/T 1804-m",
        "未注形位公差按 GB/T 1184-K",
        "锐边倒钝 R0.5",
        f"材料: {_guess_material(consultation, process)}",
        "表面去毛刺、清洁",
    ]
    title_block = {
        "name": _guess_part_name(veritas, process),
        "drawing_no": "BEACON-001",
        "material": _guess_material(consultation, process),
        "scale": scale,
        "sheet": sheet,
        "designer": "", "checker": "", "reviewer": "",
        "date": "2026-07-03",
        "company": "Beacon",
    }
    return {
        "title_block": title_block,
        "views": views,
        "annotations": annotations,
        "roughness": roughness,
        "gdt": gdt,
        "tech_req": tech_req,
        "plan_meta": {"part_name": title_block["name"], "process": process or "", "confidence": 0.6,
                      "source": "rule_fallback_machining",
                      "process_confirmed_by_user": bool((process or "").strip()) and "(未指定)" not in (process or "")},
    }


def _guess_sheet_thickness(process: str, consultation: str, veritas: dict) -> float:
    """从 process/consultation 文本提取薄板料厚; 提不到则看 veritas.thickness(<6 才合理), 否则默认 1.5mm."""
    text = (process or "") + " " + (consultation or "")
    m = re.search(r"(?:板厚|料厚|sheet[_\s-]?thick(?:ness)?|t\s*[=：:])\s*([\d.]+)", text, re.IGNORECASE)
    if m:
        try:
            v = float(m.group(1))
            if 0.1 <= v <= 20:
                return round(v, 2)
        except ValueError:
            pass
    t = veritas.get("thickness") if isinstance(veritas, dict) else None
    if isinstance(t, (int, float)) and 0 < t < 6:
        return round(float(t), 1)
    return 1.5


def _sheetmetal_fallback(veritas: dict, consultation: str, process: str) -> dict:
    """钣金兜底: 6 标准 + 折弯 section + 展开图 auxiliary + 翻边/压铆 detail; bend/weld/thickness 标注."""
    feats = veritas.get("features", []) if isinstance(veritas, dict) else []
    bbox = veritas.get("bbox") or {}
    sheet_t = _guess_sheet_thickness(process, consultation, veritas)

    w = max(abs(bbox.get("xmax", 0) - bbox.get("xmin", 0)), abs(bbox.get("width") or 0))
    h = max(abs(bbox.get("ymax", 0) - bbox.get("ymin", 0)), abs(bbox.get("height") or 0))
    max_dim = max(w, h)
    if max_dim > 300:
        scale, sheet = "1:3", "A1"
    elif max_dim > 200:
        scale, sheet = "1:2", "A2"
    elif max_dim > 100:
        scale, sheet = "1:1", "A3"
    else:
        scale, sheet = "2:1", "A4"

    views = [{"view": v, "type": "standard"} for v in STANDARD_VIEWS]
    views.append({"view": "Section-Bend-A", "type": "section",
                  "plane": {"axis": "Y", "offset": 0.0}, "source": "Front", "label": "A-A",
                  "purpose": "折弯剖面: 垂直折弯线剖切, 显示折弯角/折弯R/减薄"})
    views.append({"view": "Flat-Pattern", "type": "auxiliary", "source": "Front",
                  "label": "展开图", "purpose": "钣金展开图: 标注展开长度与孔位展开坐标"})
    views.append({"view": "Detail-A", "type": "detail", "source": "Top",
                  "zone": {"cx": 0, "cy": 0, "r": 25}, "scale": 3, "label": "A(3:1)",
                  "purpose": "翻边孔/压铆区局部放大"})

    annotations = []
    # 钣金工艺样本 (供 render/audit 锚点, 即使 veritas 无 bends 也放)
    annotations.append({"feature_id": None, "view": "Front", "type": "bend",
                        "value": f"折弯 90° R{sheet_t} ↑",
                        "spec": {"angle": 90, "radius": sheet_t, "direction": "up", "k_factor": 0.3}})
    annotations.append({"feature_id": None, "view": "Front", "type": "weld",
                        "value": "角焊缝 3♭",
                        "spec": {"weld_type": "fillet", "size": 3, "symbol": "♭", "standard": "GB/T 324"}})
    annotations.append({"feature_id": None, "view": "Front", "type": "thickness",
                        "value": f"t{sheet_t}", "spec": {"sheet_thickness": sheet_t, "note": "薄板料厚"}})

    # per-feature (翻边/压铆孔保留 csink/thread; 普孔 diameter, 用展开坐标)
    for f in feats:
        if f.get("type") != "PIERCING":
            continue
        fid = f.get("id")
        d = f.get("diameter")
        ht = f.get("hole_type", "clear")
        ax = f.get("axis_dir", "Z")
        view = "Flat-Pattern" if ax == "Z" else ("Front" if ax == "X" else "Left")
        if ht == "csink":
            big_d = round(max(f.get("all_radii", [d / 2]) or [d / 2]) * 2, 1)
            annotations.append({"feature_id": fid, "view": view, "type": "csink",
                                "value": f"%%C{big_d}x90%%D",
                                "spec": {"big_d": big_d, "angle": 90}, "note": "翻边孔/沉孔"})
        elif ht == "thread":
            annotations.append({"feature_id": fid, "view": view, "type": "thread",
                                "value": f"M{d}-6H",
                                "spec": {"d": d, "pitch": 0.8, "class": "6H"}, "note": "压铆螺母/螺纹孔"})
        else:
            annotations.append({"feature_id": fid, "view": view, "type": "diameter",
                                "value": f"%%C{d}", "spec": {"d": d}, "note": "孔位用展开坐标"})

    roughness = [
        {"face": "折弯变形面", "ra": "Ra3.2", "placement": "折弯线附近"},
        {"face": "焊接面", "ra": "Ra6.3", "placement": "焊缝附近"},
        {"face": "外形/非配合面", "ra": "Ra6.3", "placement": "外形附近"},
    ]
    gdt = [
        {"char": "∠", "name": "倾斜度", "value": 1.0, "face": "折弯角", "datum": "A"},
        {"char": "⊥", "name": "垂直度", "value": 0.2, "face": "翻边孔", "datum": "A"},
    ]
    tech_req = [
        "折弯未注尺寸按 GB/T 1804-m",
        "焊缝按 GB/T 324",
        "未注形位公差按 GB/T 1184-K",
        f"板厚 t={sheet_t}",
        "去毛刺、钝化",
        f"材料: {_guess_material(consultation, process)}",
    ]
    title_block = {
        "name": _guess_part_name(veritas, process),
        "drawing_no": "BEACON-001",
        "material": _guess_material(consultation, process),
        "scale": scale,
        "sheet": sheet,
        "designer": "", "checker": "", "reviewer": "",
        "date": "2026-07-03",
        "company": "Beacon",
    }
    return {
        "title_block": title_block,
        "views": views,
        "annotations": annotations,
        "roughness": roughness,
        "gdt": gdt,
        "tech_req": tech_req,
        "plan_meta": {"part_name": title_block["name"], "process": process or "", "confidence": 0.6,
                      "source": "rule_fallback_sheetmetal",
                      "process_confirmed_by_user": bool((process or "").strip()) and "(未指定)" not in (process or ""),
                      "sheet_thickness": sheet_t},
    }


def _simple_process_fallback(veritas: dict, consultation: str, process: str, kind: str) -> dict:
    """注塑/铸造/3D打印/焊接 的简化兜底: 6 标准 + 工艺专属 section/auxiliary + 工艺专属标注."""
    feats = veritas.get("features", []) if isinstance(veritas, dict) else []
    bbox = veritas.get("bbox") or {}
    w = max(abs(bbox.get("xmax", 0) - bbox.get("xmin", 0)), abs(bbox.get("width") or 0))
    if w > 300:
        scale, sheet = "1:3", "A1"
    elif w > 200:
        scale, sheet = "1:2", "A2"
    elif w > 100:
        scale, sheet = "1:1", "A3"
    else:
        scale, sheet = "2:1", "A4"

    views = [{"view": v, "type": "standard"} for v in STANDARD_VIEWS]
    annotations = []
    gdt = [{"char": "⊥", "name": "垂直度", "value": 0.1, "face": "基准面", "datum": "A"}]
    default_mat = _guess_material(consultation, process)

    if kind == "injection":
        views.append({"view": "Section-Wall-A", "type": "section",
                      "plane": {"axis": "Y", "offset": 0.0}, "source": "Front", "label": "A-A",
                      "purpose": "壁厚剖面: 显示壁厚均匀性"})
        views.append({"view": "Draft-Dir", "type": "auxiliary", "source": "Top",
                      "label": "拔模方向", "purpose": "拔模角参考方向"})
        annotations.append({"feature_id": None, "view": "Front", "type": "draft",
                            "value": "拔模角 1°", "spec": {"angle": 1.0}})
        annotations.append({"feature_id": None, "view": "Front", "type": "wall",
                            "value": "壁厚 2.0", "spec": {"thickness": 2.0}})
        roughness = [
            {"face": "外观面", "ra": "Ra0.8", "placement": "外观"},
            {"face": "非外观面", "ra": "Ra1.6", "placement": "内部"},
        ]
        tech_req = ["拔模斜度 1°", "未注壁厚均匀 2.0mm", "浇口位置见模型", "合模线去毛刺",
                    f"材料: {default_mat}"]
    elif kind == "casting":
        views.append({"view": "Section-Parting-A", "type": "section",
                      "plane": {"axis": "Z", "offset": 0.0}, "source": "Front", "label": "A-A",
                      "purpose": "分型面剖面"})
        views.append({"view": "Detail-Fillet-A", "type": "detail", "source": "Top",
                      "zone": {"cx": 0, "cy": 0, "r": 25}, "scale": 3, "label": "A(3:1)",
                      "purpose": "铸造圆角/壁厚放大"})
        annotations.append({"feature_id": None, "view": "Front", "type": "fillet",
                            "value": "R3-R5", "spec": {"r_min": 3, "r_max": 5}})
        annotations.append({"feature_id": None, "view": "Front", "type": "draft",
                            "value": "拔模角 2°", "spec": {"angle": 2.0}})
        roughness = [
            {"face": "铸造面", "ra": "Ra12.5", "placement": "铸态表面"},
            {"face": "机加面", "ra": "Ra1.6", "placement": "加工面"},
        ]
        tech_req = ["铸造圆角 R3-R5", "分型面错移 ≤0.3", "拔模斜度 1°-3°", "未注壁厚均匀",
                    f"材料: {default_mat}"]
    elif kind == "3dprint":
        views.append({"view": "Section-MinWall-A", "type": "section",
                      "plane": {"axis": "Y", "offset": 0.0}, "source": "Front", "label": "A-A",
                      "purpose": "最小壁厚剖面"})
        views.append({"view": "Print-Dir", "type": "auxiliary", "source": "Front",
                      "label": "打印方向", "purpose": "标 0° 基准与层纹方向"})
        annotations.append({"feature_id": None, "view": "Front", "type": "wall",
                            "value": "最小壁厚 0.8", "spec": {"thickness": 0.8}})
        annotations.append({"feature_id": None, "view": "Front", "type": "texture",
                            "value": "层纹方向 Z", "spec": {"direction": "Z"}})
        roughness = [
            {"face": "X-Y 平面", "ra": "Ra6.3", "placement": "层纹侧面"},
            {"face": "Z 方向", "ra": "Ra12.5", "placement": "层叠面"},
        ]
        tech_req = ["打印方向见向视图", "层厚 0.1-0.3mm", "最小壁厚 0.8mm", "后处理: 去支撑/打磨",
                    f"材料: {default_mat}"]
    else:  # welding
        views.append({"view": "Section-Weld-A", "type": "section",
                      "plane": {"axis": "Y", "offset": 0.0}, "source": "Front", "label": "A-A",
                      "purpose": "焊缝剖面: 显示焊深"})
        views.append({"view": "Detail-Weld-A", "type": "detail", "source": "Front",
                      "zone": {"cx": 0, "cy": 0, "r": 20}, "scale": 4, "label": "A(4:1)",
                      "purpose": "焊缝区放大"})
        annotations.append({"feature_id": None, "view": "Front", "type": "weld",
                            "value": "角焊缝 3♭",
                            "spec": {"weld_type": "fillet", "size": 3, "symbol": "♭", "standard": "GB/T 324"}})
        roughness = [
            {"face": "焊缝面", "ra": "Ra6.3", "placement": "焊缝"},
            {"face": "机加面", "ra": "Ra1.6", "placement": "加工面"},
        ]
        tech_req = ["焊缝按 GB/T 324", "焊后去应力退火", "焊缝检测等级 II", "去毛刺",
                    f"材料: {default_mat}"]

    # per-feature PIERCING 通用标注 (各工艺都可能有孔)
    for f in feats:
        if f.get("type") != "PIERCING":
            continue
        fid = f.get("id")
        d = f.get("diameter")
        ht = f.get("hole_type", "clear")
        ax = f.get("axis_dir", "Z")
        view = "Top" if ax == "Z" else ("Front" if ax == "X" else "Left")
        if ht == "thread":
            annotations.append({"feature_id": fid, "view": view, "type": "thread",
                                "value": f"M{d}-6H", "spec": {"d": d, "pitch": 0.8, "class": "6H"}})
        elif ht == "csink":
            big_d = round(max(f.get("all_radii", [d / 2]) or [d / 2]) * 2, 1)
            annotations.append({"feature_id": fid, "view": view, "type": "csink",
                                "value": f"%%C{big_d}x90%%D", "spec": {"big_d": big_d, "angle": 90}})
        else:
            annotations.append({"feature_id": fid, "view": view, "type": "diameter", "value": f"%%C{d}"})

    title_block = {
        "name": _guess_part_name(veritas, process),
        "drawing_no": "BEACON-001",
        "material": default_mat,
        "scale": scale,
        "sheet": sheet,
        "designer": "", "checker": "", "reviewer": "",
        "date": "2026-07-03",
        "company": "Beacon",
    }
    return {
        "title_block": title_block,
        "views": views,
        "annotations": annotations,
        "roughness": roughness,
        "gdt": gdt,
        "tech_req": tech_req,
        "plan_meta": {"part_name": title_block["name"], "process": process or "", "confidence": 0.55,
                      "source": f"rule_fallback_{kind}",
                      "process_confirmed_by_user": bool((process or "").strip()) and "(未指定)" not in (process or "")},
    }


def _guess_material(consultation: str, process: str) -> str:
    txt = (consultation or "") + " " + (process or "")
    if "铝" in txt or "alumin" in txt.lower() or "6061" in txt or "7075" in txt:
        return "铝6061"
    if "钢" in txt or "steel" in txt.lower() or "304" in txt:
        return "钢"
    if "铜" in txt or "copper" in txt.lower() or "brass" in txt.lower():
        return "铜"
    return "铝6061"


def _guess_part_name(veritas: dict, process: str) -> str:
    src = str(veritas.get("source", "")) if isinstance(veritas, dict) else ""
    if "后壳" in src or "back" in src.lower():
        return "后壳"
    if "前壳" in src or "front" in src.lower():
        return "前壳"
    if "支架" in src or "bracket" in src.lower():
        return "支架"
    return "零件"


def save_plan(plan: dict, path: str) -> str:
    """把 drawing_plan 写到 path(JSON, UTF-8, 缩进). 返回 path."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    return path


# -------- 向后兼容: 老 chat_workflow.py 引用了 plan() (实际未调用, 仅 stage 名). --------
async def plan(description: str, process: str, memory_context: str = "") -> str:
    """[deprecated] 保留兼容入口. 返回 drawing_plan 的 JSON 字符串."""
    veritas_like = {"features": [], "summary": {"raw": description}, "source": description}
    p = await make_drawing_plan(json.dumps(veritas_like, ensure_ascii=False),
                                consultation=memory_context, process=process)
    return json.dumps(p, ensure_ascii=False, indent=2)


# ========================= standalone 自测 (工艺感知对比: 钣金 vs 机加) =========================
async def _selftest():
    import glob, os
    # 取最新 task 的 veritas.json
    pattern = "/Users/ahs/projects/Beacon/beacon-expert/storage/tasks/*/veritas.json"
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        print("[selftest] 无 veritas.json 样本, 跳过.")
        return
    vp = files[0]
    print(f"[selftest] veritas 样本: {os.path.basename(os.path.dirname(vp))}")
    with open(vp, "r", encoding="utf-8") as f:
        vjson = f.read()
    veritas = json.loads(vjson)
    feats = veritas.get("features", [])
    piercing = [f for f in feats if f.get("type") == "PIERCING"]
    print(f"[selftest] 特征总数 {len(feats)}: PIERCING={len(piercing)}")

    # 直接走 _rule_fallback_plan 对比工艺分支 (LLM 路径会受网络/模型抖动, 走规则路径确定性更高).
    sm = _rule_fallback_plan(veritas, "零件性质:钣金 | 材料:铝6061 板厚1.5 | 折弯+焊接", "钣金")
    mc = _rule_fallback_plan(veritas, "零件性质:机加工 | 材料:铝6061 | 公差一般", "机加工")

    print("\n===== 工艺感知对比 (sheetmetal vs machining) =====")
    for label, plan in [("钣金", sm), ("机加工", mc)]:
        views = plan.get("views", [])
        anns = plan.get("annotations", [])
        view_types = {}
        for v in views:
            t = v.get("type", "?")
            view_types[t] = view_types.get(t, 0) + 1
        ann_types = {}
        for a in anns:
            t = a.get("type", "?")
            ann_types[t] = ann_types.get(t, 0) + 1
        print(f"\n--- [{label}] ---")
        print(f"  views ({len(views)}): {view_types}")
        for v in views:
            if v.get("type") != "standard":
                extra = v.get("label", "") or v.get("purpose", "") or v.get("view", "")
                print(f"    - {v.get('view')} [{v.get('type')}] {str(extra)[:60]}")
        print(f"  annotations ({len(anns)}): {ann_types}")
        print(f"  tech_req: {plan.get('tech_req', [])[:4]}")
        pm = plan.get("plan_meta", {})
        print(f"  plan_meta: source={pm.get('source')} confirmed={pm.get('process_confirmed_by_user')} sheet_t={pm.get('sheet_thickness', '-')}")

    # 断言工艺感知生效
    sm_view_types = {v.get("type") for v in sm["views"]}
    sm_ann_types = {a.get("type") for a in sm["annotations"]}
    mc_view_types = {v.get("type") for v in mc["views"]}
    mc_ann_types = {a.get("type") for a in mc["annotations"]}
    print("\n===== 工艺感知生效断言 =====")
    print(f"  钣金 views 含 auxiliary(展开图): {'auxiliary' in sm_view_types}")
    print(f"  钣金 views 含 section(折弯剖面): {'section' in sm_view_types}")
    print(f"  钣金 annotations 含 bend: {'bend' in sm_ann_types}")
    print(f"  钣金 annotations 含 weld: {'weld' in sm_ann_types}")
    print(f"  钣金 annotations 含 thickness(t=料厚): {'thickness' in sm_ann_types}")
    print(f"  机加 views 含 section(沉头/盲孔): {'section' in mc_view_types}")
    print(f"  机加 annotations 含 thread/csink: {'thread' in mc_ann_types or 'csink' in mc_ann_types}")
    print(f"  机加 annotations 无 bend: {'bend' not in mc_ann_types}")
    print(f"  机加 annotations 无 weld: {'weld' not in mc_ann_types}")
    print(f"  机加 views 无 auxiliary(展开图): {'auxiliary' not in mc_view_types}")

    # 抽样几条钣金标注
    print("\n===== 钣金标注抽样(前 8) =====")
    for a in sm["annotations"][:8]:
        print(f"  {a.get('feature_id')}: [{a.get('type')}] {a.get('value')} @ {a.get('view')}")

    print("\n[selftest] OK")


if __name__ == "__main__":
    import asyncio
    asyncio.run(_selftest())
