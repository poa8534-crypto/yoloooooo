"""The Antigravity CLI as a completion backend, and the provider chain.

`agy` is an agent harness: given a prompt it will reason, call tools, edit
files and run commands. This project already owns that loop, so the tests that
matter most are the ones keeping it inside its box -- it runs in a scratch
directory, never with `--dangerously-skip-permissions`, and is told plainly not
to use tools.

None of the wire format is documented, so every shape here was measured against
agy 1.2.5 and the measurement is written down beside it. Everything runs
against a fake `agy`: the real one is not installed on every machine, and a
test that quietly skips is a test that was never written.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.engineer.antigravity import (
    NO_TOOLS,
    AntigravityClient,
    antigravity_models,
    chain,
    result_envelope,
    stream_message,
)
from app.engineer.gemini import GeminiIncomplete, GeminiRefused, GeminiUnavailable

ANSWER = {"status": "success", "response": '{"files": []}', "conversation_id": "c1",
          "usage": {"input_tokens": 100, "output_tokens": 200}}


def events(result: dict | None) -> str:
    """What `--output-format stream-json` prints: an init event, then a result."""
    lines = [json.dumps({"event": "init", "init": {"model": "m"}})]
    if result is not None:
        lines.append(json.dumps({"event": "result", "result": result}))
    return "\n".join(lines) + "\n"


def fake_agy(calls: list, payload=ANSWER, returncode: int = 0, stderr: str = ""):
    def runner(command, **kwargs):
        calls.append({"command": command, **kwargs})
        out = payload if isinstance(payload, str) else events(payload)
        return subprocess.CompletedProcess(command, returncode, out.encode("utf-8"), stderr.encode("utf-8"))

    return runner


def client_for(calls: list, tmp_path: Path, **overrides) -> AntigravityClient:
    client = AntigravityClient(runner=fake_agy(calls, **overrides.pop("fake", {})),
                               scratch_dir=tmp_path / "scratch", **overrides)
    client.resolve = lambda: "C:/fake/agy.exe"  # type: ignore[method-assign]
    return client


def sent_prompt(call: dict) -> str:
    return json.loads(call["input"].decode("utf-8"))["message"]["content"]


# ---- keeping an agent inside its box --------------------------------------

@pytest.mark.anyio
async def test_it_never_runs_with_permissions_skipped(tmp_path):
    """`--dangerously-skip-permissions` auto-approves every tool call. An agent
    with that flag is a different product from this one, which owns its own
    worktree, gate and commit."""
    calls: list = []
    await client_for(calls, tmp_path).generate(model="m", system="rules", prompt="task")

    assert "--dangerously-skip-permissions" not in calls[0]["command"]


@pytest.mark.anyio
async def test_it_runs_in_a_scratch_directory_not_the_worktree(tmp_path):
    """So an agent that decides to edit a file edits nothing that matters."""
    calls: list = []

    await client_for(calls, tmp_path).generate(model="m", system="rules", prompt="task")

    assert Path(calls[0]["cwd"]) == tmp_path / "scratch"
    assert Path(calls[0]["cwd"]).is_dir()


@pytest.mark.anyio
async def test_the_prompt_tells_it_not_to_use_tools(tmp_path):
    """Measured: given an engineering task it called `run_command` with
    `Get-Location`, headless mode cannot prompt for that permission, the call
    was auto-denied and it stopped with an empty response after 27,082 tokens.
    The documented answer is --dangerously-skip-permissions; saying this
    instead keeps the flag off, and zero tools were attempted."""
    calls: list = []
    await client_for(calls, tmp_path).generate(model="m", system="rules", prompt="task")

    sent = sent_prompt(calls[0])
    assert sent.startswith(NO_TOOLS)
    assert "rules" in sent and "task" in sent


@pytest.mark.anyio
async def test_slash_command_expansion_is_disabled(tmp_path):
    """The prompt carries a design assembled from pages off the open web, and
    `agy` expands slash commands in print mode. A design line beginning with
    `/` would be executed rather than read."""
    calls: list = []
    await client_for(calls, tmp_path).generate(model="m", system="s", prompt="p")

    assert "--disable-slash-commands" in calls[0]["command"]
    assert "--sandbox" in calls[0]["command"]


# ---- the wire format ------------------------------------------------------

@pytest.mark.anyio
async def test_the_stream_is_asked_for_in_both_directions(tmp_path):
    calls: list = []
    await client_for(calls, tmp_path).generate(model="gemini-3.8-flash-high", system="s", prompt="p")

    command = calls[0]["command"]
    assert command[command.index("--input-format") + 1] == "stream-json"
    assert command[command.index("--output-format") + 1] == "stream-json"
    assert command[command.index("--model") + 1] == "gemini-3.8-flash-high"
    assert "-p" not in command, "with stream-json input, -p swallows the next flag as its value"


@pytest.mark.anyio
async def test_a_prompt_far_past_the_command_line_limit_is_sent_over_stdin(tmp_path):
    """CreateProcess takes 32,767 characters for a whole command line and an
    engineer prompt is 20,000-30,000, so the argument form was one larger
    design away from truncating a prompt into plausible nonsense."""
    calls: list = []
    huge = "x" * 200_000

    await client_for(calls, tmp_path).generate(model="m", system="s", prompt=huge)

    assert huge not in " ".join(calls[0]["command"]), "the prompt never reaches the command line"
    assert huge in calls[0]["input"].decode("utf-8")


def test_the_stream_message_is_the_one_shape_that_answers():
    """`{"type": "user", ...}` draws `stream input message is missing the
    "event" field`; an unknown event name is ignored with a warning and exit 0;
    `text` and `content` beside a correct event name are accepted and ignored,
    reporting SUCCESS with an empty response."""
    assert json.loads(stream_message("hello").decode("utf-8")) == {
        "event": "user", "message": {"role": "user", "content": "hello"}}


def test_the_message_is_one_ndjson_line():
    """One NDJSON message per line is what `--input-format stream-json` reads;
    a prompt's own newlines must not end the line early."""
    raw = stream_message("first\nsecond").decode("utf-8")
    assert raw.endswith("\n") and raw.count("\n") == 1


