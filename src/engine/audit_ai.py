"""M7b: AI逐特征审计.

确定性预核对(veritas features × plan.annotations × DXF 实体) + LLM(GB合规清单)判定。
返回 feature_coverage 由确定性计算给出，LLM 仅做合规/可制造性裁决。
"""
import json
import re
from typing import Any

import ezdxf

from src.engine.llm_call import call_llm
from src.engine.gb_knowledge import gb_audit_context

# ============ 确定性预核对 ============

_RE_M_THREAD = re.compile(r"(?<![A-Za-z])M\d+(?:[-×x.]\d+)?[A-Za-z0-9]*")
_RE_RA = re.compile(r"Ra\s?\.?\d", re.IGNORECASE)
_RE_CSINK = re.compile(
    r"(?:%%C|φ)\s*\d+\.?\d*\s*[xX×]\s*\d+\.?\d*\s*(?:%%D|°)"  # φ10x90°
    r"|\d+\.?\d*\s*[xX×]\s*90\s*(?:%%D|°)"  # 10x90°
)
_RE_DIAMETER = re.compile(r"%%C|φ", re.IGNORECASE)
_RE_CHAMFER = re.compile(r"(?<![A-Za-z])C\d+\.?\d*(?:×45)?", re.IGNORECASE)
_RE_BEND = re.compile(r"折弯|折邊|bend|\d+°\s*R\d", re.IGNORECASE)   # 钣金折弯标注
_RE_WELD = re.compile(r"焊|weld|▽|♭|∇", re.IGNORECASE)               # 焊缝符号(GB/T 324)


def _parse_veritas_features(veritas_features_json: str) -> list[dict]:
    """解析 veritas features 输入，容错多种形态(裸 list / {features:[...]} / 完整 veritas)."""
    data: Any = json.loads(veritas_features_json) if isinstance(veritas_features_json, str) else veritas_features_json
    if isinstance(data, dict):
        feats = data.get("features")
        if feats is None:
            # 完整 veritas 缺 features 字段 → 视为空
            feats = []
    elif isinstance(data, list):
        feats = data
    else:
        feats = []
    if isinstance(feats, dict):
        feats = list(feats.values())
    return [f for f in feats if isinstance(f, dict)]


def _stats_from_veritas(features: list[dict]) -> dict:
    """按 hole_type / type 统计 veritas 侧特征清单(含钣金 bends)。"""
    holes, csink, thread, chamfer, bends = 0, 0, 0, 0, 0
    diameters: set[float] = set()
    for f in features:
        ftype = f.get("type")
        if ftype == "PIERCING":
            holes += 1
            ht = f.get("hole_type") or "clear"
            if ht == "csink":
                csink += 1
            elif ht == "thread":
                thread += 1
            d = f.get("diameter")
            if isinstance(d, (int, float)) and d > 0:
                diameters.add(round(float(d), 2))
        elif ftype == "CHAMFER":
            chamfer += 1
        elif ftype == "BEND":
            bends += 1
    return {
        "holes_total": holes,
        "csink_total": csink,
        "thread_total": thread,
        "chamfer_total": chamfer,
        "bends_total": bends,
        "unique_diameters": sorted(diameters),
    }


def _detect_process(plan: dict, stats: dict) -> str:
    """从 plan_meta.process 判工艺；无则按特征启发(有 bends→钣金)。"""
    p = (plan.get("plan_meta", {}) or {}).get("process") or plan.get("process") or ""
    pl = (p or "").lower()
    if "钣金" in p or "sheet" in pl:
        return "sheetmetal"
    if "机加" in p or "machin" in pl:
        return "machining"
    # 启发: veritas 有 bends 或 plan 注解有 bend/weld → 钣金
    anns = plan.get("annotations", []) if isinstance(plan, dict) else []
    if any((a.get("type") or "").lower() in ("bend", "weld") for a in anns if isinstance(a, dict)):
        return "sheetmetal"
    if stats.get("bends_total", 0) > 0:
        return "sheetmetal"
    return "machining"


