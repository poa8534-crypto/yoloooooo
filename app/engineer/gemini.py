"""Gemini over REST, carried by two keys.

The keys were bought for this work, so the client spends them rather than
rationing them: calls alternate between the keys, and a key that answers 429
cools down for the delay Google names while the other key takes the call. Only
when both keys are cooling does a call wait, and never past its deadline.

What it will not do is retry a request Google refused outright. A 400, 401,
403 or 404 is a wrong model name, a bad key or a rejected prompt; asking again
spends quota on the same answer.

Keys travel in the `x-goog-api-key` header, never the URL, and every error
message is scrubbed of both keys before it leaves this module.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from ..config import Settings

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
DEFAULT_RATE_LIMIT_COOLDOWN = 30.0
TRANSIENT_COOLDOWN = 5.0
MAX_TRANSIENT_FAILURES = 6
# Waiting out rate limits is fine; waiting forever is not, when no deadline was given.
MAX_RATE_LIMITS_PER_CALL = 40


class GeminiError(RuntimeError):
    pass


class GeminiUnavailable(GeminiError):
    """No key could answer before the deadline. Planned: the budget held."""

    planned = True


class GeminiRefused(GeminiError):
    """Google refused the request itself. Retrying would repeat the refusal."""

    planned = False


class GeminiIncomplete(GeminiError):
    """An answer arrived but stopped early (length, safety, recitation)."""

    planned = False


@dataclass(frozen=True)
class GeminiReply:
    text: str
    model: str
    key_label: str
    usage: dict[str, int]


@dataclass
class _Key:
    label: str
    secret: str
    cooling_until: float = 0.0
    calls: int = 0


UsageHook = Callable[[str, str, str, dict[str, int]], None]


@dataclass
class GeminiClient:
    keys: list[str]
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout: float = 900.0
    max_output_tokens: int = 65_536
    client: httpx.AsyncClient | None = None
    on_usage: UsageHook | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    _keys: list[_Key] = field(init=False)
    _next: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self._keys = [_Key(f"key{index}", secret)
                      for index, secret in enumerate(self.keys, start=1) if secret]
        self._owns_client = self.client is None
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=30.0))

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs) -> GeminiClient:
        return cls(keys=[settings.gemini_api_key_1, settings.gemini_api_key_2],
                   base_url=settings.gemini_base_url, timeout=settings.gemini_timeout_seconds,
                   max_output_tokens=settings.gemini_max_output_tokens, **kwargs)

    async def close(self) -> None:
        if self._owns_client and self.client is not None:
            await self.client.aclose()

    def scrub(self, text: str) -> str:
        for key in self._keys:
            text = text.replace(key.secret, "[REDACTED]")
        return text

    def _choose(self) -> _Key:
        """The next key in rotation that is not cooling, else the soonest ready."""
        now = self.clock()
        ordered = self._keys[self._next:] + self._keys[:self._next]
        ready = [key for key in ordered if key.cooling_until <= now]
        chosen = ready[0] if ready else min(self._keys, key=lambda key: key.cooling_until)
        self._next = (self._keys.index(chosen) + 1) % len(self._keys)
        return chosen

    async def generate(self, *, model: str, system: str, prompt: str,
                       json_output: bool = True, deadline: float | None = None) -> GeminiReply:
        """One completion. `deadline` is a `clock()` value this call must not wait past."""
        if not self._keys:
            raise GeminiUnavailable("no Gemini API key is configured (GEMINI_API_KEY_1 / GEMINI_API_KEY_2)")
        body: dict = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": self.max_output_tokens},
        }
        if json_output:
            body["generationConfig"]["responseMimeType"] = "application/json"
        url = f"{self.base_url}/models/{model}:generateContent"
        transient = rate_limited = 0
        while True:
            key = self._choose()
            wait = key.cooling_until - self.clock()
            if wait > 0:
                if deadline is not None and self.clock() + wait > deadline:
                    raise GeminiUnavailable(
                        f"every Gemini key is rate limited for another {wait:.0f}s, past this run's deadline")
                await self.sleep(wait)
            try:
                response = await self.client.post(url, json=body, headers={"x-goog-api-key": key.secret})
            except httpx.HTTPError as exc:
                transient += 1
                key.cooling_until = self.clock() + TRANSIENT_COOLDOWN * transient
                if transient >= MAX_TRANSIENT_FAILURES:
                    raise GeminiUnavailable(self.scrub(f"Gemini unreachable: {type(exc).__name__}: {exc}")) from None
                continue
            key.calls += 1
            if response.status_code == 200:
                return self._reply(response, model, key)
            detail = self.scrub(_error_message(response))
            self._record(key, model, "rate_limited" if response.status_code == 429 else "error", {})
            if response.status_code == 429:
                rate_limited += 1
                if rate_limited >= MAX_RATE_LIMITS_PER_CALL:
                    raise GeminiUnavailable(f"Gemini rate limited {rate_limited} times in one call: {detail}")
                key.cooling_until = self.clock() + _retry_delay(response)
                continue
            if response.status_code in RETRYABLE_STATUS:
                transient += 1
                key.cooling_until = self.clock() + TRANSIENT_COOLDOWN * transient
                if transient >= MAX_TRANSIENT_FAILURES:
                    raise GeminiUnavailable(f"Gemini kept failing ({response.status_code}): {detail}")
                continue
            raise GeminiRefused(f"Gemini refused the request ({response.status_code}): {detail}")

    def _reply(self, response: httpx.Response, model: str, key: _Key) -> GeminiReply:
        data = response.json()
        meta = data.get("usageMetadata") or {}
        usage = {
            "prompt_tokens": int(meta.get("promptTokenCount") or 0),
            "output_tokens": int(meta.get("candidatesTokenCount") or 0),
            "thought_tokens": int(meta.get("thoughtsTokenCount") or 0),
            "total_tokens": int(meta.get("totalTokenCount") or 0),
        }
        candidates = data.get("candidates") or []
        if not candidates:
            self._record(key, model, "blocked", usage)
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates returned")
            raise GeminiRefused(f"Gemini returned no answer: {reason}")
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        # Thought summaries are the model talking to itself, not the answer.
        text = "".join(part.get("text", "") for part in (candidate.get("content") or {}).get("parts", [])
                       if not part.get("thought"))
        self._record(key, model, "ok" if finish in (None, "STOP") else "incomplete", usage)
        if finish not in (None, "STOP"):
            raise GeminiIncomplete(f"Gemini stopped early: finishReason={finish}")
        return GeminiReply(text=text, model=model, key_label=key.label, usage=usage)

    def _record(self, key: _Key, model: str, outcome: str, usage: dict[str, int]) -> None:
        if self.on_usage:
            self.on_usage(key.label, model, outcome, usage)


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["message"])[:500]
    except (ValueError, KeyError, TypeError):
        return response.text[:500]


def _retry_delay(response: httpx.Response) -> float:
    """The delay Google asks for: RetryInfo in the body, else Retry-After."""
    try:
        for detail in response.json()["error"].get("details", []):
            if str(detail.get("@type", "")).endswith("RetryInfo"):
                match = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(detail.get("retryDelay", "")))
                if match:
                    return float(match.group(1))
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    header = response.headers.get("retry-after", "")
    return float(header) if header.replace(".", "", 1).isdigit() else DEFAULT_RATE_LIMIT_COOLDOWN
