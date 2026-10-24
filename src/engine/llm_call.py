"""统一LLM调用: 多协议 (Anthropic / OpenAI兼容) + cc-switch 网关 + provider failover.

协议跟随 provider 的 protocol 字段:
  anthropic  → POST {base_url}/v1/messages   (x-api-key / ANTHROPIC_API_KEY)
  openai     → POST {base_url}/chat/completions (Bearer / BEACON_API_KEY)
cc-switch 网关: 环境变量 BEACON_LLM_GATEWAY (如 http://127.0.0.1:PORT/v1) 优先 —
  全部流量走本地网关 (统一 Key 池/限流/审计), 此时 provider/model 仅透传给网关.
failover: 主 provider 失败 → DEEPSEEK_API_KEY 的 deepseek-chat 兜底.
"""
import httpx, os, json
from src.config import CONFIG_FILE, LLM_PROVIDERS

def load_llm_config() -> dict:
    """读取config.json的LLM配置."""
    if os.path.exists(CONFIG_FILE):
        cfg = json.load(open(CONFIG_FILE))
        return cfg.get('llm', {})
    return {}


def _resolve(provider: str, cfg: dict) -> dict:
    """解析 provider → {base_url, model, api_key, protocol}. cc-switch 网关优先."""
    spec = LLM_PROVIDERS.get(provider, {})
    gateway = os.getenv('BEACON_LLM_GATEWAY', '').strip()
    if gateway:
        # 网关接管: 协议/Key 由网关管理 (默认 OpenAI 兼容协议, 可用 BEACON_LLM_PROTOCOL 指定)
        return {'base_url': gateway, 'protocol': os.getenv('BEACON_LLM_PROTOCOL', 'openai'),
                'model': cfg.get('model') or spec.get('models', [''])[0] or 'default',
                'api_key': os.getenv('BEACON_LLM_GATEWAY_KEY', ''),
                'note': 'cc-switch'}
    protocol = cfg.get('protocol') or spec.get('protocol', 'openai')
    if protocol == 'anthropic':
        api_key = cfg.get('api_key') or os.getenv('ANTHROPIC_API_KEY', '')
    else:
        api_key = cfg.get('api_key') or os.getenv('BEACON_API_KEY', '')
    return {'base_url': cfg.get('base_url') or spec.get('base_url', ''),
            'protocol': protocol,
            'model': cfg.get('model') or spec.get('models', [''])[0],
            'api_key': api_key, 'note': ''}


async def _call_anthropic(base_url: str, api_key: str, model: str, system: str,
                          prompt: str, max_tokens: int, temperature: float) -> str:
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    messages = ([{"role": "system", "content": system}] if system else [])
    messages.append({"role": "user", "content": prompt})
    body = {"model": model, "max_tokens": max_tokens, "messages": messages,
            "temperature": temperature}
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(f"{base_url}/v1/messages", headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
        return ''.join(block.get('text', '') for block in data.get('content', []))


async def _call_openai(base_url: str, api_key: str, model: str, system: str,
                       prompt: str, max_tokens: int, temperature: float) -> str:
    headers = {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}
    messages = ([{"role": "system", "content": system}] if system else [])
    messages.append({"role": "user", "content": prompt})
    body = {"model": model, "max_tokens": max_tokens, "messages": messages,
            "temperature": temperature}
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)
        r.raise_for_status()
        return r.json()['choices'][0]['message']['content']


async def call_llm(prompt: str, model: str = None, system: str = "",
                   max_tokens: int = 4096, temperature: float = 0) -> dict:
    """调用LLM. 返回 {text, usage, provider, model, fallback?, error?}.

    自动failover: 主provider失败 → deepseek (OpenAI兼容) 兜底.
    """
    cfg = load_llm_config()
    provider = cfg.get('provider', 'zhipu')
    spec = _resolve(provider, cfg)
    model = model or spec['model']
    errors = []
    try:
        if spec['protocol'] == 'anthropic':
            text = await _call_anthropic(spec['base_url'], spec['api_key'], model,
                                         system, prompt, max_tokens, temperature)
        else:
            text = await _call_openai(spec['base_url'], spec['api_key'], model,
                                      system, prompt, max_tokens, temperature)
        return {"text": text, "usage": {}, "provider": provider, "model": model,
                "note": spec['note']}
    except Exception as ex:
        errors.append(f"{provider}: {str(ex)[:150]}")
        # failover → deepseek (OpenAI兼容)
        try:
            key = os.getenv('DEEPSEEK_API_KEY', '')
            fb = LLM_PROVIDERS.get('deepseek', {})
            if key and fb.get('base_url'):
                text = await _call_openai(fb['base_url'], key, 'deepseek-chat',
                                          system, prompt, max_tokens, temperature)
                return {"text": text, "usage": {}, "provider": "deepseek",
                        "model": "deepseek-chat", "fallback": True,
                        "note": 'failover:' + '; '.join(errors)}
        except Exception as ex2:
            errors.append(f"deepseek: {str(ex2)[:150]}")
        return {"text": "", "error": '; '.join(errors)[:300], "provider": provider}
