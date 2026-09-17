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
import shutil
import subprocess
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from .gemini import GeminiIncomplete, GeminiRefused, GeminiUnavailable

UsageHook = Callable[[str, str, str, dict], None] | None

# CreateProcess takes at most 32,767 characters for the whole command line, and
# an engineer prompt is 20,000-30,000 before the rules are prepended. Refusing
# early with the measurement beats a truncated prompt that produces plausible
# nonsense, or a Windows error nobody can read.
WINDOWS_COMMAND_LIMIT = 32_767
COMMAND_HEADROOM = 2_000
# What tells `agy`'s own envelope apart from an answer that happens to be JSON.
ENVELOPE_KEYS = frozenset({"status", "response", "structured_output", "error",
                           "conversation_id", "num_turns", "duration_seconds"})


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
        """The executable's full path, or None when it is not installed."""
        return shutil.which(self.executable)

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

    def command(self, model: str, prompt: str) -> list[str]:
        return [self.executable, "-p", prompt, "--output-format", "json",
                "--model", model, "--effort", self.effort,
                "--print-timeout", f"{max(int(self.timeout), 60)}s"]

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

        full = f"{system}\n\n{prompt}" if system else prompt
        command = self.command(model, full)
        length = sum(len(part) + 3 for part in command)
        if os.name == "nt" and length > WINDOWS_COMMAND_LIMIT - COMMAND_HEADROOM:
            raise GeminiIncomplete(
                f"the prompt is {length} characters and Windows takes {WINDOWS_COMMAND_LIMIT} on a "
                "command line; return a smaller answer or ask for fewer files")

        try:
            completed = await asyncio.to_thread(self._run, command)
        except FileNotFoundError:
            raise GeminiUnavailable(f"`{self.executable}` disappeared between the check and the call") from None
        except subprocess.TimeoutExpired:
            raise GeminiUnavailable(f"{model}: `{self.executable}` did not answer within {self.timeout:.0f}s") from None

        stdout = completed.stdout.decode("utf-8", "replace")
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        if completed.returncode != 0 and not stdout.strip():
            raise GeminiUnavailable(f"{model}: `{self.executable}` exited {completed.returncode}: {stderr[:300]}")

        try:
            envelope = json.loads(stdout)
        except ValueError:
            # `text` output, or a crash that still printed something. The loop
            # validates the answer itself, so plain text is worth passing on.
            if stdout.strip():
                return self._reply(model, stdout.strip(), {})
            raise GeminiUnavailable(f"{model}: `{self.executable}` printed nothing usable: {stderr[:300]}") from None

        if not isinstance(envelope, dict):
            raise GeminiUnavailable(f"{model}: expected a JSON object from `{self.executable}`")
        if not ENVELOPE_KEYS & envelope.keys():
            # Valid JSON that is not an envelope: `--output-format text` with a
            # model that answered in JSON, which is the shape the loop asked
            # for. Reading it as an empty envelope would throw the answer away.
            return self._reply(model, stdout.strip(), {})

        error = envelope.get("error")
        status = str(envelope.get("status") or "")
        if error:
            # A refusal is the model declining, which is not a reason to try a
            # different model with the same prompt; the loop stops on it.
            raise GeminiRefused(f"{model}: {str(error)[:300]}")
        if status and status not in ("success", "completed", "ok"):
            raise GeminiUnavailable(f"{model}: `{self.executable}` reported status {status!r}")

        text = envelope.get("structured_output") or envelope.get("response") or ""
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)
        if not text.strip():
            raise GeminiUnavailable(f"{model}: `{self.executable}` returned an empty response")

        usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
        return self._reply(model, text, usage)

    def _run(self, command: list[str]) -> subprocess.CompletedProcess:
        return (self.runner or subprocess.run)(
            command, cwd=str(self.workspace()), capture_output=True,
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
