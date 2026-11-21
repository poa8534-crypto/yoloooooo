"""One-time: record creation dates and metadata for every game the census has seen.

The census started on 15 September 2026 without creation dates. This fetches
`games.roblox.com/v1/games` (first-party, batched by 50) for every known
universe that has no snapshot yet, through the same code path the daily job
uses, so each row points at the stored payload it was read from.

Safe to run repeatedly: games that already have a snapshot are skipped.

    .venv\\Scripts\\python.exe scripts\\backfill_game_metadata.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.market_pulse import (  # noqa: E402
    known_universes,
    snapshot_games,
    universes_without_snapshot,
)


async def main() -> int:
    init_db()
    with SessionLocal() as db:
        missing = universes_without_snapshot(db, known_universes(db))
    print(f"{len(missing)} universe(s) without a snapshot")
    if not missing:
        return 0
    outcome = await snapshot_games(missing, retrieval_method="backfill_game_snapshot")
    print(outcome)
    return 0 if outcome["failed_batches"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
