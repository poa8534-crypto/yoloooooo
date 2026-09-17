"""Gemini over its native REST API, for the Roblox Engineer.

Rate limits are tracked per model, because Google's quotas are per model: Pro
answering 429 says nothing about Flash. And by default a 429 cools every key
for that model, because the keys were measured to share one project quota --
switching keys after a 429 only earns a second 429.

A caller can choose not to wait. `max_wait=0` turns "every key is rate limited
for this model" into an immediate GeminiUnavailable, which is how the engineer
hands an attempt from Pro to Flash instead of sitting out Pro's quota. With no
`max_wait`, a call waits for the soonest key, but never past its deadline.

What it will not do is retry a request Google refused outright. A 400, 401,
403 or 404 is a wrong model name, a bad key or a rejected prompt; asking again
spends time on the same answer.

Keys travel in the `x-goog-api-key` header, never the URL, and every error
message is scrubbed of every key before it leaves this module.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

RETRYABLE_STATUS = {500, 502, 503, 504}
DEFAULT_RATE_LIMIT_COOLDOWN = 30.0
TRANSIENT_COOLDOWN = 5.0
MAX_TRANSIENT_FAILURES = 6
# Waiting out rate limits is fine; waiting forever is not, when no deadline was given.
MAX_RATE_LIMITS_PER_CALL = 40


class GeminiError(RuntimeError):
    pass


class GeminiUnavailable(GeminiError):
    """No key could answer in time. Planned: a limit held, nothing is broken."""

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


UsageHook = Callable[[str, str, str, dict[str, int]], None]


@dataclass
class GeminiClient:
    keys: list[str]
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout: float = 900.0
    max_output_tokens: int = 65_536
    # Measured: both keys draw on one project quota. False only for keys in
    # separate projects, where a 429 on one key says nothing about the other.
    shared_quota: bool = True
    client: httpx.AsyncClient | None = None
    on_usage: UsageHook | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    _keys: list[_Key] = field(init=False)
    _next: int = field(init=False, default=0)
    _cooling: dict[tuple[str, str], float] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._keys = [_Key(f"key{index}", secret)
                      for index, secret in enumerate(self.keys, start=1) if secret]
        self._owns_client = self.client is None
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=30.0))

    async def close(self) -> None:
        if self._owns_client and self.client is not None:
            await self.client.aclose()

    def scrub(self, text: str) -> str:
        for key in self._keys:
            text = text.replace(key.secret, "[REDACTED]")
        return text

    def _ready_at(self, key: _Key, model: str) -> float:
        return self._cooling.get((key.label, model), 0.0)

    def _cool(self, key: _Key, model: str, seconds: float, *, every_key: bool) -> None:
        until = self.clock() + seconds
        for target in (self._keys if every_key else [key]):
            self._cooling[(target.label, model)] = max(self._ready_at(target, model), until)

    def _choose(self, model: str) -> _Key:
        """The next key in rotation that is ready for this model, else the soonest ready."""
        now = self.clock()
        ordered = self._keys[self._next:] + self._keys[:self._next]
        ready = [key for key in ordered if self._ready_at(key, model) <= now]
        chosen = ready[0] if ready else min(self._keys, key=lambda key: self._ready_at(key, model))
        self._next = (self._keys.index(chosen) + 1) % len(self._keys)
        return chosen

    async def generate(self, *, model: str, system: str, prompt: str, json_output: bool = True,
                       deadline: float | None = None, max_wait: float | None = None) -> GeminiReply:
        """One completion.

        `deadline` is a `clock()` value this call must not wait past; `max_wait`
        caps any single wait for a rate-limited or failing model.
        """
        if not self._keys:
            raise GeminiUnavailable("no Gemini API key is configured")
        body: dict = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": self.max_output_tokens},
        }
        if json_output:
            body["generationConfig"]["responseMimeType"] = "application/json"
        url = f"{self.base_url}/models/{model}:generateContent"
        transient = rate_limited = 0
        last_problem = ""
        while True:
            key = self._choose(model)
            wait = self._ready_at(key, model) - self.clock()
            if wait > 0:
                if max_wait is not None and wait > max_wait:
                    raise GeminiUnavailable(f"{model} is unavailable for another {wait:.0f}s: {last_problem}")
                if deadline is not None and self.clock() + wait > deadline:
                    raise GeminiUnavailable(f"{model} is unavailable for another {wait:.0f}s, "
                                            f"past this run's deadline: {last_problem}")
                await self.sleep(wait)
            try:
                response = await self.client.post(url, json=body, headers={"x-goog-api-key": key.secret})
            except httpx.HTTPError as exc:
                transient += 1
                last_problem = self.scrub(f"{type(exc).__name__}: {exc}")
                if transient >= MAX_TRANSIENT_FAILURES:
                    raise GeminiUnavailable(f"Gemini unreachable: {last_problem}") from None
                self._cool(key, model, TRANSIENT_COOLDOWN * transient, every_key=False)
                continue
            if response.status_code == 200:
                return self._reply(response, model, key)
            last_problem = f"{response.status_code} {self.scrub(_error_message(response))}"
            self._record(key, model, "rate_limited" if response.status_code == 429 else "error", {})
            if response.status_code == 429:
                rate_limited += 1
                if rate_limited >= MAX_RATE_LIMITS_PER_CALL:
                    raise GeminiUnavailable(f"{model} rate limited {rate_limited} times in one call: {last_problem}")
                self._cool(key, model, _retry_delay(response), every_key=self.shared_quota)
                continue
            if response.status_code in RETRYABLE_STATUS:
                transient += 1
                if transient >= MAX_TRANSIENT_FAILURES:
                    raise GeminiUnavailable(f"{model} kept failing: {last_problem}")
                self._cool(key, model, TRANSIENT_COOLDOWN * transient, every_key=False)
                continue
            raise GeminiRefused(f"Gemini refused the request for {model}: {last_problem}")

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
