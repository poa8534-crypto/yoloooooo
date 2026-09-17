"""Turn captured attempts into a repair dataset.

    python scripts/build_training_set.py [--out data/training/repairs.jsonl]

The fix for a failed attempt is a later attempt in the same run that passed the
gate, or -- so far, always -- the file as it stands on the game repository's
base branch, which is there because it passed. Attempts with no known-good fix
are counted and skipped, never guessed at (app/engineer/dataset.py).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.engineer.capture import load_attempts  # noqa: E402
from app.engineer.dataset import repairs, write_jsonl  # noqa: E402
from app.engineer.runs import game_repo  # noqa: E402
from app.engineer.workspace import READABLE_ROOTS  # noqa: E402


def accepted_files(repo: Path, ref: str) -> dict[str, str]:
    """Every .luau file on the base branch: the code that passed."""
    listing = subprocess.run(["git", "ls-tree", "-r", "--name-only", ref], cwd=repo,
                             capture_output=True, check=False)
    files: dict[str, str] = {}
    for path in listing.stdout.decode("utf-8", "replace").splitlines():
        if not path.endswith(".luau") or not any(path.startswith(root + "/") for root in READABLE_ROOTS):
            continue
        blob = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=repo, capture_output=True, check=False)
        if blob.returncode == 0:
            files[path] = blob.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
    return files


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=settings.engineer_capture_dir.parent / "repairs.jsonl")
    args = parser.parse_args()

    attempts = load_attempts(settings.engineer_capture_dir)
    accepted = accepted_files(game_repo(settings), settings.game_base_branch)
    print(f"{len(attempts)} captured attempt(s); {len(accepted)} accepted file(s) on "
          f"{settings.game_base_branch}")

    examples = list(repairs(attempts, accepted))
    count = write_jsonl(examples, args.out)
    sources = Counter(example.source for example in examples)
    unpaired = len([a for a in attempts if not a.passed]) - count

    print(f"{count} repair example(s) written to {args.out}")
    for source, number in sorted(sources.items()):
        print(f"  fix from {source}: {number}")
    if unpaired:
        print(f"  {unpaired} failed attempt(s) had no known-good fix and were skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
