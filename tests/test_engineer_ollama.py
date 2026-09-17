"""The local model client.

The test that matters most here is the dull one: that every sampling parameter
is sent. Ollama fills anything a request omits from the installed Modelfile, so
a model someone repackaged on this machine can change how the engineer
generates without a line of this project changing -- and `venture-coder:14b`
was found doing exactly that, carrying `repeat_penalty 1.15` into thirty-four
attempts nobody had configured.
"""

from __future__ import annotations

import httpx
import pytest

from app.engineer.gemini import GeminiIncomplete, GeminiUnavailable
from app.engineer.ollama import OllamaClient, ollama_models

ANSWER = {"message": {"content": '{"files": []}'}, "prompt_eval_count": 12, "eval_count": 34}


def client_for(handler, **overrides) -> OllamaClient:
    transport = httpx.MockTransport(handler)
    return OllamaClient(client=httpx.AsyncClient(transport=transport),
                        sleep=_no_sleep, **overrides)


async def _no_sleep(_seconds: float) -> None:
    return None


def capturing(bodies: list, payload=ANSWER, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        bodies.append(json.loads(request.content))
        return httpx.Response(status, json=payload)

    return handler


# ---- sampling parameters --------------------------------------------------

@pytest.mark.anyio
async def test_every_sampling_parameter_is_sent_not_inherited():
    bodies: list = []
    client = client_for(capturing(bodies))

    await client.generate(model="venture-coder:14b", system="rules", prompt="build it")

    assert bodies[0]["options"] == {"num_ctx": 16_384, "temperature": 0.2,
                                    "repeat_penalty": 1.0, "top_p": 0.9}


@pytest.mark.anyio
async def test_the_default_repetition_penalty_is_none_at_all():
    """A repetition penalty discounts tokens already in the context, and code
    repeats tokens constantly: `end`, `local`, `--`, a tab. 1.0 is no penalty."""
    assert OllamaClient().repeat_penalty == 1.0


@pytest.mark.anyio
async def test_the_configured_values_are_the_ones_sent():
    bodies: list = []
    client = client_for(capturing(bodies), repeat_penalty=1.15, top_p=0.95,
                        temperature=0.1, num_ctx=8192)

    await client.generate(model="m", system="s", prompt="p")

    assert bodies[0]["options"] == {"num_ctx": 8192, "temperature": 0.1,
                                    "repeat_penalty": 1.15, "top_p": 0.95}


# ---- the request itself ---------------------------------------------------

@pytest.mark.anyio
async def test_the_system_prompt_is_sent_as_its_own_message():
    """It overrides whatever SYSTEM the installed Modelfile carries, which is
    the point: the rules the gate enforces are the ones the model must see."""
    bodies: list = []
    client = client_for(capturing(bodies))

    await client.generate(model="m", system="THE RULES", prompt="THE TASK")

    assert bodies[0]["messages"] == [{"role": "system", "content": "THE RULES"},
                                     {"role": "user", "content": "THE TASK"}]
    assert bodies[0]["stream"] is False
    assert bodies[0]["format"] == "json"


@pytest.mark.anyio
async def test_usage_is_reported_from_the_reply():
    seen: list = []
    client = client_for(capturing([]), on_usage=lambda *args: seen.append(args))

    reply = await client.generate(model="m", system="s", prompt="p")

    assert reply.usage == {"prompt_tokens": 12, "output_tokens": 34}
    assert seen[0][0] == "local" and seen[0][2] == "ok"


# ---- failures the loop has to tell apart ----------------------------------

@pytest.mark.anyio
async def test_a_model_that_is_not_pulled_fails_immediately_with_the_command():
    """Retrying cannot make a missing model appear, and the operator needs the
    one command that fixes it."""
    calls: list = []
    client = client_for(capturing(calls, payload={"error": "model not found"}, status=404))

    with pytest.raises(GeminiUnavailable, match="ollama pull nope:14b"):
        await client.generate(model="nope:14b", system="s", prompt="p")
    assert len(calls) == 1, "a 404 must not be retried"


@pytest.mark.anyio
async def test_hitting_the_context_limit_is_incomplete_not_unavailable():
    """The loop answers them differently: incomplete asks for a shorter answer,
    unavailable ends the run."""
    client = client_for(capturing([], payload={**ANSWER, "done_reason": "length"}))

    with pytest.raises(GeminiIncomplete, match="truncated"):
        await client.generate(model="m", system="s", prompt="p")


@pytest.mark.anyio
async def test_an_empty_answer_is_retried_then_reported():
    calls: list = []
    client = client_for(capturing(calls, payload={"message": {"content": "   "}}))

    with pytest.raises(GeminiUnavailable, match="empty message"):
        await client.generate(model="m", system="s", prompt="p")
    assert len(calls) == 2, "an empty answer is worth one retry"


@pytest.mark.anyio
async def test_the_deadline_is_checked_before_calling():
    client = client_for(capturing([]), clock=lambda: 100.0)

    with pytest.raises(GeminiUnavailable, match="deadline"):
        await client.generate(model="m", system="s", prompt="p", deadline=50.0)


# ---- falling back through the model list ----------------------------------

@pytest.mark.anyio
async def test_a_missing_model_falls_through_to_the_next():
    asked: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        model = json.loads(request.content)["model"]
        asked.append(model)
        if model == "venture-coder:14b":
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json=ANSWER)

    client = client_for(handler)
    call = ollama_models(client, ["venture-coder:14b", "qwen2.5-coder:14b"])

    text, model = await call("s", "p", 1e9)

    assert model.startswith("qwen2.5-coder:14b (local)")
    assert text == '{"files": []}'
    assert asked == ["venture-coder:14b", "qwen2.5-coder:14b"]


@pytest.mark.anyio
async def test_every_model_failing_names_all_of_them():
    client = client_for(capturing([], payload={"error": "no"}, status=404))
    call = ollama_models(client, ["a:14b", "b:14b"])

    with pytest.raises(GeminiUnavailable) as failure:
        await call("s", "p", 1e9)
    assert "a:14b" in str(failure.value) and "b:14b" in str(failure.value)


def test_an_empty_model_list_is_refused_when_it_is_built():
    with pytest.raises(ValueError, match="at least one"):
        ollama_models(OllamaClient(), [])
