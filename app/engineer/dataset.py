"""Captured attempts, turned into repair examples.

The valuable shape is not "prompt -> good code". It is

    the prompt the model will actually see on a retry -- task, rules, its own
    refused answer, the exact tool output that refused it -- answered with code
    that passes

because everything but the last part comes free from every failed run.

TRAIN/SERVE SYMMETRY is why the example is three messages and not five. The
obvious framing is user -> assistant(broken) -> tool(errors) -> assistant(fixed),
which reads well and is wrong to train on: the usual recipe trains on every
assistant turn, so the model would be taught to produce the broken answer as
well as the fix, and masking one assistant turn but not the other is a footgun
nobody will remember in six months. The loop already puts the refused attempt
and its feedback inside the NEXT prompt (`<previous_attempt>` in
app/engineer/prompts.py), so the honest example is that prompt, answered
correctly. One assistant turn, and it is the only thing worth imitating.

Where a later attempt exists in the same run, its stored prompt is used
verbatim -- it is exactly what the model saw, including the feedback. Only the
final attempt of a run needs its retry prompt synthesised, and those are marked
`prompt: "synthesised"` in the metadata.

A repair is only emitted when the fix is KNOWN GOOD -- a later attempt in the
same run that passed the gate, or the file as it stands on the game
repository's base branch, which is there because it passed. A failing attempt
followed by another failing attempt is not a repair, and training on it would
teach the model its wrong second guess was right.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .capture import Attempt
from .prompts import SYSTEM

RETRY_TEMPLATE = """{prompt}

<previous_attempt number="{attempt}">
Your previous answer was refused. Fix every problem below and return the complete answer again.
{feedback}
</previous_attempt>"""


def _answer(files: dict[str, str], services: Iterable[str], summary: str) -> str:
    return json.dumps({"files": [{"path": path, "content": content} for path, content in sorted(files.items())],
                       "services": sorted(services), "summary": summary}, ensure_ascii=False)


def tool_message(attempt: Attempt) -> str:
    """What the toolchain said, verbatim. This is the whole point of the example."""
    if attempt.refusal:
        return attempt.refusal
    return "\n\n".join(f"## {check['check']} FAILED\n{check.get('output', '').strip()}"
                       for check in attempt.failures()) or "The attempt was refused."


def retry_prompt(attempt: Attempt, following: Attempt | None) -> tuple[str, str]:
    """(the prompt a retry sees, where it came from).

    `following` is the next attempt of the same run, whose stored prompt IS the
    retry prompt -- the loop built it from this attempt's failure. Using it
    means the training example is byte-identical to what the model is sent at
    inference, which no reconstruction can promise.
    """
    if following is not None:
        return following.prompt, "captured"
    return RETRY_TEMPLATE.format(prompt=attempt.prompt, attempt=attempt.attempt,
                                 feedback=tool_message(attempt)), "synthesised"


@dataclass(frozen=True)
class Repair:
    run_id: str
    attempt: int
    model: str
    source: str  # where the fix came from: "attempt" or "human"
    prompt: str  # how the user turn was built: "captured" or "synthesised"
    messages: list[dict]

    def to_json(self) -> str:
        return json.dumps({"messages": self.messages, "metadata": {
            "run_id": self.run_id, "attempt": self.attempt, "model": self.model,
            "fix_source": self.source, "prompt": self.prompt,
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
        for index, attempt in enumerate(run):
            if attempt.passed:
                continue
            fix = _fix_for(attempt, passing, accepted)
            if fix is None:
                continue
            files, source = fix
            prompt, origin = retry_prompt(attempt, run[index + 1] if index + 1 < len(run) else None)
            yield Repair(run_id=run_id, attempt=attempt.attempt, model=attempt.model, source=source,
                         prompt=origin,
                         messages=[
                             {"role": "system", "content": SYSTEM},
                             {"role": "user", "content": prompt},
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