def _plan_coverage(features: list[dict], plan: dict) -> dict:
    """逐 feature_id 核对 plan.annotations 是否覆盖。

    覆盖判据(按 drawing_plan 契约):
      - thread 孔 → annotations 里有 feature_id 匹配且 type=thread
      - csink 孔 → type=csink (φ×°)
      - clear 孔 → type in (diameter, thread, csink) 任意直径标注即算覆盖
      - CHAMFER → type=chamfer
    """
    anns = plan.get("annotations", []) if isinstance(plan, dict) else []
    # 按 feature_id 索引 annotation types
    by_fid: dict[str, set[str]] = {}
    for a in anns:
        if not isinstance(a, dict):
            continue
        fid = a.get("feature_id")
        atype = (a.get("type") or "").lower()
        if fid and atype:
            by_fid.setdefault(str(fid), set()).add(atype)

    uncovered: list[dict] = []
    for f in features:
        fid = str(f.get("id", ""))
        ftype = f.get("type")
        if ftype == "PIERCING":
            ht = f.get("hole_type") or "clear"
            types = by_fid.get(fid, set())
            if ht == "thread":
                if "thread" not in types:
                    uncovered.append({"id": fid, "kind": "thread", "expect": "M规格螺纹标注"})
            elif ht == "csink":
                if "csink" not in types:
                    uncovered.append({"id": fid, "kind": "csink", "expect": "φ×° 沉头标注"})
            else:  # clear
                if not (types & {"diameter", "thread", "csink"}):
                    uncovered.append({"id": fid, "kind": "diameter", "expect": "φ 直径标注"})
        # CHAMFER 覆盖在 cov_chamfer() 单独统计

    return {
        "by_fid": by_fid,
        "uncovered": uncovered,
    }


def _scan_dxf(dxf_path: str) -> dict:
    """解析 DXF: 数 DIMENSION/TOLERANCE 实体 + 扫文字样本(M/Ra/φ×°/%%C/C)."""
    dims = tols = leaders = 0
    m_hits, ra_hits, csink_hits, dia_hits, cham_hits = [], [], [], [], []
    text_sample: list[str] = []

    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception as ex:
        return {"dxf_readable": False, "error": str(ex)[:200]}

    msp = doc.modelspace()
    text_chunks: list[str] = []

    def _collect(entity) -> None:
        nonlocal dims, tols, leaders
        dx = entity.dxftype()
        if dx == "DIMENSION":
            dims += 1
            # DIMENSION 文字在关联匿名块(*Dxx)里
            try:
                blk_name = entity.dxf.geometry
                blk = doc.blocks.get(blk_name) if blk_name else None
                if blk is not None:
                    for sub in blk:
                        if sub.dxftype() in ("TEXT", "MTEXT"):
                            t = sub.dxf.text if sub.dxftype() == "TEXT" else sub.text
                            if t:
                                text_chunks.append(t)
            except Exception:
                pass
        elif dx == "TOLERANCE":
            tols += 1
            try:
                text_chunks.append(entity.dxf.text or "")
            except Exception:
                pass
        elif dx == "LEADER":
            leaders += 1
        if dx in ("TEXT", "MTEXT"):
            t = entity.dxf.text if dx == "TEXT" else entity.text
            if t:
                text_chunks.append(t)

    for e in msp:
        _collect(e)

    joined = " ".join(text_chunks)
    m_hits = _RE_M_THREAD.findall(joined)
    ra_hits = _RE_RA.findall(joined)
    csink_hits = _RE_CSINK.findall(joined)
    dia_hits = _RE_DIAMETER.findall(joined)
    cham_hits = _RE_CHAMFER.findall(joined)
    bend_hits = _RE_BEND.findall(joined)
    weld_hits = _RE_WELD.findall(joined)
    text_sample = [t.strip()[:60] for t in text_chunks if t.strip()][:8]

    return {
        "dxf_readable": True,
        "dimensions": dims,
        "tolerances": tols,
        "leaders": leaders,
        "m_text_count": len(m_hits),
        "ra_text_count": len(ra_hits),
        "csink_text_count": len(csink_hits),
        "diameter_text_count": len(dia_hits),
        "chamfer_text_count": len(cham_hits),
        "bend_text_count": len(bend_hits),
        "weld_text_count": len(weld_hits),
        "m_samples": m_hits[:6],
        "ra_samples": ra_hits[:4],
        "csink_samples": csink_hits[:4],
        "bend_samples": bend_hits[:4],
        "weld_samples": weld_hits[:4],
        "text_sample": text_sample,
    }


