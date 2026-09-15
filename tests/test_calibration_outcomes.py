"""P(CCU >= target) is measured from watched outcomes, or it is not reported.

This is the module where a fabricated number would do the most damage, because
a probability is the most authoritative-looking thing the system can print. So
the guarantees are about refusal as much as about arithmetic:

* Below a real cohort there is no rate at all, not a rate over three games.
* A score judged against an outcome must be computed from history *before* the
  outcome window. Letting the future leak in is how a calibration reports
  itself as excellent.
* A band nobody landed in has no rate. It does not have a rate of zero.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app import calibration_outcomes as calibration
from app.evidence import record_artifact
from app.models import MarketSample

BASE = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def census(session_factory):
    def add(universe, points, *, genre="Simulation", up=900, down=100):
        with session_factory() as db:
            artifact = record_artifact(
                db, url="https://apis.roblox.com/explore-api/v1/get-sorts",
                retrieval_method="scheduled_market_sample", content_type="application/json",
                payload={"sorts": []}, source_tier="primary", owner="roblox.com")
            for hours, count in points:
                db.add(MarketSample(
                    artifact_id=artifact.id, captured_at=BASE + timedelta(hours=hours),
                    sort_id="top-trending", rank=0, universe_id=str(universe),
                    name=f"Game {universe}", player_count=count,
                    up_votes=up, down_votes=down, genre=genre))
            db.commit()
    return add


def climbing(hours_before_window=6, start=1_000, end=9_000, peak=9_000):
    """A game watched before the window, then peaking inside it."""
    return [(0, start), (1, start + 200), (hours_before_window, end),
            (hours_before_window + 12, peak), (hours_before_window + 24, peak)]


def test_no_rate_is_reported_over_a_handful_of_games(census, session_factory):
    """A base rate over three games presented as a probability is worse than
    no number: an absence can be discounted, a computed-looking figure cannot."""
    for index in range(3):
        census(index, climbing())

    with session_factory() as db:
        result = calibration.calibrate(db)

    assert result.state == "insufficient_outcomes"
    assert result.overall_rate is None
    assert result.required == calibration.MIN_OUTCOMES
    assert "required before a rate means anything" in result.reason


def test_a_game_watched_for_less_than_the_horizon_is_not_an_outcome(census,
                                                                    session_factory):
    census(1, [(0, 100), (1, 9_000)])  # only an hour of history

    with session_factory() as db:
        assert calibration.collect_outcomes(db) == []


def test_reaching_the_target_inside_the_window_is_what_counts(census, session_factory):
    census(1, [(0, 100), (1, 200), (6, 300), (18, 9_000), (30, 500)])

    with session_factory() as db:
        outcomes = calibration.collect_outcomes(db, target_ccu=5_000)

    assert len(outcomes) == 1
    assert outcomes[0].reached is True
    assert outcomes[0].peak_ccu == 9_000


def test_a_game_that_never_reaches_the_target_is_recorded_as_not_reached(census,
                                                                        session_factory):
    census(1, [(0, 100), (1, 120), (6, 150), (18, 200), (30, 210)])

    with session_factory() as db:
        outcomes = calibration.collect_outcomes(db, target_ccu=5_000)

    assert len(outcomes) == 1
    assert outcomes[0].reached is False


def test_the_score_cannot_see_the_outcome_it_is_judged_against(census, session_factory):
    """The failure that makes a calibration look excellent and be worthless.

    Two games with identical history before the window and opposite outcomes
    inside it must receive the same score.
    """
    census(1, [(0, 1_000), (1, 1_200), (6, 1_400), (18, 90_000), (30, 90_000)])
    census(2, [(0, 1_000), (1, 1_200), (6, 1_400), (18, 100), (30, 100)])

    with session_factory() as db:
        outcomes = {item.universe_id: item for item in calibration.collect_outcomes(db)}

    assert outcomes["1"].reached != outcomes["2"].reached, "the fixture proves nothing"
    assert outcomes["1"].score == pytest.approx(outcomes["2"].score), (
        "the outcome leaked into the score meant to predict it"
    )


def test_a_measured_cohort_reports_a_rate_per_band(census, session_factory):
    for index in range(calibration.MIN_OUTCOMES + 5):
        # Alternate strong and weak so more than one band is populated.
        if index % 2:
            census(index, [(0, 40_000), (1, 41_000), (6, 60_000), (18, 90_000), (30, 90_000)])
        else:
            census(index, [(0, 50), (1, 45), (6, 40), (18, 30), (30, 25)])

    with session_factory() as db:
        result = calibration.calibrate(db, target_ccu=5_000)

    assert result.state == "measured"
    assert result.outcomes >= calibration.MIN_OUTCOMES
    assert 0.0 <= result.overall_rate <= 1.0
    populated = [band for band in result.bands if band["games"]]
    assert len(populated) >= 2, "every game landed in one band; the fixture is degenerate"
    for band in populated:
        assert 0.0 <= band["rate"] <= 1.0


def test_a_band_nobody_landed_in_has_no_rate_rather_than_a_rate_of_zero(census,
                                                                        session_factory):
    for index in range(calibration.MIN_OUTCOMES + 2):
        census(index, [(0, 40_000), (1, 41_000), (6, 60_000), (18, 90_000), (30, 90_000)])

    with session_factory() as db:
        result = calibration.calibrate(db)

    empty = [band for band in result.bands if band["games"] == 0]
    assert empty, "the fixture populated every band"
    assert all(band["rate"] is None for band in empty), (
        "an empty band reported a 0% chance, which is a claim nobody measured"
    )


def test_a_probability_is_refused_while_the_cohort_is_too_small(census, session_factory):
    census(1, climbing())

    with session_factory() as db:
        answer = calibration.probability_for(db, "1")

    assert answer["probability"] is None
    assert answer["state"] == "insufficient_outcomes"


def test_the_target_is_configurable_and_actually_changes_the_answer(census,
                                                                    session_factory):
    census(1, [(0, 100), (1, 200), (6, 300), (18, 6_000), (30, 6_000)])

    with session_factory() as db:
        low = calibration.collect_outcomes(db, target_ccu=5_000)
        high = calibration.collect_outcomes(db, target_ccu=50_000)

    assert low[0].reached is True
    assert high[0].reached is False


def test_the_reading_carries_the_version_that_produced_it(session_factory):
    with session_factory() as db:
        assert calibration.calibrate(db).version == calibration.CALIBRATION_VERSION
