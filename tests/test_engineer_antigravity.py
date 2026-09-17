"""The Antigravity CLI as a completion backend, and the provider chain.

`agy` is an agent harness: given a prompt it will reason, call tools, edit
files and run commands. This project already owns that loop, so the tests that
matter most are the ones about keeping it inside its box -- it runs in a
scratch directory, and never with `--dangerously-skip-permissions`.

Everything here runs against a fake `agy`. The real one is not installed on
every machine, and a test that quietly skips is a test that was never written.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.engineer.antigravity import (
    WINDOWS_COMMAND_LIMIT,
    AntigravityClient,
    antigravity_models,
    chain,
)
from app.engineer.gemini import GeminiIncomplete, GeminiRefused, GeminiUnavailable

ANSWER = {"status": "success", "response": '{"files": []}', "conversation_id": "c1",
          "usage": {"input_tokens": 100, "output_tokens": 200}}


def fake_agy(calls: list, payload=ANSWER, returncode: int = 0, stderr: str = ""):
    def runner(command, **kwargs):
        calls.append({"command": command, **kwargs})
        out = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.CompletedProcess(command, returncode, out.encode("utf-8"), stderr.encode("utf-8"))

    return runner


def client_for(calls: list, tmp_path: Path, **overrides) -> AntigravityClient:
    client = AntigravityClient(runner=fake_agy(calls, **overrides.pop("fake", {})),
                               scratch_dir=tmp_path / "scratch", **overrides)
    client.resolve = lambda: "C:/fake/agy.exe"  # type: ignore[method-assign]
    return client


# ---- keeping an agent inside its box --------------------------------------

@pytest.mark.anyio
async def test_it_never_runs_with_permissions_skipped(tmp_path):
    """`--dangerously-skip-permissions` auto-approves every tool call. An agent
    with that flag pointed at a repository is a different product from this
    one, which owns its own worktree, gate and commit."""
    calls: list = []
    await client_for(calls, tmp_path).generate(model="m", system="rules", prompt="task")

    assert "--dangerously-skip-permissions" not in calls[0]["command"]


@pytest.mark.anyio
async def test_it_runs_in_a_scratch_directory_not_the_worktree(tmp_path):
    """So an agent that decides to edit a file edits nothing that matters."""
    calls: list = []
    client = client_for(calls, tmp_path)

    await client.generate(model="m", system="rules", prompt="task")

    assert Path(calls[0]["cwd"]) == tmp_path / "scratch"
    assert Path(calls[0]["cwd"]).is_dir()


@pytest.mark.anyio
async def test_the_answer_is_asked_for_as_json(tmp_path):
    calls: list = []
    await client_for(calls, tmp_path, effort="high").generate(model="gemini-3.8-flash-high",
                                                              system="rules", prompt="task")

    command = calls[0]["command"]
    assert command[:2] == ["agy", "-p"]
    assert "rules" in command[2] and "task" in command[2], "the rules are prepended to the prompt"
    assert command[command.index("--output-format") + 1] == "json"
    assert command[command.index("--model") + 1] == "gemini-3.8-flash-high"
    assert command[command.index("--effort") + 1] == "high"


# ---- what it does with the answer -----------------------------------------

@pytest.mark.anyio
async def test_a_successful_envelope_yields_its_response_and_usage(tmp_path):
    reply = await client_for([], tmp_path).generate(model="m", system="s", prompt="p")

    assert reply.text == '{"files": []}'
    assert reply.usage == {"prompt_tokens": 100, "output_tokens": 200}
    assert reply.key_label == "antigravity"


@pytest.mark.anyio
async def test_structured_output_wins_over_the_prose_response(tmp_path):
    """`--json-schema` puts the parsed answer in `structured_output`, and that
    is the one the loop can validate."""
    payload = {**ANSWER, "structured_output": '{"files": [{"path": "a"}]}'}
    reply = await client_for([], tmp_path, fake={"payload": payload}).generate(
        model="m", system="s", prompt="p")

    assert reply.text == '{"files": [{"path": "a"}]}'


@pytest.mark.anyio
async def test_plain_text_output_is_passed_through(tmp_path):
    """The loop validates the answer itself, so text that is not an envelope is
    still worth handing it rather than discarding."""
    reply = await client_for([], tmp_path, fake={"payload": '{"files": []}'}).generate(
        model="m", system="s", prompt="p")
    assert reply.text == '{"files": []}'


@pytest.mark.anyio
async def test_an_error_in_the_envelope_is_a_refusal_not_an_outage(tmp_path):
    """A refusal is the model declining. Asking a different provider the same
    question would hide the reason, so it stops the run."""
    payload = {"status": "error", "error": "blocked by safety settings"}
    with pytest.raises(GeminiRefused, match="safety"):
        await client_for([], tmp_path, fake={"payload": payload}).generate(model="m", system="s", prompt="p")


@pytest.mark.anyio
async def test_an_empty_response_is_unavailable(tmp_path):
    payload = {"status": "success", "response": "  "}
    with pytest.raises(GeminiUnavailable, match="empty response"):
        await client_for([], tmp_path, fake={"payload": payload}).generate(model="m", system="s", prompt="p")


@pytest.mark.anyio
async def test_a_nonzero_exit_with_no_output_names_the_stderr(tmp_path):
    with pytest.raises(GeminiUnavailable, match="not signed in"):
        await client_for([], tmp_path, fake={"payload": "", "returncode": 1,
                                             "stderr": "not signed in"}).generate(
            model="m", system="s", prompt="p")


# ---- not installed, and too long ------------------------------------------

@pytest.mark.anyio
async def test_a_missing_executable_says_what_to_do(tmp_path):
    client = AntigravityClient(scratch_dir=tmp_path, runner=fake_agy([]))
    client.resolve = lambda: None  # type: ignore[method-assign]

    with pytest.raises(GeminiUnavailable, match="not installed"):
        await client.generate(model="m", system="s", prompt="p")


@pytest.mark.anyio
async def test_a_prompt_too_long_for_a_windows_command_line_is_refused_early(tmp_path, monkeypatch):
    """CreateProcess takes 32,767 characters and an engineer prompt is
    20,000-30,000. A truncated prompt produces plausible nonsense, which is
    worse than a refusal that says the measurement."""
    monkeypatch.setattr("app.engineer.antigravity.os.name", "nt")
    calls: list = []

    with pytest.raises(GeminiIncomplete, match="command line"):
        await client_for(calls, tmp_path).generate(model="m", system="s", prompt="x" * WINDOWS_COMMAND_LIMIT)
    assert calls == [], "nothing is spent on a prompt that cannot be sent"


@pytest.mark.anyio
async def test_the_deadline_is_checked_before_spending_anything(tmp_path):
    calls: list = []
    client = client_for(calls, tmp_path, clock=lambda: 100.0)

    with pytest.raises(GeminiUnavailable, match="deadline"):
        await client.generate(model="m", system="s", prompt="p", deadline=50.0)
    assert calls == []


# ---- the chain ------------------------------------------------------------

async def answers(_system, _prompt, _deadline):
    return "ok", "second"


async def unavailable(_system, _prompt, _deadline):
    raise GeminiUnavailable("`agy` is not installed")


async def refuses(_system, _prompt, _deadline):
    raise GeminiRefused("declined")


@pytest.mark.anyio
async def test_the_chain_falls_through_an_unavailable_provider():
    text, label = await chain([unavailable, answers])("s", "p", 1e9)
    assert (text, label) == ("ok", "second")


@pytest.mark.anyio
async def test_a_refusal_stops_the_chain():
    """Only unavailability moves down. A refusal is an answer, and asking the
    next provider the same question would bury why the first said no."""
    with pytest.raises(GeminiRefused):
        await chain([refuses, answers])("s", "p", 1e9)


@pytest.mark.anyio
async def test_every_provider_unavailable_names_all_of_them():
    async def other(_s, _p, _d):
        raise GeminiUnavailable("ollama is not running")

    with pytest.raises(GeminiUnavailable) as failure:
        await chain([unavailable, other])("s", "p", 1e9)
    assert "agy" in str(failure.value) and "ollama" in str(failure.value)


def test_an_empty_chain_is_refused_when_it_is_built():
    with pytest.raises(ValueError, match="at least one provider"):
        chain([])


def test_an_empty_model_list_is_refused_when_it_is_built():
    with pytest.raises(ValueError, match="at least one"):
        antigravity_models(AntigravityClient(), [])
