"""The census has to be trustworthy before anything is derived from it.

Every rate-of-change quantity the opportunity model will compute is a
derivative of these rows. A single wrong player count does not stay a wrong
player count: it becomes a velocity, then an acceleration, then a ranking. So
the sampler's job is to record exactly what Roblox said and to drop, loudly,
anything it could not read -- never to fill a gap with a plausible number.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app import market_pulse
from app.connectors import ConnectorError, ConnectorResult
from app.models import MarketSample, SystemState

URL = "https://apis.roblox.com/explore-api/v1/get-sorts"


def shelf(sort_id, games):
    return {"sortId": sort_id, "sortDisplayName": sort_id, "games": games}


def game(universe, players, name="Game", up=10, down=1, genre="Simulation", **extra):
    return {"universeId": universe, "playerCount": players, "name": name,
            "totalUpVotes": up, "totalDownVotes": down, "genreL1": genre,
            "isSponsored": False, **extra}


class Charts:
    def __init__(self, payload=None, fail=None):
        self.payload, self.fail, self.calls = payload, fail, 0

    async def roblox_explore_sorts(self):
        self.calls += 1
        if self.fail:
            raise self.fail
        return ConnectorResult(URL, self.payload)

    async def close(self):
        return None


@pytest.fixture
def sample(session_factory, monkeypatch):
    monkeypatch.setattr(market_pulse, "SessionLocal", session_factory)

    async def run(payload=None, fail=None):
        return await market_pulse.sample_market(Charts(payload, fail))
    return run


def rows(session_factory):
    with session_factory() as db:
        return list(db.scalars(select(MarketSample).order_by(MarketSample.sort_id,
                                                            MarketSample.rank)))


@pytest.mark.asyncio
async def test_a_game_missing_its_player_count_is_dropped_not_zeroed(sample, session_factory):
    """A count defaulted to zero is indistinguishable from a game nobody is
    playing, and would drag every genre average it lands in downwards."""
    outcome = await sample({"sorts": [shelf("up-and-coming", [
        game(1, 5_000), {"universeId": 2, "name": "No count"}, game(3, 4_000)])]})

    assert outcome["rows"] == 2
    assert [row.universe_id for row in rows(session_factory)] == ["1", "3"]


@pytest.mark.asyncio
async def test_rank_counts_kept_games_and_leaves_no_gap(sample, session_factory):
    await sample({"sorts": [shelf("up-and-coming", [
        game(1, 9), {"universeId": 2}, game(3, 7)])]})

    assert [row.rank for row in rows(session_factory)] == [0, 1]


@pytest.mark.asyncio
async def test_layout_shelves_carrying_no_games_are_ignored(sample, session_factory):
    """Roblox returns entries such as `filters_v5` that are page furniture."""
    outcome = await sample({"sorts": [shelf("filters_v5", []),
                                      shelf("unknown-future-shelf", [game(9, 100)]),
                                      shelf("top-trending", [game(1, 100)])]})

    assert outcome["shelves"] == 1
    assert [row.universe_id for row in rows(session_factory)] == ["1"]


@pytest.mark.asyncio
async def test_one_game_on_two_shelves_is_two_placements(sample, session_factory):
    """Appearing in both Top Trending and Up-and-Coming is a real, separate
    fact about each shelf, not a duplicate to collapse."""
    await sample({"sorts": [shelf("top-trending", [game(1, 100)]),
                            shelf("up-and-coming", [game(1, 100)])]})

    stored = rows(session_factory)
    assert len(stored) == 2
    assert {row.sort_id for row in stored} == {"top-trending", "up-and-coming"}


@pytest.mark.asyncio
async def test_one_shelf_listing_a_game_twice_is_one_placement(sample, session_factory):
    await sample({"sorts": [shelf("top-trending", [game(1, 100), game(1, 100), game(2, 50)])]})

    assert [row.universe_id for row in rows(session_factory)] == ["1", "2"]


@pytest.mark.asyncio
async def test_votes_and_genre_are_recorded_as_given(sample, session_factory):
    """These are first-party integers. They are the reason no sentiment
    classifier is needed to know how a game is received."""
    await sample({"sorts": [shelf("top-trending",
                                  [game(1, 100, up=27_101, down=917, genre="Action")])]})

    row = rows(session_factory)[0]
    assert (row.up_votes, row.down_votes, row.genre) == (27_101, 917, "Action")


@pytest.mark.asyncio
async def test_a_failed_sample_writes_nothing_and_does_not_raise(sample, session_factory):
    """The scheduler runs this unattended; one bad sample must not stop the
    next one."""
    outcome = await sample(fail=ConnectorError("charts unavailable"))

    assert outcome["error"] == "charts unavailable"
    assert outcome["rows"] == 0
    assert rows(session_factory) == []


@pytest.mark.asyncio
async def test_an_unreadable_payload_is_reported_not_recorded_as_an_empty_market(
        sample, session_factory):
    """Zero rows and "the market is empty" must never look the same."""
    outcome = await sample({"sorts": []})

    assert outcome["error"], "an unreadable sample reported success"
    assert outcome["rows"] == 0


@pytest.mark.asyncio
async def test_every_row_points_at_the_artifact_it_was_read_from(sample, session_factory):
    from app.models import SourceArtifact

    await sample({"sorts": [shelf("top-trending", [game(1, 100), game(2, 50)])]})

    with session_factory() as db:
        artifacts = {artifact.id for artifact in db.scalars(select(SourceArtifact))}
    assert artifacts, "the raw payload was not captured"
    assert all(row.artifact_id in artifacts for row in rows(session_factory))


@pytest.mark.asyncio
async def test_a_sample_is_append_only(sample, session_factory):
    await sample({"sorts": [shelf("top-trending", [game(1, 100)])]})

    with session_factory() as db:
        row = db.scalars(select(MarketSample)).one()
        row.player_count = 999_999
        with pytest.raises(ValueError, match="append-only"):
            db.commit()


@pytest.mark.asyncio
async def test_the_last_sample_is_recorded_so_staleness_is_visible(sample, session_factory):
    await sample({"sorts": [shelf("top-trending", [game(1, 100)])]})

    with session_factory() as db:
        state = db.get(SystemState, market_pulse.STATE_KEY)
    assert state and state.value_json["rows"] == 1
    assert state.value_json["captured_at"]


@pytest.mark.asyncio
async def test_two_samples_of_the_same_game_are_two_observations(sample, session_factory):
    """The whole point. One observation supports no rate of change at all."""
    await sample({"sorts": [shelf("top-trending", [game(1, 100)])]})
    await sample({"sorts": [shelf("top-trending", [game(1, 140)])]})

    counts = [row.player_count for row in rows(session_factory) if row.universe_id == "1"]
    assert sorted(counts) == [100, 140]