@pytest.mark.anyio
async def test_effort_is_not_sent_when_the_model_name_already_carries_it(tmp_path):
    """Measured: `--model gemini-3.8-flash-low conflicts with --effort=medium`.
    The models are named with the effort built in."""
    calls: list = []
    client = client_for(calls, tmp_path, effort="medium")

    await client.generate(model="gemini-3.8-flash-low", system="s", prompt="p")
    assert "--effort" not in calls[0]["command"]

    await client.generate(model="claude-sonnet-4-6", system="s", prompt="p")
    assert calls[1]["command"][calls[1]["command"].index("--effort") + 1] == "medium"


# ---- reading the stream back ----------------------------------------------

def test_the_result_is_read_from_the_result_event():
    stream = events({"status": "SUCCESS", "response": "answer"})
    assert result_envelope(stream)["response"] == "answer"


def test_a_truncated_line_in_the_stream_does_not_lose_the_result():
    """A process that died mid-write leaves half a line behind."""
    stream = events({"status": "SUCCESS", "response": "kept"}) + '{"event": "step_upda'
    assert result_envelope(stream)["response"] == "kept"


def test_step_events_are_not_mistaken_for_the_result():
    stream = (json.dumps({"event": "step_update", "step_update": {"state": "DONE"}}) + "\n"
              + events({"status": "SUCCESS", "response": "answer"}))
    assert result_envelope(stream)["response"] == "answer"


def test_no_result_event_at_all_is_not_a_result():
    assert result_envelope(events(None)) is None


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
async def test_an_uppercase_status_is_still_success(tmp_path):
    """agy 1.2.5 answers "SUCCESS". Comparing case-sensitively turned a working
    call into an outage."""
    reply = await client_for([], tmp_path, fake={"payload": {"status": "SUCCESS",
                                                             "response": '{"files": []}'}}).generate(
        model="m", system="s", prompt="p")
    assert reply.text == '{"files": []}'