def _build_feature_coverage(features: list[dict], plan: dict, dxf_scan: dict) -> dict:
    """组装 feature_coverage. covered = DXF实测有效覆盖(plan标了且DXF真画出来才算);
    plan_covered = plan 规划覆盖; dxf_rendered = DXF 是否有对应标注证据."""
    stats = _stats_from_veritas(features)
    cov = _plan_coverage(features, plan)
    dxf = dxf_scan or {}
    process = _detect_process(plan, stats)

    holes_cov = stats["holes_total"] - sum(
        1 for u in cov["uncovered"] if u["kind"] in ("thread", "csink", "diameter")
    )
    thread_cov = stats["thread_total"] - sum(1 for u in cov["uncovered"] if u["kind"] == "thread")
    csink_cov = stats["csink_total"] - sum(1 for u in cov["uncovered"] if u["kind"] == "csink")
    chamfer_cov = cov_chamfer(features, cov)

    # 钣金: bends(来自 veritas BEND) + welds(来自 plan.annotations type=weld) 覆盖
    anns = plan.get("annotations", []) if isinstance(plan, dict) else []
    plan_bends = sum(1 for a in anns if isinstance(a, dict) and (a.get("type") or "").lower() == "bend")
    plan_welds = sum(1 for a in anns if isinstance(a, dict) and (a.get("type") or "").lower() == "weld")
    bends_total = max(stats["bends_total"], plan_bends)   # veritas 实际 或 plan 规划, 取大
    bend_dxf = dxf.get("bend_text_count", 0) > 0
    weld_dxf = dxf.get("weld_text_count", 0) > 0

    # DXF 实测证据 (分组标注: 一条 "n-M5-6H" leader 即覆盖该类全部)
    thread_dxf = dxf.get("m_text_count", 0) > 0
    csink_dxf = dxf.get("csink_text_count", 0) > 0
    holes_dxf = dxf.get("diameter_text_count", 0) > 0 or dxf.get("m_text_count", 0) > 0 or dxf.get("csink_text_count", 0) > 0
    chamfer_dxf = dxf.get("chamfer_text_count", 0) > 0

    def _eff(plan_cov: int, dxf_ok: bool) -> int:
        return plan_cov if dxf_ok else 0

    out = {
        "process": process,
        "holes": {"covered": _eff(max(0, holes_cov), holes_dxf), "plan_covered": max(0, holes_cov),
                  "dxf_rendered": holes_dxf, "total": stats["holes_total"]},
        "csink": {"covered": _eff(max(0, csink_cov), csink_dxf), "plan_covered": max(0, csink_cov),
                  "dxf_rendered": csink_dxf, "total": stats["csink_total"]},
        "thread": {"covered": _eff(max(0, thread_cov), thread_dxf), "plan_covered": max(0, thread_cov),
                   "dxf_rendered": thread_dxf, "total": stats["thread_total"]},
        "chamfer": {"covered": _eff(chamfer_cov, chamfer_dxf), "plan_covered": chamfer_cov,
                    "dxf_rendered": chamfer_dxf, "total": stats["chamfer_total"]},
        "bends": {"covered": _eff(bends_total, bend_dxf), "plan_covered": bends_total,
                  "dxf_rendered": bend_dxf, "total": bends_total},
        "welds": {"covered": _eff(plan_welds, weld_dxf), "plan_covered": plan_welds,
                  "dxf_rendered": weld_dxf, "total": plan_welds},
        "unique_diameters": stats["unique_diameters"],
        "uncovered": cov["uncovered"],
        "dxf": dxf_scan,
    }
    return out


def cov_chamfer(features: list[dict], cov: dict) -> int:
    """CHAMFER 覆盖数 (单独算避免与 _plan_coverage 内部计数耦合)."""
    anns = {}
    for f in features:
        if f.get("type") == "CHAMFER":
            anns[str(f.get("id", ""))] = False
    # 复用 by_fid
    by_fid = cov.get("by_fid", {})
    cnt = 0
    for f in features:
        if f.get("type") != "CHAMFER":
            continue
        fid = str(f.get("id", ""))
        if "chamfer" in by_fid.get(fid, set()):
            cnt += 1
    return cnt


# ============ LLM 判定 ============

