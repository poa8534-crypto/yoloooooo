"""Idea-level rates: observed, shrunk, caveated, and judged on later games."""

from __future__ import annotations

import numpy as np

from app import idea_rates
from app import success_model as sm

DEFINITION = {"key": "young", "label": "young game reaches 5k", "threshold_ccu": 5000,
              "horizon_days": 2, "young_days": 1}
SETTINGS = {"test_fraction": 0.3, "min_test": 5, "idea_min_cohort": 5,
            "bootstrap_rounds": 100, "bootstrap_confidence": 0.95, "idea_interval": 0.9}


def build(games):
    """games: universe -> (created_hour, genre, [(t, ccu), ...])"""
    last = max(point[0] for _c, _g, points in games.values() for point in points)
    census = sm.Census(captures=np.arange(0, last + 1, dtype=float))
    for universe, (created, genre, points) in games.items():
        t = np.array([p[0] for p in points], dtype=float)
        census.series[universe] = sm.Series(t, np.array([p[1] for p in points], dtype=float),
                                            np.zeros(len(t)), np.zeros(len(t)), np.ones(len(t)))
        census.created[universe] = float(created)
        census.genre[universe] = (genre, "")
    return census


def test_old_games_and_games_whose_horizon_has_not_passed_are_excluded():
    census = build({
        "young": (0, "Obby", [(1, 100), (40, 6000), (100, 10)]),
        "old": (-500, "Obby", [(1, 9000), (100, 9000)]),        # first seen at 501 h old
        "unfinished": (90, "Obby", [(91, 100), (100, 100)]),    # horizon ends at 138 h
    })
    members = {m["universe_id"]: m for m in idea_rates.cohort(census, DEFINITION)}
    assert set(members) == {"young"}
    assert members["young"]["reached"] is True


def test_the_peak_after_the_horizon_does_not_count():
    census = build({"late": (0, "Obby", [(1, 100), (60, 9000), (100, 9000)])})
    member = idea_rates.cohort(census, DEFINITION)[0]
    assert member["reached"] is False, "60 h is after the 48 h horizon"


def test_as_of_hides_the_future():
    census = build({"g": (0, "Obby", [(1, 100), (40, 6000), (100, 10)])})
    assert idea_rates.cohort(census, DEFINITION, as_of=30) == []


def test_a_small_genre_is_shrunk_towards_the_overall_rate():
    games = {}
    for number in range(40):
        reached = number % 2 == 0
        games[f"s{number}"] = (number * 3, "Simulation",
                               [(number * 3 + 1, 100), (number * 3 + 20, 6000 if reached else 200)])
    games["o1"] = (2, "Obby", [(3, 100), (20, 9000)])  # one obby, one success
    census = build(games | {"tail": (200, "Simulation", [(201, 1), (400, 1)])})
    overall, rates = idea_rates._rates(idea_rates.cohort(census, DEFINITION), "genre_l1", 0.9)
    obby = rates["Obby"]
    assert obby["observed_share"] == 1.0
    assert overall < obby["rate"] < 1.0, "one success is not a 100 % rate"
    low, high = obby["interval"]
    assert low < obby["rate"] < high


def test_backtest_refuses_a_tiny_cohort():
    census = build({"g": (0, "Obby", [(1, 100), (40, 6000), (100, 10)])})
    result = idea_rates.backtest(census, DEFINITION, SETTINGS)
    assert result["state"] == "insufficient_data"


def test_backtest_compares_genre_rates_with_the_overall_rate_on_later_games():
    games = {}
    rng = np.random.default_rng(0)
    for number in range(400):
        genre = "Simulation" if number % 2 else "Obby"
        reached = rng.random() < (0.8 if genre == "Simulation" else 0.1)
        created = number * 2
        games[str(number)] = (created, genre,
                              [(created + 1, 100), (created + 10, 6000 if reached else 100)])
    games["end"] = (900, "Obby", [(901, 1), (1000, 1)])
    result = idea_rates.backtest(build(games), DEFINITION, SETTINGS)
    assert result["state"] == "measured"
    assert result["brier_genre_rate"] < result["brier_all_genre_rate"]
    assert result["genre_helps"] is True


def test_an_answer_always_carries_its_caveats(monkeypatch):
    monkeypatch.setattr(idea_rates, "idea_definition", lambda key=None: DEFINITION)
    monkeypatch.setattr(idea_rates, "evaluation_settings", lambda: SETTINGS)
    census = build({"g": (0, "Obby", [(1, 100), (40, 6000), (100, 10)])})
    answer = idea_rates.for_idea(census, "Obby")
    assert answer["probability"] is None and answer["state"] == "insufficient_data"
    assert any("noticed" in caveat for caveat in answer["caveats"])
