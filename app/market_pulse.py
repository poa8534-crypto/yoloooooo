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

import asyncio
import logging
from datetime import UTC, datetime

from .connectors import ConnectorError, Connectors
from .db import SessionLocal
from .evidence import record_artifact
from sqlalchemy import select

from .models import GameSnapshot, MarketSample, SystemState

log = logging.getLogger("venture-agents")

# Shelves worth keeping. Roblox also returns layout-only entries such as
# `filters_v5`, which carry no games at all.
SHELVES = ("top-trending", "up-and-coming", "top-playing-now",
           "fun-with-friends", "top-revisited", "top-earning")

STATE_KEY = "last_market_sample"

# Games a run is researching, measured on the same clock as the census. They
# are almost never on Roblox's shelves -- 9 of 168 here -- so without this the
# pillars abstain on precisely the games being studied and the opportunity
# model has nothing to compute on. Kept out of SHELVES so it never counts as
# shelf visibility.
TRACKED_SORT = "tracked"


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


async def _tracked_rows(connectors, universes: list[str]) -> tuple[list[dict], list[dict]]:
    """One row per researched game, shaped exactly like a census row.

    Two first-party calls per batch: the games endpoint for the live player
    count and genre, the votes endpoint for reception. A batch that fails is
    skipped rather than defaulted -- a missing count is not a count of zero.

    Returns the rows and the payloads they were read from. The payloads are
    stored as their own artifact: these rows used to cite the explore-sorts
    payload, which never contained them, so nearly half the census could not
    be traced back to the bytes it came from.
    """
    rows: list[dict] = []
    payloads: list[dict] = []
    for start in range(0, len(universes), 50):
        batch = universes[start:start + 50]
        try:
            games = await connectors.roblox_games(batch)
        except Exception:
            continue
        votes: dict[str, tuple[int, int]] = {}
        votes_payload = None
        try:
            answer = await connectors.roblox_votes(batch)
            votes_payload = answer.payload
            for entry in answer.payload.get("data") or []:
                votes[str(entry.get("id"))] = (int(entry.get("upVotes") or 0),
                                               int(entry.get("downVotes") or 0))
        except Exception:
            votes = {}  # reception abstains; the player count is still worth having
        payloads.append({"games_url": games.url, "games": games.payload,
                         "votes": votes_payload})
        for item in games.payload.get("data") or []:
            universe = str(item.get("id") or "")
            if not universe or item.get("playing") is None:
                continue
            up, down = votes.get(universe, (0, 0))
            try:
                rows.append({"sort_id": TRACKED_SORT, "rank": len(rows),
                             "universe_id": universe, "name": str(item.get("name") or ""),
                             "player_count": int(item["playing"]), "up_votes": up,
                             "down_votes": down,
                             "genre": str(item.get("genre_l1") or ""), "sponsored": False})
            except (TypeError, ValueError):
                continue
    return rows, payloads


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _int_or_none(value) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def snapshot_rows(payload: dict) -> list[dict]:
    """GameSnapshot fields from one `games/v1/games` payload.

    A field Roblox did not send stays None. A game without an ID is dropped.
    """
    rows = []
    for item in payload.get("data") or []:
        universe = str(item.get("id") or "")
        if not universe:
            continue
        rows.append({
            "universe_id": universe, "name": str(item.get("name") or ""),
            "created": _parse_time(item.get("created")),
            "updated": _parse_time(item.get("updated")),
            "playing": _int_or_none(item.get("playing")),
            "visits": _int_or_none(item.get("visits")),
            "favorites": _int_or_none(item.get("favoritedCount")),
            "genre_l1": str(item.get("genre_l1") or ""),
            "genre_l2": str(item.get("genre_l2") or ""),
        })
    return rows


