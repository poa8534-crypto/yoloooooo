"""The engineer, run on the model already on this machine.

Gemini's free tier is 20 requests for Flash and zero for Pro. Two runs of the
DataService task blocked on attempt 1 without the model writing a single file,
while `qwen2.5-coder:14b` sat idle on the GPU with no quota and no rate limit.
This is the same loop pointed at that.

A weaker model is safe here in a way it would not be elsewhere, because the
engineer does not trust its output: five checks read every file, and anything
that fails comes back as exact errors for another attempt. What a cheaper model
costs is attempts, not correctness.

The interface is deliberately the same shape as `gemini.py` -- the loop asks for
a `ModelCall` and neither knows nor cares which answered.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from .gemini import GeminiIncomplete, GeminiRefused, GeminiUnavailable, UsageHook

# A local model has no quota to exhaust, so a failure here means the daemon is
# down or the model is missing. Neither is fixed by hammering.
ATTEMPTS_PER_MODEL = 2
RETRY_PAUSE_SECONDS = 2.0


@dataclass(frozen=True)
class OllamaReply:
    text: str
    model: str
    key_label: str
    usage: dict[str, int]


@dataclass
class OllamaClient:
    """Ollama's native chat API, shaped like `GeminiClient` for the loop.

    The exceptions are Gemini's on purpose. The loop already distinguishes
    unavailable from refused from incomplete, and inventing a parallel set
    would mean teaching it a second vocabulary for the same three facts.
    """

    base_url: str = "http://127.0.0.1:11434"
    timeout: float = 900.0
    num_ctx: int = 16_384
    temperature: float = 0.2
    client: httpx.AsyncClient | None = None
    on_usage: UsageHook | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    _owns_client: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self._owns_client = self.client is None
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=15.0))

    async def close(self) -> None:
        if self._owns_client and self.client is not None:
            await self.client.aclose()

    def scrub(self, text: str) -> str:
        """No credential to remove: the model is on this machine."""
        return text

    async def generate(self, *, model: str, system: str, prompt: str, json_output: bool = True,
                       deadline: float | None = None, max_wait: float | None = None) -> OllamaReply:
        """One completion. `max_wait` is accepted for parity and ignored.

        There is nothing to wait out: a local model is either serving or it is
        not, so a caller that says "do not wait" and one that says "wait as
        long as you like" get the same answer.
        """
        body = {
            "model": model,
            "stream": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "options": {"num_ctx": self.num_ctx, "temperature": self.temperature},
        }
        if json_output:
            # Ollama's JSON mode. The loop parses the answer itself and rejects
            # anything that is not the shape it asked for, so this is a nudge
            # rather than the guarantee.
            body["format"] = "json"

        problems: list[str] = []
        for attempt in range(ATTEMPTS_PER_MODEL):
            if deadline is not None and self.clock() >= deadline:
                raise GeminiUnavailable(f"{model}: the run's deadline passed before it answered")
            try:
                response = await self.client.post(f"{self.base_url}/api/chat", json=body)
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                detail = exc.response.text[:200].replace("\n", " ")
                if status == 404:
                    # Ollama says 404 for a model it has not pulled. Retrying
                    # cannot make it appear, so this fails at once and names it.
                    raise GeminiUnavailable(
                        f"{model}: not installed. Pull it with `ollama pull {model}`"
                    ) from None
                problems.append(f"HTTP {status}: {detail}")
            except (httpx.HTTPError, ValueError) as exc:
                problems.append(f"{type(exc).__name__}: {str(exc)[:200]}")
            else:
                text = (payload.get("message") or {}).get("content") or ""
                if not text.strip():
                    problems.append("the model returned an empty message")
                else:
                    usage = {
                        "prompt_tokens": int(payload.get("prompt_eval_count") or 0),
                        "output_tokens": int(payload.get("eval_count") or 0),
                    }
                    if self.on_usage:
                        self.on_usage("local", model, "ok", usage)
                    if payload.get("done_reason") == "length":
                        raise GeminiIncomplete(
                            f"{model} stopped at the context limit; the answer is truncated"
                        )
                    return OllamaReply(text=text, model=model, key_label="local", usage=usage)
            if attempt + 1 < ATTEMPTS_PER_MODEL:
                await self.sleep(RETRY_PAUSE_SECONDS)

        raise GeminiUnavailable(
            f"{model} did not answer after {ATTEMPTS_PER_MODEL} attempts "
            f"({'; '.join(problems[-2:])}). Is `ollama serve` running at {self.base_url}?"
        )


def ollama_models(client: OllamaClient, models: list[str]):
    """Adapt `OllamaClient` to the loop, trying `models` in order.

    Same contract as `gemini_models`: every model but the last gives up
    immediately so the attempt moves on, and the last one may use what remains
    of the run's deadline.
    """
    if not models:
        raise ValueError("at least one Ollama model is required")

    async def call(system: str, prompt: str, deadline: float) -> tuple[str, str]:
        passed_over: list[str] = []
        for index, model in enumerate(models):
            last = index == len(models) - 1
            try:
                reply = await client.generate(model=model, system=system, prompt=prompt,
                                              deadline=deadline, max_wait=None if last else 0.0)
            except GeminiUnavailable as exc:
                if last:
                    raise GeminiUnavailable("; ".join([*passed_over, str(exc)])) from None
                passed_over.append(str(exc))
                continue
            label = f"{reply.model} (local)"
            if passed_over:
                label += f" after {len(passed_over)} unavailable model(s)"
            return reply.text, label
        raise AssertionError("unreachable")

    return call


__all__ = ["OllamaClient", "OllamaReply", "ollama_models",
           "GeminiIncomplete", "GeminiRefused", "GeminiUnavailable"]
