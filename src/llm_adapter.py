"""
EPLAN AI Bridge -- LLM Provider Adapter

Unified chat/generate interface supporting OpenAI, Anthropic, DeepSeek,
Ollama (local), and OpenRouter.  Features:

- Async and sync generation
- Token counting and cost estimation
- Configurable rate limiting (requests per minute)
- Retry with exponential backoff
- Streaming response support
- Dict-based configuration
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cost table: cost per 1K tokens (prompt / completion) in USD
# ---------------------------------------------------------------------------
_COST_TABLE: dict[str, dict[str, float]] = {
    "gpt-4o": {"prompt": 0.0025, "completion": 0.01},
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-4.1": {"prompt": 0.002, "completion": 0.008},
    "gpt-4.1-mini": {"prompt": 0.0004, "completion": 0.0016},
    "gpt-4.1-nano": {"prompt": 0.0001, "completion": 0.0004},
    "claude-sonnet-4-20250514": {"prompt": 0.003, "completion": 0.015},
    "claude-opus-4-20250514": {"prompt": 0.015, "completion": 0.075},
    "claude-haiku-3.5-20241022": {"prompt": 0.0008, "completion": 0.004},
    "deepseek-chat": {"prompt": 0.00014, "completion": 0.00028},
    "deepseek-reasoner": {"prompt": 0.00055, "completion": 0.00219},
}

_DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "ollama": "http://localhost:11434/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


# ---------------------------------------------------------------------------
# Token counting (rough approximation for planning/cost estimation)
# ---------------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    """Rough token count estimation (~4 chars per token for English, ~2 for CJK)."""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    others = len(text) - cjk
    return int(cjk / 1.5 + others / 4)


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate cost in USD for a request."""
    entry = _COST_TABLE.get(model)
    if not entry:
        return 0.0
    return (prompt_tokens * entry["prompt"] + completion_tokens * entry["completion"]) / 1000.0


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
class RateLimiter:
    """Sliding-window rate limiter (requests per minute)."""

    def __init__(self, rpm: int = 60) -> None:
        self._rpm = rpm
        self._timestamps: deque[float] = deque()

    @property
    def rpm(self) -> int:
        return self._rpm

    def wait_if_needed(self) -> None:
        """Block until a request slot is available."""
        now = time.monotonic()
        cutoff = now - 60.0
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
        if len(self._timestamps) >= self._rpm:
            sleep_time = 60.0 - (now - self._timestamps[0]) + 0.05
            if sleep_time > 0:
                logger.debug("Rate limiter: sleeping %.2fs", sleep_time)
                time.sleep(sleep_time)
        self._timestamps.append(time.monotonic())

    async def async_wait_if_needed(self) -> None:
        """Async version of wait_if_needed."""
        now = time.monotonic()
        cutoff = now - 60.0
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
        if len(self._timestamps) >= self._rpm:
            sleep_time = 60.0 - (now - self._timestamps[0]) + 0.05
            if sleep_time > 0:
                logger.debug("Rate limiter: async sleeping %.2fs", sleep_time)
                await asyncio.sleep(sleep_time)
        self._timestamps.append(time.monotonic())


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class LLMConfig:
    """LLM provider configuration.

    Can be constructed directly or via ``LLMConfig.from_dict(cfg_dict)``.
    """

    provider: str = "openai"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    base_url: str = ""
    temperature: float = 0.3
    max_tokens: int = 2048
    max_retries: int = 3
    rpm: int = 60  # requests per minute rate limit

    def __post_init__(self) -> None:
        if not self.base_url:
            self.base_url = _DEFAULT_BASE_URLS.get(self.provider, "")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LLMConfig:
        """Create config from a plain dictionary."""
        return cls(
            provider=d.get("provider", "openai"),
            api_key=d.get("api_key", ""),
            model=d.get("model", "gpt-4o-mini"),
            base_url=d.get("base_url", ""),
            temperature=d.get("temperature", 0.3),
            max_tokens=d.get("max_tokens", 2048),
            max_retries=d.get("max_retries", 3),
            rpm=d.get("rpm", 60),
        )