async def _llm_judge(feature_coverage: dict, dxf_scan: dict) -> dict:
    """把确定性 coverage + GB 审计清单喂 LLM，让它判可制造性/合规/缺失/歧义. 工艺感知."""
    h = feature_coverage["holes"]
    c = feature_coverage["csink"]
    t = feature_coverage["thread"]
    ch = feature_coverage["chamfer"]
    bd = feature_coverage.get("bends", {})
    wd = feature_coverage.get("welds", {})
    proc = feature_coverage.get("process", "machining")
    uncov = feature_coverage["uncovered"][:20]
    dxf = feature_coverage["dxf"]

    # 工艺专属审计重点
    if proc == "sheetmetal":
        proc_focus = ("钣金件: 重点查 折弯标注(折弯角/R/方向/展开长度, GB/T 1804 折弯公差)、"
                      "焊缝符号(GB/T 324)、板厚(料厚非bbox)、展开图、孔位(展开坐标)、折弯线/折弯方向。"
                      "折弯/焊缝是钣金关键, 缺则大幅扣分。")
    else:
        proc_focus = "机加件: 重点查 沉头φ×°(GB/T 4458.4)、螺纹M(GB/T 4459.1)、盲孔深度、公差(4458.5)、粗糙度Ra(131)、形位(1182)。"

    prompt = f"""你是制造工程师 + GB 国标合规审计员。我已对工程图做了【确定性逐特征核对】，请你据此 + system 的 GB 合规清单给出最终结论。

## 工艺: {proc}
{proc_focus}

## 确定性预核对结果（已逐特征比对 veritas识别 vs plan标注 vs DXF实体，非LLM数数）
- 孔(PIERCING) 标注覆盖: {h['covered']}/{h['total']}
- 沉头(csink) 标注覆盖: {c['covered']}/{c['total']}  (期望每个有 φ×°)
- 螺纹(thread) 标注覆盖: {t['covered']}/{t['total']}  (期望每个有 M规格)
- 倒角(CHAMFER) 标注覆盖: {ch['covered']}/{ch['total']}
- 折弯(bend) 标注覆盖: {bd.get('covered',0)}/{bd.get('total',0)}  (钣金: 期望折弯角/R/方向)
- 焊缝(weld) 标注覆盖: {wd.get('covered',0)}/{wd.get('total',0)}  (钣金: 期望 GB/T 324 符号)
- 识别出的不同直径: {feature_coverage['unique_diameters']}

## 未覆盖特征(feature_id 缺对应标注)
{json.dumps(uncov, ensure_ascii=False)}

## DXF 实体扫描
- DIMENSION 实体数: {dxf.get('dimensions',0)} ; TOLERANCE 实体数: {dxf.get('tolerances',0)} ; LEADER: {dxf.get('leaders',0)}
- 文字扫描命中: 螺纹M={dxf.get('m_text_count',0)} {dxf.get('m_samples',[])} ; 粗糙度Ra={dxf.get('ra_text_count',0)} ; 沉头φ×°={dxf.get('csink_text_count',0)} ; 直径φ/%%C={dxf.get('diameter_text_count',0)} ; 倒角C={dxf.get('chamfer_text_count',0)} ; 折弯={dxf.get('bend_text_count',0)} {dxf.get('bend_samples',[])} ; 焊缝={dxf.get('weld_text_count',0)} {dxf.get('weld_samples',[])}
- 文字样本: {dxf.get('text_sample',[])}

## 你的任务(基于上述事实 + GB清单 + 工艺重点)
1. can_manufacture: 工人能照图加工吗? true / false / "partial"
2. readiness_score: 0-100 (A类违规-10/项, B类缺失-5/项, 覆盖率低大幅扣分; 钣金件折弯/焊缝缺重扣)
3. missing: 仍缺的关键信息(材料/公差/粗糙度/热处理/未注公差说明 等)
4. violations: 违反哪些GB标准(每项写"GB/T xxxx: 问题")，重点查 4458.4尺寸/4458.5公差/131粗糙度/1182形位/4459.1螺纹
5. ambiguities: 标注歧义

仅输出JSON: {{"can_manufacture": true|false|"partial", "readiness_score": <int>, "missing": ["..."], "violations": ["GB/T xxxx: ..."], "ambiguities": ["..."]}}"""

    result = await call_llm(
        prompt, model="glm-5-turbo", max_tokens=1400, system=gb_audit_context()
    )
    text = result.get("text", "")
    if not text:
        return {"_llm_failed": True, "error": result.get("error", "empty")}
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start:end])
            parsed["_llm_failed"] = False
            return parsed
        except Exception:
            pass
    return {"_llm_failed": True, "error": "parse", "raw": text[:200]}


