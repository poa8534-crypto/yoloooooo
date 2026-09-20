"""GLM and DeepSeek, as backups behind Antigravity.

Both speak the OpenAI chat-completions API, so one client serves both. What
matters is how each failure is classified, because the chain moves to the
next provider only on "unavailable": an exhausted quota or a wrong key must
hand over, a rejected prompt must not. And the key must never appear in
anything this raises.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.engineer.gemini import GeminiIncomplete, GeminiRefused, GeminiUnavailable
from app.engineer.openai_compat import OpenAICompatClient, compat_models
from app.engineer.runs import NotConfigured, build_clients, provider_names

KEY = "sk-test-secret-0123456789"


def answer(text: str = '{"files": []}', finish: str = "stop") -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7}}


def client_answering(status: int, payload: dict | str, seen: list | None = None) -> OpenAICompatClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        body = payload if isinstance(payload, str) else json.dumps(payload)
        return httpx.Response(status, text=body)

    async def no_sleep(_seconds: float) -> None:
        return None

    return OpenAICompatClient(provider="deepseek", base_url="https://api.example/v1/", api_key=KEY,
                              client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                              sleep=no_sleep)


@pytest.mark.anyio
async def test_an_answer_is_read_from_the_first_choice_with_its_usage():
    seen: list = []
    used: list = []
    client = client_answering(200, answer(), seen)
    client.on_usage = lambda *args: used.append(args)

    reply = await client.generate(model="deepseek-chat", system="rules", prompt="task")

    assert reply.text == '{"files": []}'
    assert reply.usage == {"prompt_tokens": 11, "output_tokens": 7}
    request = seen[0]
    assert str(request.url) == "https://api.example/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    sent = json.loads(request.content)
    assert sent["messages"][0] == {"role": "system", "content": "rules"}
    assert sent["response_format"] == {"type": "json_object"}
    assert used[0][2] == "ok"


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 402, 403, 404, 429])
async def test_a_quota_a_bad_key_or_a_missing_model_is_unavailable(status):
    """Each is a reason to ask the next provider, which only "unavailable" does."""
    with pytest.raises(GeminiUnavailable):
        await client_answering(status, {"error": "no"}).generate(model="m", system="s", prompt="p")


@pytest.mark.anyio
async def test_a_rejected_request_is_a_refusal():
    with pytest.raises(GeminiRefused):
        await client_answering(400, {"error": "bad prompt"}).generate(model="m", system="s", prompt="p")


@pytest.mark.anyio
async def test_a_reply_cut_at_the_token_limit_is_incomplete():
    with pytest.raises(GeminiIncomplete):
        await client_answering(200, answer(finish="length")).generate(model="m", system="s", prompt="p")


@pytest.mark.anyio
async def test_the_key_never_appears_in_an_error():
    """A provider that echoes the Authorization header back in its error body
    would otherwise put the key into build records and logs."""
    with pytest.raises(GeminiUnavailable) as raised:
        await client_answering(401, f"invalid key {KEY}").generate(model="m", system="s", prompt="p")
    assert KEY not in str(raised.value)


@pytest.mark.anyio
async def test_not_even_a_fragment_of_the_key_appears_in_an_error():
    """DeepSeek's 401 echoes the key's last four characters behind stars.
    That fragment reached a transcript once; it must not again."""
    echoed = f'{{"error":{{"message":"Authentication Fails, Your api key: ****{KEY[-4:]} is invalid"}}}}'
    with pytest.raises(GeminiUnavailable) as raised:
        await client_answering(401, echoed).generate(model="m", system="s", prompt="p")
    assert KEY[-4:] not in str(raised.value)
    assert KEY[:6] not in client_answering(200, answer()).scrub(f"prefix {KEY[:6]} here")


@pytest.mark.anyio
async def test_a_server_error_is_retried_then_unavailable():
    calls: list = []
    client = client_answering(503, "down", calls)
    with pytest.raises(GeminiUnavailable):
        await client.generate(model="m", system="s", prompt="p")
    assert len(calls) == 2


@pytest.mark.anyio
async def test_an_unavailable_model_hands_over_to_the_next_listed():
    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        return httpx.Response(404 if model == "glm-wrong" else 200, text=json.dumps(answer("ok")))

    client = OpenAICompatClient(provider="glm", base_url="https://g/v4", api_key=KEY,
                                client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    call = compat_models(client, ["glm-wrong", "glm-right"])
    text, label = await call("s", "p", 1e12)
    assert text == "ok" and "glm-right" in label and "1 unavailable" in label


def settings(**overrides) -> SimpleNamespace:
    base = dict(
        engineer_provider=None,
        engineer_providers="antigravity,glm,deepseek,gemini,ollama",
        glm_api_key="", glm_base_url="https://api.z.ai/api/paas/v4", engineer_glm_models="glm-5.2",
        deepseek_api_key="", deepseek_base_url="https://api.deepseek.com",
        engineer_deepseek_models="deepseek-chat",
        engineer_compat_timeout_seconds=30.0, engineer_compat_max_output_tokens=8192,
        gemini_api_key_1="", gemini_api_key_2="", engineer_use_both_gemini_keys=False,
        engineer_ollama_base_url="http://127.0.0.1:11434", engineer_ollama_timeout_seconds=30.0,
        engineer_ollama_context=4096, engineer_ollama_repeat_penalty=1.0, engineer_ollama_top_p=0.9,
        engineer_antigravity_executable="agy", engineer_antigravity_timeout_seconds=30.0,
        engineer_antigravity_effort="medium",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_glm_and_deepseek_sit_behind_antigravity_in_the_default_chain():
    from app.config import Settings

    names = provider_names(Settings(_env_file=None))
    assert names[:3] == ["antigravity", "glm", "deepseek"]


def test_a_backup_with_no_key_is_skipped_not_fatal():
    built = [name for name, _client in build_clients(settings())]
    assert "glm" not in built and "deepseek" not in built


def test_a_backup_with_a_key_joins_the_chain_with_its_own_address():
    built = dict(build_clients(settings(glm_api_key="k1", deepseek_api_key="k2")))
    assert built["glm"].base_url == "https://api.z.ai/api/paas/v4"
    assert built["deepseek"].base_url == "https://api.deepseek.com"
    assert built["deepseek"].max_output_tokens == 8192


def test_an_unknown_provider_name_is_refused():
    with pytest.raises(NotConfigured):
        provider_names(settings(engineer_providers="antigravity,claude"))
