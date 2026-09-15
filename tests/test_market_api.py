"""The census has to report its own age correctly, or it lies about freshness.

Shelves turn over within a day, so "when was this taken" carries as much
weight as "what did it say". SQLite hands back a naive datetime even for a
timezone-aware column, and a browser reads a naive ISO string as local time --
so a census taken minutes ago renders as hours stale on any machine that is
not on UTC, and the staleness warning fires on fresh data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.evidence import record_artifact
from app.models import MarketSample


@pytest.fixture
def client(session_factory, settings, monkeypatch):
    from app import main as main_module

    def override():
        with session_factory() as db:
            yield db

    main_module.app.dependency_overrides[main_module.get_db] = override
    yield TestClient(main_module.app)
    main_module.app.dependency_overrides.clear()


def census(session_factory, *, at, games):
    with session_factory() as db:
        artifact = record_artifact(
            db, url="https://apis.roblox.com/explore-api/v1/get-sorts",
            retrieval_method="scheduled_market_sample", content_type="application/json",
            payload={"sorts": []}, source_tier="primary", owner="roblox.com")
        for index, (universe, players, genre, sort_id) in enumerate(games):
            db.add(MarketSample(artifact_id=artifact.id, captured_at=at, sort_id=sort_id,
                                rank=index, universe_id=str(universe), name=f"Game {universe}",
                                player_count=players, up_votes=90, down_votes=10, genre=genre))
        db.commit()


def test_the_census_timestamp_carries_an_explicit_offset(client, session_factory):
    """Without one, a browser assumes local time and misreads the age by the
    machine's UTC offset."""
    taken = datetime.now(UTC) - timedelta(minutes=17)
    census(session_factory, at=taken, games=[(1, 5_000, "Simulation", "up-and-coming")])

    stamp = client.get("/api/market/pulse").json()["captured_at"]

    assert datetime.fromisoformat(stamp).tzinfo is not None, "the timestamp is naive"
    age = datetime.now(UTC) - datetime.fromisoformat(stamp)
    assert timedelta(minutes=16) < age < timedelta(minutes=19), (
        f"a 17-minute-old census reported as {age} old"
    )


def test_an_unsampled_market_says_so_rather_than_reporting_an_empty_one(client):
    body = client.get("/api/market/pulse").json()

    assert body["captured_at"] is None
    assert body["rows"] == 0
    assert "No census has been sampled yet" in body["note"]


def test_only_the_latest_census_is_reported(client, session_factory):
    """Mixing two censuses would double-count every game that appears in both
    and make the genre shares meaningless."""
    older = datetime.now(UTC) - timedelta(hours=3)
    census(session_factory, at=older, games=[(1, 100, "Shooter", "top-trending"),
                                             (2, 100, "Shooter", "top-trending")])
    census(session_factory, at=datetime.now(UTC),
           games=[(1, 900, "Simulation", "top-trending")])

    body = client.get("/api/market/pulse").json()

    assert body["rows"] == 1
    assert body["universes"] == 1
    assert body["samples"] == 2, "the count of censuses taken is still both"
    assert [entry["genre"] for entry in body["genres"]] == ["Simulation"]


def test_genre_share_counts_a_game_once_however_many_shelves_carry_it(client,
                                                                     session_factory):
    census(session_factory, at=datetime.now(UTC), games=[
        (1, 100, "Simulation", "top-trending"),
        (1, 100, "Simulation", "up-and-coming"),
        (2, 100, "Shooter", "top-trending"),
    ])

    body = client.get("/api/market/pulse").json()

    assert body["rows"] == 3 and body["universes"] == 2
    shares = {entry["genre"]: entry["share"] for entry in body["genres"]}
    assert shares == {"Simulation": pytest.approx(0.5), "Shooter": pytest.approx(0.5)}


def test_the_rising_shelf_is_reported_in_roblox_rank_order(client, session_factory):
    census(session_factory, at=datetime.now(UTC), games=[
        (7, 3_000, "Simulation", "up-and-coming"),
        (8, 9_000, "Simulation", "up-and-coming"),
        (9, 1_000, "Simulation", "top-trending"),
    ])

    rising = client.get("/api/market/pulse").json()["rising"]

    assert [entry["universe_id"] for entry in rising] == ["7", "8"], (
        "rank order is Roblox's judgement and must not be re-sorted by player count"
    )


def test_the_pulse_never_carries_a_combined_score(client, session_factory):
    census(session_factory, at=datetime.now(UTC), games=[(1, 100, "Simulation", "up-and-coming")])

    body = client.get("/api/market/pulse").json()

    assert not {"score", "total", "overall", "opportunity", "probability"} & set(body)


def test_one_games_pillars_are_reachable_and_abstain_without_a_series(client,
                                                                     session_factory):
    census(session_factory, at=datetime.now(UTC), games=[(42, 5_000, "Simulation", "up-and-coming")])

    body = client.get("/api/market/game/42").json()
    readings = {entry["key"]: entry for entry in body["pillars"]}

    assert body["universe_id"] == "42"
    assert readings["demand"]["value"] == 5_000
    assert readings["momentum"]["value"] is None
    assert readings["momentum"]["state"] == "insufficient_evidence"


def test_an_unknown_game_abstains_rather_than_failing(client):
    body = client.get("/api/market/game/does-not-exist").json()

    assert body["measured"] == 0
    assert all(entry["value"] is None for entry in body["pillars"])