# ---- failures the loop has to tell apart ----------------------------------

@pytest.mark.anyio
async def test_an_error_in_the_envelope_is_a_refusal_not_an_outage(tmp_path):
    """A refusal is the model declining. Asking a different provider the same
    question would hide the reason, so it stops the run."""
    payload = {"status": "ERROR", "error": "blocked by safety settings"}
    with pytest.raises(GeminiRefused, match="safety"):
        await client_for([], tmp_path, fake={"payload": payload}).generate(model="m", system="s", prompt="p")


@pytest.mark.anyio
@pytest.mark.parametrize("message", [
    "Your previous response was cut off because it exceeded the output token limit. "
    "Please continue from where you left off, keeping your response shorter. Retries remaining: 3",
    "the response was truncated",
    "exceeded the maximum output length",
])
async def test_a_truncated_answer_is_incomplete_not_a_refusal(tmp_path, message):
    """`agy` reports both through the same `error` field, and the loop answers
    them differently: a refusal ends the run, a truncation asks for a smaller
    answer and tries again. The first end-to-end run was stopped with five
    attempts unspent by exactly this."""
    payload = {"status": "ERROR", "error": message}
    with pytest.raises(GeminiIncomplete):
        await client_for([], tmp_path, fake={"payload": payload}).generate(
            model="m", system="s", prompt="p")


@pytest.mark.anyio
async def test_a_real_refusal_is_still_a_refusal(tmp_path):
    """The truncation test must not be so broad that it swallows a decline."""
    payload = {"status": "ERROR", "error": "blocked by safety settings"}
    with pytest.raises(GeminiRefused):
        await client_for([], tmp_path, fake={"payload": payload}).generate(
            model="m", system="s", prompt="p")


@pytest.mark.anyio
async def test_a_permission_denial_is_reported_as_itself(tmp_path):
    """Unrecognisable as an empty response, and the fix is a different one."""
    payload = {"status": "SUCCESS", "response": "",
               "denied_actions": [{"action": "command", "display_name": "RunCommand"}]}
    with pytest.raises(GeminiUnavailable, match="RunCommand"):
        await client_for([], tmp_path, fake={"payload": payload}).generate(model="m", system="s", prompt="p")


@pytest.mark.anyio
async def test_an_empty_response_with_no_denial_is_unavailable(tmp_path):
    payload = {"status": "SUCCESS", "response": "  "}
    with pytest.raises(GeminiUnavailable, match="empty response"):
        await client_for([], tmp_path, fake={"payload": payload}).generate(model="m", system="s", prompt="p")


@pytest.mark.anyio
async def test_a_nonzero_exit_with_no_output_names_the_stderr(tmp_path):
    with pytest.raises(GeminiUnavailable, match="not signed in"):
        await client_for([], tmp_path, fake={"payload": "", "returncode": 1,
                                             "stderr": "not signed in"}).generate(
            model="m", system="s", prompt="p")


@pytest.mark.anyio
async def test_a_stream_with_no_result_event_names_the_stderr(tmp_path):
    """How an ignored input message ends: exit 0, an init event, nothing else."""
    with pytest.raises(GeminiUnavailable, match="unsupported"):
        await client_for([], tmp_path, fake={
            "payload": None,
            "stderr": 'warning: ignoring unsupported stream input message event "user_message"',
        }).generate(model="m", system="s", prompt="p")


@pytest.mark.anyio
async def test_a_missing_executable_says_what_to_do(tmp_path):
    client = AntigravityClient(scratch_dir=tmp_path, runner=fake_agy([]))
    client.resolve = lambda: None  # type: ignore[method-assign]

    with pytest.raises(GeminiUnavailable, match="not installed"):
        await client.generate(model="m", system="s", prompt="p")


@pytest.mark.anyio
async def test_the_deadline_is_checked_before_spending_anything(tmp_path):
    calls: list = []
    client = client_for(calls, tmp_path, clock=lambda: 100.0)

    with pytest.raises(GeminiUnavailable, match="deadline"):
        await client.generate(model="m", system="s", prompt="p", deadline=50.0)
    assert calls == []


