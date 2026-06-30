"""M7b: AI审查(LLM读audit_report→工程师视角审查)."""
import json
from src.engine.llm_call import call_llm

async def audit(audit_report: str, dxf_summary: str) -> dict:
    """返回 {issues, recommendations, readiness_score}."""
    prompt = f"""你是制造工程师。审查这张工程图:

审计报告:
{audit_report}

DXF摘要:
{dxf_summary[:2000]}

回答:
1. 工人能加工吗? (能/不能/部分)
2. 缺什么信息?
3. 有什么标注歧义?
4. 加工就绪度评分(0-100)

输出JSON: {{"can_manufacture": true/false/"partial", "missing": ["缺失项"], "ambiguities": ["歧义"], "readiness_score": 0-100}}
"""
    result = await call_llm(prompt, model="glm-5-turbo", max_tokens=1000)
    try:
        text = result.get('text','')
        start = text.find('{'); end = text.rfind('}')+1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except: pass
    return {"can_manufacture": "unknown", "missing": [], "readiness_score": 0, "error": "审查失败"}
