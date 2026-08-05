"""M0: AI工艺顾问 — 两阶段(注入 GB 国标知识).

阶段一 classify: LLM读veritas特征 → 首选工艺 + 列出所有几何可行工艺及成本档位.
阶段二 refine: 用户提供材料/公差/量级/优先/特殊 → LLM结合约束推荐最优工艺.
"""
import json
from src.engine.llm_call import call_llm
from src.engine.gb_knowledge import gb_consultation_context

PROMPT_TEMPLATE = """你是制造工艺专家。根据3D零件的几何特征判断制造工艺,并列出所有几何上可行的工艺及成本档位。

特征数据:
{features}

工艺字典: sheet_metal=钣金 / injection_molding=注塑 / machining=机加工 / stamping=冲压 / casting=铸造 / welding=焊接 / additive=3D打印

判断依据:
- 钣金: 等厚薄板(壁厚<5mm) + 折弯圆柱面 + 压铆/翻边孔
- 注塑: 薄壁 + BSpline自由曲面 + 拔模角 + 均匀壁厚
- 机加工: 实心块 + 凸台凹槽 + 倒角 + 高精度孔(注意:从实心毛坯切削,去除材料多、废料成本高)
- 冲压: 薄板 + 拉伸 + 翻边
- 铸造: 壁厚均匀 + 拔模 + 分型面
- 焊接: 多体组合 + 焊缝
- 3D打印: 复杂曲面 + 无拔模 + 小批量

【重要】即使首选工艺明确,也要思考其他工艺几何上是否可行。尤其机加件——若几何上钣金(拆成薄板折弯焊接)或铸造也可行,必须列入 viable_processes,因为成本可能低得多。viable_processes 至少含首选;凡几何可行的都要列。

请只输出JSON:
{{"process": "首选工艺(中文)", "confidence": 0.0-1.0, "reasoning": "首选理由", "viable_processes": [{{"process": "工艺(中文)", "cost": "高/中/低", "note": "可行性或优劣一句话"}}]}}
"""

REFINE_PROMPT = """你是制造工艺顾问。零件已有多种可行工艺,现在结合用户实际约束,推荐最省/最合适的工艺。

可行工艺及成本: {viable}
用户回答的约束: {answers}

推荐规则(灵活运用):
- 打样/小批量 + 成本敏感 → 优先钣金/3D打印(无模具费、废料少);机加从实心切削很贵
- 量产 + 外观一致 → 注塑/冲压(模具摊销后单件便宜)
- 高强度/高精度/承压密封/精密配合 → 机加工(钣金/焊接达不到精密公差和强度)
- 公差: 精密(±0.01)或配合面高精度 → 只能机加;一般(±0.1)/配合(±0.05) → 钣金/铸造可行
- 材料: 铝/铜易钣金和机加;钢强度高适合机加/焊接;不锈钢加工贵,更倾向钣金/焊接
- 焊接钣金件: 适合外壳/框架,打样便宜,但密封/精度不如机加

只输出JSON: {{"process": "推荐工艺(中文)", "reasoning": "结合用户约束为何最优(1-2句)", "cost_note": "成本提示一句话"}}
"""


async def classify(features_json: str) -> dict:
    """阶段一: {process, confidence, reasoning, ask_user, viable_processes}."""
    result = await call_llm(
        PROMPT_TEMPLATE.format(features=features_json),
        model="glm-4.5-air", temperature=0, max_tokens=700,
    )
    if result.get('text'):
        try:
            text = result['text']
            s = text.find('{'); e = text.rfind('}') + 1
            if s >= 0 and e > s:
                p = json.loads(text[s:e])
                p['ask_user'] = p.get('confidence', 0) < 0.7
                if not p.get('viable_processes'):
                    p['viable_processes'] = [{"process": p.get("process", "未知"), "cost": "中", "note": "仅首选"}]
                return p
        except Exception:
            pass
    return _rule_fallback(features_json)


async def refine_process(features_json: str, viable_processes: list, answers: str) -> dict:
    """阶段二: 结合用户约束(材料/公差/量级/优先/特殊)推荐最优工艺. 注入 GB 顾问上下文."""
    viable_str = json.dumps(viable_processes or [], ensure_ascii=False)
    result = await call_llm(
        REFINE_PROMPT.format(viable=viable_str, answers=answers or "(用户未明确回答)"),
        model="glm-5-turbo", temperature=0, max_tokens=400,
        system=gb_consultation_context(),  # GB 国标: 加工商关键信息清单
    )
    if result.get('text'):
        try:
            text = result['text']
            s = text.find('{'); e = text.rfind('}') + 1
            if s >= 0 and e > s:
                return json.loads(text[s:e])
        except Exception:
            pass
    rank = {"低": 0, "中": 1, "高": 2}
    cheap = sorted(viable_processes or [], key=lambda v: rank.get(v.get("cost", "中"), 1))
    pick = cheap[0]["process"] if cheap else "钣金"
    return {"process": pick, "reasoning": "成本优先的兜底推荐", "cost_note": ""}


def _rule_fallback(features_json: str) -> dict:
    """规则兜底: LLM不可用."""
    try:
        v = json.loads(features_json) if isinstance(features_json, str) else features_json
        bends = sum(1 for f in v.get('features', []) if f.get('type') == 'BEND')
        thickness = v.get('thickness', 0)
        bspline = sum(1 for f in v.get('faces', []) if 'BSpline' in str(f.get('type', '')))
        if thickness < 5 and bends > 0:
            return {"process": "钣金", "confidence": 0.8, "reasoning": f"薄板({thickness}mm)+{bends}折弯", "ask_user": False,
                    "viable_processes": [{"process": "钣金", "cost": "低", "note": "薄板+折弯"}, {"process": "冲压", "cost": "中", "note": "薄板拉伸"}, {"process": "机加工", "cost": "高", "note": "实心切削,贵"}]}
        if bspline > 5:
            return {"process": "注塑", "confidence": 0.7, "reasoning": f"BSpline曲面{bspline}个", "ask_user": True,
                    "viable_processes": [{"process": "注塑", "cost": "中", "note": "BSpline曲面"}, {"process": "3D打印", "cost": "低", "note": "小批量"}]}
        return {"process": "机加工", "confidence": 0.6, "reasoning": "实心块,默认机加", "ask_user": True,
                "viable_processes": [{"process": "机加工", "cost": "高", "note": "实心块切削"}, {"process": "钣金", "cost": "低", "note": "若可拆为薄板件"}, {"process": "铸造", "cost": "中", "note": "批量"}]}
    except Exception:
        return {"process": "unknown", "confidence": 0.3, "reasoning": "特征提取失败", "ask_user": True, "viable_processes": []}
