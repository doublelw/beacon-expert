"""M-gen 规划器: 自然语言 → 结构化图纸 spec (dxf_gen 的输入).

架构纪律: LLM 只负责 自然语言→spec 的翻译 (数值/几何/标注/技术要求),
DXF 实体一律由 dxf_gen 确定性绘制 — 质量可回读校验、可复现、不依赖 LLM 稳定性。
spec schema 见 dxf_gen 模块 docstring。

流程: call_llm → 剥壳取JSON → schema校验 → 有错带真实报错回炉(≤2轮, huashu-cad 纪律)
      → 仍不过则返回错误清单 (route 层转任务失败, 不产出半成品图)。
诚实原则: 用户没给的尺寸按工程合理默认, 写入 spec['assumptions'] 声明, 不编造精度。
"""
import json
import re
from typing import Optional, Tuple

from src.engine.llm_call import call_llm

MAX_REPAIR_ROUNDS = 2

PLAN_SYSTEM = """你是机械制图规划器。把用户的自然语言需求转成一个 JSON 图纸规格 (spec),
供确定性引擎出 GB 工程图 DXF。只输出 JSON, 不要 markdown 围栏, 不要解释。

spec schema:
{
  "title": "图名(短)",
  "material": "材料(已知则填, 否则 'Q235' 并记入 assumptions)",
  "scale_text": "1:1",
  "frame": {"width": 数值mm, "height": 数值mm, "thickness": 数值mm},
  "geometry": [
    {"type": "line", "p1": [x,y], "p2": [x,y], "layer": "OUTLINE"},
    {"type": "rect", "x": 0, "y": 0, "w": 宽, "h": 高, "layer": "OUTLINE"},
    {"type": "circle", "cx": x, "cy": y, "r": 半径, "layer": "HOLE"},
    {"type": "arc", "cx": x, "cy": y, "r": 半径, "start_angle": 度, "end_angle": 度},
    {"type": "text", "at": [x,y], "text": "M6", "height": 3.5}
  ],
  "dimensions": [
    {"type": "linear", "p1": [x,y], "p2": [x,y], "offset": 10, "angle": 0或90},
    {"type": "diameter", "center": [cx,cy], "r": 半径},
    {"type": "radius", "center": [cx,cy], "r": 半径}
  ],
  "tech_reqs": ["未注公差按GB/T 1804-m", "材料 <material>", "去毛刺"],
  "assumptions": ["假设: <用户没给但影响结果的尺寸/参数>"]
}

硬性规则:
1. 坐标第一象限, 原点放几何左下角, 单位毫米, 真实尺寸 (不预缩放)。
2. 总包围盒须能放进 A4 内容区 (约 250×140mm); 超大件可出 A3 (375×230mm),
   仍放不下则如实缩小并记 assumptions。
3. layer 白名单: OUTLINE/HOLE/CENTER/TEXT; 孔用 HOLE, 轮廓用 OUTLINE。
4. 关键尺寸必须标注: 外形 (linear)、孔径 (diameter)、位置 (孔距/孔到边 linear)。
5. 用户没给的尺寸: 工程惯例默认 (如安装孔距边=2~3倍孔径), 写进 assumptions, 不编造。
6. tech_reqs 至少含: 材料 / 未注公差GB/T 1804-m / 去毛刺。
7. 只输出一个 JSON 对象。
"""


def extract_json(text: str) -> Optional[dict]:
    """剥 ```json 围栏与前后废话, 取第一个 JSON 对象."""
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


_GEOM_TYPES = {'line', 'rect', 'circle', 'arc', 'text'}
_DIM_TYPES = {'linear', 'diameter', 'radius'}


def validate_spec(spec: dict) -> list:
    """schema 校验, 返回错误清单 (空=通过)."""
    errs = []
    if not spec.get('title'):
        errs.append('缺少 title')
    geom = spec.get('geometry')
    if not isinstance(geom, list) or not geom:
        errs.append('geometry 必须是非空数组')
    else:
        for i, g in enumerate(geom):
            t = g.get('type')
            if t not in _GEOM_TYPES:
                errs.append(f'geometry[{i}].type={t!r} 不在 {sorted(_GEOM_TYPES)}')
            if t in ('line',) and (not g.get('p1') or not g.get('p2')):
                errs.append(f'geometry[{i}] line 需要 p1/p2')
            if t in ('circle', 'arc') and not g.get('r'):
                errs.append(f'geometry[{i}] {t} 需要 r')
    dims = spec.get('dimensions')
    if not isinstance(dims, list) or not dims:
        errs.append('dimensions 必须是非空数组 (至少外形标注)')
    else:
        for i, d in enumerate(dims):
            if d.get('type') not in _DIM_TYPES:
                errs.append(f'dimensions[{i}].type={d.get("type")!r} 无效')
            if d.get('type') in ('diameter', 'radius') and not d.get('r'):
                errs.append(f'dimensions[{i}] 需要 r')
    if not spec.get('tech_reqs'):
        errs.append('缺少 tech_reqs (至少材料/公差/去毛刺)')
    return errs


async def plan_from_nl(prompt: str) -> Tuple[Optional[dict], dict]:
    """自然语言 → (spec|None, 元信息). spec 已过 validate_spec; 失败原因在 meta['errors']."""
    meta = {'attempts': 0, 'llm': None, 'errors': []}
    user_msg = prompt
    for _ in range(MAX_REPAIR_ROUNDS + 1):
        meta['attempts'] += 1
        r = await call_llm(user_msg, system=PLAN_SYSTEM, max_tokens=2048, temperature=0)
        meta['llm'] = {'provider': r.get('provider'), 'model': r.get('model'),
                       'usage': r.get('usage')}
        if r.get('error') or not r.get('text'):
            meta['errors'].append(f"LLM调用失败: {r.get('error') or '空回复'}")
            return None, meta
        spec = extract_json(r['text'])
        if spec is None:
            meta['errors'].append('回复中找不到合法JSON')
        else:
            errs = validate_spec(spec)
            if not errs:
                spec.setdefault('assumptions', [])
                return spec, meta
            meta['errors'] = errs
        user_msg = (f"{prompt}\n\n你上次的 spec 有以下问题, 逐一修正后重新输出完整JSON"
                     f"(仍只输出JSON):\n" + '\n'.join(f'- {e}' for e in meta['errors'][:10]))
    return None, meta
