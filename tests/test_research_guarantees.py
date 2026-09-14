"""Guarantees the deep-research work claims but that nothing was defending.

Each test here was written after a mutation check: the corresponding guard was
deleted, the suite stayed green, and the claim turned out to be unprotected.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from app.calibration import _daily_values, calibration_status, load_artifact
from app.llm import LLMUnavailable, OllamaProposalClient
from app.models import Observation, SourceArtifact
from app.research_budget import BudgetExceeded, RunBudget
from tests.test_deep_research import VALID, FakeConnectors, seed


def _llm(payload, settings_obj) -> OllamaProposalClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": json.dumps(payload)}})

    return OllamaProposalClient(
        settings=settings_obj,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


# --- 1. The model may only cite evidence IDs it was actually given ----------

@pytest.mark.asyncio
async def test_a_well_formed_but_unsupplied_fact_id_is_rejected(settings):
    """The schema only checks the ID is a UUID; the allowlist is separate.

    An invented-but-valid UUID passes validation, so without the subset check
    the model could cite evidence that was never in its packet.
    """
    smuggled = str(uuid4())
    supplied = str(uuid4())
    payload = {**VALID, "supporting_fact_ids": [smuggled]}
    with pytest.raises(LLMUnavailable):
        await _llm(payload, settings).generate(
            agent="Meta Hunter", niche="cozy farming",
            sourced_name="Evidence Garden", fact_ids=[supplied],
        )


@pytest.mark.asyncio
async def test_a_supplied_fact_id_is_accepted(settings):
    supplied = str(uuid4())
    payload = {**VALID, "supporting_fact_ids": [supplied]}
    generated = await _llm(payload, settings).generate(
        agent="Meta Hunter", niche="cozy farming",
        sourced_name="Evidence Garden", fact_ids=[supplied, str(uuid4())],
    )
    assert generated.payload.supporting_fact_ids == [supplied]


# --- 2. The deadline stops spending mid-run --------------------------------

def test_reserving_after_the_deadline_is_refused(session_factory, settings):
    run_id, *_ = seed(session_factory)
    budget = RunBudget(session_factory, run_id)
    budget.state["limits"]["seconds"] = 0
    budget.save()
    assert budget.remaining == 0
    with pytest.raises(BudgetExceeded, match="deadline"):
        budget.reserve("tavily_search")


@pytest.mark.asyncio
async def test_no_connector_call_is_made_once_the_deadline_passes(session_factory, settings):
    run_id, *_ = seed(session_factory)
    budget = RunBudget(session_factory, run_id)
    budget.state["limits"]["seconds"] = 0
    budget.save()
    connectors = FakeConnectors()
    with pytest.raises(BudgetExceeded):
        await budget.call(connectors, "tavily_search", "anything")
    assert connectors.calls == [], "a call was made after the deadline"


# --- 3. An interrupted request is never silently replayed -------------------

@pytest.mark.asyncio
async def test_an_interrupted_request_is_not_replayed_on_resume(session_factory, settings):
    """A request whose outcome is unknown must not be re-sent automatically.

    The existing cache test only covered the completed path, so replaying an
    unknown request — spending quota twice — went unnoticed.
    """
    run_id, *_ = seed(session_factory)
    first = RunBudget(session_factory, run_id)

    class Interrupting(FakeConnectors):
        async def tavily_search(self, query):
            self.calls.append(("tavily", query))
            raise TimeoutError("connector died mid-flight")

    connectors = Interrupting()
    with pytest.raises(TimeoutError):
        await first.call(connectors, "tavily_search", "same query")
    assert len(connectors.calls) == 1

    resumed = RunBudget(session_factory, run_id)
    clean = FakeConnectors()
    with pytest.raises(BudgetExceeded, match="request_previously_failed"):
        await resumed.call(clean, "tavily_search", "same query")
    assert clean.calls == [], "the unknown request was replayed and spent again"


# --- 4. Repeated same-day captures do not inflate calibration ---------------

def test_repeated_daily_observations_are_not_summed(db):
    """Rediscovery writes another observation for the same day and entity.

    `_daily_values` feeds the calibration windows. Summing duplicates would
    fabricate growth out of the act of looking twice.
    """
    artifact = SourceArtifact(
        url="https://games.roblox.com/v1/games", publisher_owner="roblox.com",
        retrieval_method="test", sha256="f" * 64, content_type="application/json",
        raw_path="unused", source_tier="primary",
    )
    db.add(artifact)
    db.flush()
    day = datetime(2026, 3, 1, tzinfo=UTC)
    for index, value in enumerate((100, 100, 100)):
        db.add(Observation(
            artifact_id=artifact.id, candidate_id=None, metric="roblox_playing",
            value_json=value, unit="players", extraction_method="json_pointer",
            pointer="/data/0/playing", observed_at=day + timedelta(minutes=index),
        ))
    db.flush()
    rows = list(db.scalars(__import__("sqlalchemy").select(Observation)))

    values = _daily_values(rows, "roblox_playing")

    assert len(values) == 1, "three captures on one day became more than one point"
    assert values[0][1] == 100.0, f"duplicate captures summed to {values[0][1]}"


# --- 5. Scoring cannot activate from a legacy artifact ----------------------

def test_a_legacy_artifact_claiming_active_cannot_unlock_scoring(db, settings):
    """Activation needs a validated niche-cluster dataset, which does not exist.

    Writing `active: true` into the artifact file is exactly the shape of the
    accident this has to survive.
    """
    settings.model_dir.mkdir(parents=True, exist_ok=True)
    (settings.model_dir / "current.json").write_text(json.dumps({
        "active": True, "version": "legacy-v1", "threshold": 0.5,
        "dataset_hash": "0" * 64, "heldout_precision": 0.99,
        "heldout_recommendations": 50, "reason": "legacy artifact",
    }), encoding="utf-8")

    artifact = load_artifact()
    assert artifact is not None
    assert artifact["active"] is False, "a legacy artifact re-enabled scoring"
    assert "not implemented" in artifact["reason"].lower()

    status = calibration_status(db)
    assert status.scoring_active is False
    assert status.phase != "active"


# --- 6. A deliberate abstention is not a failure ---------------------------

def test_abstaining_does_not_mark_a_run_partial(session_factory, settings):
    """Refusing to emit a duplicate concept is the system working.

    The live run recorded two duplicate-concept abstentions as errors, which
    flipped an otherwise clean run to `partial` and hid them among real faults.
    """
    run_id, *_ = seed(session_factory)
    budget = RunBudget(session_factory, run_id)

    budget.abstain("hunter", "duplicate concept title; no second concept emitted")
    budget.abstain("hunter", "duplicate concept title; no second concept emitted")

    assert budget.state["errors"] == [], "an abstention was recorded as an error"
    assert len(budget.state["abstentions"]) == 2
    assert budget.state["abstentions"][0]["stage"] == "hunter"
    assert "duplicate" in budget.state["abstentions"][0]["reason"]

    # `partial` is derived from errors, so abstaining alone must not trigger it.
    assert not bool(budget.state["errors"])

    budget.error("discovery", TimeoutError("connector timed out"))
    assert bool(budget.state["errors"]), "a genuine fault must still register"


def test_progress_reports_abstentions_separately(session_factory, settings):
    from app.models import ResearchRun
    from app.research_budget import progress

    run_id, *_ = seed(session_factory)
    budget = RunBudget(session_factory, run_id)
    budget.abstain("hunter", "duplicate concept title")
    with session_factory() as db:
        reported = progress(db, db.get(ResearchRun, run_id))
    assert reported["abstentions"] and reported["errors"] == []
