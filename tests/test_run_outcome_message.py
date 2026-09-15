"""A finished run says what it produced, not only that it finished.

Concept drafting can now decline: if no inspected game carries the niche in
its own Roblox name or description, nothing is drafted rather than a concept
being invented from an unrelated game. That is the right call, but the run
list showed one fixed sentence for every completed run, so declining looked
exactly like succeeding until somebody opened the report.
"""

from __future__ import annotations

import pytest

from app.models import ResearchRun


def finalized(session_factory, *, hunter: bool, stop: str = "all_questions_answered"):
    from app.deep_research import DeepResearch
    from tests.test_deep_research import orchestrator, seed

    run_id, *_ = seed(session_factory, hunter=hunter)
    research = DeepResearch(orchestrator(session_factory), run_id)
    research.finalize(stop)
    with session_factory() as db:
        return db.get(ResearchRun, run_id)


def test_a_run_that_drafted_nothing_says_so_in_the_run_list(session_factory, settings):
    run = finalized(session_factory, hunter=False)

    assert "No concepts drafted" in run.message
    assert "niche" in run.message, "the reason has to travel with the outcome"


def test_a_run_that_drafted_concepts_keeps_the_standing_caveat(session_factory, settings):
    run = finalized(session_factory, hunter=True)

    assert "Research concepts only" in run.message
    assert "No concepts drafted" not in run.message


def test_the_two_outcomes_do_not_share_a_message(session_factory, settings):
    """The whole point is that they are distinguishable at a glance."""
    drafted = finalized(session_factory, hunter=True).message
    declined = finalized(session_factory, hunter=False).message

    assert drafted != declined


@pytest.mark.parametrize("partial", [False, True])
def test_declining_to_draft_is_not_recorded_as_a_failed_run(session_factory, settings,
                                                            partial):
    """An abstention is a result. The status still reflects whether anything
    went wrong, which drafting nothing does not."""
    from app.deep_research import DeepResearch
    from tests.test_deep_research import orchestrator, seed

    run_id, *_ = seed(session_factory, hunter=False)
    research = DeepResearch(orchestrator(session_factory), run_id)
    research.finalize("all_questions_answered", partial=partial)

    with session_factory() as db:
        assert db.get(ResearchRun, run_id).status == ("partial" if partial else "complete")
