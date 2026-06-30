"""M0: AI工艺判断(LLM读veritas特征→判钣金/注塑/机加工/冲压/铸造/焊接/3D打印)."""
import json
from src.engine.llm_call import call_llm

PROMPT_TEMPLATE = """你是制造工艺专家。根据3D零件的几何特征,判断制造工艺。

特征数据:
{features}

可选工艺: sheet_metal(钣金) / injection_molding(注塑) / machining(机加工) / stamping(冲压) / casting(铸造) / welding(焊接) / additive(3D打印)

判断依据:
- 钣金: 等厚板(D<5mm) + 折弯圆柱面 + 压铆孔
- 注塑: 薄壁 + BSpline曲面 + 拔模角 + 均匀壁厚
- 机加工: 实心 + 凸台凹槽 + 倒角 + 高精度
- 冲压: 薄板 + 拉伸 + 翻边
- 铸造: 壁厚均匀 + 拔模 + 分型面
- 焊接: 多体 + 焊缝
- 3D打印: 复杂曲面 + 无拔模

请输出JSON: {{"process": "工艺名", "confidence": 0.0-1.0, "reasoning": "判断理由"}}
如果置信度<0.7, 说明不确定原因。
"""

async def classify(features_json: str) -> dict:
    """返回 {process, confidence, reasoning, ask_user}."""
    # 先尝试LLM
    result = await call_llm(
        PROMPT_TEMPLATE.format(features=features_json),
        model="glm-4.5-air", temperature=0, max_tokens=500
    )
    if result.get('text'):
        try:
            # 提取JSON
            text = result['text']
            start = text.find('{'); end = text.rfind('}') + 1
            if start >= 0 and end > start:
                parsed = json.loads(text[start:end])
                parsed['ask_user'] = parsed.get('confidence', 0) < 0.7
                return parsed
        except: pass
    # 规则兜底
    return _rule_fallback(features_json)

def _rule_fallback(features_json: str) -> dict:
    """规则兜底: 当LLM不可用."""
    try:
        v = json.loads(features_json) if isinstance(features_json, str) else features_json
        bends = sum(1 for f in v.get('features',[]) if f.get('type')=='BEND')
        stamps = sum(1 for f in v.get('features',[]) if f.get('type')=='STAMP')
        thickness = v.get('thickness', 0)
        bspline = sum(1 for f in v.get('faces',[]) if 'BSpline' in str(f.get('type','')))
        if thickness < 5 and bends > 0:
            return {"process": "sheet_metal", "confidence": 0.8, "reasoning": f"薄板({thickness}mm)+{bends}折弯", "ask_user": False}
        if bspline > 5:
            return {"process": "injection_molding", "confidence": 0.7, "reasoning": f"BSpline曲面{bspline}个", "ask_user": True}
        return {"process": "machining", "confidence": 0.6, "reasoning": "默认机加工", "ask_user": True}
    except:
        return {"process": "unknown", "confidence": 0.3, "reasoning": "特征提取失败", "ask_user": True}
