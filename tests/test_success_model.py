"""A probability is shown only when it has beaten its baselines on held-out data.

The in-sample band table this replaces reported 96 % for a score band whose
games were simply already big. These tests pin the properties that stop that
happening again: labels never see the future, the split is chronological, an
uninformative model shows nothing, and a question that does not apply to a
game is not answered for it.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app import success_model as sm

HOUR = 1.0
SETTINGS = {"cutoff_step_hours": 24, "fresh_observation_hours": 1.0,
            "min_window_coverage": 0.5, "test_fraction": 0.3, "min_train": 20,
            "min_test": 10, "min_test_positives": 3, "reliability_bins": 5,
            "bootstrap_rounds": 200, "bootstrap_confidence": 0.95}
REACH = {"key": "reach", "label": "reach 5k", "kind": "threshold", "threshold_ccu": 5000,
         "horizon_hours": 24, "require_below_at_cutoff": True}


def census_from(games: dict[str, list[tuple[float, float]]], hours: int,
                created: dict[str, float] | None = None) -> sm.Census:
    """Every game sampled hourly unless its curve says otherwise."""
    captures = np.arange(0, hours, HOUR, dtype=float)
    census = sm.Census(captures=captures)
    for universe, points in games.items():
        t = np.array([p[0] for p in points], dtype=float)
        ccu = np.array([p[1] for p in points], dtype=float)
        census.series[universe] = sm.Series(t, ccu, np.full(len(t), 90.0),
                                            np.full(len(t), 10.0), np.ones(len(t)))
    census.created = created or {}
    return census


def test_features_use_nothing_after_the_observation():
    series = sm.Series(np.array([0.0, 24.0, 48.0]), np.array([100.0, 200.0, 9_000.0]),
                       np.full(3, 9.0), np.full(3, 1.0), np.ones(3))
    at_day_one = sm.features_at(series, 1, created=None)
    names = dict(zip(sm.FEATURES, at_day_one))
    assert names["log_ccu"] == pytest.approx(math.log1p(200))
    assert names["mom24"] == pytest.approx(math.log1p(200) - math.log1p(100))
    assert names["mom72_missing"] == 1.0, "no observation 72 h earlier exists"


def test_a_change_needs_the_same_time_of_day_not_whatever_came_before():
    """A reference 12 hours earlier is a different part of the daily cycle."""
    series = sm.Series(np.array([0.0, 12.0]), np.array([100.0, 900.0]),
                       np.full(2, 9.0), np.full(2, 1.0), np.ones(2))
    names = dict(zip(sm.FEATURES, sm.features_at(series, 1, created=None)))
    assert names["mom24_missing"] == 1.0
    assert names["mom24"] == 0.0


def test_a_game_already_above_the_target_is_not_an_example_of_reaching_it():
    games = {"big": [(t, 9_000.0) for t in range(0, 120)]}
    examples = sm.build_examples(census_from(games, 120), REACH, SETTINGS)
    assert len(examples.y) == 0


def test_labels_come_only_from_the_horizon_after_the_cutoff():
    # Below target for two days, then a spike on day 4.
    curve = [(t, 1_000.0 if t < 72 else 8_000.0) for t in range(0, 120)]
    examples = sm.build_examples(census_from({"g": curve}, 120), REACH, SETTINGS)
    by_cutoff = dict(zip(examples.cutoffs.tolist(), examples.y.tolist()))
    assert by_cutoff[0.0] == 0.0, "the spike is outside the first 24 h window"
    assert by_cutoff[48.0] == 1.0, "the spike is inside the window starting at hour 48"


def test_an_unobserved_window_is_skipped_not_counted_as_failure():
    """The census stops seeing a shelf game when it leaves the shelf."""
    curve = [(t, 1_000.0) for t in range(0, 25)]  # vanishes after the first day
    examples = sm.build_examples(census_from({"g": curve, "other": [
        (t, 10.0) for t in range(0, 120)]}, 120), REACH, SETTINGS)
    assert "g" not in examples.universes[1:], "no example once the game is unobserved"
    assert examples.skipped_low_coverage >= 1


def test_too_little_history_is_refused():
    result = sm.backtest(census_from({"g": [(t, 100.0) for t in range(30)]}, 30),
                         REACH, SETTINGS)
    assert result["state"] == sm.INSUFFICIENT
    assert "probability" not in result


def _market(seed: int, days: int, informative: bool) -> sm.Census:
    """Many games. When informative, games closer to the target reach it more."""
    rng = np.random.default_rng(seed)
    games = {}
    for number in range(120):
        level = float(rng.uniform(500, 4_900))
        curve = []
        for day in range(days):
            if informative:
                chance = (level / 5_000) ** 3
            else:
                chance = 0.3
            spike = rng.random() < chance
            for hour in range(24):
                value = 6_000.0 if spike and hour == 12 else level
                curve.append((day * 24 + hour, value))
        games[str(number)] = curve
    return census_from(games, days * 24)


def test_noise_gets_no_probability():
    """If outcomes are coin flips, nothing beats the base rate and nothing is shown."""
    result = sm.backtest(_market(1, 14, informative=False), REACH, SETTINGS)
    assert result["state"] == sm.NO_SKILL
    assert result["selected_model"] is None
    assert "no probability is shown" in result["reason"]


def test_real_signal_is_found_and_reported_with_its_baselines():
    result = sm.backtest(_market(2, 14, informative=True), REACH, SETTINGS)
    assert result["state"] == sm.SKILL
    assert result["selected_model"] in {"full", "ccu_only"}
    scores = result["scores"]
    assert {"model", "baseline_base_rate", "baseline_ccu_only"} <= set(scores)
    assert result["selected_scores"]["brier"] < scores["baseline_base_rate"]["brier"]
    assert sum(entry["games"] for entry in result["reliability"]) == result["test"]


def test_the_split_is_chronological_with_no_overlap():
    result = sm.backtest(_market(3, 14, informative=True), REACH, SETTINGS)
    examples = sm.build_examples(_market(3, 14, informative=True), REACH, SETTINGS)
    split = np.datetime64(result["split_at"][:19]).astype("datetime64[s]").astype(float) / 3600
    train = examples.cutoffs + REACH["horizon_hours"] <= split
    test = examples.cutoffs >= split
    assert not (train & test).any()
    assert result["train"] == int(train.sum()) and result["test"] == int(test.sum())


def test_no_probability_for_a_game_the_question_does_not_fit():
    census = _market(4, 14, informative=True)
    evaluation = sm.backtest(census, REACH, SETTINGS)
    model = sm.final_model(census, REACH, SETTINGS, evaluation["selected_model"])
    census.series["giant"] = sm.Series(np.array([census.last]), np.array([50_000.0]),
                                       np.array([9.0]), np.array([1.0]), np.array([1.0]))
    answer = sm.predict(census, REACH, SETTINGS, "giant", evaluation, model)
    assert answer["probability"] is None
    assert "does not apply" in answer["reason"]


def test_a_prediction_carries_its_held_out_track_record():
    census = _market(5, 14, informative=True)
    evaluation = sm.backtest(census, REACH, SETTINGS)
    model = sm.final_model(census, REACH, SETTINGS, evaluation["selected_model"])
    answer = sm.predict(census, REACH, SETTINGS, "7", evaluation, model)
    assert 0.0 <= answer["probability"] <= 1.0
    assert answer["held_out_brier"] == evaluation["selected_scores"]["brier"]
    assert answer["model"] == evaluation["selected_model"]


def test_an_unproven_definition_never_predicts():
    census = _market(6, 14, informative=False)
    evaluation = sm.backtest(census, REACH, SETTINGS)
    answer = sm.predict(census, REACH, SETTINGS, "7", evaluation, None)
    assert answer["probability"] is None


def test_definitions_are_data():
    loaded = sm.definitions()
    assert loaded["game"] and loaded["idea"] and loaded["evaluation"]
    keys = [entry["key"] for entry in loaded["game"] + loaded["idea"]]
    assert len(keys) == len(set(keys)), "a key must name exactly one question"
    for entry in loaded["game"]:
        assert entry["kind"] in {"threshold", "ratio", "persist"}
        assert sm.applies(entry, 0.0) in {True, False}


def test_ratio_and_persist_questions():
    ratio = {"key": "r", "label": "", "kind": "ratio", "ratio": 2.0,
             "min_ccu_at_cutoff": 200, "horizon_hours": 24}
    assert sm._label(ratio, 100.0, np.array([999.0]), np.array([5.0]), 0.0) is None
    assert sm._label(ratio, 300.0, np.array([650.0]), np.array([5.0]), 0.0) is True
    persist = {"key": "p", "label": "", "kind": "persist", "threshold_ccu": 5000,
               "horizon_hours": 24}
    window_t = np.array([2.0, 23.0])
    assert sm._label(persist, 6000.0, np.array([9000.0, 4000.0]), window_t, 0.0) is False
    assert sm._label(persist, 6000.0, np.array([100.0, 7000.0]), window_t, 0.0) is True
