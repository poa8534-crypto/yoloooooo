"""Gemini for the engineer: shared quotas, per-model limits, Pro falling to Flash, no leaked keys."""

from __future__ import annotations

import json

import httpx
import pytest

from app.engineer.gemini import (
    GeminiClient,
    GeminiIncomplete,
    GeminiRefused,
    GeminiUnavailable,
)
from app.engineer.loop import gemini_models
from app.engineer.runs import engineer_keys, engineer_models

KEY1, KEY2 = "SYNTHETIC_GEMINI_KEY_ONE_111", "SYNTHETIC_GEMINI_KEY_TWO_222"


def ok(text='{"ok": true}', *, thought=None, finish="STOP"):
    parts = ([{"text": thought, "thought": True}] if thought else []) + [{"text": text}]
    return httpx.Response(200, json={
        "candidates": [{"content": {"parts": parts}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "thoughtsTokenCount": 7, "totalTokenCount": 22},
    })


def rate_limited(seconds="12s"):
    return httpx.Response(429, json={"error": {"code": 429, "message": "RESOURCE_EXHAUSTED", "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": seconds}]}})


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def make(handler, *, keys=(KEY1, KEY2), shared_quota=True, usage=None):
    clock = Clock()
    seen: list[httpx.Request] = []

    def record(request):
        seen.append(request)
        return handler(request, len(seen))

    client = GeminiClient(keys=list(keys), shared_quota=shared_quota, clock=clock, sleep=clock.sleep,
                          client=httpx.AsyncClient(transport=httpx.MockTransport(record)), on_usage=usage)
    return client, seen, clock


def key_of(request):
    return {KEY1: "key1", KEY2: "key2"}[request.headers["x-goog-api-key"]]


def model_of(request):
    return request.url.path.rsplit("/", 1)[-1].split(":")[0]


async def test_answer_excludes_thoughts_and_key_stays_out_of_the_url():
    usage = []
    client, seen, _ = make(lambda request, n: ok(thought="let me think"), usage=lambda *args: usage.append(args))
    reply = await client.generate(model="gemini-3.1-pro-preview", system="sys", prompt="hi")

    assert reply.text == '{"ok": true}'
    assert (reply.key_label, reply.usage["thought_tokens"]) == ("key1", 7)
    request = seen[0]
    assert KEY1 not in str(request.url)
    assert request.url.path.endswith("/v1beta/models/gemini-3.1-pro-preview:generateContent")
    body = json.loads(request.content)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert usage == [("key1", "gemini-3.1-pro-preview", "ok", reply.usage)]


async def test_calls_alternate_between_configured_keys():
    client, seen, _ = make(lambda request, n: ok())
    for _ in range(4):
        await client.generate(model="m", system="s", prompt="p")
    assert [key_of(r) for r in seen] == ["key1", "key2", "key1", "key2"]


async def test_shared_quota_a_429_is_waited_out_not_retried_on_the_other_key():
    """Measured: both keys draw on one quota, so the other key would only 429 too."""
    client, seen, clock = make(lambda request, n: rate_limited("12s") if n == 1 else ok())
    reply = await client.generate(model="m", system="s", prompt="p")
    assert clock.slept == [12.0] and len(seen) == 2 and reply.text


async def test_separate_quotas_hand_a_429_to_the_other_key_without_waiting():
    client, _, clock = make(lambda request, n: rate_limited() if key_of(request) == "key1" else ok(),
                               shared_quota=False)
    reply = await client.generate(model="m", system="s", prompt="p")
    assert reply.key_label == "key2" and clock.slept == []


async def test_limits_are_per_model():
    """Pro being out of quota must not make Flash wait."""
    client, _, clock = make(lambda request, n: rate_limited("600s") if model_of(request) == "pro" else ok())
    with pytest.raises(GeminiUnavailable):
        await client.generate(model="pro", system="s", prompt="p", max_wait=0.0)
    reply = await client.generate(model="flash", system="s", prompt="p")
    assert reply.model == "flash" and clock.slept == []


async def test_max_wait_zero_gives_up_at_once_instead_of_sleeping():
    client, seen, clock = make(lambda request, n: rate_limited("45s"))
    with pytest.raises(GeminiUnavailable, match="45s") as caught:
        await client.generate(model="m", system="s", prompt="p", max_wait=0.0)
    assert clock.slept == [] and len(seen) == 1 and caught.value.planned is True


async def test_waiting_past_the_deadline_is_refused_as_planned():
    client, _, clock = make(lambda request, n: rate_limited("300s"))
    with pytest.raises(GeminiUnavailable, match="deadline"):
        await client.generate(model="m", system="s", prompt="p", deadline=clock.now + 60)


