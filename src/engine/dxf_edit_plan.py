"""C线 Phase 4: 自然语言修改指令 → DXF 编辑操作计划 (LLM 规划器).

LLM 只做"选窗口、定位移"的翻译; 几何修改由 dxf_edit 确定性执行。
计划 schema:
  {"ops": [{"op":"stretch","window":[x1,y1,x2,y2],"dx":n,"dy":n},
           {"op":"delete","window":[...]},
           {"op":"retext_dim","handle":"XX"}],
   "note": "一句话修改说明"}
"""
import json
import re
from typing import Optional, Tuple

from src.engine.llm_call import call_llm
from src.engine.dxf_edit import extract_summary
from src.engine.cad2d_plan import extract_json

MAX_REPAIR = 2

PLAN_SYSTEM = """你是CAD图纸编辑规划器。输入一张DXF图纸的实体摘要(坐标/半径/标注测量值,
图纸坐标)和用户的修改指令。输出一个编辑操作计划JSON (只输出JSON)。

可用操作 (STRETCH语义, 窗口=[x1,y1,x2,y2] 图纸坐标矩形):
- {"op":"stretch","window":[x1,y1,x2,y2],"dx":30,"dy":0}
  窗口内的线端点/多段线顶点/圆/文字整体位移(dx,dy); 跨窗口边界的线被拉伸。
- {"op":"scale_region","window":[x1,y1,x2,y2],"factor":1.28}
  整体在窗口内的实体绕窗口中心缩放 factor 倍 (圆的半径随之缩放)。
  改孔径/特征大小用这个: φA→φB 时 factor=B/A, 窗口以该特征为中心只圈住它。
- {"op":"delete","window":[...]}  整体在窗口内的实体被删除
- {"op":"retext_dim","handle":"AB"}  指定标注文字改回真实测量值(仅几何改过后用)

禁止: 用 dx=0,dy=0 的空操作 + retext_dim 来"改尺寸" — 那是只改文字不改几何。

规则:
1. 图纸几何可能按比例缩放绘制: 标注的 shown 是显示值(真实mm), measured 是纸面
   值, 比例 = measured/shown。指令说真实mm时, dx/dy 与窗口坐标要按该比例换算
   成纸面值 (例: 比例1.5, 指令"加宽30" → dx=45)
2. 指令"宽度A改成B": 找到右/上边界的线端点 (其坐标对应原宽), 用 stretch 平移
   (B-A)×比例; 应跟随的孔/文字要圈进窗口
3. 指令"孔径/直径 A 改成 B": scale_region, factor=B/A。窗口必须以特征圆心为中心
   对称圈住整圆+中心线+其标注引点 (窗口中心偏离圆心会导致标注 defpoint 缩偏)
   (摘要 circles 里的 r 是纸面半径; A/B 用标注 shown 值或纸面值都可, 比值一致)
4. 指令"孔移到X": stretch 窗口套住该孔, dx/dy 为目标位移(纸面值)
4. "删除某特征": delete 窗口只圈该特征的实体
5. 修改几何后, 受影响标注 defpoint 若被窗口套住会自动重测量; 套不住时在 ops
   末尾加 retext_dim
6. 窗口外的任何实体不得圈入 (作用范围外对象必须保全)
7. 多个独立修改用多个 op 按序表达; "note" 用一句话说明改了什么
"""


def validate_ops(ops_obj) -> list:
    errs = []
    if not isinstance(ops_obj, dict) or not isinstance(ops_obj.get('ops'), list):
        return ['计划必须是 {"ops": [...]} 对象']
    if not ops_obj['ops']:
        return ['ops 为空 — 若指令无需修改请如实返回 {"ops":[],"note":"无改动"}']
    for i, op in enumerate(ops_obj['ops']):
        k = op.get('op')
        if k in ('stretch', 'delete', 'scale_region'):
            w = op.get('window')
            if not (isinstance(w, list) and len(w) == 4
                    and all(isinstance(v, (int, float)) for v in w)):
                errs.append(f'ops[{i}] window 需要 [x1,y1,x2,y2] 数值')
            if k == 'stretch' and not all(
                    isinstance(op.get(x), (int, float)) for x in ('dx', 'dy')):
                errs.append(f'ops[{i}] stretch 需要 dx/dy 数值')
            if k == 'scale_region' and not (isinstance(op.get('factor'), (int, float))
                                            and op['factor'] > 0):
                errs.append(f'ops[{i}] scale_region 需要 factor>0')
        elif k == 'retext_dim':
            if not op.get('handle'):
                errs.append(f'ops[{i}] retext_dim 需要 handle')
        else:
            errs.append(f'ops[{i}] 未知操作 {k!r}')
    return errs


async def plan_dxf_edit(dxf_path: str, instructions: str) -> Tuple[Optional[dict], dict]:
    """DXF+指令 → 编辑计划 (校验+修复循环)."""
    meta = {'attempts': 0, 'errors': []}
    summary = extract_summary(dxf_path)
    digest = json.dumps(summary, ensure_ascii=False)
    # 摘要可能较大, 截到 LLM 可处理范围 (保留结构)
    if len(digest) > 24000:
        digest = digest[:24000] + '...截断'
    user_msg = f'图纸摘要:\n{digest}\n\n修改指令: {instructions}'
    for _ in range(MAX_REPAIR + 1):
        meta['attempts'] += 1
        r = await call_llm(user_msg, system=PLAN_SYSTEM, max_tokens=2048, temperature=0)
        if r.get('error') or not r.get('text'):
            meta['errors'].append(f"LLM调用失败: {r.get('error') or '空回复'}")
            return None, meta
        plan = extract_json(r['text'])
        if plan is None:
            meta['errors'].append('回复中找不到合法JSON计划')
        else:
            errs = validate_ops(plan)
            if not errs:
                return plan, meta
            meta['errors'] = errs
        user_msg = (f'图纸摘要:\n{digest}\n\n修改指令: {instructions}'
                    + '\n\n上次计划问题, 修正后重新输出完整JSON:\n'
                    + '\n'.join(f'- {e}' for e in meta['errors'][:8]))
    return None, meta
