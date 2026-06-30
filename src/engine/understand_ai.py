"""M1: AI理解(LLM读veritas.json→语言描述零件)."""
from src.engine.llm_call import call_llm

async def understand(veritas_json: str, memory_context: str = "") -> str:
    """返回零件的语言描述."""
    prompt = f"""你是CAD工程师。描述这个3D零件:
{veritas_json[:8000]}

用中文描述: 类型/外形尺寸/孔(数量/直径/方向)/折弯/冲压/倒角/特征。
简洁, 工程师能理解。
"""
    if memory_context:
        prompt += f"\n历史记忆:\n{memory_context}"
    result = await call_llm(prompt, model="glm-4.5-air", max_tokens=1000)
    return result.get('text', '理解失败')