async def snapshot_games(universes: list[str], connectors: Connectors | None = None,
                         retrieval_method: str = "scheduled_game_snapshot",
                         pause_seconds: float = 1.5,
                         retry_delays: tuple[float, ...] = (15.0, 45.0)) -> dict:
    """Record what the games endpoint says about each universe, batched by 50.

    Each batch is stored as its own artifact and its rows point at it. A
    failed batch is retried after `retry_delays`, then counted and skipped;
    it never writes defaults. Batches are paced by `pause_seconds` because
    Roblox answers 429 to a burst of 30 back-to-back requests.
    """
    own = connectors is None
    if own:
        from .quotas import QuotaMeter
        connectors = Connectors(quota_meter=QuotaMeter(SessionLocal))
    outcome = {"games": 0, "batches": 0, "failed_batches": 0}
    try:
        for start in range(0, len(universes), 50):
            batch = list(dict.fromkeys(universes[start:start + 50]))
            if start and pause_seconds:
                await asyncio.sleep(pause_seconds)
            result = None
            for attempt, delay in enumerate((0.0, *retry_delays)):
                if delay:
                    await asyncio.sleep(delay)
                try:
                    result = await connectors.roblox_games(batch)
                    break
                except Exception as exc:
                    log.warning("game snapshot batch failed (attempt %d): %s", attempt + 1, exc)
            if result is None:
                outcome["failed_batches"] += 1
                continue
            rows = snapshot_rows(result.payload)
            with SessionLocal() as db:
                artifact = record_artifact(
                    db, url=result.url, retrieval_method=retrieval_method,
                    content_type=result.content_type, payload=result.payload,
                    source_tier="primary", owner="roblox.com")
                captured = datetime.now(UTC)
                for row in rows:
                    db.add(GameSnapshot(artifact_id=artifact.id, captured_at=captured, **row))
                db.commit()
            outcome["games"] += len(rows)
            outcome["batches"] += 1
    finally:
        if own:
            await connectors.close()
    return outcome


def known_universes(db) -> list[str]:
    """Every universe the census has ever recorded."""
    return sorted(set(db.scalars(select(MarketSample.universe_id).distinct())))


def universes_without_snapshot(db, universes) -> list[str]:
    have = set(db.scalars(select(GameSnapshot.universe_id).distinct()))
    return [universe for universe in dict.fromkeys(universes) if universe not in have]


async def snapshot_all_games() -> dict:
    """Daily: metadata for every universe the census knows. Scheduled job."""
    with SessionLocal() as db:
        universes = known_universes(db)
    return await snapshot_games(universes)


def _tracked_universes(db) -> list[str]:
    from .models import Candidate
    return sorted({row for row in db.scalars(select(Candidate.external_id)) if row})


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
        with SessionLocal() as db:
            tracked = _tracked_universes(db)
        tracked_rows, tracked_payloads = await _tracked_rows(connectors, tracked)
        shelf_rows = rows
        rows = shelf_rows + tracked_rows
        if not rows:
            outcome["error"] = "the sample contained no readable shelves"
        with SessionLocal() as db:
            artifact = record_artifact(
                db, url=result.url, retrieval_method="scheduled_market_sample",
                content_type=result.content_type, payload=result.payload,
                source_tier="primary", owner="roblox.com",
            )
            tracked_artifact = None
            if tracked_rows:
                tracked_artifact = record_artifact(
                    db, url="https://games.roblox.com/v1/games",
                    retrieval_method="scheduled_tracked_sample",
                    content_type="application/json",
                    payload={"batches": tracked_payloads},
                    source_tier="primary", owner="roblox.com",
                )
            captured = datetime.now(UTC)
            for row in shelf_rows:
                db.add(MarketSample(artifact_id=artifact.id, captured_at=captured, **row))
            for row in tracked_rows:
                db.add(MarketSample(artifact_id=tracked_artifact.id, captured_at=captured,
                                    **row))
            state = db.get(SystemState, STATE_KEY)
            value = {"captured_at": captured.isoformat(), "rows": len(rows),
                     "shelves": len({row["sort_id"] for row in rows}),
                     "artifact_id": artifact.id}
            if state is None:
                db.add(SystemState(key=STATE_KEY, value_json=value))
            else:
                state.value_json, state.updated_at = value, captured
            db.commit()
            # A game seen for the first time is dated now, not at the next
            # daily snapshot: a breakout that lasts a day would otherwise never
            # get a creation date.
            fresh = universes_without_snapshot(db, [row["universe_id"] for row in rows])
        if fresh:
            try:
                # No retries here: the daily job catches anything missed, and the
                # census must not wait on metadata.
                dated = await snapshot_games(fresh, connectors, retry_delays=())
                outcome["new_game_snapshots"] = dated["games"]
            except Exception as exc:  # metadata must never cost the census
                log.warning("new-game snapshot failed: %s", exc)
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
