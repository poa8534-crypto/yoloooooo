"""Captured attempts, turned into repair examples.

The valuable shape is not "prompt -> good code". It is

    prompt -> the code this model actually wrote -> the exact tool output that
    refused it -> code that passes

because the first three come free from every failed run and the fourth is the
only part a person has to supply. A model trained on that is being taught what
its own mistakes look like from the compiler's side, which is the thing a
downloaded corpus cannot contain.

A repair is only emitted when the fix is KNOWN GOOD -- a later attempt in the
same run that passed the gate, or the file as it stands on the game
repository's base branch, which is there because it passed. A failing attempt
followed by another failing attempt is not a repair, and training on it would
teach the model that its second guess was right.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .capture import Attempt
from .prompts import SYSTEM


def _answer(files: dict[str, str], services: Iterable[str], summary: str) -> str:
    return json.dumps({"files": [{"path": path, "content": content} for path, content in sorted(files.items())],
                       "services": sorted(services), "summary": summary}, ensure_ascii=False)


def tool_message(attempt: Attempt) -> str:
    """What the toolchain said, verbatim. This is the whole point of the example."""
    if attempt.refusal:
        return attempt.refusal
    return "\n\n".join(f"## {check['check']} FAILED\n{check.get('output', '').strip()}"
                       for check in attempt.failures()) or "The attempt was refused."


@dataclass(frozen=True)
class Repair:
    run_id: str
    attempt: int
    model: str
    source: str  # "attempt" (a later attempt passed) or "human" (the accepted file)
    messages: list[dict]

    def to_json(self) -> str:
        return json.dumps({"messages": self.messages, "metadata": {
            "run_id": self.run_id, "attempt": self.attempt, "model": self.model, "fix_source": self.source,
        }}, ensure_ascii=False)


def repairs(attempts: list[Attempt], accepted: dict[str, str] | None = None) -> Iterator[Repair]:
    """One example per failed attempt that has a known-good fix.

    `accepted` is the code as it stands on the base branch, keyed by path. It
    is the fallback when no attempt in the run ever passed -- which, so far, is
    every run.
    """
    accepted = accepted or {}
    by_run: dict[str, list[Attempt]] = {}
    for attempt in attempts:
        by_run.setdefault(attempt.run_id, []).append(attempt)

    for run_id, run in by_run.items():
        run = sorted(run, key=lambda a: a.attempt)
        passing = next((a for a in run if a.passed and a.files), None)
        for attempt in run:
            if attempt.passed:
                continue
            fix = _fix_for(attempt, passing, accepted)
            if fix is None:
                continue
            files, source = fix
            yield Repair(run_id=run_id, attempt=attempt.attempt, model=attempt.model, source=source,
                         messages=[
                             {"role": "system", "content": SYSTEM},
                             {"role": "user", "content": attempt.prompt},
                             {"role": "assistant", "content": attempt.answer.strip()},
                             {"role": "tool", "content": tool_message(attempt)},
                             {"role": "assistant", "content": _answer(
                                 files, attempt.services or (), "Fixed every problem the checks reported.")},
                         ])


def _fix_for(attempt: Attempt, passing: Attempt | None,
             accepted: dict[str, str]) -> tuple[dict[str, str], str] | None:
    if passing is not None and passing.attempt > attempt.attempt:
        return passing.files, "attempt"
    # Only the paths this attempt actually wrote: the accepted tree holds the
    # whole project, and an example that answers with files the task never
    # asked for teaches the model to invent them.
    wanted = {path: accepted[path] for path in attempt.files if path in accepted}
    if wanted and wanted != attempt.files:
        return wanted, "human"
    return None


def write_jsonl(items: Iterable[Repair], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in items:
            handle.write(item.to_json() + "\n")
            count += 1
    return count
