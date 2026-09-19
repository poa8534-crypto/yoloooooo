"""The Antigravity CLI (`agy`) as a completion backend.

`agy` is an agent harness, not a completion endpoint: given a prompt it will
reason, call tools, edit files and run commands. This project already owns that
loop -- a worktree, a gate, a commit, a refusal (app/engineer/loop.py) -- so
here it is used for the one thing the loop cannot do itself, which is write the
Luau. That is why it runs:

  * in a scratch directory, never the run's worktree, so an agent that decides
    to edit a file edits nothing that matters;
  * WITHOUT `--dangerously-skip-permissions`, so tool calls needing approval
    are soft-denied rather than run unattended. Auto-approving an agent pointed
    at a repository is a different product from this one.

Its exceptions are Gemini's, as Ollama's are, because the loop already knows
how to tell unavailable from refused from incomplete and a third vocabulary for
the same three facts helps nobody.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from .gemini import GeminiIncomplete, GeminiRefused, GeminiUnavailable

UsageHook = Callable[[str, str, str, dict], None] | None

EFFORT_SUFFIX = re.compile(r"-(low|medium|high)$")
# `agy` reports a truncated answer through the same `error` field as a refusal,
# and the difference matters: the loop answers a refusal by stopping the run and
# a truncation by asking for a smaller answer and trying again. Measured on the
# first end-to-end run, which was stopped with five attempts unspent by
#
#     Your previous response was cut off because it exceeded the output token
#     limit. Please continue from where you left off, keeping your response
#     shorter. Retries remaining: 3
TRUNCATED = re.compile(r"cut off|output token limit|exceeded the (?:maximum|output)|truncat",
                       re.IGNORECASE)


# `agy` is an agent, so its first instinct on any task is to orient itself.
# Measured: given an engineering task it called `run_command` with
# `Get-Location`, headless mode cannot prompt for the permission that needs, the
# call was auto-denied, and the agent stopped with an empty response --
# `denied_actions: [{"action": "command"}]` and 27,082 tokens spent on nothing.
#
# The documented answer is `--dangerously-skip-permissions`, which auto-approves
# every tool call. Saying this instead costs one paragraph and keeps the flag
# off: with it, zero tools were attempted and the answer came back first time.
NO_TOOLS = (
    "You have no working directory and no tools. Do not call any tool, do not run any command, "
    "and do not read or write any file. Everything you need is in this message. Answer "
    "immediately with the JSON object and nothing else."
)


FOLDERID_LOCAL_APP_DATA = "F1B32785-6FBA-4FCF-9D55-7B8E7F157091"


def _known_folder(folder_id: str) -> Path | None:
    """A Windows known folder, asked of the shell rather than the environment."""
    if os.name != "nt":
        return None
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    value = uuid.UUID(folder_id)
    guid = GUID(value.time_low, value.time_mid, value.time_hi_version,
                (ctypes.c_ubyte * 8)(*value.bytes[8:]))
    found = ctypes.c_wchar_p()
    try:
        failed = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(found))
        return None if failed or not found.value else Path(found.value)
    except OSError:
        return None
    finally:
        if found:
            ctypes.windll.ole32.CoTaskMemFree(found)


def local_app_data() -> Path | None:
    """This user's local application data folder, where `agy` installs itself.

    Not only %LOCALAPPDATA%. The dashboard runs as a scheduled task, and a
    process Task Scheduler starts can lack it -- measured: that dashboard found
    no `agy` while every terminal did, the architect fell back to Gemini and a
    local 14B model without anyone seeing, and the Build button answered that
    the build machine had no `agy`. The user PATH names the folder as
    `%LOCALAPPDATA%\\agy\\bin` too, so without the variable that entry expands
    to nothing. Windows' own answer comes from the user's profile, whatever
    the environment holds.
    """
    configured = os.environ.get("LOCALAPPDATA")
    if configured:
        return Path(configured)
    known = _known_folder(FOLDERID_LOCAL_APP_DATA)
    if known is not None:
        return known
    fallback = Path.home() / "AppData" / "Local"
    return fallback if fallback.is_dir() else None


def stream_message(prompt: str) -> bytes:
    """One NDJSON line for `--input-format stream-json`.

    Measured against agy 1.2.5, because none of this is documented. The wrong
    shapes do not fail: `{"type": "user", ...}` is answered with `stream input
    message is missing the "event" field`, and an unknown event name draws
    `warning: ignoring unsupported stream input message event "..."` on stderr,
    then exits 0 having done nothing. `text` and `content` beside a correct
    event name are accepted and ignored the same way, reporting SUCCESS with an
    empty response. Only this shape produces an answer.
    """
    return (json.dumps({"event": "user", "message": {"role": "user", "content": prompt}},
                       ensure_ascii=False) + "\n").encode("utf-8")


def result_envelope(stdout: str) -> dict | None:
    """The `result` event's payload, from a stream of NDJSON events.

    The stream also carries `init` and per-tool events, and a truncated line is
    possible if the process died mid-write, so unreadable lines are skipped
    rather than failing the call.
    """
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("event") == "result":
            payload = event.get("result")
            return payload if isinstance(payload, dict) else None
    return None


@dataclass(frozen=True)
class AntigravityReply:
    text: str
    model: str
    key_label: str
    usage: dict[str, int]


@dataclass
class AntigravityClient:
    executable: str = "agy"
    timeout: float = 900.0
    effort: str = "medium"
    # Where `agy` is allowed to think. Never the worktree: see the module note.
    scratch_dir: Path | None = None
    on_usage: UsageHook = None
    clock: Callable[[], float] = time.monotonic
    runner: Callable[..., subprocess.CompletedProcess] | None = None
    _scratch: Path | None = field(init=False, default=None)

    def resolve(self) -> str | None:
        """The executable's full path, or None when it is not installed.

        The installer adds `%LOCALAPPDATA%\\agy\\bin` to the PATH in the
        registry and broadcasts the change, which a running process never sees:
        on Windows CreateProcess resolves an executable against the PARENT's
        PATH. So a service started before the install -- or any terminal that
        has not been restarted -- finds nothing on PATH alone. The same trap
        made `gate.format` silently do nothing for six attempts of a real run
        (app/engineer/gate.py), so it is answered the same way here.
        """
        found = shutil.which(self.executable)
        if found:
            return found
        for candidate in self.install_candidates():
            if candidate.is_file():
                return str(candidate)
        return None

    def install_candidates(self) -> list[Path]:
        """Where the installer puts it, for a report that says where it looked."""
        local = local_app_data()
        if local is None:
            return []
        return [local / "agy" / "bin" / name for name in (f"{self.executable}.exe", self.executable)]

    def workspace(self) -> Path:
        if self._scratch is None:
            base = self.scratch_dir or Path(tempfile.gettempdir()) / "venture-engineer-agy"
            base.mkdir(parents=True, exist_ok=True)
            self._scratch = base
        return self._scratch

    async def close(self) -> None:
        """Nothing to close: each call is its own process."""

    def scrub(self, text: str) -> str:
        """No credential to remove: `agy` authenticates through the keyring."""
        return text

    def command(self, model: str) -> list[str]:
        """The command, which never carries the prompt.

        The prompt goes over stdin (`stream_message`) instead. Windows resolves
        a process through CreateProcess, which takes 32,767 characters for the
        whole command line, and an engineer prompt measures 20,000-30,000
        before the rules are prepended -- so the argument form was one larger
        design away from truncating a prompt into plausible nonsense. Over
        stdin there is no limit to be near.

        Note `-p` is absent: with `--input-format stream-json` it is not only
        unnecessary, it swallows the next flag as its value.
        """
        # `agy models` names its models with the effort built in --
        # `gemini-3.8-flash-high`, `gemini-3.1-pro-low` -- and passing --effort
        # as well is refused: "--model gemini-3.8-flash-low conflicts with
        # --effort=medium". So the flag is only sent when the name leaves the
        # question open.
        effort = [] if EFFORT_SUFFIX.search(model) else ["--effort", self.effort]
        return [self.resolve() or self.executable,
                "--input-format", "stream-json", "--output-format", "stream-json",
                "--model", model, *effort,
                "--print-timeout", f"{max(int(self.timeout), 60)}s",
                # The prompt carries a Venture Scout design, which is assembled
                # from pages off the open web. `agy` expands slash commands and
                # skills in print mode, so a design containing a line that
                # begins with `/` would be executed rather than read. The loop
                # already treats that text as data (prompts.fence); this is the
                # same decision one layer down.
                "--disable-slash-commands",
                # Terminal restrictions. The model is here to write Luau; the
                # worktree, the gate and the commit belong to this project.
                "--sandbox"]

    async def generate(self, *, model: str, system: str, prompt: str, json_output: bool = True,
                       deadline: float | None = None, max_wait: float | None = None) -> AntigravityReply:
        """One completion.

        `agy` documents no flag for a system instruction, so the rules are
        prepended to the prompt. They are the first thing the model reads
        either way; what changes is only that they are not privileged, which is
        why the gate and not the prompt is what makes the output safe.
        """
        if self.resolve() is None:
            raise GeminiUnavailable(
                f"`{self.executable}` is not installed. Install the Antigravity CLI, or drop "
                "antigravity from ENGINEER_PROVIDERS")
        if deadline is not None and self.clock() >= deadline:
            raise GeminiUnavailable(f"{model}: the run's deadline passed before it was asked")

        full = "\n\n".join(part for part in (NO_TOOLS, system, prompt) if part)
        command = self.command(model)

        try:
            completed = await asyncio.to_thread(self._run, command, stream_message(full))
        except FileNotFoundError:
            raise GeminiUnavailable(f"`{self.executable}` disappeared between the check and the call") from None
        except subprocess.TimeoutExpired:
            raise GeminiUnavailable(f"{model}: `{self.executable}` did not answer within {self.timeout:.0f}s") from None

        stdout = completed.stdout.decode("utf-8", "replace")
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        if completed.returncode != 0 and not stdout.strip():
            raise GeminiUnavailable(f"{model}: `{self.executable}` exited {completed.returncode}: {stderr[:300]}")

        envelope = result_envelope(stdout)
        if envelope is None:
            # No result event. A message `agy` ignored ends this way, and so
            # does a crash part-way through the stream, so the stderr is the
            # only thing that says which.
            raise GeminiUnavailable(
                f"{model}: `{self.executable}` produced no result event: {stderr[:300]}")

        error = envelope.get("error")
        status = str(envelope.get("status") or "")
        if error and TRUNCATED.search(str(error)):
            # Not a refusal: the model had more to say. The loop's answer to
            # this is a smaller ask, not the end of the run.
            raise GeminiIncomplete(f"{model}: {str(error)[:200]}")
        if error:
            # A refusal is the model declining, which is not a reason to try a
            # different model with the same prompt; the loop stops on it.
            raise GeminiRefused(f"{model}: {str(error)[:300]}")
        # Measured: `agy` 1.2.5 answers "SUCCESS". Compared casefolded so the
        # check does not turn a working call into an outage over capitalisation.
        if status and status.casefold() not in ("success", "completed", "ok", "done"):
            raise GeminiUnavailable(f"{model}: `{self.executable}` reported status {status!r}")

        text = envelope.get("structured_output") or envelope.get("response") or ""
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)
        if not text.strip():
            denied = envelope.get("denied_actions")
            if denied:
                # The specific way this fails, and unrecognisable as itself
                # without naming the tool: the agent asked for a permission
                # headless mode cannot grant, and stopped rather than answering.
                names = ", ".join(str(action.get("display_name") or action.get("action"))
                                  for action in denied if isinstance(action, dict))
                raise GeminiUnavailable(
                    f"{model}: `{self.executable}` stopped to ask permission for {names} and "
                    "answered nothing; the prompt must tell it not to use tools")
            raise GeminiUnavailable(f"{model}: `{self.executable}` returned an empty response")

        usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
        return self._reply(model, text, usage)

    def _run(self, command: list[str], message: bytes) -> subprocess.CompletedProcess:
        return (self.runner or subprocess.run)(
            command, input=message, cwd=str(self.workspace()), capture_output=True,
            timeout=self.timeout, check=False)

    def _reply(self, model: str, text: str, usage: dict) -> AntigravityReply:
        counts = {"prompt_tokens": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
                  "output_tokens": int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)}
        if self.on_usage:
            self.on_usage("antigravity", model, "ok", counts)
        return AntigravityReply(text=text, model=model, key_label="antigravity", usage=counts)


def antigravity_models(client: AntigravityClient, models: list[str]):
    """Adapt the client to the loop, trying `models` in order.

    Same contract as `gemini_models` and `ollama_models`.
    """
    if not models:
        raise ValueError("at least one Antigravity model is required")

    async def call(system: str, prompt: str, deadline: float) -> tuple[str, str]:
        passed_over: list[str] = []
        for index, model in enumerate(models):
            last = index == len(models) - 1
            try:
                reply = await client.generate(model=model, system=system, prompt=prompt, deadline=deadline)
            except GeminiUnavailable as exc:
                if last:
                    raise GeminiUnavailable("; ".join([*passed_over, str(exc)])) from None
                passed_over.append(str(exc))
                continue
            label = f"{reply.model} (antigravity)"
            return reply.text, label if not passed_over else f"{label} after {len(passed_over)} unavailable model(s)"
        raise GeminiUnavailable("no Antigravity model answered")

    return call


def chain(calls: list[Callable[[str, str, float], Awaitable[tuple[str, str]]]]):
    """Try each provider in turn; the first that answers wins.

    Only `GeminiUnavailable` moves to the next provider. A refusal and a
    truncated answer are answers: they belong to the attempt that got them, and
    asking a different provider the same question would hide the reason.
    """
    if not calls:
        raise ValueError("at least one provider is required")

    async def call(system: str, prompt: str, deadline: float) -> tuple[str, str]:
        unavailable: list[str] = []
        for index, provider in enumerate(calls):
            try:
                return await provider(system, prompt, deadline)
            except GeminiUnavailable as exc:
                if index == len(calls) - 1:
                    raise GeminiUnavailable("; ".join([*unavailable, str(exc)])) from None
                unavailable.append(str(exc))
        raise GeminiUnavailable("no provider answered")

    return call
