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
    """保存LLM配置到 data/config.json."""
    if req.provider not in LLM_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的provider: {req.provider}, 可选: {list(LLM_PROVIDERS.keys())}",
        )
    provider_cfg = LLM_PROVIDERS[req.provider]
    base_url = req.base_url or provider_cfg["base_url"]
    cfg = _load_config()
    cfg["llm"] = {
        "provider": req.provider,
        "model": req.model,
        "api_key": req.api_key,
        "base_url": base_url,
        "protocol": provider_cfg["protocol"],
    }
    _save_config(cfg)
    return {
        "provider": req.provider,
        "model": req.model,
        "base_url": base_url,
        "saved": True,
    }
