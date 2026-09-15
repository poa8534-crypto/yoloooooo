"""Pillars measure, abstain, and never quietly become a score.

The failure this guards against is not a wrong number; it is a confident one.
A momentum of 0.0 that actually means "never measured", or a rate extrapolated
from two samples taken seconds apart, reads exactly like a real measurement
and would be carried downstream into a ranking with nothing to flag it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app import pillars
from app.evidence import record_artifact
from app.models import MarketSample

BASE = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def ledger(session_factory):
    def add(universe, offsets_and_counts, *, genre="Simulation", up=90, down=10,
            sort_id="top-trending"):
        with session_factory() as db:
            artifact = record_artifact(
                db, url="https://apis.roblox.com/explore-api/v1/get-sorts",
                retrieval_method="scheduled_market_sample", content_type="application/json",
                payload={"sorts": []}, source_tier="primary", owner="roblox.com")
            for minutes, count in offsets_and_counts:
                db.add(MarketSample(
                    artifact_id=artifact.id, captured_at=BASE + timedelta(minutes=minutes),
                    sort_id=sort_id, rank=0, universe_id=str(universe), name=f"Game {universe}",
                    player_count=count, up_votes=up, down_votes=down, genre=genre))
            db.commit()
    return add


def pillar(result, key):
    return next(p for p in result["pillars"] if p["key"] == key)


def test_one_observation_cannot_produce_a_rate(ledger, session_factory):
    ledger(1, [(0, 5_000)])
    with session_factory() as db:
        found = pillars.pillars_for(db, "1")

    moving = pillar(found, "momentum")
    assert moving["state"] == pillars.INSUFFICIENT
    assert moving["value"] is None, "an unmeasured rate must not be reported as a number"
    assert "two observations" in moving["detail"]


def test_an_unmeasured_pillar_is_not_a_zero(ledger, session_factory):
    """0.0 means measured and flat. The two must never look the same."""
    ledger(1, [(0, 5_000), (60, 5_000)])
    with session_factory() as db:
        flat = pillar(pillars.pillars_for(db, "1"), "momentum")
        missing = pillar(pillars.pillars_for(db, "does-not-exist"), "momentum")

    assert (flat["state"], flat["value"]) == (pillars.MEASURED, 0.0)
    assert (missing["state"], missing["value"]) == (pillars.INSUFFICIENT, None)


def test_two_samples_seconds_apart_do_not_become_a_spectacular_rate(ledger, session_factory):
    """Extrapolating a 200 player difference over 30 seconds gives 24,000 per
    hour, which is noise wearing a velocity's clothes."""
    ledger(1, [(0, 5_000), (0.5, 5_200)])
    with session_factory() as db:
        moving = pillar(pillars.pillars_for(db, "1"), "momentum")

    assert moving["state"] == pillars.INSUFFICIENT
    assert "noise" in moving["detail"]


def test_a_real_interval_produces_the_arithmetic_rate(ledger, session_factory):
    ledger(1, [(0, 5_000), (120, 7_000)])
    with session_factory() as db:
        moving = pillar(pillars.pillars_for(db, "1"), "momentum")

    assert moving["state"] == pillars.MEASURED
    assert moving["value"] == pytest.approx(1_000.0), "2,000 players over 2 hours"


def test_a_falling_game_reports_a_negative_rate(ledger, session_factory):
    ledger(1, [(0, 9_000), (60, 6_000)])
    with session_factory() as db:
        assert pillar(pillars.pillars_for(db, "1"), "momentum")["value"] == pytest.approx(-3_000.0)


def test_acceleration_needs_three_observations(ledger, session_factory):
    ledger(1, [(0, 1_000), (60, 2_000)])
    with session_factory() as db:
        speeding = pillar(pillars.pillars_for(db, "1"), "acceleration")

    assert speeding["state"] == pillars.INSUFFICIENT
    assert "three observations" in speeding["detail"]


