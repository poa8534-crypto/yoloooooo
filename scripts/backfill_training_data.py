"""Recover training examples from the refused branches made before capture existed.

Every engineer run so far kept its last refused attempt on a branch
(docs/GATING.md), with the gate's verbatim output in the commit message. That
is a real mistake, a real compiler error, and -- since the accepted code now
sits on the base branch -- a real fix.

What this cannot recover is the exact prompt: it was never stored. The user
turn is rebuilt from the run's task, and every attempt written here is marked
`prompt_reconstructed` in its task metadata so nothing downstream mistakes it
for a byte-exact replay of what the model saw.

    python scripts/backfill_training_data.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.engineer.capture import Attempt, AttemptRecorder  # noqa: E402
from app.engineer.runs import PREFIX, game_repo  # noqa: E402

FEEDBACK = re.compile(r"^## .+ FAILED$", re.MULTILINE)


def git(args: list[str], cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    if done.returncode != 0:
        raise SystemExit(f"git {' '.join(args[:2])}: {done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout.decode("utf-8", "replace")


def refused_branches(repo: Path) -> list[str]:
    return [line.strip() for line in
            git(["for-each-ref", "--format=%(refname:short)", "refs/heads/engineer"], repo).splitlines()
            if line.strip()]


def changed_luau(repo: Path, base: str, branch: str) -> list[str]:
    return [path for path in git(["diff", "--name-only", base, branch], repo).splitlines()
            if path.endswith(".luau")]


def file_at(repo: Path, ref: str, path: str) -> str | None:
    done = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=repo, capture_output=True, check=False)
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", "replace").replace("\r\n", "\n")


def tasks_by_run(factory) -> dict[str, dict]:
    from app.models import SystemState
    with factory() as db:
        return {row.key[len(PREFIX):]: (row.value_json or {}).get("task") or {}
                for row in db.query(SystemState).all() if row.key.startswith(PREFIX)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="say what would be written, write nothing")
    args = parser.parse_args()

    from app.db import SessionLocal

    settings = get_settings()
    repo = game_repo(settings)
    base = settings.game_base_branch
    tasks = tasks_by_run(SessionLocal)
    recorder = AttemptRecorder(settings.engineer_capture_dir, on_error=print)
    written = 0

    for branch in refused_branches(repo):
        run_id = branch.split("/", 1)[1]
        message = git(["log", "-1", "--format=%B", branch], repo)
        attempt_number = int(match.group(1)) if (match := re.search(r"attempt (\d+)", message)) else 1
        files = {path: content for path in changed_luau(repo, base, branch)
                 if (content := file_at(repo, branch, path)) is not None}
        if not files:
            print(f"{branch}: no .luau changes against {base}, skipped")
            continue
        # The gate's verbatim output starts at the first "## <check> FAILED".
        start = FEEDBACK.search(message)
        feedback = message[start.start():].strip() if start else ""
        if not feedback:
            print(f"{branch}: the commit message holds no gate output, skipped")
            continue
        task = dict(tasks.get(run_id) or {"system": run_id})
        task["prompt_reconstructed"] = True
        attempt = Attempt(
            run_id=run_id, attempt=attempt_number, model="unknown (before capture existed)",
            task=task, prompt=json.dumps(task, indent=2), answer=json.dumps(
                {"files": [{"path": p, "content": c} for p, c in sorted(files.items())],
                 "services": [], "summary": "recovered from the refused branch"}, ensure_ascii=False),
            passed=False, files=files, services=[],
            checks=[{"check": "verify.ps1", "passed": False, "output": feedback}])
        if args.dry_run:
            print(f"{branch}: would record attempt {attempt_number}, "
                  f"{len(files)} file(s), {len(feedback)} characters of gate output")
            continue
        if recorder.record(attempt):
            written += 1
            print(f"{branch}: recorded attempt {attempt_number} ({', '.join(sorted(files))})")

    print(f"\n{written} attempt(s) recovered into {settings.engineer_capture_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
