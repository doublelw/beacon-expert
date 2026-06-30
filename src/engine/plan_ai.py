"""M2: AI规划(LLM根据理解+工艺→规划工程图画法)."""
from src.engine.llm_call import call_llm

async def plan(description: str, process: str, memory_context: str = "") -> str:
    """返回工程图画法规划(文字)."""
    prompt = f"""你是工程图专家。零件描述: {description}
工艺: {process}

规划这个零件的工程图画法:
1. 视图选择(几个?哪几个?)
2. 标注清单(外形/孔径/孔距/折弯/公差)
3. 标准件代号(沉头/压铆/过孔各用什么钉)
4. 技术要求(材料/表面/公差等级)
"""
    if memory_context:
        prompt += f"\n用户偏好:\n{memory_context}"
    result = await call_llm(prompt, model="glm-5-turbo", max_tokens=2000)
    return result.get('text', '规划失败')