def test_a_steepening_rise_reports_positive_acceleration(ledger, session_factory):
    ledger(1, [(0, 1_000), (60, 2_000), (120, 6_000)])
    with session_factory() as db:
        speeding = pillar(pillars.pillars_for(db, "1"), "acceleration")

    assert speeding["state"] == pillars.MEASURED
    assert speeding["value"] > 0


def test_a_flattening_rise_reports_negative_acceleration(ledger, session_factory):
    ledger(1, [(0, 1_000), (60, 5_000), (120, 5_200)])
    with session_factory() as db:
        assert pillar(pillars.pillars_for(db, "1"), "acceleration")["value"] < 0


def test_reception_comes_from_roblox_vote_totals(ledger, session_factory):
    ledger(1, [(0, 5_000)], up=27_101, down=917)
    with session_factory() as db:
        received = pillar(pillars.pillars_for(db, "1"), "reception")

    assert received["value"] == pytest.approx(27_101 / (27_101 + 917))
    assert "27101 up" in received["detail"]


def test_a_game_with_no_votes_abstains_rather_than_scoring_zero(ledger, session_factory):
    ledger(1, [(0, 5_000)], up=0, down=0)
    with session_factory() as db:
        received = pillar(pillars.pillars_for(db, "1"), "reception")

    assert received["state"] == pillars.INSUFFICIENT
    assert received["value"] is None


def test_saturation_is_a_share_of_the_latest_census(ledger, session_factory):
    ledger(1, [(0, 100)], genre="Simulation")
    ledger(2, [(0, 100)], genre="Simulation")
    ledger(3, [(0, 100)], genre="Shooter")
    with session_factory() as db:
        crowded = pillar(pillars.pillars_for(db, "1"), "saturation")

    assert crowded["value"] == pytest.approx(2 / 3)
    assert "2 of 3" in crowded["detail"]


def test_a_game_on_several_shelves_is_one_point_per_capture(ledger, session_factory):
    """Being on three shelves is three placements but one measurement; it must
    not look like three observations and unlock a rate that does not exist."""
    ledger(1, [(0, 5_000)], sort_id="top-trending")
    ledger(1, [(0, 5_000)], sort_id="up-and-coming")
    ledger(1, [(0, 5_000)], sort_id="top-playing-now")
    with session_factory() as db:
        found = pillars.pillars_for(db, "1")

    assert found["observations"] == 1
    assert pillar(found, "momentum")["state"] == pillars.INSUFFICIENT
    assert pillar(found, "visibility")["value"] == 3.0
    assert sorted(found["shelves"]) == ["top-playing-now", "top-trending", "up-and-coming"]


def test_a_measured_pillar_names_the_rows_it_was_computed_from(ledger, session_factory):
    ledger(1, [(0, 1_000), (120, 3_000)])
    with session_factory() as db:
        ids = {row.id for row in db.scalars(__import__("sqlalchemy").select(MarketSample))}
        moving = pillar(pillars.pillars_for(db, "1"), "momentum")

    assert moving["basis"], "a measured pillar with no basis cannot be checked"
    assert set(moving["basis"]) <= ids


def test_pillars_are_never_combined_into_one_number(ledger, session_factory):
    """The whole design. A blend would be a score, and scoring is locked
    behind calibration."""
    ledger(1, [(0, 1_000), (120, 3_000), (180, 9_000)])
    with session_factory() as db:
        found = pillars.pillars_for(db, "1")

    banned = {"score", "total", "overall", "opportunity", "rating", "index", "probability"}
    assert not banned & set(found), "a combined figure appeared alongside the pillars"
    assert isinstance(found["pillars"], list)
    assert not any(banned & set(entry) for entry in found["pillars"])


def test_the_reading_carries_the_version_of_the_rules_that_produced_it(ledger,
                                                                      session_factory):
    ledger(1, [(0, 1_000)])
    with session_factory() as db:
        assert pillars.pillars_for(db, "1")["version"] == pillars.PILLARS_VERSION


def test_a_game_nobody_has_sampled_abstains_on_every_pillar(session_factory):
    with session_factory() as db:
        found = pillars.pillars_for(db, "404")

    assert found["measured"] == 0
    assert all(entry["value"] is None for entry in found["pillars"])
