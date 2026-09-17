"""The Roblox Engineer: generate, verify, retry -- and refuse rather than accept.

    audit (must hold a design)  ->  worktree on its own branch
        -> preflight: the untouched project must already pass every check
        -> attempt: model answer -> schema -> paths -> services -> write -> format -> gate
             pass  -> commit to the run's branch, keep the branch
             fail  -> exact failures become the next attempt's feedback
        -> out of attempts, or the same failure three times running
             -> the last checked attempt committed to the run's branch as refused
                evidence, and a Claude Code handoff packet
    The worktree is always removed. The branch is kept whenever it holds a commit:
    docs/GATING.md -- a branch that cannot go green is evidence, not litter. A run
    that never wrote code (preflight failed, every answer refused) has nothing to
    keep, and its empty branch is deleted.

Preflight exists so that attempts are only ever spent on the model's own code.
If the project fails before the model writes anything, every attempt would be
blamed for a problem it did not cause.

Budget, in the pattern of app/research_budget.py: an attempt is reserved
before the model is called, the deadline is checked before every attempt, and
a planned stop (attempts spent, deadline, keys rate limited) is recorded
separately from a failure.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .gate import Gate, GateReport
from .gemini import GeminiIncomplete, GeminiRefused, GeminiUnavailable
from .prompts import SYSTEM, build_prompt
from .schemas import EngineeringTask, EngineerOutput
from .workspace import UnsafePath, Worktree, validate_path

REPEATED_FAILURE_LIMIT = 3

ModelCall = Callable[[str, str, float], Awaitable[tuple[str, str]]]
EventHook = Callable[..., None]


@dataclass
class EngineerResult:
    status: str  # "complete" | "blocked"
    reason: str
    attempts: int
    branch: str | None = None
    commit: str | None = None
    handoff: str | None = None
    planned: bool = True
    files: list[str] = field(default_factory=list)


class Refusal(Exception):
    """An answer refused before it reached the gate; its message is the feedback."""


class EngineerLoop:
    def __init__(self, *, model: ModelCall, gate: Gate, known_services: frozenset[str],
                 create_worktree: Callable[[str], Worktree], handoff_dir: Path,
                 max_attempts: int, run_seconds: float, on_event: EventHook | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.model = model
        self.gate = gate
        self.known_services = known_services
        self.create_worktree = create_worktree
        self.handoff_dir = handoff_dir
        self.max_attempts = max_attempts
        self.run_seconds = run_seconds
        self.on_event = on_event or (lambda stage, detail="", **extra: None)
        self.clock = clock

    def emit(self, stage: str, detail: str = "", **extra) -> None:
        self.on_event(stage, detail, **extra)

    async def run(self, run_id: str, task: EngineeringTask, audit_payload: dict) -> EngineerResult:
        deadline = self.clock() + self.run_seconds
        worktree = await asyncio.to_thread(self.create_worktree, run_id)
        self.emit("worktree", f"Working on branch {worktree.branch}", branch=worktree.branch)
        keep_branch = False
        try:
            preflight = await asyncio.to_thread(self.gate.run, worktree.path)
            if not preflight.passed:
                self.emit("preflight_failed", "The project fails its checks before any code was written",
                          checks=preflight.summary())
                return EngineerResult(
                    "blocked", "The untouched project already fails its checks; fix the base branch first:\n"
                    + preflight.feedback(), attempts=0, planned=False)
            self.emit("preflight_passed", "The untouched project passes every check")
            result = await self._attempts(run_id, task, audit_payload, worktree, deadline)
            keep_branch = result.commit is not None
            return result
        finally:
            await asyncio.to_thread(worktree.remove, delete_branch=not keep_branch)

    async def _attempts(self, run_id: str, task: EngineeringTask, audit_payload: dict,
                        worktree: Worktree, deadline: float) -> EngineerResult:
        existing = worktree.read_existing_sources()
        project_file = worktree.project_file()
        base_services = worktree.existing_services()
        feedback: str | None = None
        last_output: EngineerOutput | None = None
        last_written: list[str] | None = None
        last_report: GateReport | None = None
        last_checked = 0
        fingerprints: list[str] = []
        stop_reason, planned = f"All {self.max_attempts} attempts were refused", True

        for attempt in range(1, self.max_attempts + 1):
            remaining = deadline - self.clock()
            if remaining <= 0:
                stop_reason = "The run's deadline passed before the system passed its checks"
                break
            self.emit("attempt", f"Attempt {attempt} of {self.max_attempts}", attempt=attempt)
            prompt = build_prompt(task, audit_payload, existing, feedback, attempt, project_file)
            try:
                text, model_name = await self.model(SYSTEM, prompt, deadline)
            except GeminiUnavailable as exc:
                stop_reason = f"Gemini unavailable: {exc}"
                break
            except GeminiRefused as exc:
                stop_reason, planned = f"Gemini refused the request: {exc}", False
                break
            except GeminiIncomplete as exc:
                feedback = (f"Your answer was cut off ({exc}). Return a smaller answer: fewer or shorter "
                            "files, and no commentary outside the JSON.")
                self.emit("attempt_refused", str(exc), attempt=attempt)
                fingerprints.append("incomplete")
                continue

            try:
                output, services = self._accept(text, base_services)
            except Refusal as exc:
                feedback = str(exc)
                self.emit("attempt_refused", feedback[:600], attempt=attempt, model=model_name)
                fingerprints.append(feedback)
                if self._repeating(fingerprints):
                    stop_reason = "The same refusal three attempts running"
                    break
                continue
            last_output = output

            files = {file.path: file.content for file in output.files}
            written = await asyncio.to_thread(self._write, worktree, files, services)
            report: GateReport = await asyncio.to_thread(self.gate.run, worktree.path)
            last_written, last_report, last_checked = written, report, attempt
            self.emit("gate", "All checks passed" if report.passed else
                      f"Failed: {', '.join(check.name for check in report.failed)}",
                      attempt=attempt, model=model_name, checks=report.summary())
            if report.passed:
                message = (f"feat({task.system}): {output.summary.splitlines()[0][:60] if output.summary else 'generated system'}"
                           f"\n\nRoblox Engineer run {run_id}, attempt {attempt}, model {model_name}.\n"
                           f"Venture Scout audit {task.audit_id}.")
                commit = await asyncio.to_thread(worktree.commit, written, message)
                self.emit("committed", f"Committed {commit[:10]} to {worktree.branch}", commit=commit)
                return EngineerResult("complete", "Every check passed", attempts=attempt,
                                      branch=worktree.branch, commit=commit, files=written)
            feedback = report.feedback()
            fingerprints.append(report.fingerprint())
            if self._repeating(fingerprints):
                stop_reason = "The same failure three attempts running; more attempts would repeat it"
                break

        branch = commit = None
        if last_written is not None and last_report is not None:
            # The tree still holds the last attempt that reached the gate: a
            # refusal before the gate never writes. Kept, and marked unmergeable.
            message = (f"refused({task.system}): attempt {last_checked} failed "
                       f"{', '.join(check.name for check in last_report.failed)}\n\n"
                       f"Roblox Engineer run {run_id} stopped: {stop_reason}\n"
                       "Kept as evidence (docs/GATING.md). Do not merge: this commit fails the gate.\n\n"
                       + last_report.feedback(limit=3000))
            commit = await asyncio.to_thread(worktree.commit, last_written, message, allow_empty=True)
            branch = worktree.branch
            self.emit("refused_attempt_kept", f"Attempt {last_checked} kept on {branch} as {commit[:10]}",
                      commit=commit, branch=branch)
        handoff = self._write_handoff(run_id, task, last_output, feedback, stop_reason, branch)
        self.emit("blocked", stop_reason, handoff=str(handoff))
        return EngineerResult("blocked", stop_reason, attempts=attempt, handoff=str(handoff), planned=planned,
                              branch=branch, commit=commit, files=last_written or [])

    def _accept(self, text: str, base_services: set[str]) -> tuple[EngineerOutput, set[str]]:
        try:
            output = EngineerOutput.model_validate_json(_strip_fence(text))
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'answer'}: {e['msg']}"
                                 for e in exc.errors()[:8])
            raise Refusal(f"Your answer was not the required JSON object: {problems}") from None
        problems: list[str] = []
        seen: set[str] = set()
        for file in output.files:
            try:
                validate_path(file.path)
            except UnsafePath as exc:
                problems.append(str(exc))
            if file.path.lower() in seen:
                problems.append(f"{file.path!r} appears more than once")
            seen.add(file.path.lower())
        unknown = sorted(set(output.services) - self.known_services)
        if unknown:
            problems.append(f"not Roblox services: {', '.join(unknown)} -- `services` lists services by exact class name")
        if problems:
            raise Refusal("Your answer was refused before checking:\n- " + "\n- ".join(problems))
        return output, base_services | set(output.services)

    def _write(self, worktree: Worktree, files: dict[str, str], services: set[str]) -> list[str]:
        worktree.reset()
        written = worktree.write(files, services)
        self.gate.format(worktree.path, written)
        return written

    @staticmethod
    def _repeating(fingerprints: list[str]) -> bool:
        tail = fingerprints[-REPEATED_FAILURE_LIMIT:]
        return len(tail) == REPEATED_FAILURE_LIMIT and len(set(tail)) == 1

    def _write_handoff(self, run_id: str, task: EngineeringTask, output: EngineerOutput | None,
                       feedback: str | None, reason: str, branch: str | None) -> Path:
        """Everything Claude Code needs to take over, in one file."""
        self.handoff_dir.mkdir(parents=True, exist_ok=True)
        path = self.handoff_dir / f"{run_id}.md"
        parts = [
            f"# Roblox Engineer handoff: {task.system}",
            f"Run `{run_id}` stopped: {reason}",
            "## What to do",
            ("Write the system below into the game repository so that every check passes. Follow the rules "
             "exactly: they close holes the type checker cannot see. Work on your own branch, verify with "
             "`powershell -File scripts\\verify.ps1` in the game repository, and ask for a merge only when "
             "it exits 0. Never edit scripts/verify.ps1, selene.toml, stylua.toml, .gitattributes or "
             "default.project.json to make a check pass."),
            "## Task", "```json\n" + task.model_dump_json(indent=2) + "\n```",
            "## Rules the code must follow", "```text\n" + SYSTEM + "\n```",
        ]
        if feedback:
            parts += ["## Why the last attempt was refused", "```text\n" + feedback + "\n```"]
        if branch:
            parts.append(f"The last checked attempt is committed on branch `{branch}` (refused; do not merge it).")
        if output is not None:
            parts.append("## The last attempt's files (refused; a starting point, not a solution)")
            parts += [f"### {file.path}\n```lua\n{file.content}\n```" for file in output.files]
            parts.append("Services requested: " + (", ".join(output.services) or "none"))
        path.write_text("\n\n".join(parts) + "\n", encoding="utf-8", newline="\n")
        return path


def _strip_fence(text: str) -> str:
    """Accept a JSON answer wrapped in a Markdown code fence; nothing looser."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if stripped.endswith("```") and first_newline != -1:
            return stripped[first_newline + 1:-3].strip()
    return stripped


def gemini_models(client, models: list[str]) -> ModelCall:
    """Adapt GeminiClient to the loop, trying `models` in order for each attempt.

    Every model but the last is asked not to wait: if it is rate limited or
    failing, the attempt moves to the next model at once. The last model may
    wait, up to the run's deadline. Each attempt starts again from the first,
    so Pro is used again as soon as its quota returns.
    """
    if not models:
        raise ValueError("at least one Gemini model is required")

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
            label = f"{reply.model} ({reply.key_label})"
            return reply.text, label + (f" after {len(passed_over)} unavailable model(s)" if passed_over else "")
        raise AssertionError("unreachable")
    return call

