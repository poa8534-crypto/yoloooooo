"""The Venture Scout works on Meta Hunter output and nothing else.

Both Scout operations used to be reachable for any captured game. The
candidate list ran a hundred and forty rows deep and most of them read "no
Hunter proposal", so the obvious thing to do with the page was point the Scout
at a game the Hunter had never judged worth proposing anything about. That
spends several minutes of a serialized local model and writes an audit no
concept will ever reference.

The gate lives in `ResearchOrchestrator.audit`, which is where both entry
points converge -- the async job runner and the direct audit endpoint. A
filtered dropdown is a courtesy; this is the rule.
"""

from __future__ import annotations

import pytest

from tests.test_deep_research import FakeLLM, orchestrator, seed


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [None, "analyze_game", "audit_idea"])
async def test_no_operation_can_reach_a_game_the_hunter_never_sent(session_factory,
                                                                   settings, operation):
    """Including analyze_game, which is the one that used to need no proposal
    and was therefore the way around the rule."""
    _, candidate_id, *_ = seed(session_factory, hunter=False)
    llm = FakeLLM()

    with pytest.raises(ValueError, match="no Hunter proposal"):
        await orchestrator(session_factory, llm).audit(candidate_id, operation=operation)

    assert not llm.calls, "the model was asked about a game the Hunter never sent"


@pytest.mark.asyncio
async def test_the_refusal_happens_before_any_model_time_is_spent(session_factory, settings):
    """Failing late would still burn the minutes this rule exists to protect."""
    _, candidate_id, *_ = seed(session_factory, hunter=False)
    llm = FakeLLM()

    with pytest.raises(ValueError):
        await orchestrator(session_factory, llm).audit(candidate_id)

    with session_factory() as db:
        from app.models import AuditRecord
        from sqlalchemy import select
        assert not list(db.scalars(select(AuditRecord))), (
            "a refused game still wrote an audit record"
        )


@pytest.mark.asyncio
async def test_a_game_the_hunter_proposed_a_concept_for_runs_normally(session_factory,
                                                                      settings):
    """The rule narrows what is eligible. It must not break what is."""
    _, candidate_id, *_ = seed(session_factory)
    llm = FakeLLM()

    result = await orchestrator(session_factory, llm).audit(candidate_id)

    assert result["candidate_id"] == candidate_id
    assert llm.calls, "a Hunter-backed game was refused"


@pytest.mark.asyncio
async def test_analyze_game_still_ignores_the_concept_once_the_game_qualifies(session_factory,
                                                                              settings):
    """The two operations stay different. What changed is which games are
    eligible at all, not what an operation does once one is."""
    _, candidate_id, *_ = seed(session_factory)
    llm = FakeLLM()

    await orchestrator(session_factory, llm).audit(candidate_id, operation="analyze_game")

    assert llm.calls[0]["hunter_proposal"] is None, (
        "analyze_game was handed the concept it is supposed to work without"
    )


@pytest.mark.asyncio
async def test_the_direct_endpoint_is_gated_too_not_only_the_job_runner(session_factory,
                                                                        settings, monkeypatch):
    """Two ways in. Gating one of them is gating neither."""
    from fastapi.testclient import TestClient

    from app import main as main_module

    _, candidate_id, *_ = seed(session_factory, hunter=False)
    llm = FakeLLM()

    def override():
        with session_factory() as db:
            yield db

    monkeypatch.setattr(main_module.app.state, "orchestrator",
                        orchestrator(session_factory, llm), raising=False)
    main_module.app.dependency_overrides[main_module.get_db] = override
    try:
        client = TestClient(main_module.app, raise_server_exceptions=False)
        response = client.post(f"/api/candidates/{candidate_id}/audit")
    finally:
        main_module.app.dependency_overrides.clear()

    assert response.status_code >= 400, "the direct endpoint audited an ungated game"
    assert not llm.calls


@pytest.mark.asyncio
async def test_only_a_hunter_proposal_opens_the_gate_not_any_proposal(session_factory,
                                                                      settings):
    """Today the Hunter is the only writer of proposals, so "has a proposal"
    and "has a Hunter proposal" happen to agree. They would stop agreeing the
    moment another agent writes one, and a game could then qualify itself for
    the Scout without the Hunter ever having judged it.
    """
    from app.models import Proposal
    from tests.test_deep_research import VALID

    _, candidate_id, *_ = seed(session_factory, hunter=False)
    with session_factory() as db:
        db.add(Proposal(candidate_id=candidate_id, agent="venture_scout",
                        payload=VALID, model_name="fake"))
        db.commit()
    llm = FakeLLM()

    with pytest.raises(ValueError, match="no Hunter proposal"):
        await orchestrator(session_factory, llm).audit(candidate_id)

    assert not llm.calls