# ---- finding the executable -----------------------------------------------

def test_the_executable_is_found_off_path_where_the_installer_puts_it(tmp_path, monkeypatch):
    """The installer writes the PATH to the registry and broadcasts it, which a
    running process never sees: CreateProcess resolves against the PARENT's
    PATH. A service started before the install would otherwise never find it."""
    binary = tmp_path / "agy" / "bin" / "agy.exe"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("app.engineer.antigravity.shutil.which", lambda _name: None)

    assert AntigravityClient().resolve() == str(binary)


def test_resolution_gives_up_when_it_is_really_not_there(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("app.engineer.antigravity.shutil.which", lambda _name: None)

    assert AntigravityClient().resolve() is None


def test_it_is_found_even_by_a_process_without_localappdata(tmp_path, monkeypatch):
    # The dashboard runs as a scheduled task, and that process had no
    # %LOCALAPPDATA%: agy was installed and reported missing, and the architect
    # fell back to other models. The folder is asked of Windows instead.
    binary = tmp_path / "agy" / "bin" / "agy.exe"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr("app.engineer.antigravity.shutil.which", lambda _name: None)
    monkeypatch.setattr("app.engineer.antigravity._known_folder", lambda _folder: tmp_path)

    assert AntigravityClient().resolve() == str(binary)


@pytest.mark.skipif(__import__("os").name != "nt", reason="a Windows known folder")
def test_windows_names_the_same_folder_the_variable_does(monkeypatch):
    import os

    from app.engineer.antigravity import FOLDERID_LOCAL_APP_DATA, _known_folder

    expected = os.environ.get("LOCALAPPDATA")
    if not expected:
        pytest.skip("this shell has no LOCALAPPDATA to compare with")
    assert _known_folder(FOLDERID_LOCAL_APP_DATA) == Path(expected)


def test_a_missing_agy_is_reported_with_where_it_was_looked_for(tmp_path, monkeypatch):
    from app.engineer.toolchain import check_agy

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("app.engineer.antigravity.shutil.which", lambda _name: None)

    report = check_agy()

    assert report.present is False
    assert str(tmp_path / "agy" / "bin" / "agy.exe") in report.detail


# ---- the chain ------------------------------------------------------------

async def answers(_system, _prompt, _deadline):
    return "ok", "second"


async def unavailable(_system, _prompt, _deadline):
    raise GeminiUnavailable("`agy` is not installed")


async def refuses(_system, _prompt, _deadline):
    raise GeminiRefused("declined")


@pytest.mark.anyio
async def test_the_chain_falls_through_an_unavailable_provider():
    assert await chain([unavailable, answers])("s", "p", 1e9) == ("ok", "second")


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


def test_agy_installed_inside_another_apps_private_storage_is_named_as_such(tmp_path, monkeypatch):
    # Measured on the owner's machine: installed from a terminal inside the
    # Claude desktop app, an MSIX package, agy landed in that app's private
    # AppData. Programs run from inside the app saw it; the dashboard, started
    # by Task Scheduler, got "the system cannot find the path specified".
    from app.engineer.toolchain import check_agy, inside_packaged_apps

    local = tmp_path / "AppData" / "Local"
    private = local / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Local" / "agy" / "bin"
    private.mkdir(parents=True)
    (private / "agy.exe").write_text("", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr("app.engineer.antigravity.shutil.which", lambda _name: None)

    assert inside_packaged_apps(local / "agy" / "bin" / "agy.exe") == [
        ("Claude", private / "agy.exe")]
    report = check_agy()
    assert report.present is False
    assert "Claude app" in report.detail and "ordinary terminal" in report.detail


def test_an_ordinary_absence_is_not_blamed_on_a_packaged_app(tmp_path, monkeypatch):
    from app.engineer.toolchain import check_agy

    local = tmp_path / "AppData" / "Local"
    (local / "Packages" / "Some.App_123" / "LocalCache" / "Local").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr("app.engineer.antigravity.shutil.which", lambda _name: None)

    assert "private storage" not in check_agy().detail
