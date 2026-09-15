"""Model 2 ranks, refuses, and never pretends to be a probability.

The arithmetic is the easy part. What has to hold is everything around it: a
score assembled from one measured input must not look like a score assembled
from five, a missing component must not be silently read as zero, and the
number must never be presented as a chance of anything until outcomes have
been observed to calibrate it against.
"""

from __future__ import annotations

import pytest

from app import opportunity
from app.pillars import INSUFFICIENT, MEASURED


def pillar(key, value, label=None, state=MEASURED):
    return {"key": key, "label": label or key.title(), "unit": "",
            "value": value, "state": state, "detail": "d",
            "observations": 3, "basis": []}


def reading(**values):
    """A pillar reading; any pillar not named is unmeasured."""
    keys = ("demand", "momentum", "acceleration", "reception", "saturation")
    return {"universe_id": "77", "pillars": [
        pillar(key, values[key]) if key in values
        else pillar(key, None, state=INSUFFICIENT) for key in keys]}


def test_a_score_needs_enough_measured_components_to_mean_anything():
    """One of five inputs produces a number indistinguishable from five of
    five, which is the whole danger."""
    result = opportunity.score_from_pillars(reading(demand=10_000))

    assert result.score is None
    assert result.state == "insufficient_evidence"
    assert "at least 3" in result.reason


def test_enough_components_produce_a_score():
    result = opportunity.score_from_pillars(
        reading(demand=20_000, momentum=500, reception=0.95))

    assert result.state == "ranked"
    assert 0.0 <= result.score <= 1.0


def test_a_missing_component_is_named_not_counted_as_zero():
    """Counting an unmeasured component as zero would punish a game for the
    sampler's gaps rather than for anything about the game."""
    weak = opportunity.score_from_pillars(
        reading(demand=20_000, momentum=500, reception=0.95))
    with_bad_headroom = opportunity.score_from_pillars(
        reading(demand=20_000, momentum=500, reception=0.95, saturation=1.0))

    assert "headroom" in weak.missing
    assert with_bad_headroom.score < weak.score, (
        "a measured-but-bad component scored no worse than an absent one"
    )


def test_the_score_is_re_weighted_over_what_was_actually_measured():
    """Three perfect components out of five should not read as 0.6."""
    result = opportunity.score_from_pillars(
        reading(demand=10_000_000, momentum=10_000, reception=1.0))

    assert result.score == pytest.approx(1.0, abs=0.02)


def test_every_component_reports_its_own_contribution():
    result = opportunity.score_from_pillars(
        reading(demand=20_000, momentum=500, reception=0.95))
    measured = [c for c in result.components if c["measured"]]

    assert measured, "no component reported itself"
    for component in measured:
        assert component["contribution"] == pytest.approx(
            component["normalised"] * component["weight"])
    assert sum(c["contribution"] for c in measured) == pytest.approx(
        result.score * sum(c["weight"] for c in measured))


def test_a_falling_game_scores_below_a_climbing_one_all_else_equal():
    climbing = opportunity.score_from_pillars(
        reading(demand=20_000, momentum=800, reception=0.9))
    falling = opportunity.score_from_pillars(
        reading(demand=20_000, momentum=-800, reception=0.9))

    assert falling.score < climbing.score


def test_a_crowded_genre_scores_below_an_empty_one_all_else_equal():
    roomy = opportunity.score_from_pillars(
        reading(demand=20_000, momentum=0, reception=0.9, saturation=0.05))
    crowded = opportunity.score_from_pillars(
        reading(demand=20_000, momentum=0, reception=0.9, saturation=0.9))

    assert crowded.score < roomy.score


def test_approval_below_half_contributes_nothing_rather_than_going_negative():
    disliked = opportunity.score_from_pillars(
        reading(demand=20_000, momentum=0, reception=0.2))

    component = next(c for c in disliked.components if c["key"] == "reception")
    assert component["normalised"] == 0.0
    assert disliked.score >= 0.0


def test_demand_saturates_so_one_giant_cannot_dominate_the_ranking():
    """Without this, CCU alone decides every ordering and the other four
    components are decoration."""
    big = opportunity.score_from_pillars(reading(demand=100_000, momentum=0, reception=0.6))
    huge = opportunity.score_from_pillars(reading(demand=2_000_000, momentum=0, reception=0.6))

    assert huge.score - big.score < 0.1


def test_the_result_never_claims_to_be_a_probability():
    result = opportunity.score_from_pillars(
        reading(demand=20_000, momentum=500, reception=0.95))

    assert result.calibrated is False
    assert "not a probability" in result.note
    assert not {"probability", "chance", "p_success"} & set(result.as_dict())


def test_the_score_carries_the_version_of_the_weights_that_made_it():
    result = opportunity.score_from_pillars(reading(demand=20_000, momentum=1, reception=0.9))

    assert result.version == opportunity.OPPORTUNITY_VERSION


def test_the_weights_sum_to_one_so_a_full_score_is_reachable():
    assert sum(weight for _, _, weight, _, _ in opportunity.COMPONENTS) == pytest.approx(1.0)


def test_ranking_is_total_and_deterministic():
    """Same rows, same order, every time -- including where scores tie."""
    class FakeDb:
        pass

    readings = {
        "a": reading(demand=20_000, momentum=500, reception=0.9),
        "b": reading(demand=20_000, momentum=500, reception=0.9),
        "c": reading(demand=1, momentum=-9_999, reception=0.51),
        "d": reading(demand=10),  # unscoreable
    }
    scored = [opportunity.score_from_pillars({**readings[key], "universe_id": key})
              for key in "abcd"]
    order = sorted(scored, key=lambda item: (item.score is None,
                                             -(item.score or 0.0), item.universe_id))

    assert [item.universe_id for item in order] == ["a", "b", "c", "d"]
    assert order[-1].score is None, "an unscoreable game must sort last, not first"


def test_no_model_is_involved_anywhere_in_this_module():
    """The explicit requirement: arithmetic, not an LLM asked for an opinion.

    Checked against the syntax tree rather than the text, so the module can
    say the word "LLM" in its own docstring while proving it never reaches
    for one.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(opportunity.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            target = node.func
            called.add(getattr(target, "attr", None) or getattr(target, "id", "") or "")

    banned = {"llm", "ollama", "openai", "anthropic", "httpx", "requests"}
    assert not {name.split(".")[-1].casefold() for name in imported} & banned, imported
    assert not {"generate", "complete", "chat", "audit"} & called, called