def _fallback_estimate(coverage: dict) -> dict:
    """LLM 失败时的确定性兜底：按覆盖率估算 readiness + 列缺失/违规."""
    h, c, t = coverage["holes"], coverage["csink"], coverage["thread"]
    dxf = coverage["dxf"]
    score = 100

    # 覆盖率扣分（按重要性）
    thread_miss = max(0, t["total"] - t["covered"])
    csink_miss = max(0, c["total"] - c["covered"])
    hole_miss = max(0, h["total"] - h["covered"])

    score -= thread_miss * 12
    score -= csink_miss * 6
    score -= hole_miss * 3

    # DXF 证据缺失扣分（plan 标了但 DXF 没画出来 = 违规）
    if t["total"] > 0 and dxf.get("m_text_count", 0) == 0:
        score -= 10
    if c["total"] > 0 and dxf.get("csink_text_count", 0) == 0:
        score -= 6
    if h["total"] > 0 and dxf.get("diameter_text_count", 0) == 0 and dxf.get("dimensions", 0) < h["total"]:
        score -= 5
    if dxf.get("ra_text_count", 0) == 0:
        score -= 8  # 粗糙度缺失
    if dxf.get("tolerances", 0) == 0:
        score -= 4

    score = max(0, min(100, score))

    # can_manufacture 判定
    if score >= 85 and thread_miss == 0 and csink_miss == 0:
        can = "true"
    elif score < 50 or thread_miss > 0:
        can = "false"
    else:
        can = "partial"

    missing: list[str] = []
    violations: list[str] = []
    if thread_miss:
        violations.append(f"GB/T 4459.1: {thread_miss}个螺纹孔缺M规格标注(feature_id见uncovered)")
    if csink_miss:
        violations.append(f"GB/T 4458.4: {csink_miss}个沉头孔缺φ×°标注")
    if hole_miss:
        violations.append(f"GB/T 4458.4: {hole_miss}个孔缺φ直径标注")
    if dxf.get("ra_text_count", 0) == 0:
        missing.append("配合面粗糙度 Ra (GB/T 131)")
    if dxf.get("tolerances", 0) == 0:
        missing.append("形位公差/基准定义 (GB/T 1182)")
    missing.append("确认材料/热处理/未注公差说明是否齐全(标题栏+技术要求)")

    return {
        "can_manufacture": can,
        "readiness_score": score,
        "missing": missing,
        "violations": violations,
        "ambiguities": ["LLM判定失败，结果为确定性兜底估算，建议人工复核"],
    }


# ============ 主入口 ============

async def audit(veritas_features_json: str, plan: dict, dxf_path: str) -> dict:
    """逐特征审计。

    参数:
      veritas_features_json: veritas.json 的 features 列表(JSON 字符串或已解析对象均可)。
      plan: drawing_plan.json 契约 dict(含 annotations[]，每条带 feature_id + type)。
      dxf_path: 渲染产出的 DXF 文件路径。

    返回: {can_manufacture, readiness_score, feature_coverage, missing, violations, ambiguities}
    feature_coverage 为确定性计算结果(不依赖 LLM)。
    """
    # 1. 确定性预核对
    features = _parse_veritas_features(veritas_features_json)
    dxf_scan = _scan_dxf(dxf_path)
    feature_coverage = _build_feature_coverage(features, plan, dxf_scan)

    # 2. LLM 合规判定
    llm_result = await _llm_judge(feature_coverage, dxf_scan)

    # 3. 合并输出(确定性 coverage 必出，LLM 失败走兜底)
    if llm_result.get("_llm_failed"):
        fb = _fallback_estimate(feature_coverage)
        return {
            "can_manufacture": fb["can_manufacture"],
            "readiness_score": fb["readiness_score"],
            "feature_coverage": feature_coverage,
            "missing": fb["missing"],
            "violations": fb["violations"],
            "ambiguities": fb["ambiguities"],
            "llm_status": "fallback",
        }

    return {
        "can_manufacture": llm_result.get("can_manufacture", "partial"),
        "readiness_score": int(llm_result.get("readiness_score", 0) or 0),
        "feature_coverage": feature_coverage,
        "missing": llm_result.get("missing", []),
        "violations": llm_result.get("violations", []),
        "ambiguities": llm_result.get("ambiguities", []),
        "llm_status": "ok",
    }
