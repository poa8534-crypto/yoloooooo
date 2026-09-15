"""Erase the evidence ledger and start collecting from scratch.

The ledger is append-only and enforced by database triggers, so wiping it is
deliberately awkward: the triggers have to be dropped, the rows deleted, and
the triggers reinstalled. That awkwardness is the point, and this script is the
only sanctioned way through it.

Two things are kept on purpose:

* **Today's API quota reservations.** Those record real spend against real
  remote allowances. Zeroing them would let the next run burn past a limit the
  provider is still counting, so they survive the reset.
* **A backup of the database file**, written beside it before anything is
  touched. Captured measurements cannot be re-fetched for past dates; if the
  wipe turns out to be a mistake there is no other way back.

Run it with the service stopped:

    schtasks /End /TN "Roblox Venture Agents"
    .venv\\Scripts\\python.exe -m scripts.reset_ledger --yes
    schtasks /Run /TN "Roblox Venture Agents"
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import engine  # noqa: E402
from app.migrations import drop_append_only_triggers, install_append_only_triggers  # noqa: E402

# Every table the ledger owns. Ordered children first so the deletes stay valid
# even where foreign keys are enforced.
TABLES = (
    "association_overrides",
    "association_reviews",
    "association_records",
    "match_candidates",
    "match_subjects",
    "matcher_versions",
    "audit_activity_events",
    "scout_audit_runs",
    "audit_records",
    "research_reports",
    "research_checkpoints",
    "decision_overrides",
    "decision_records",
    "confidence_records",
    "score_records",
    "inferences",
    "proposals",
    "facts",
    "observations",
    # Ahead of source_artifacts: every census row points at the artifact the
    # sample was read from.
    "market_samples",
    "tracked_videos",
    "candidates",
    "research_runs",
    "source_artifacts",
    "security_redactions",
)

# Quota rows are the exception: they mirror spend the provider is still
# counting, so they outlive the data they were spent on.
KEEP_SYSTEM_STATE_PREFIXES = ("quota:",)


def backup_database(database: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = database.with_name(f"{database.stem}.before-reset-{stamp}.db")
    shutil.copy2(database, target)
    return target


def reset(*, drop_artifacts: bool = True, target=None, artifact_dir: Path | None = None) -> dict[str, int]:
    """Erase every ledger table, then put the append-only guards back.

    `target` and `artifact_dir` exist so the destructive path can be exercised
    against a throwaway ledger. Something this irreversible should not be
    reachable only by pointing it at the real database.
    """
    if target is not None and artifact_dir is None:
        # Refuse to pair a caller-supplied ledger with the real payload
        # directory. A test that passed its own throwaway engine still fell
        # back to the live artifact_dir here, so an unguarded code path could
        # delete captured payloads that belonged to a completely different
        # database. Name the directory or keep the files.
        raise ValueError(
            "reset(target=...) also requires artifact_dir=...; "
            "refusing to delete the live payload directory for another ledger"
        )
    target = target if target is not None else engine
    artifact_dir = artifact_dir if artifact_dir is not None else get_settings().artifact_dir
    removed: dict[str, int] = {}
    drop_append_only_triggers(target)
    try:
        with target.begin() as connection:
            existing = {
                row[0] for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            }
            for table in TABLES:
                if table not in existing:
                    continue
                count = connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
                connection.execute(text(f"DELETE FROM {table}"))
                removed[table] = count
            if "system_state" in existing:
                kept = " OR ".join(f"key LIKE '{prefix}%'" for prefix in KEEP_SYSTEM_STATE_PREFIXES)
                count = connection.execute(
                    text(f"SELECT COUNT(*) FROM system_state WHERE NOT ({kept})")
                ).scalar() or 0
                connection.execute(text(f"DELETE FROM system_state WHERE NOT ({kept})"))
                removed["system_state"] = count
    finally:
        # Reinstalling these is not optional. Leaving them off would quietly
        # turn an append-only ledger into an ordinary mutable table.
        install_append_only_triggers(target)

    if drop_artifacts and artifact_dir.exists():
        files = [path for path in artifact_dir.iterdir() if path.is_file()]
        for path in files:
            path.unlink()
        removed["artifact_files"] = len(files)

    with target.begin() as connection:
        connection.execute(text("VACUUM"))
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--keep-artifacts", action="store_true",
                        help="leave captured payload files on disk")
    parser.add_argument("--no-backup", action="store_true",
                        help="do not copy the database first (not recommended)")
    args = parser.parse_args()

    database = Path(get_settings().database_url.removeprefix("sqlite:///"))
    if not args.yes:
        print(f"This permanently erases the evidence ledger at {database}.")
        print("Captured measurements cannot be re-fetched for past dates.")
        if input("Type 'erase' to continue: ").strip() != "erase":
            print("Cancelled; nothing was changed.")
            return 1

    if not args.no_backup and database.exists():
        print(f"Backup written to {backup_database(database)}")

    removed = reset(drop_artifacts=not args.keep_artifacts)
    for name, count in sorted(removed.items()):
        if count:
            print(f"  removed {count:>6} from {name}")
    print("Ledger reset. Quota reservations for today were kept.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