async def test_a_refused_request_is_not_retried_and_never_echoes_a_key():
    def handler(request, n):
        return httpx.Response(400, json={"error": {"message": f"API key not valid: {KEY1} / {KEY2}"}})
    client, seen, _ = make(handler)
    with pytest.raises(GeminiRefused) as caught:
        await client.generate(model="m", system="s", prompt="p")
    assert len(seen) == 1
    assert KEY1 not in str(caught.value) and KEY2 not in str(caught.value)


async def test_server_errors_move_to_the_other_key():
    client, seen, _ = make(lambda request, n: httpx.Response(503, json={"error": {"message": "high demand"}}) if n == 1 else ok())
    reply = await client.generate(model="m", system="s", prompt="p")
    assert reply.key_label == "key2" and len(seen) == 2


async def test_a_model_that_keeps_failing_becomes_unavailable():
    client, seen, _ = make(lambda request, n: httpx.Response(503, json={"error": {"message": "high demand"}}),
                           keys=(KEY1,))
    with pytest.raises(GeminiUnavailable, match="high demand"):
        await client.generate(model="m", system="s", prompt="p")
    assert len(seen) == 6


async def test_a_truncated_answer_is_incomplete_not_accepted():
    client, _, _ = make(lambda request, n: ok('{"files": [', finish="MAX_TOKENS"))
    with pytest.raises(GeminiIncomplete):
        await client.generate(model="m", system="s", prompt="p")


async def test_a_blocked_prompt_is_refused():
    client, _, _ = make(lambda request, n: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(GeminiRefused, match="SAFETY"):
        await client.generate(model="m", system="s", prompt="p")


async def test_no_keys_is_unavailable():
    client, _, _ = make(lambda request, n: ok(), keys=("", ""))
    with pytest.raises(GeminiUnavailable, match="no Gemini API key"):
        await client.generate(model="m", system="s", prompt="p")


# ---- the engineer's model chain ------------------------------------------

async def test_pro_out_of_quota_hands_the_attempt_to_flash_at_once():
    client, seen, clock = make(lambda request, n: rate_limited("3600s") if model_of(request) == "pro" else ok())
    call = gemini_models(client, ["pro", "flash"])
    text, label = await call("s", "p", clock.now + 5000)
    assert text and label.startswith("flash (") and "after 1 unavailable model" in label
    assert clock.slept == [] and [model_of(r) for r in seen] == ["pro", "flash"]


async def test_each_attempt_tries_pro_again_first():
    answers = iter([rate_limited("5s"), ok(), ok()])
    client, seen, clock = make(lambda request, n: next(answers), keys=(KEY1,))
    call = gemini_models(client, ["pro", "flash"])
    await call("s", "p", clock.now + 5000)
    clock.now += 10  # Pro's cooldown has passed by the next attempt
    _, label = await call("s", "p", clock.now + 5000)
    assert label.startswith("pro (") and [model_of(r) for r in seen] == ["pro", "flash", "pro"]


async def test_every_model_unavailable_reports_all_of_them():
    client, _, clock = make(lambda request, n: rate_limited("9999s"))
    call = gemini_models(client, ["pro", "flash"])
    with pytest.raises(GeminiUnavailable) as caught:
        await call("s", "p", clock.now + 60)
    assert "pro is unavailable" in str(caught.value) and "flash is unavailable" in str(caught.value)


async def test_a_refused_model_is_not_papered_over_by_the_next():
    client, _, clock = make(lambda request, n: httpx.Response(404, json={"error": {"message": "model not found"}}))
    with pytest.raises(GeminiRefused, match="model not found"):
        await gemini_models(client, ["pro", "flash"])("s", "p", clock.now + 60)


def test_engineer_uses_key_one_unless_told_to_use_both(settings):
    settings.gemini_api_key_1, settings.gemini_api_key_2 = KEY1, KEY2
    assert engineer_keys(settings) == [KEY1]
    settings.engineer_use_both_gemini_keys = True
    assert engineer_keys(settings) == [KEY1, KEY2]
    settings.engineer_gemini_models = " gemini-3.1-pro-preview , gemini-3.8-flash ,"
    assert engineer_models(settings) == ["gemini-3.1-pro-preview", "gemini-3.8-flash"]


def test_engineer_settings_do_not_shadow_the_scouts(settings):
    """The Scout talks to the OpenAI-compatible surface; the engineer must not redirect it."""
    assert settings.gemini_base_url.endswith("/v1beta/openai")
    assert settings.engineer_gemini_base_url.endswith("/v1beta")
    assert settings.gemini_timeout_seconds == 180.0
