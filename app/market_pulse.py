"""Model 1: sample what Roblox itself says is being played, on a schedule.

Every rate-of-change quantity the opportunity model needs -- CCU velocity,
CCU acceleration, trend strength, "grew in the last 24 hours", genre
saturation drift -- is a derivative. A derivative needs two observations of
the same game at two different times. Before this module the ledger held one
observation per game, taken when a run first found it, and nothing ever
measured it again: 140 candidates, 140 player counts, zero repeats. Any
velocity computed from that would have been invented.

So this samples `explore-api/v1/get-sorts`, Roblox's own front page. It is the
only source here that reports the market rather than the games a query
happened to surface, and it carries, per game, the live player count, the up
and down vote totals, and the genre. The vote totals are worth noting: they
are first-party integers, which is why no sentiment classifier is needed to
know how a game is received.

A sample is a census, not a claim about any one game, so rows land in
`market_samples` rather than in `facts`. They are append-only and each points
at the artifact it was read from.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from .connectors import ConnectorError, Connectors
from .db import SessionLocal
from .evidence import record_artifact
from .models import MarketSample, SystemState

log = logging.getLogger("venture-agents")

# Shelves worth keeping. Roblox also returns layout-only entries such as
# `filters_v5`, which carry no games at all.
SHELVES = ("top-trending", "up-and-coming", "top-playing-now",
           "fun-with-friends", "top-revisited", "top-earning")

STATE_KEY = "last_market_sample"


def _rows(payload: dict) -> list[dict]:
    """Flatten the shelves into rows, dropping anything unreadable.

    A malformed game is skipped rather than defaulted: a player count that
    parsed as zero because a field was missing would be indistinguishable
    from a game nobody is playing, and would drag every average down.
    """
    rows: list[dict] = []
    for shelf in payload.get("sorts") or []:
        sort_id = str(shelf.get("sortId") or "")
        if sort_id not in SHELVES:
            continue
        seen: set[str] = set()
        rank = 0
        for game in shelf.get("games") or []:
            universe = game.get("universeId")
            if universe is None or game.get("playerCount") is None:
                continue
            universe = str(universe)
            if universe in seen:
                continue  # one shelf listing a game twice is still one placement
            try:
                row = {
                    "sort_id": sort_id, "rank": rank, "universe_id": universe,
                    "name": str(game.get("name") or ""),
                    "player_count": int(game["playerCount"]),
                    "up_votes": int(game.get("totalUpVotes") or 0),
                    "down_votes": int(game.get("totalDownVotes") or 0),
                    "genre": str(game.get("genreL1") or ""),
                    "sponsored": bool(game.get("isSponsored")),
                }
            except (TypeError, ValueError):
                continue
            seen.add(universe)
            rank += 1
            rows.append(row)
    return rows


async def sample_market(connectors: Connectors | None = None) -> dict:
    """Record one census. Returns what was written, never raises upward.

    The scheduler runs this unattended, so a bad sample must not stop the
    next one. An empty result is reported as an empty result; it is never
    silently treated as a market where nothing is being played.
    """
    own = connectors is None
    if own:
        from .quotas import QuotaMeter
        connectors = Connectors(quota_meter=QuotaMeter(SessionLocal))
    outcome: dict = {"rows": 0, "shelves": 0, "universes": 0, "error": None}
    try:
        result = await connectors.roblox_explore_sorts()
        rows = _rows(result.payload)
        if not rows:
            outcome["error"] = "the sample contained no readable shelves"
        with SessionLocal() as db:
            artifact = record_artifact(
                db, url=result.url, retrieval_method="scheduled_market_sample",
                content_type=result.content_type, payload=result.payload,
                source_tier="primary", owner="roblox.com",
            )
            captured = datetime.now(UTC)
            for row in rows:
                db.add(MarketSample(artifact_id=artifact.id, captured_at=captured, **row))
            state = db.get(SystemState, STATE_KEY)
            value = {"captured_at": captured.isoformat(), "rows": len(rows),
                     "shelves": len({row["sort_id"] for row in rows}),
                     "artifact_id": artifact.id}
            if state is None:
                db.add(SystemState(key=STATE_KEY, value_json=value))
            else:
                state.value_json, state.updated_at = value, captured
            db.commit()
        outcome |= {"rows": len(rows), "shelves": len({row["sort_id"] for row in rows}),
                    "universes": len({row["universe_id"] for row in rows})}
    except ConnectorError as exc:
        outcome["error"] = str(exc)
    except Exception as exc:  # a bad sample must not stop the next one
        outcome["error"] = f"{type(exc).__name__}: {exc}"
        log.warning("market sample failed: %s", outcome["error"])
    finally:
        if own:
            await connectors.close()
    return outcome
