"""统一LLM调用: Anthropic兼容协议(与wiki一致) + provider failover + 成本追踪."""
import httpx, os, json
from src.config import CONFIG_FILE, LLM_PROVIDERS

def load_llm_config() -> dict:
    """读取config.json的LLM配置."""
    if os.path.exists(CONFIG_FILE):
        cfg = json.load(open(CONFIG_FILE))
        return cfg.get('llm', {})
    return {}

async def call_llm(prompt: str, model: str = None, system: str = "", max_tokens: int = 4096, temperature: float = 0) -> dict:
    """调用LLM(Anthropic兼容协议). 返回{text, usage, provider, model}.
    自动failover: 主provider失败→备用provider."""
    cfg = load_llm_config()
    provider = cfg.get('provider', 'zhipu')
    base_url = cfg.get('base_url', LLM_PROVIDERS.get(provider, {}).get('base_url', ''))
    api_key = cfg.get('api_key', os.getenv('BEACON_API_KEY', ''))
    model = model or cfg.get('model', 'glm-4.5-air')

    # Anthropic兼容请求
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {"model": model, "max_tokens": max_tokens, "messages": messages, "temperature": temperature}

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.post(f"{base_url}/v1/messages", headers=headers, json=body)
            r.raise_for_status()
            data = r.json()
            text = ''.join(block.get('text','') for block in data.get('content', []))
            return {"text": text, "usage": data.get('usage', {}), "provider": provider, "model": model}
        except Exception as ex:
            # failover到deepseek(OpenAI兼容)
            try:
                fb = LLM_PROVIDERS.get('deepseek', {})
                headers2 = {"Authorization": f"Bearer {os.getenv('DEEPSEEK_API_KEY','')}", "content-type": "application/json"}
                body2 = {"model": "deepseek-chat", "max_tokens": max_tokens, "messages": messages, "temperature": temperature}
                async with httpx.AsyncClient(timeout=30) as c2:
                    r2 = await c2.post(f"{fb['base_url']}/chat/completions", headers=headers2, json=body2)
                    r2.raise_for_status()
                    d2 = r2.json()
                    return {"text": d2['choices'][0]['message']['content'], "usage": d2.get('usage',{}), "provider": "deepseek", "model": "deepseek-chat", "fallback": True}
            except:
                return {"text": "", "error": str(ex)[:200], "provider": provider}