# ---------------------------------------------------------------------------
# Usage stats returned alongside responses
# ---------------------------------------------------------------------------
@dataclass
class UsageStats:
    """Token usage and cost for a single request."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    model: str = ""
    provider: str = ""

    @classmethod
    def from_api_response(cls, data: dict[str, Any], model: str, provider: str) -> UsageStats:
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        return cls(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated_cost_usd=estimate_cost(model, prompt_tokens, completion_tokens),
            model=model,
            provider=provider,
        )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------
class LLMAdapter:
    """Unified LLM interface with retry, rate limiting, streaming, and cost tracking."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._client = httpx.Client(timeout=60.0)
        self._async_client: httpx.AsyncClient | None = None
        self._rate_limiter = RateLimiter(config.rpm)
        self._total_cost: float = 0.0
        self._request_count: int = 0

    # -- convenience constructor -------------------------------------------
    @classmethod
    def from_dict(cls, cfg: dict[str, Any]) -> LLMAdapter:
        return cls(LLMConfig.from_dict(cfg))

    # -- synchronous chat ---------------------------------------------------
    def chat(
        self,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """Send a chat request and return the assistant text (sync)."""
        result, _ = self.generate(system_prompt, user_message, history)
        return result

    def generate(
        self,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[str, UsageStats]:
        """Send a chat request; return (text, usage_stats).

        Applies rate limiting and exponential-backoff retry.
        """
        messages = self._build_messages(system_prompt, user_message, history)

        last_error: Exception | None = None
        for attempt in range(self.config.max_retries):
            self._rate_limiter.wait_if_needed()
            try:
                text, stats = self._dispatch(messages)
                self._total_cost += stats.estimated_cost_usd
                self._request_count += 1
                return text, stats
            except httpx.TimeoutException as exc:
                last_error = exc
                self._backoff(attempt)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (429, 503):
                    last_error = exc
                    self._backoff(attempt)
                else:
                    raise ValueError(f"LLM API error: {exc.response.status_code}") from exc
            except httpx.ConnectError as exc:
                raise ConnectionError(f"Cannot connect to LLM service at {self.config.base_url}") from exc

        raise ConnectionError(f"LLM request failed after {self.config.max_retries} retries: {last_error}")

    # -- async generate -----------------------------------------------------
    async def async_generate(
        self,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[str, UsageStats]:
        """Async version of generate."""
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=60.0)
        messages = self._build_messages(system_prompt, user_message, history)

        last_error: Exception | None = None
        for attempt in range(self.config.max_retries):
            await self._rate_limiter.async_wait_if_needed()
            try:
                text, stats = await self._async_dispatch(messages)
                self._total_cost += stats.estimated_cost_usd
                self._request_count += 1
                return text, stats
            except httpx.TimeoutException as exc:
                last_error = exc
                await asyncio.sleep(self._backoff_delay(attempt))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (429, 503):
                    last_error = exc
                    await asyncio.sleep(self._backoff_delay(attempt))
                else:
                    raise ValueError(f"LLM API error: {exc.response.status_code}") from exc
            except httpx.ConnectError as exc:
                raise ConnectionError(f"Cannot connect to LLM service at {self.config.base_url}") from exc

        raise ConnectionError(f"LLM request failed after {self.config.max_retries} retries: {last_error}")

    # -- streaming ----------------------------------------------------------
    def stream(
        self,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> Iterator[str]:
        """Stream response chunks (sync). Only supported for OpenAI-compatible providers."""
        messages = self._build_messages(system_prompt, user_message, history)
        self._rate_limiter.wait_if_needed()

        if self.config.provider == "anthropic":
            yield from self._stream_anthropic(messages)
        else:
            yield from self._stream_openai(messages)

    async def async_stream(
        self,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        """Stream response chunks (async)."""
        messages = self._build_messages(system_prompt, user_message, history)
        await self._rate_limiter.async_wait_if_needed()

        if self.config.provider == "anthropic":
            async for chunk in self._async_stream_anthropic(messages):
                yield chunk
        else:
            async for chunk in self._async_stream_openai(messages):
                yield chunk

    # -- cost tracking ------------------------------------------------------
    @property
    def total_cost(self) -> float:
        return self._total_cost

    @property
    def request_count(self) -> int:
        return self._request_count

    # -- internal: message building ----------------------------------------
    @staticmethod
    def _build_messages(
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        return messages

    # -- internal: sync dispatch --------------------------------------------
    def _dispatch(self, messages: list[dict[str, str]]) -> tuple[str, UsageStats]:
        if self.config.provider == "anthropic":
            return self._call_anthropic(messages)
        return self._call_openai_compatible(messages)

    def _call_openai_compatible(self, messages: list[dict[str, str]]) -> tuple[str, UsageStats]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        resp = self._client.post(
            f"{self.config.base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        stats = UsageStats.from_api_response(data, self.config.model, self.config.provider)
        if stats.prompt_tokens == 0:
            approx_prompt = sum(estimate_tokens(m["content"]) for m in messages)
            approx_comp = estimate_tokens(text)
            stats = UsageStats(
                prompt_tokens=approx_prompt,
                completion_tokens=approx_comp,
                total_tokens=approx_prompt + approx_comp,
                estimated_cost_usd=estimate_cost(self.config.model, approx_prompt, approx_comp),
                model=self.config.model,
                provider=self.config.provider,
            )
        return text, stats

    def _call_anthropic(self, messages: list[dict[str, str]]) -> tuple[str, UsageStats]:
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        chat_messages = [m for m in messages if m["role"] != "system"]

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": system,
            "messages": chat_messages,
        }
        resp = self._client.post(
            f"{self.config.base_url}/messages",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"]
        stats = UsageStats.from_api_response(data, self.config.model, self.config.provider)
        if stats.prompt_tokens == 0:
            approx_prompt = sum(estimate_tokens(m["content"]) for m in messages)
            approx_comp = estimate_tokens(text)
            stats = UsageStats(
                prompt_tokens=approx_prompt,
                completion_tokens=approx_comp,
                total_tokens=approx_prompt + approx_comp,
                estimated_cost_usd=estimate_cost(self.config.model, approx_prompt, approx_comp),
                model=self.config.model,
                provider=self.config.provider,
            )
        return text, stats

    # -- internal: streaming implementations --------------------------------
    def _stream_openai(self, messages: list[dict[str, str]]) -> Iterator[str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": True,
        }
        with self._client.stream(
            "POST",
            f"{self.config.base_url}/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("data: "):
                    chunk = line[6:]
                    if chunk.strip() == "[DONE]":
                        break
                    import json as _json
                    try:
                        obj = _json.loads(chunk)
                        delta = obj.get("choices", [{}])[0].get("delta", {})
                        if "content" in delta:
                            yield delta["content"]
                    except _json.JSONDecodeError:
                        continue

    def _stream_anthropic(self, messages: list[dict[str, str]]) -> Iterator[str]:
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        chat_messages = [m for m in messages if m["role"] != "system"]
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": system,
            "messages": chat_messages,
            "stream": True,
        }
        with self._client.stream(
            "POST",
            f"{self.config.base_url}/messages",
            headers=headers,
            json=payload,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("data: "):
                    import json as _json
                    try:
                        obj = _json.loads(line[6:])
                        if obj.get("type") == "content_block_delta":
                            yield obj["delta"].get("text", "")
                    except _json.JSONDecodeError:
                        continue

    async def _async_stream_openai(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=60.0)
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": True,
        }
        async with self._async_client.stream(
            "POST",
            f"{self.config.base_url}/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    chunk = line[6:]
                    if chunk.strip() == "[DONE]":
                        break
                    import json as _json
                    try:
                        obj = _json.loads(chunk)
                        delta = obj.get("choices", [{}])[0].get("delta", {})
                        if "content" in delta:
                            yield delta["content"]
                    except _json.JSONDecodeError:
                        continue

    async def _async_stream_anthropic(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=60.0)
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        chat_messages = [m for m in messages if m["role"] != "system"]
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": system,
            "messages": chat_messages,
            "stream": True,
        }
        async with self._async_client.stream(
            "POST",
            f"{self.config.base_url}/messages",
            headers=headers,
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    import json as _json
                    try:
                        obj = _json.loads(line[6:])
                        if obj.get("type") == "content_block_delta":
                            yield obj["delta"].get("text", "")
                    except _json.JSONDecodeError:
                        continue

    # -- internal: async dispatch -------------------------------------------
    async def _async_dispatch(self, messages: list[dict[str, str]]) -> tuple[str, UsageStats]:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=60.0)
        if self.config.provider == "anthropic":
            return await self._async_call_anthropic(messages)
        return await self._async_call_openai_compatible(messages)

    async def _async_call_openai_compatible(
        self, messages: list[dict[str, str]]
    ) -> tuple[str, UsageStats]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        resp = await self._async_client.post(
            f"{self.config.base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        stats = UsageStats.from_api_response(data, self.config.model, self.config.provider)
        if stats.prompt_tokens == 0:
            approx_prompt = sum(estimate_tokens(m["content"]) for m in messages)
            approx_comp = estimate_tokens(text)
            stats = UsageStats(
                prompt_tokens=approx_prompt,
                completion_tokens=approx_comp,
                total_tokens=approx_prompt + approx_comp,
                estimated_cost_usd=estimate_cost(self.config.model, approx_prompt, approx_comp),
                model=self.config.model,
                provider=self.config.provider,
            )
        return text, stats

    async def _async_call_anthropic(
        self, messages: list[dict[str, str]]
    ) -> tuple[str, UsageStats]:
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        chat_messages = [m for m in messages if m["role"] != "system"]
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": system,
            "messages": chat_messages,
        }
        resp = await self._async_client.post(
            f"{self.config.base_url}/messages",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"]
        stats = UsageStats.from_api_response(data, self.config.model, self.config.provider)
        if stats.prompt_tokens == 0:
            approx_prompt = sum(estimate_tokens(m["content"]) for m in messages)
            approx_comp = estimate_tokens(text)
            stats = UsageStats(
                prompt_tokens=approx_prompt,
                completion_tokens=approx_comp,
                total_tokens=approx_prompt + approx_comp,
                estimated_cost_usd=estimate_cost(self.config.model, approx_prompt, approx_comp),
                model=self.config.model,
                provider=self.config.provider,
            )
        return text, stats

    # -- internal: backoff --------------------------------------------------
    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """Exponential backoff: 1s, 2s, 4s, ... capped at 30s."""
        return min(2**attempt, 30.0)

    def _backoff(self, attempt: int) -> None:
        delay = self._backoff_delay(attempt)
        logger.warning("LLM request failed, retrying in %.1fs (attempt %d)", delay, attempt + 1)
        time.sleep(delay)

    # -- cleanup ------------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    async def async_close(self) -> None:
        self._client.close()
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # pragma: no cover
            pass
