"""Every attempt the engineer makes, kept on disk as training material.

A refused attempt is the most valuable thing a run produces. It is a complete
worked example of the mistake this model actually makes -- the prompt it was
given, the code it wrote, and the exact compiler output that refused it -- and
it cannot be bought or downloaded, because it is specific to this model, this
toolchain and this project's rules.

Only the last attempt of a run reaches a branch (app/engineer/loop.py), so
without this the other five are generated, judged and thrown away. A run that
fails six times produces six examples here.

What is NOT kept: nothing is written unless a directory is configured, the
model's text is passed through `redact` first, and a captured attempt never
influences a run. This is a recorder, not a participant.

The file is the interchange format, not the training format: turning these
into chat examples is `scripts/build_training_set.py`, so the decision about
how to frame a repair can change without re-running the engineer.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..security import redact

SCHEMA = 1
_SAFE_RUN_ID = str.maketrans({c: "-" for c in '<>:"/\\|?*'})


@dataclass
class Attempt:
    """One model answer and the verdict on it."""

    run_id: str
    attempt: int
    model: str
    task: dict
    prompt: str
    answer: str
    passed: bool
    # Empty when the answer never parsed: there were no files to write.
    files: dict[str, str] = field(default_factory=dict)
    services: list[str] = field(default_factory=list)
    # Why it was refused before the gate ever ran -- bad JSON, an unsafe path,
    # a service that does not exist. A different kind of mistake from failing a
    # check, and worth training on separately.
    refusal: str | None = None
    # The gate's own verdict, one entry per check. Empty when `refusal` is set.
    checks: list[dict] = field(default_factory=list)
    recorded_at: str = ""
    schema: int = SCHEMA

    def failures(self) -> list[dict]:
        return [check for check in self.checks if not check.get("passed")]


class AttemptRecorder:
    """Writes attempts under `root/<run-id>/attempt-<n>.json`.

    Recording must never be able to fail a run, so every error here is
    swallowed after being reported through `on_error`. A full disk is a reason
    to lose a training example, not a reason to lose six hours of generation.
    """

    def __init__(self, root: Path, *, on_error=None):
        self.root = root
        self.on_error = on_error or (lambda message: None)

    def record(self, attempt: Attempt) -> Path | None:
        attempt.recorded_at = attempt.recorded_at or datetime.now(UTC).isoformat()
        attempt.prompt = redact(attempt.prompt)
        attempt.answer = redact(attempt.answer)
        if attempt.refusal:
            attempt.refusal = redact(attempt.refusal)
        directory = self.root / (attempt.run_id.translate(_SAFE_RUN_ID) or "unnamed")
        path = directory / f"attempt-{attempt.attempt:02d}.json"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(attempt), indent=2, ensure_ascii=False),
                            encoding="utf-8", newline="\n")
        except OSError as exc:
            self.on_error(f"attempt {attempt.attempt} was not recorded: {exc}")
            return None
        return path


def load_attempts(root: Path) -> list[Attempt]:
    """Every recorded attempt, oldest run first, in attempt order.

    A file this cannot read is skipped rather than raising: the directory is
    append-only in practice and one bad file should not cost the rest.
    """
    attempts: list[Attempt] = []
    if not root.is_dir():
        return attempts
    for path in sorted(root.rglob("attempt-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            continue
        known = {f for f in Attempt.__dataclass_fields__}
        try:
            attempts.append(Attempt(**{k: v for k, v in data.items() if k in known}))
        except TypeError:
            continue
    return attempts
