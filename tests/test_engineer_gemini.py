"""Gemini across two keys: spend both, wait only when both are limited, never leak either."""

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

KEY1, KEY2 = "SYNTHETIC_GEMINI_KEY_ONE_111", "SYNTHETIC_GEMINI_KEY_TWO_222"


def ok(text='{"ok": true}', *, thought=None, finish="STOP"):
    parts = ([{"text": thought, "thought": True}] if thought else []) + [{"text": text}]
    return httpx.Response(200, json={
        "candidates": [{"content": {"parts": parts}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "thoughtsTokenCount": 7, "totalTokenCount": 22},
    })


def rate_limited(seconds="12s"):
    return httpx.Response(429, json={"error": {"code": 429, "message": "Resource exhausted", "details": [
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


def make(handler, clock=None, usage=None):
    clock = clock or Clock()
    seen: list[httpx.Request] = []

    def record(request):
        seen.append(request)
        return handler(request, len(seen))

    client = GeminiClient(keys=[KEY1, KEY2], client=httpx.AsyncClient(transport=httpx.MockTransport(record)),
                          clock=clock, sleep=clock.sleep, on_usage=usage)
    return client, seen, clock


def key_of(request):
    return {KEY1: "key1", KEY2: "key2"}[request.headers["x-goog-api-key"]]


async def test_answer_excludes_thoughts_and_key_stays_out_of_the_url():
    usage = []
    client, seen, _ = make(lambda request, n: ok(thought="let me think"),
                           usage=lambda *args: usage.append(args))
    reply = await client.generate(model="gemini-3.1-pro-preview", system="sys", prompt="hi")

    assert reply.text == '{"ok": true}'
    assert (reply.key_label, reply.usage["thought_tokens"]) == ("key1", 7)
    request = seen[0]
    assert KEY1 not in str(request.url) and request.url.path.endswith("/models/gemini-3.1-pro-preview:generateContent")
    body = json.loads(request.content)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert usage == [("key1", "gemini-3.1-pro-preview", "ok", reply.usage)]


async def test_calls_alternate_between_both_keys():
    client, seen, _ = make(lambda request, n: ok())
    for _ in range(4):
        await client.generate(model="m", system="s", prompt="p")
    assert [key_of(r) for r in seen] == ["key1", "key2", "key1", "key2"]


async def test_a_rate_limited_key_hands_over_without_waiting():
    client, seen, clock = make(lambda request, n: rate_limited() if key_of(request) == "key1" else ok())
    reply = await client.generate(model="m", system="s", prompt="p")
    assert reply.key_label == "key2" and clock.slept == []
    # key1 is still cooling, so the next call goes straight to key2 again.
    await client.generate(model="m", system="s", prompt="p")
    assert [key_of(r) for r in seen] == ["key1", "key2", "key2"]


async def test_both_keys_limited_waits_for_the_sooner_one():
    def handler(request, n):
        if n == 1:
            return rate_limited("40s")
        if n == 2:
            return rate_limited("15s")
        return ok()
    client, _, clock = make(handler)
    reply = await client.generate(model="m", system="s", prompt="p")
    assert reply.key_label == "key2" and clock.slept == [15.0]


async def test_waiting_past_the_deadline_is_refused_as_planned():
    client, _, clock = make(lambda request, n: rate_limited("300s"))
    with pytest.raises(GeminiUnavailable) as caught:
        await client.generate(model="m", system="s", prompt="p", deadline=clock.now + 60)
    assert caught.value.planned is True


async def test_a_refused_request_is_not_retried_and_never_echoes_a_key():
    def handler(request, n):
        return httpx.Response(400, json={"error": {"message": f"API key not valid: {KEY1} / {KEY2}"}})
    client, seen, _ = make(handler)
    with pytest.raises(GeminiRefused) as caught:
        await client.generate(model="m", system="s", prompt="p")
    assert len(seen) == 1
    assert KEY1 not in str(caught.value) and KEY2 not in str(caught.value)


async def test_server_errors_move_to_the_other_key():
    client, seen, _ = make(lambda request, n: httpx.Response(503, json={"error": {"message": "overloaded"}}) if n == 1 else ok())
    reply = await client.generate(model="m", system="s", prompt="p")
    assert reply.key_label == "key2" and len(seen) == 2


async def test_a_truncated_answer_is_incomplete_not_accepted():
    client, _, _ = make(lambda request, n: ok('{"files": [', finish="MAX_TOKENS"))
    with pytest.raises(GeminiIncomplete):
        await client.generate(model="m", system="s", prompt="p")


async def test_a_blocked_prompt_is_refused():
    client, _, _ = make(lambda request, n: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(GeminiRefused, match="SAFETY"):
        await client.generate(model="m", system="s", prompt="p")


async def test_no_keys_is_unavailable():
    client = GeminiClient(keys=["", ""], client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: ok())))
    with pytest.raises(GeminiUnavailable, match="no Gemini API key"):
        await client.generate(model="m", system="s", prompt="p")
