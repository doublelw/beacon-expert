"""B线 Phase 2: 自然语言 → 出图 spec (LLM 规划器).

分工纪律 (来自 huashu-cad SKILL.md「与用户谈尺寸、对象和结果; 依赖由agent处理」):
  LLM 只产出结构化 spec (几何/尺寸/注记的真实毫米数值), DXF 实体一律由
  cad2d_gen 确定性绘制 — LLM 不直接触碰图形文件。

流程: call_llm → 剥壳取JSON → validate_spec → 有错带回错误重新规划(最多N轮)
  → 仍不过则返回错误清单 (route 层转任务失败, 不产出半成品图)。
诚实原则: 用户没给的尺寸按合理默认, 并写入 notes 假设声明; 不编造精度。
"""
import json
import re
from typing import List, Optional, Tuple

from src.engine.llm_call import call_llm
from src.engine.cad2d_gen import validate_spec, GEOM_TYPES, KNOWN_LAYERS

MAX_REPAIR_ROUNDS = 2

PLAN_SYSTEM = """你是GB工程图规划器。把用户的自然语言需求转成一个JSON出图规格。
只输出一个JSON对象, 不要markdown代码块, 不要解释文字。

JSON schema:
{
  "title": "图名(短)",
  "sheet": "A4" 或 "A3",
  "notes": ["技术要求条目, 含假设声明"],
  "geometry": [
    {"type":"rect","x":左下角x,"y":左下角y,"w":宽,"h":高,"layer":"OUTLINE"},
    {"type":"line","p1":[x,y],"p2":[x,y],"layer":"OUTLINE"},
    {"type":"polyline","points":[[x,y],...],"closed":true,"layer":"OUTLINE"},
    {"type":"circle","cx":x,"cy":y,"r":半径,"layer":"HOLE"},
    {"type":"hole","cx":x,"cy":y,"r":半径},
    {"type":"arc","cx":x,"cy":y,"r":半径,"start_angle":度,"end_angle":度,"layer":"OUTLINE"},
    {"type":"text","s":"文字","at":[x,y],"h":3.5,"layer":"TEXT"}
  ],
  "dims": [
    {"type":"linear","p1":[x,y],"p2":[x,y],"side":"bottom|top|left|right","offset":12},
    {"type":"diameter","cx":x,"cy":y,"r":半径,"angle":135}
  ]
}

硬性规则:
1. 单位毫米, 坐标是1:1真实尺寸, 原点建议放几何左下角附近。
2. 总尺寸必须能放进图幅: A4 内容区约 250×130mm, A3 约 375×220mm; 放不下就换A3, 仍放不下要如实说做不到(输出 {"error": "..."} )。
3. layer 只能用: %s。
4. geometry 类型只能: %s。孔一律用 hole (自动加十字中心线)。
5. dims 标关键尺寸: 外形、孔距、孔径(diameter)。不要每个几何都标。
6. 用户没给的尺寸: 按工程合理默认(如安装孔距边=孔径2~3倍), 并在 notes 写"假设:"声明。
7. notes 第一条写材料/公差等已知信息, 未知不编造。
8. side 选择: 水平尺寸用 bottom/top, 垂直尺寸用 left/right。
""" % (', '.join(sorted(KNOWN_LAYERS)), ', '.join(sorted(GEOM_TYPES)))


def extract_json(text: str) -> Optional[dict]:
    """从 LLM 回复剥出 JSON 对象 (容忍 ```json 围栏与前后废话)."""
    if not text:
        return None
    t = text.strip()
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', t, re.S)
    if m:
        t = m.group(1)
    else:
        i, j = t.find('{'), t.rfind('}')
        if i < 0 or j <= i:
            return None
        t = t[i:j + 1]
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


async def plan_from_nl(prompt: str) -> Tuple[Optional[dict], dict]:
    """NL → (spec|None, 元信息). spec 已过 validate_spec; 失败原因在 meta['errors']."""
    meta: dict = {'attempts': 0, 'llm': None, 'errors': []}
    user_msg = prompt
    for _ in range(MAX_REPAIR_ROUNDS + 1):
        meta['attempts'] += 1
        r = await call_llm(user_msg, system=PLAN_SYSTEM, temperature=0)
        meta['llm'] = {'provider': r.get('provider'), 'model': r.get('model'),
                       'usage': r.get('usage')}
        if r.get('error') or not r.get('text'):
            meta['errors'].append(f"LLM调用失败: {r.get('error') or '空回复'}")
            return None, meta
        spec = extract_json(r['text'])
        if spec is None:
            meta['errors'].append('回复中找不到合法JSON')
        elif spec.get('error'):
            meta['errors'].append(f"规划器如实拒绝: {spec['error']}")
            return None, meta
        else:
            errs = validate_spec(spec)
            if not errs:
                return spec, meta
            meta['errors'] = errs
        # 带错误重试 (huashu-cad: 失败先读真实报错再修)
        user_msg = (f"{prompt}\n\n你上次的输出有以下问题, 逐一修正后重新输出完整JSON"
                    f"(仍然只输出JSON):\n" + '\n'.join(f'- {e}' for e in meta['errors'][:10]))
    return None, meta
