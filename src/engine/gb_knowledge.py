"""GB 机械制图国标知识加载器.

把 ~/.claude/skills/gb-mechanical-drawing 的内容按场景注入 Beacon 的 LLM 调用(system 上下文),
让 audit/plan/refine 真正按国标走。读取带缓存(进程级)。
"""
import functools
from pathlib import Path
from src.config import GB_SKILL_DIR


@functools.lru_cache(maxsize=8)
def _read(rel: str) -> str:
    try:
        return (Path(GB_SKILL_DIR) / rel).read_text(encoding="utf-8")
    except Exception:
        return ""


def gb_audit_context() -> str:
    """审计阶段: 核心规则 + 合规清单(GB/T 4458.4/4458.5/131/1182/4459.1)."""
    return _read("SKILL.md") + "\n\n==== 审计清单 ====\n" + _read("references/audit-checklist.md")


def gb_dimensioning_context() -> str:
    """标注规划阶段: 尺寸注法详规(GB/T 4458.4)."""
    return _read("SKILL.md") + "\n\n==== 尺寸注法详规 ====\n" + _read("references/dimensioning-4458-4.md")


def gb_consultation_context() -> str:
    """顾问阶段: 精简的加工商关键信息 + 约束→工艺映射(避免灌入整套3KB标准细节拖慢调用)."""
    return _read("references/consultation-guide.md")
