"""Which code is actually running.

The health page reported a hardcoded application version, so a stale service
and a freshly deployed one were indistinguishable — and after a restart there
was no way to confirm the fix under test was the code answering requests.

Resolved once at import and cached: a running process does not change commit.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


@lru_cache(maxsize=1)
def build_identity() -> dict:
    """Commit, cleanliness and start time, or an honest 'unknown'.

    A checkout with uncommitted changes is reported as dirty rather than as the
    commit it was branched from: the running code is not that commit.
    """
    commit = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    return {
        "commit": commit or "unknown",
        "short_commit": commit[:8] if commit else "unknown",
        "dirty": dirty if commit else None,
        "committed_at": _git("log", "-1", "--format=%cI") or None,
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
        "started_at": datetime.now(UTC).isoformat(),
        # Says plainly when the answer is not knowable, rather than implying a
        # clean checkout at an unknown commit.
        "source": "git" if commit else "unavailable",
    }
