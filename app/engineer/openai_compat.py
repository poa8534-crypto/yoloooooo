"""Any provider that speaks the OpenAI chat-completions API, for the Engineer.

GLM (Zhipu / Z.ai) and DeepSeek both serve `POST {base_url}/chat/completions`
with a bearer key, so one client covers both, and a third provider of the same
kind is a base URL, a key and a model name in `.env` rather than new code.

It answers in the loop's own vocabulary, the same three exceptions Gemini and
Ollama raise, because the chain acts on them: only "unavailable" moves on to
the next provider. So an exhausted quota (429), a key that is wrong or
suspended (401/403), a model name the provider does not know (404) and a
server that is down all read as unavailable -- each is a reason to ask someone
else, not an answer. A request the provider rejected for its content (400,
or `finish_reason` "content_filter") is a refusal, and a reply cut short at
the token limit is incomplete.

The key is sent in the Authorization header and removed from every message
this module raises. It is never logged.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from .gemini import GeminiIncomplete, GeminiRefused, GeminiUnavailable, UsageHook

RETRYABLE_STATUS = {500, 502, 503, 504}
# A 400 whose complaint is the size of the prompt, not its content. The next
# model may have a larger window, so this is a reason to move on rather than a
# verdict on the request: measured when Dahl's MiniMax refused a 171,809-token
# prompt that Antigravity had been reading all run.
TOO_LONG = re.compile(r"context length|context_length|too many tokens|maximum context|"
                      r"reduce the length|input is too long|prompt is too long", re.IGNORECASE)
UNAVAILABLE_STATUS = {401, 402, 403, 404, 408, 429}
ATTEMPTS = 2
RETRY_PAUSE_SECONDS = 3.0
# A key as providers echo it back in errors: masking stars, then its tail.
MASKED_FRAGMENT = re.compile(r"\*{2,}[A-Za-z0-9_\-.]{2,}")


@dataclass(frozen=True)
class CompatReply:
    text: str
    model: str
    key_label: str
    usage: dict[str, int]


@dataclass
class OpenAICompatClient:
    provider: str
    base_url: str
    api_key: str
    timeout: float = 900.0
    max_output_tokens: int = 16_384
    temperature: float = 0.2
    client: httpx.AsyncClient | None = None
    on_usage: UsageHook | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    _owns_client: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self._owns_client = self.client is None
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=15.0))

    async def close(self) -> None:
        if self._owns_client and self.client is not None:
            await self.client.aclose()

    def scrub(self, text: str) -> str:
        """The key never leaves this module, not even in part.

        Removing the whole key is not enough: DeepSeek's 401 names the key it
        refused by its last four characters ("Your api key: ****ABCD"), and
        that fragment reached a transcript before this caught it. So any
        masked-key fragment a provider echoes is removed, and so is every
        run of four or more characters taken from either end of the key.
        """
        if not text:
            return text
        cleaned = MASKED_FRAGMENT.sub("[key]", text)
        key = self.api_key or ""
        if key:
            cleaned = cleaned.replace(key, "[key]")
            for size in range(len(key) - 1, 3, -1):
                for piece in (key[:size], key[-size:]):
                    if piece in cleaned:
                        cleaned = cleaned.replace(piece, "[key]")
        return cleaned

    def _usage(self, model: str, status: str, usage: dict[str, int]) -> None:
        if self.on_usage:
            self.on_usage(self.provider, model, status, usage)

    async def generate(self, *, model: str, system: str, prompt: str, json_output: bool = True,
                       deadline: float | None = None, max_wait: float | None = None) -> CompatReply:
        """One completion. `max_wait` is accepted for parity: a 429 here is
        handed straight to the chain rather than waited out, because the next
        provider is the better use of the time."""
        body: dict = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "stream": False,
        }
        if json_output:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        problems: list[str] = []
        for attempt in range(ATTEMPTS):
            if deadline is not None and self.clock() >= deadline:
                raise GeminiUnavailable(f"{self.provider}/{model}: the run's deadline passed before it answered")
            try:
                response = await self.client.post(f"{self.base_url}/chat/completions",
                                                  json=body, headers=headers)
            except httpx.HTTPError as exc:
                problems.append(self.scrub(f"{type(exc).__name__}: {str(exc)[:200]}"))
            else:
                status = response.status_code
                detail = self.scrub(response.text[:300].replace("\n", " "))
                if status in UNAVAILABLE_STATUS:
                    self._usage(model, f"http_{status}", {})
                    raise GeminiUnavailable(f"{self.provider}/{model}: HTTP {status}: {detail}")
                if status == 400 or status == 422:
                    if TOO_LONG.search(detail):
                        self._usage(model, f"http_{status}_too_long", {})
                        raise GeminiUnavailable(
                            f"{self.provider}/{model}: the prompt does not fit this model: {detail}")
                    raise GeminiRefused(f"{self.provider}/{model}: HTTP {status}: {detail}")
                if status in RETRYABLE_STATUS:
                    problems.append(f"HTTP {status}: {detail}")
                elif status != 200:
                    raise GeminiUnavailable(f"{self.provider}/{model}: HTTP {status}: {detail}")
                else:
                    return self._reply(model, response)
            if attempt + 1 < ATTEMPTS:
                await self.sleep(RETRY_PAUSE_SECONDS)
        raise GeminiUnavailable(f"{self.provider}/{model} did not answer: {'; '.join(problems[-2:])}")

    def _reply(self, model: str, response: httpx.Response) -> CompatReply:
        try:
            payload = response.json()
            choice = payload["choices"][0]
        except (ValueError, KeyError, IndexError, TypeError):
            raise GeminiUnavailable(
                f"{self.provider}/{model}: an answer that is not a chat completion: "
                f"{self.scrub(response.text[:200])}") from None
        message = choice.get("message") or {}
        text = message.get("content") or ""
        finish = choice.get("finish_reason")
        raw = payload.get("usage") or {}
        usage = {"prompt_tokens": int(raw.get("prompt_tokens") or 0),
                 "output_tokens": int(raw.get("completion_tokens") or 0)}
        self._usage(model, "ok", usage)
        if finish == "content_filter":
            raise GeminiRefused(f"{self.provider}/{model}: the provider's content filter stopped the answer")
        if finish == "length":
            raise GeminiIncomplete(f"{self.provider}/{model} stopped at the token limit; the answer is truncated")
        if not isinstance(text, str) or not text.strip():
            raise GeminiUnavailable(f"{self.provider}/{model}: an empty answer")
        return CompatReply(text=text, model=model, key_label=self.provider, usage=usage)


def compat_models(client: OpenAICompatClient, models: list[str]):
    """Adapt the client to the loop, trying `models` in order: an unavailable
    model hands the attempt to the next, and the last one's failure goes up to
    the chain."""
    if not models:
        raise ValueError(f"at least one {client.provider} model is required")

    async def call(system: str, prompt: str, deadline: float) -> tuple[str, str]:
        passed_over: list[str] = []
        for index, model in enumerate(models):
            try:
                reply = await client.generate(model=model, system=system, prompt=prompt, deadline=deadline)
            except GeminiUnavailable as exc:
                if index == len(models) - 1:
                    raise GeminiUnavailable("; ".join([*passed_over, str(exc)])) from None
                passed_over.append(str(exc))
                continue
            label = f"{reply.model} ({client.provider})"
            if passed_over:
                label += f" after {len(passed_over)} unavailable model(s)"
            return reply.text, label
        raise AssertionError("unreachable")

    return call


__all__ = ["OpenAICompatClient", "CompatReply", "compat_models"]
