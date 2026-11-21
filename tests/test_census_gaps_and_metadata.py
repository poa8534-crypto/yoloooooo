"""Gaps are named, rates refuse to straddle them, and every row has its evidence.

Three audit findings from 21 November 2026, each pinned here:

* The PC sleeps most nights, leaving 8-20 hour holes in the census. A rate
  across one compared two different times of day and called it growth.
* Researched games' rows cited the explore-sorts payload, which never held
  them, so 45 % of the census pointed at the wrong bytes.
* No creation date was recorded, so a new game could not be told from an old one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import coverage, market_pulse, pillars
from app.connectors import ConnectorResult
from app.evidence import record_artifact
from app.models import Candidate, GameSnapshot, MarketSample, ResearchRun, SourceArtifact

BASE = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def add_rows(session_factory, universe, offsets_minutes_and_counts):
    with session_factory() as db:
        artifact = record_artifact(
            db, url="https://apis.roblox.com/explore-api/v1/get-sorts",
            retrieval_method="scheduled_market_sample", content_type="application/json",
            payload={"sorts": []}, source_tier="primary", owner="roblox.com")
        for minutes, count in offsets_minutes_and_counts:
            db.add(MarketSample(
                artifact_id=artifact.id, captured_at=BASE + timedelta(minutes=minutes),
                sort_id="top-trending", rank=0, universe_id=str(universe), name="G",
                player_count=count, up_votes=9, down_votes=1, genre="Simulation"))
        db.commit()


def pillar(reading, key):
    return next(entry for entry in reading["pillars"] if entry["key"] == key)


# ---------------------------------------------------------------- gap-aware rates

def test_a_rate_never_straddles_a_night_gap(session_factory):
    # Evening at 1,000; ten hours of nothing; morning at 400. Not a collapse:
    # a different time of day.
    add_rows(session_factory, 1, [(0, 1_000), (30, 1_000), (30 + 600, 400)])
    with session_factory() as db:
        moving = pillar(pillars.pillars_for(db, "1"), "momentum")
    assert moving["state"] == pillars.INSUFFICIENT
    assert "gap" in moving["detail"]


def test_a_rate_uses_the_latest_unbroken_stretch_only(session_factory):
    # Old history, a gap, then two hours rising from 500 to 700.
    add_rows(session_factory, 1, [(0, 9_000), (60, 9_000), (60 + 720, 500),
                                  (60 + 780, 600), (60 + 840, 700)])
    with session_factory() as db:
        moving = pillar(pillars.pillars_for(db, "1"), "momentum")
    assert moving["state"] == pillars.MEASURED
    assert moving["value"] == pytest.approx(100.0), "200 players over 2 hours"


def test_a_rate_looks_back_at_most_a_day(session_factory):
    points = [(minutes, 1_000 + minutes) for minutes in range(0, 48 * 60 + 1, 30)]
    add_rows(session_factory, 1, points)
    with session_factory() as db:
        moving = pillar(pillars.pillars_for(db, "1"), "momentum")
    assert "24.0h" in moving["detail"]


# ---------------------------------------------------------------- coverage

def test_coverage_names_every_gap(session_factory):
    add_rows(session_factory, 1, [(0, 1), (30, 1), (60, 1), (60 + 600, 1), (60 + 630, 1)])
    with session_factory() as db:
        report = coverage.report(db, 30, days=2, now=BASE + timedelta(hours=12))
    assert report["gap_count"] == 1
    assert report["gaps"][0]["hours"] == 10.0
    assert report["hours_in_gaps"] == 10.0
    assert all(0 <= day["coverage"] <= 1 for day in report["daily"])


def test_no_census_is_reported_as_never_sampled(session_factory):
    with session_factory() as db:
        assert coverage.report(db, 30)["state"] == "never_sampled"


# ---------------------------------------------------------------- sampler

class Roblox:
    """Explore sorts plus the games and votes endpoints."""

    def __init__(self, shelf_games, tracked=None):
        self.shelf_games, self.tracked = shelf_games, tracked or {}
        self.games_calls = []

    async def roblox_explore_sorts(self):
        return ConnectorResult("https://apis.roblox.com/explore-api/v1/get-sorts", {
            "sorts": [{"sortId": "top-trending", "games": [
                {"universeId": u, "playerCount": n, "name": f"G{u}", "totalUpVotes": 9,
                 "totalDownVotes": 1, "genreL1": "Simulation"} for u, n in self.shelf_games]}]})

    async def roblox_games(self, ids):
        self.games_calls.append(list(ids))
        return ConnectorResult("https://games.roblox.com/v1/games", {"data": [
            {"id": int(u), "name": f"G{u}", "playing": self.tracked.get(str(u), 50),
             "visits": 1234, "favoritedCount": 56, "created": "2026-10-30T08:00:00.123Z",
             "updated": "2026-11-01T08:00:00Z", "genre_l1": "Simulation",
             "genre_l2": "Tycoon"} for u in ids]})

    async def roblox_votes(self, ids):
        return ConnectorResult("https://games.roblox.com/v1/games/votes", {
            "data": [{"id": int(u), "upVotes": 7, "downVotes": 3} for u in ids]})

    async def close(self):
        return None


@pytest.fixture
def sampler(session_factory, monkeypatch):
    monkeypatch.setattr(market_pulse, "SessionLocal", session_factory)
    return session_factory


def _track(session_factory, universe):
    with session_factory() as db:
        run = ResearchRun(niche="test", status="completed")
        db.add(run)
        db.flush()
        db.add(Candidate(run_id=run.id, external_kind="roblox_universe",
                         external_id=str(universe)))
        db.commit()


@pytest.mark.asyncio
async def test_tracked_rows_cite_the_games_payload_they_came_from(sampler):
    _track(sampler, 77)
    await market_pulse.sample_market(Roblox([(1, 500)], tracked={"77": 321}))
    with sampler() as db:
        tracked = db.scalars(select(MarketSample).where(MarketSample.universe_id == "77")).one()
        artifact = db.get(SourceArtifact, tracked.artifact_id)
        shelf = db.scalars(select(MarketSample).where(MarketSample.universe_id == "1")).one()
        shelf_artifact = db.get(SourceArtifact, shelf.artifact_id)
    assert tracked.player_count == 321
    assert artifact.retrieval_method == "scheduled_tracked_sample"
    assert "games.roblox.com" in artifact.url
    assert shelf_artifact.retrieval_method == "scheduled_market_sample"


@pytest.mark.asyncio
async def test_a_game_seen_for_the_first_time_is_dated_once(sampler):
    roblox = Roblox([(1, 500), (2, 900)])
    await market_pulse.sample_market(roblox)
    await market_pulse.sample_market(roblox)
    with sampler() as db:
        snapshots = list(db.scalars(select(GameSnapshot)))
    assert sorted(s.universe_id for s in snapshots) == ["1", "2"], "dated once, not every sample"
    first = snapshots[0]
    assert first.created == datetime(2026, 10, 30, 8, 0, 0, 123000, tzinfo=UTC).replace(tzinfo=None) \
        or first.created.replace(tzinfo=UTC) == datetime(2026, 10, 30, 8, 0, 0, 123000, tzinfo=UTC)
    assert (first.visits, first.favorites, first.genre_l2) == (1234, 56, "Tycoon")


def test_fields_roblox_did_not_send_stay_empty_not_zero():
    rows = market_pulse.snapshot_rows({"data": [{"id": 5, "name": "x"}, {"name": "no id"}]})
    assert len(rows) == 1
    assert rows[0]["created"] is None and rows[0]["visits"] is None and rows[0]["playing"] is None


@pytest.mark.asyncio
async def test_a_failed_metadata_batch_writes_nothing(sampler):
    class Broken(Roblox):
        async def roblox_games(self, ids):
            raise RuntimeError("down")

    outcome = await market_pulse.snapshot_games(["1", "2"], Broken([]), retry_delays=(0.0,))
    assert outcome == {"games": 0, "batches": 0, "failed_batches": 1}
    with sampler() as db:
        assert list(db.scalars(select(GameSnapshot))) == []


def test_snapshots_are_append_only(session_factory):
    with session_factory() as db:
        artifact = record_artifact(db, url="https://games.roblox.com/v1/games",
                                   retrieval_method="scheduled_game_snapshot",
                                   content_type="application/json", payload={"data": []},
                                   source_tier="primary", owner="roblox.com")
        db.add(GameSnapshot(artifact_id=artifact.id, universe_id="1", visits=10))
        db.commit()
        row = db.scalars(select(GameSnapshot)).one()
        row.visits = 99
        with pytest.raises(ValueError, match="append-only"):
            db.commit()


# ---------------------------------------------------------------- API

@pytest.fixture
def client(session_factory, monkeypatch):
    from app import main as main_module
    from app import success_model

    def override():
        with session_factory() as db:
            yield db

    monkeypatch.setattr(main_module, "SessionLocal", session_factory)
    monkeypatch.setattr(success_model, "_cache",
                        {"census_key": None, "census": None, "backtests": {}, "models": {}})
    main_module.app.dependency_overrides[main_module.get_db] = override
    yield TestClient(main_module.app)
    main_module.app.dependency_overrides.clear()


def test_coverage_endpoint(client, session_factory):
    add_rows(session_factory, 1, [(0, 1), (30, 1)])
    body = client.get("/api/census/coverage").json()
    assert body["state"] == "measured" and body["captures"] == 2


def test_backtest_endpoint_computes_then_reports_without_inventing(client, session_factory):
    from app import success_model

    add_rows(session_factory, 1, [(minutes, 100) for minutes in range(0, 240, 30)])
    success_model.refresh(session_factory)
    body = client.get("/api/success/backtest").json()
    assert set(body["definitions"]) == {d["key"] for d in success_model.definitions()["game"]}
    assert all(result["state"] in {"insufficient_data", "no_skill", "skill"}
               for result in body["definitions"].values())


def test_game_probability_is_absent_without_a_backtest_that_showed_skill(client,
                                                                          session_factory):
    from app import success_model

    add_rows(session_factory, 1, [(minutes, 100) for minutes in range(0, 240, 30)])
    success_model.refresh(session_factory)
    body = client.get("/api/success/game/1").json()
    assert body["probability"] is None
    assert body["reason"]


def test_idea_endpoint_refuses_a_tiny_cohort(client, session_factory):
    from app import success_model

    add_rows(session_factory, 1, [(0, 100), (30, 100)])
    success_model.refresh(session_factory)
    body = client.get("/api/success/idea", params={"genre": "Simulation"}).json()
    assert body["probability"] is None
    assert body["state"] == "insufficient_data"
    assert body["caveats"]


def test_retired_calibration_endpoint_says_so(client, session_factory):
    body = client.get("/api/calibration/outcomes").json()
    assert body["state"] == "retired"
    assert body["replaced_by"] == "/api/success/backtest"
