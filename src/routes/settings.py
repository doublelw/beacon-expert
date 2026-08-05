"""Beacon专家 - 设置路由 (LLM配置)."""
import json
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional

from src.auth import get_current_user
from src.config import CONFIG_FILE, LLM_PROVIDERS
from src.database import User

router = APIRouter(prefix="/api/settings", tags=["设置"])


class LLMConfig(BaseModel):
    provider: str = Field(..., description="zhipu/anthropic/openai/deepseek/ollama")
    model: str
    api_key: str = ""
    base_url: Optional[str] = None


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


@router.get("/llm")
def get_llm_config(user: User = Depends(get_current_user)):
    """返回LLM配置 (api_key隐藏, 只显示provider/model/base_url)."""
    cfg = _load_config().get("llm", {})
    return {
        "provider": cfg.get("provider"),
        "model": cfg.get("model"),
        "base_url": cfg.get("base_url"),
        "has_api_key": bool(cfg.get("api_key")),
        "available_providers": list(LLM_PROVIDERS.keys()),
    }


@router.post("/llm")
def save_llm_config(req: LLMConfig, user: User = Depends(get_current_user)):
    """保存LLM配置到 data/config.json. 表单api_key为空时保留旧key(界面永不回填真实key)."""
    if req.provider not in LLM_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的provider: {req.provider}, 可选: {list(LLM_PROVIDERS.keys())}",
        )
    provider_cfg = LLM_PROVIDERS[req.provider]
    base_url = req.base_url or provider_cfg["base_url"]
    old = _load_config().get("llm", {})
    # 界面key框留空=保留原值, 避免真实key回传前端
    api_key = req.api_key.strip() if req.api_key and req.api_key.strip() else old.get("api_key", "")
    cfg = _load_config()
    cfg["llm"] = {
        "provider": req.provider,
        "model": req.model,
        "api_key": api_key,
        "base_url": base_url,
        "protocol": provider_cfg["protocol"],
    }
    _save_config(cfg)
    return {
        "provider": req.provider,
        "model": req.model,
        "base_url": base_url,
        "saved": True,
        "has_api_key": bool(api_key),
    }


@router.post("/llm/test")
async def test_llm_config(user: User = Depends(get_current_user)):
    """测试已保存的LLM配置是否有效. 只返回有效性, 永不返回key."""
    import asyncio
    from src.engine.llm_call import call_llm
    if not _load_config().get("llm", {}).get("api_key"):
        return {"valid": False, "error": "未配置 API Key"}
    try:
        r = await asyncio.wait_for(call_llm("回复OK", max_tokens=10), timeout=35)
        if r.get("text") and not r.get("error"):
            return {"valid": True, "provider": r.get("provider"), "model": r.get("model")}
        return {"valid": False, "error": r.get("error") or "LLM 无响应"}
    except asyncio.TimeoutError:
        return {"valid": False, "error": "请求超时"}
    except Exception as e:
        return {"valid": False, "error": str(e)[:200]}
