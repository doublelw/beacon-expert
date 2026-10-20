"""B线 Phase 3: 自然语言 → build123d 参数化构建代码 (LLM 规划器).

产出契约 (LLM 只写"构建体", 导出/文件由 runner 控制):
  - 首行必须是 `PARAMS = {...}` 参数字典 (参数源, 改图功能复用)
  - 必须定义 `part` (build123d 实体, 代数式)
  - 只允许 import math / from build123d import ...; 禁文件/网络/子进程
  - 经 _lint 白名单围栏 + runner 沙盒执行 (隔离venv, 超时, 一次性)

修复循环 (huashu-cad 纪律: 失败先读真实报错): 运行错误原样带回 LLM 修, ≤2轮。
"""
import re
from typing import Optional, Tuple

from src.engine.llm_call import call_llm

MAX_REPAIR_ROUNDS = 2

# 构建体围栏: 只许 build123d/math, 禁一切IO与动态执行
_BANNED = re.compile(
    r'subprocess|socket|urllib|requests|http|shutil|os\.system|os\.popen|'
    r'\beval\s*\(|\bexec\s*\(|__import__|\bopen\s*\(|\binput\s*\(|'
    r'^\s*import\s+(?!math\b)\w|^\s*from\s+(?!build123d\b|math\b)\w',
    re.M)

PLAN_SYSTEM = """你是参数化CAD建模器。用户给你一个零件需求, 你写一段 build123d (代数式) 构建代码。

代码契约 (违反即失败):
1. 第一行必须是: PARAMS = {"参数名": 数值, ...}  (所有尺寸进字典, 单位mm)
2. 最后必须定义变量 part (一个 build123d 实体, 用 PARAMS 里的值构建)
3. 允许的 import 只有: from build123d import ..., import math
4. 不写任何文件/打印/循环外的副作用; part 表达式用代数式组合 (+ - * Pos() Location)

build123d 语法要点 (代数式):
- 盒: Box(w, h, d)  圆柱: Cylinder(半径, 高)  注意第一个参数是半径不是直径
- 平移: Pos(x, y, z) * 实体
- 对齐: align=(Align.CENTER, Align.CENTER, Align.MIN) 表示 x/y 居中、z 从0起。
  默认 Box/Cylinder 三轴都居中(中心在原点)。位置计算必须先想清楚对齐基准。
- 布尔: 实体A - 实体B (挖) / 实体A + 实体B (并)
- 例: 底板带孔 part = Box(100, 60, 10, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Pos(0, 0, -1) * Cylinder(11, 12)

建模要求:
1. 尺寸全部来自 PARAMS (用 PARAMS["宽度"] 这样的引用, 不在表达式里写裸数字)
2. 单一实体优先 (布尔运算后 solids 数为 1)
3. 整体外廓控制在 400×300×200mm 内, 零件放置让主平面特征朝 +Z
4. 用户没给的尺寸: 按工程惯例取值并放进 PARAMS, 不要编造精度
5. 输出只有代码本身, 不要 ``` 围栏, 不要解释
"""


def lint_build_code(code: str) -> list:
    """白名单围栏检查, 返回违规清单 (空=通过)."""
    errs = []
    if not code or not code.strip():
        return ['空代码']
    if 'PARAMS' not in code:
        errs.append('缺少 PARAMS 参数字典 (第一行)')
    m = re.search(r'^\s*PARAMS\s*=', code, re.M)
    if not m or m.start() != 0:
        errs.append('PARAMS 必须在代码第一行')
    if not re.search(r'^\s*part\s*=', code, re.M):
        errs.append('缺少 part 实体定义')
    for hit in _BANNED.finditer(code):
        errs.append(f'禁用内容: {hit.group(0)!r} (只允许 build123d/math)')
        if len(errs) >= 6:
            break
    return errs


def extract_code(text: str) -> Optional[str]:
    """剥 ``` 围栏与前后废话, 取代码体."""
    if not text:
        return None
    t = text.strip()
    m = re.search(r'```(?:python)?\s*(.*?)```', t, re.S)
    if m:
        t = m.group(1).strip()
    return t if 'PARAMS' in t and 'part' in t else None


async def plan_3d(prompt: str) -> Tuple[Optional[str], dict]:
    """NL → (构建代码|None, 元信息). 代码已过 lint; 失败原因在 meta['errors']."""
    meta = {'attempts': 0, 'llm': None, 'errors': []}
    user_msg = prompt
    for _ in range(MAX_REPAIR_ROUNDS + 1):
        meta['attempts'] += 1
        r = await call_llm(user_msg, system=PLAN_SYSTEM, max_tokens=4096, temperature=0)
        meta['llm'] = {'provider': r.get('provider'), 'model': r.get('model'),
                       'usage': r.get('usage')}
        if r.get('error') or not r.get('text'):
            meta['errors'].append(f"LLM调用失败: {r.get('error') or '空回复'}")
            return None, meta
        code = extract_code(r['text'])
        if code is None:
            meta['errors'].append('回复中找不到 PARAMS+part 结构的代码')
        else:
            errs = lint_build_code(code)
            if not errs:
                return code, meta
            meta['errors'] = errs
        user_msg = (f"{prompt}\n\n你上次的代码有以下问题, 修正后重新输出完整代码"
                    f"(只输出代码):\n" + '\n'.join(f'- {e}' for e in meta['errors'][:8]))
    return None, meta


async def repair_3d(prompt: str, code: str, error: str) -> Tuple[Optional[str], dict]:
    """运行失败修复: 带真实报错回炉 (≤1轮)."""
    meta = {'attempts': 0, 'llm': None, 'errors': [error[:500]]}
    r = await call_llm(
        f"{prompt}\n\n现有代码:\n{code}\n\n运行报错:\n{error[:1500]}\n"
        f"修复后重新输出完整代码(只输出代码, 保持 PARAMS 第一行 + part 定义):",
        system=PLAN_SYSTEM, max_tokens=4096, temperature=0)
    meta['attempts'] = 1
    meta['llm'] = {'provider': r.get('provider'), 'model': r.get('model')}
    if r.get('error') or not r.get('text'):
        return None, meta
    code2 = extract_code(r['text'])
    if code2 is None or lint_build_code(code2):
        return None, meta
    return code2, meta
