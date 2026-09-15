"""Rounds have to be driven by what is still unanswered.

The loop used to execute a precomputed slice of searches and only then ask
which questions remained open, so the answers never influenced the next round:
a run did the same work whatever it had already found.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.deep_research import QUESTION_ACTIONS, QUESTION_PRIORITY, plan_round

QUERIES = [f"query-{index}" for index in range(12)]


def context(name, videos=0):
    return SimpleNamespace(candidate_id=f"cand-{name}", display_name=name,
                           video_ids=[f"v{index}" for index in range(videos)])


def questions(**states):
    """Every known question, answered unless named otherwise."""
    return [{"id": key, "state": states.get(key, "answered")} for key in QUESTION_ACTIONS]


def test_an_open_question_chooses_the_capture_that_could_answer_it():
    plan = plan_round(questions(creators="open"), [context("Alpha")], QUERIES, 0)
    assert [action["kind"] for action in plan] == ["youtube"]
    assert plan[0]["question"] == "creators"
    assert plan[0]["candidate_id"] == "cand-Alpha"


def test_a_page_question_captures_the_page_not_a_search():
    plan = plan_round(questions(mechanics="open"), [context("Alpha")], QUERIES, 0)
    assert [action["kind"] for action in plan] == ["capture_page"]
    assert plan[0]["question"] == "mechanics"


def test_a_coverage_question_widens_discovery():
    plan = plan_round(questions(relevance="open"), [context("Alpha")], QUERIES, 0)
    assert [action["kind"] for action in plan] == ["discover"]
    assert plan[0]["query"] in QUERIES


def test_coverage_gaps_are_answered_before_widening_the_pool():
    """Widening discovery while a captured game has no creator evidence at all
    spends the round on the least useful thing available."""
    plan = plan_round(questions(creators="open", relevance="open", mechanics="open"),
                      [context("Alpha")], QUERIES, 0)
    kinds = [action["question"] for action in plan]
    assert kinds.index("creators") < kinds.index("relevance"), kinds
    assert kinds.index("mechanics") < kinds.index("relevance"), kinds


def test_priority_is_not_merely_alphabetical():
    """`counterevidence` sorts first alphabetically and is last by priority, so
    an accidental `sorted()` would reorder the round without failing anything
    else."""
    plan = plan_round(questions(counterevidence="open", creators="open"),
                      [context("Alpha")], QUERIES, 0)
    assert plan[0]["question"] == "creators", [a["question"] for a in plan]


def test_an_answered_question_is_not_investigated_again():
    """Positive control: planning everything regardless would also satisfy the
    tests above."""
    plan = plan_round(questions(creators="open"), [context("Alpha")], QUERIES, 0)
    assert {action["question"] for action in plan} == {"creators"}
    assert all(action["question"] != "mechanics" for action in plan)


def test_a_question_nothing_can_answer_does_not_consume_the_round():
    """`mvp` has no capture that could answer it. Spending a round pretending
    otherwise is worse than saying so."""
    assert QUESTION_ACTIONS["mvp"] is None
    plan = plan_round(questions(mvp="open"), [context("Alpha")], QUERIES, 1)
    assert all(action["question"] != "mvp" for action in plan)
    # It falls back to widening, rather than returning nothing to do.
    assert plan and {action["kind"] for action in plan} == {"discover"}


def test_captures_spread_across_games_with_the_least_evidence():
    """One game absorbing every capture leaves the others bare."""
    contexts = [context("Rich", videos=5), context("Bare", videos=0), context("Middle", videos=2)]
    plan = plan_round(questions(creators="open", mechanics="open", demand="open"), contexts, QUERIES, 0)
    assert plan[0]["display_name"] == "Bare", [a["display_name"] for a in plan]


def test_the_plan_is_deterministic():
    """Two runs of the same state must choose the same work, or the record of
    why a round did what it did means nothing."""
    contexts = [context("Alpha"), context("Beta"), context("Gamma")]
    state = questions(creators="open", mechanics="open", relevance="open")
    assert plan_round(state, contexts, QUERIES, 2) == plan_round(state, contexts, QUERIES, 2)


def test_every_prioritised_question_has_an_action():
    """A question in the priority list with no action would be silently skipped
    every round."""
    for key in QUESTION_PRIORITY:
        assert key in QUESTION_ACTIONS, key
        assert QUESTION_ACTIONS[key] is not None, key


def test_a_plan_never_exceeds_the_round_allowance():
    plan = plan_round(questions(**{key: "open" for key in QUESTION_ACTIONS}),
                      [context("Alpha"), context("Beta")], QUERIES, 0, per_round=2)
    assert len(plan) == 2


def test_every_action_records_the_question_it_serves():
    """The round's choices have to be auditable afterwards, not implicit in a
    slice index."""
    plan = plan_round(questions(creators="open", mechanics="open"), [context("Alpha")], QUERIES, 0)
    assert plan
    for action in plan:
        assert action["question"] in QUESTION_ACTIONS
        assert action["kind"] in {"discover", "youtube", "capture_page"}


@pytest.mark.parametrize("round_index", [0, 1, 2, 3])
def test_discovery_does_not_repeat_the_same_query_every_round(round_index):
    plans = [plan_round(questions(relevance="open"), [context("Alpha")], QUERIES, index)
             for index in range(4)]
    chosen = [plan[0]["query"] for plan in plans]
    assert len(set(chosen)) > 1, chosen
    assert plans[round_index][0]["query"] in QUERIES


def test_the_round_plan_reaches_the_report(session_factory, settings):
    """A plan the run cannot show is not auditable. It was saved to the
    checkpoint but dropped by `progress`, so nothing outside the database
    could see why a round did what it did.
    """
    from app.models import ResearchRun
    from app.research_budget import RunBudget, progress
    from tests.test_deep_research import seed

    run_id, *_ = seed(session_factory)
    budget = RunBudget(session_factory, run_id)
    chosen = [{"kind": "youtube", "candidate_id": "c1", "display_name": "Alpha", "question": "creators"}]
    budget.save(plan=chosen)

    with session_factory() as db:
        reported = progress(db, db.get(ResearchRun, run_id))

    assert reported["plan"] == chosen, "the round's choices never left the checkpoint"
