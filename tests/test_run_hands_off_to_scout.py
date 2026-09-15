"""A run drafts concepts and stops. The Scout is a separate decision.

The queue that holds Hunter concepts for a human to approve was built, and it
was empty after every single run -- because the run audited each concept
inline as its last step. The decision the queue exists to offer was therefore
never actually offered: by the time anyone could look, it had been made.

Auditing is the expensive half. It is several minutes of a model that serves
one request at a time, per concept, and it is the part worth choosing to
spend. Drafting a concept is cheap and belongs to the run.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app import scout_queue
from app.models import AuditRecord, Proposal, ResearchCheckpoint, ResearchRun


async def deep_run(session_factory, llm):
    from tests.test_deep_research import FakeConnectors, orchestrator

    with session_factory() as db:
        run = ResearchRun(niche="cooperative cozy farming short social sessions")
        db.add(run)
        db.flush()
        run_id = run.id
        db.add(ResearchCheckpoint(run_id=run_id, state={"mode": "deep"}))
        db.commit()
    orchestra = orchestrator(session_factory, llm, FakeConnectors())
    await orchestra.research(run_id)
    return orchestra, run_id


@pytest.mark.asyncio
async def test_a_run_audits_nothing_by_itself(session_factory, settings):
    from tests.test_deep_research import FakeLLM

    llm = FakeLLM()
    await deep_run(session_factory, llm)

    with session_factory() as db:
        assert not list(db.scalars(select(AuditRecord))), (
            "the run audited its own concepts, so nothing was left to decide"
        )
    assert [call["agent"] for call in llm.calls] == ["Meta Hunter"], (
        "the Venture Scout ran without anyone asking it to"
    )


@pytest.mark.asyncio
async def test_the_concepts_a_run_drafts_are_waiting_in_the_queue(session_factory, settings):
    """Routing is still automatic. Only the running is not."""
    from tests.test_deep_research import FakeLLM

    await deep_run(session_factory, FakeLLM())

    with session_factory() as db:
        drafted = {row.id for row in db.scalars(
            select(Proposal).where(Proposal.agent == "meta_hunter"))}
        waiting = {row["proposal_id"] for row in scout_queue.pending(db)}

    assert drafted, "the run drafted no concepts at all"
    assert drafted == waiting, "a drafted concept never reached the Scout queue"


@pytest.mark.asyncio
async def test_the_queue_empties_only_when_the_scout_is_actually_run(session_factory,
                                                                     settings):
    """The whole point of the split, end to end."""
    from tests.test_deep_research import FakeLLM

    llm = FakeLLM()
    orchestra, _ = await deep_run(session_factory, llm)

    with session_factory() as db:
        waiting = scout_queue.pending(db)
    assert len(waiting) >= 1

    await orchestra.audit(waiting[0]["candidate_id"], waiting[0]["proposal_id"])

    with session_factory() as db:
        remaining = {row["proposal_id"] for row in scout_queue.pending(db)}
    assert waiting[0]["proposal_id"] not in remaining, "an audited concept stayed queued"
    assert "Venture Scout" in [call["agent"] for call in llm.calls]


@pytest.mark.asyncio
async def test_the_run_reports_the_handoff_rather_than_an_audit(session_factory, settings):
    """A stage of "auditing selected proposal" on a run that audits nothing
    would describe work that is not happening."""
    from tests.test_deep_research import FakeLLM

    _, run_id = await deep_run(session_factory, FakeLLM())

    with session_factory() as db:
        state = db.get(ResearchCheckpoint, run_id).state
        report = db.scalar(select(ResearchRun).where(ResearchRun.id == run_id))

    assert "auditing" not in str(state.get("stage", "")).casefold()
    assert report.status == "complete"


@pytest.mark.asyncio
async def test_drafting_costs_one_model_attempt_per_concept_not_two(session_factory,
                                                                    settings):
    """Auditing inline doubled the model time a run spent before anyone had
    seen a single concept."""
    from tests.test_deep_research import FakeLLM

    llm = FakeLLM()
    _, run_id = await deep_run(session_factory, llm)

    with session_factory() as db:
        usage = db.get(ResearchCheckpoint, run_id).state["usage"]
        concepts = len(list(db.scalars(select(Proposal).where(Proposal.agent == "meta_hunter"))))

    assert usage["model_attempts"] == concepts, (
        f"{usage['model_attempts']} model attempts for {concepts} concept(s)"
    )
