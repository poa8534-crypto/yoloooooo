"""The Scout can run on Gemini without changing what it will accept.

Swapping the backend must change the wire format and nothing else. The retry,
the fallback, the schema validation and the refusal to accept an unvalidated
answer are what make an audit trustworthy, so they stay identical for either
provider and are asserted here against the Gemini path specifically.

The other thing these pin is the key. It travels in a header, never in a URL,
so it cannot reach a proxy log or the request line of an exception.
"""

from __future__ import annotations

import json as _json
from types import SimpleNamespace

import httpx
import pytest

from app.llm import LLMUnavailable, OllamaProposalClient
from app.schemas import ProposalPayload
from tests.test_deep_research import VALID

FACT_IDS = ["f1"]
EVIDENCE = [{"id": "f1", "text": "Evidence Garden has players.", "template_id": "roblox_name"}]


def settings(**overrides) -> SimpleNamespace:
    base = dict(
        ollama_base_url="http://127.0.0.1:11434",
        ollama_primary_model="local-primary",
        ollama_fallback_model="local-fallback",
        ollama_context=1024,
        ollama_timeout_seconds=5.0,
        ollama_think=False,
        scout_deliberation_passes=1,
        llm_provider="gemini",
        gemini_base_url="https://example.invalid/v1beta/openai",
        gemini_api_key_1="SYNTHETIC_KEY_NOT_REAL_000",
        gemini_primary_model="gemini-primary",
        gemini_fallback_model="gemini-fallback",
        gemini_timeout_seconds=5.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def client(handler, **overrides) -> OllamaProposalClient:
    return OllamaProposalClient(
        settings=settings(**overrides),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def gemini_reply(payload: dict, reasoning: str | None = None) -> httpx.Response:
    message = {"content": _json.dumps(payload)}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return httpx.Response(200, json={"choices": [{"message": message}]})


async def generate(agent):
    return await agent.generate(
        agent="Venture Scout", niche="cozy farming", sourced_name="Evidence Garden",
        fact_ids=FACT_IDS, evidence=EVIDENCE, require_citations=False,
    )


@pytest.mark.asyncio
async def test_a_gemini_answer_is_accepted_like_any_other():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return gemini_reply(VALID)

    result = await generate(client(handler))

    assert isinstance(result.payload, ProposalPayload)
    assert result.model == "gemini-primary"
    assert str(seen[0].url).endswith("/chat/completions")


@pytest.mark.asyncio
async def test_the_key_travels_in_a_header_and_never_in_the_url():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return gemini_reply(VALID)

    await generate(client(handler))

    request = seen[0]
    assert "SYNTHETIC_KEY_NOT_REAL_000" not in str(request.url)
    assert "SYNTHETIC_KEY_NOT_REAL_000" not in request.url.query.decode()
    assert request.headers["authorization"] == "Bearer SYNTHETIC_KEY_NOT_REAL_000"


@pytest.mark.asyncio
async def test_the_schema_is_sent_in_the_shape_google_accepts():
    """Ollama takes `format`; this surface takes `response_format`. Sending the
    wrong one means an unconstrained answer that happens to parse."""
    seen: list[dict] = []

    def handler(request):
        seen.append(_json.loads(request.content))
        return gemini_reply(VALID)

    await generate(client(handler))

    body = seen[0]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["type"] == "object"
    assert "format" not in body, "the Ollama field leaked into the Gemini request"
    assert "think" not in body, "Ollama's reasoning flag is not a Google field"


@pytest.mark.asyncio
async def test_a_refused_answer_still_retries_and_falls_back():
    """The part that decides whether an answer is trustworthy must not change
    with the provider."""
    models: list[str] = []

    def handler(request):
        models.append(_json.loads(request.content)["model"])
        return gemini_reply({"concept_title": "no other required fields"})

    with pytest.raises(LLMUnavailable):
        await generate(client(handler))

    expected = ["gemini-primary"] * 2 + ["gemini-fallback"] * 2 + ["local-primary"] * 2
    assert models == expected, "the chain must end at the local model, not at a failure"


@pytest.mark.asyncio
async def test_an_unreachable_google_fails_closed():
    def handler(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(LLMUnavailable):
        await generate(client(handler))


@pytest.mark.asyncio
async def test_a_missing_key_says_so_once_rather_than_four_times():
    """A misconfiguration is not a model refusal. Retrying the fallback would
    fail identically and bury the cause under copies of itself."""
    def handler(request):
        raise AssertionError("a request was sent with no key configured")

    with pytest.raises(LLMUnavailable, match="no key is configured"):
        await generate(client(handler, gemini_api_key_1=""))


@pytest.mark.asyncio
async def test_googles_reasoning_is_reported_but_never_returned():
    """Same rule as Ollama's `thinking`: shown so a reader can follow the work,
    and never mistaken for evidence."""
    events: list[tuple[str, str]] = []

    def handler(request):
        return gemini_reply(VALID, reasoning="I considered the evidence.")

    agent = client(handler)
    await agent.generate(
        agent="Venture Scout", niche="cozy farming", sourced_name="Evidence Garden",
        fact_ids=FACT_IDS, evidence=EVIDENCE, require_citations=False,
        on_event=lambda stage, detail, **extra: events.append((stage, detail)),
    )

    reasoning = [detail for stage, detail in events if stage == "reasoning"]
    assert reasoning == ["I considered the evidence."]


@pytest.mark.asyncio
async def test_the_local_provider_is_untouched_by_any_of_this():
    """The default stays local, and it still speaks Ollama's wire format."""
    seen: list[dict] = []

    def handler(request):
        seen.append(_json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": _json.dumps(VALID)}})

    result = await generate(client(handler, llm_provider="ollama"))

    assert result.model == "local-primary"
    assert "format" in seen[0] and "response_format" not in seen[0]
