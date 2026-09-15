"""Guarantees the deep-research work claims but that nothing was defending.

Each test here was written after a mutation check: the corresponding guard was
deleted, the suite stayed green, and the claim turned out to be unprotected.
"""

from __future__ import annotations

import hashlib
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


# --- 7. A concept must not restate a game the run just discovered ----------

def test_a_concept_restating_a_discovered_game_is_refused():
    """Observed live: the model returned an existing game as its "concept".

    A run that discovered "Cheese Escape [Horror]" reported "Cheese Escape" as
    a research concept. The duplicate check only compared against other
    proposals, so an existing game's identity passed straight through.
    """
    from app.deep_research import restates_discovered_game

    discovered = [
        "Cheese Escape [Horror]",
        "Cheese Escape: Scary Maze",
        "(\U0001f50e) Escape Room 2!",
        "Brainrot Horror \U0001f480",
    ]

    assert restates_discovered_game("Cheese Escape", discovered) == "Cheese Escape [Horror]"
    assert restates_discovered_game("cheese  escape", discovered) is not None
    assert restates_discovered_game("Escape Room 2", discovered) is not None


def test_a_genuinely_new_concept_is_allowed():
    from app.deep_research import restates_discovered_game

    discovered = ["Cheese Escape [Horror]", "Brainrot Horror \U0001f480"]
    assert restates_discovered_game("Shadowed Asylum: Escape from the Forgotten", discovered) is None
    assert restates_discovered_game("Lantern Hollow", discovered) is None


def test_a_single_shared_word_does_not_block_a_concept():
    """Otherwise every horror concept collides with every horror game."""
    from app.deep_research import restates_discovered_game

    discovered = ["Brainrot Horror \U0001f480", "(\U0001f50e) Escape Room 2!"]
    assert restates_discovered_game("Escape", discovered) is None
    assert restates_discovered_game("Horror", discovered) is None


def test_the_check_survives_titles_with_no_usable_tokens():
    from app.deep_research import restates_discovered_game

    assert restates_discovered_game("", ["Cheese Escape"]) is None
    assert restates_discovered_game("\U0001f480", ["Cheese Escape"]) is None
    assert restates_discovered_game("Cheese Escape", []) is None


# --- 8. The audit must actually contain an audit ---------------------------

SCOUT_SECTIONS = ("essential_features", "excluded_features", "dependencies", "validation_tasks")


def _scout_payload(**overrides) -> dict:
    payload = {
        **VALID,
        "essential_features": ["A shared objective board"],
        "excluded_features": ["Monetisation"],
        "dependencies": ["A single reusable interaction script"],
        "validation_tasks": ["Playtest the loop with two people"],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", SCOUT_SECTIONS)
async def test_a_scout_audit_missing_a_required_section_fails_closed(settings, missing):
    """An audit that skips scope or validation is not an audit.

    The guard existed but nothing exercised its rejecting branch, so it could
    be deleted and the suite would stay green.
    """
    cited = str(uuid4())
    payload = _scout_payload(supporting_fact_ids=[cited], **{missing: []})
    with pytest.raises(LLMUnavailable):
        await _llm(payload, settings).generate(
            agent="Venture Scout", niche="cozy farming",
            sourced_name="Evidence Garden", fact_ids=[cited],
            require_citations=True,
        )


@pytest.mark.asyncio
async def test_a_complete_scout_audit_is_accepted(settings):
    """Positive control, so the test above cannot pass by rejecting everything."""
    cited = str(uuid4())
    generated = await _llm(_scout_payload(supporting_fact_ids=[cited]), settings).generate(
        agent="Venture Scout", niche="cozy farming",
        sourced_name="Evidence Garden", fact_ids=[cited],
        require_citations=True,
    )
    for section in SCOUT_SECTIONS:
        assert getattr(generated.payload, section), f"{section} came back empty"
    assert generated.payload.supporting_fact_ids == [cited]


@pytest.mark.asyncio
async def test_a_hunter_proposal_without_a_citation_fails_closed(settings):
    cited = str(uuid4())
    with pytest.raises(LLMUnavailable):
        await _llm({**VALID, "supporting_fact_ids": []}, settings).generate(
            agent="Meta Hunter", niche="cozy farming",
            sourced_name="Evidence Garden", fact_ids=[cited],
            require_citations=True,
        )


# --- 9. The pipeline must really ask for citations --------------------------

@pytest.mark.asyncio
async def test_both_agents_are_invoked_with_citations_required(session_factory, settings):
    """Guards the flag itself, not just the enforcement behind it.

    Dropping `require_citations=True` at either call site silently removes the
    whole citation guarantee from the real pipeline while every unit test on
    the enforcement keeps passing.
    """
    from app.models import ResearchCheckpoint, ResearchRun
    from tests.test_deep_research import FakeConnectors as DeepConnectors
    from tests.test_deep_research import FakeLLM, orchestrator

    with session_factory() as db:
        run = ResearchRun(niche="cooperative cozy farming short social sessions")
        db.add(run)
        db.flush()
        run_id = run.id
        db.add(ResearchCheckpoint(run_id=run_id, state={"mode": "deep"}))
        db.commit()

    llm = FakeLLM()
    await orchestrator(session_factory, llm, DeepConnectors()).research(run_id)

    agents = [call["agent"] for call in llm.calls]
    assert agents == ["Meta Hunter", "Venture Scout"], agents
    for call in llm.calls:
        assert call.get("require_citations") is True, (
            f"{call['agent']} was asked for a proposal without requiring citations"
        )
        assert call.get("evidence"), f"{call['agent']} received no evidence packet"


# --- 10. A webpage metric is external evidence too --------------------------

@pytest.mark.asyncio
async def test_a_primary_page_claim_carries_an_association(session_factory, settings):
    """Found on the live ledger: 33 web_description facts had no association.

    The game's own Roblox page is still a webpage source. The place ID in the
    URL is exact-ID evidence, so it auto-associates rather than queueing for
    review, but it must resolve through a record like every other external
    metric.
    """
    from sqlalchemy import select as sa_select

    from app.association import is_association_usable
    from app.models import (
        AssociationRecord,
        Observation,
        ResearchCheckpoint,
        ResearchRun,
    )
    from tests.test_deep_research import FakeConnectors as DeepConnectors
    from tests.test_deep_research import FakeLLM, orchestrator

    class PageConnectors(DeepConnectors):
        async def capture_page(self, url):
            from app.connectors import ConnectorResult

            # The captured page really contains the API-sourced description.
            return ConnectorResult(url, "Header. Plant seeds together. Footer.", "text/plain")

    with session_factory() as db:
        run = ResearchRun(niche="cooperative cozy farming short social sessions")
        db.add(run)
        db.flush()
        run_id = run.id
        db.add(ResearchCheckpoint(run_id=run_id, state={"mode": "deep"}))
        db.commit()

    await orchestrator(session_factory, FakeLLM(), PageConnectors()).research(run_id)

    with session_factory() as db:
        claims = list(db.scalars(
            sa_select(Observation).where(Observation.metric == "web_description")
        ))
        assert claims, "the fixture page should have produced a web claim"
        for claim in claims:
            assert claim.association_id, "a webpage metric was stored with no association"
            record = db.get(AssociationRecord, claim.association_id)
            assert record is not None
            assert is_association_usable(db, record)
            assert record.outcome == "auto_associate"
            assert "exact_verified_id_evidence" in record.rationale_codes


# --- 11. A configured cap is not a failure ---------------------------------


def test_reaching_a_configured_cap_is_not_recorded_as_an_error(session_factory, settings):
    """A run that spends its whole discovery budget behaved as specified.

    `partial` is derived from `errors`, so filing a planned cap there marked a
    correct run as degraded. The cap must still be recorded — just not as a
    failure.
    """
    run_id, *_ = seed(session_factory)
    budget = RunBudget(session_factory, run_id)
    budget.state["limits"]["universes"] = 1
    budget.save()
    budget.reserve("universes")
    with pytest.raises(BudgetExceeded) as caught:
        budget.reserve("universes")
    budget.error("discovery", caught.value)

    assert budget.state["errors"] == [], "a configured cap was reported as an error"
    stops = budget.state["budget_stops"]
    assert [entry["stage"] for entry in stops] == ["discovery"]
    assert "universes_limit" in stops[0]["error"], "the cap that stopped work must stay visible"


@pytest.mark.asyncio
async def test_a_refusal_to_replay_an_unknown_request_still_counts_as_an_error(
    session_factory, settings
):
    """Positive control: not every BudgetExceeded is a planned cap.

    Declining to replay a request whose outcome was never recorded means the
    run's bookkeeping is in an unexpected state. Routing every BudgetExceeded
    to `budget_stops` would silence that, so this asserts the opposite case.
    """
    run_id, *_ = seed(session_factory)
    budget = RunBudget(session_factory, run_id)
    key = hashlib.sha256(json.dumps(["tavily_search", "x"], sort_keys=True).encode()).hexdigest()
    budget.state["requests"][key] = {"status": "interrupted", "method": "tavily_search"}
    budget.save()

    with pytest.raises(BudgetExceeded) as caught:
        await budget.call(FakeConnectors(), "tavily_search", "x")
    budget.error("resume", caught.value)

    assert budget.state["budget_stops"] == [], "an unknown-outcome refusal is not a planned cap"
    assert [entry["stage"] for entry in budget.state["errors"]] == ["resume"]


def test_a_run_that_only_hit_caps_is_not_reported_as_partial(session_factory, settings):
    """The flag the report shows users, end to end."""
    from app.models import ResearchRun
    from app.research_budget import progress

    run_id, *_ = seed(session_factory)
    budget = RunBudget(session_factory, run_id)
    budget.state["limits"]["videos"] = 0
    budget.save()
    with pytest.raises(BudgetExceeded) as caught:
        budget.reserve("videos")
    budget.error("discovery", caught.value)

    # This is the expression the controller uses to decide `partial`.
    assert not bool(budget.state["errors"])

    with session_factory() as db:
        reported = progress(db, db.get(ResearchRun, run_id))
    assert reported["errors"] == []
    assert len(reported["budget_stops"]) == 1, "the cap must reach the report, not vanish"


# --- 12. The fact index must not serve a stale answer ----------------------


def test_a_fact_appended_after_the_index_was_built_is_still_returned(session_factory):
    """The per-session cache is only safe if it notices new facts.

    `candidate_facts` reads through an index built once per session. A fact
    written after that index exists must still appear, or the dashboard would
    quietly show an outdated evidence packet for the rest of the session.
    """
    from app.evidence import add_json_observation, candidate_facts, create_fact, record_artifact
    from app.models import Candidate

    run_id, candidate_id, *_ = seed(session_factory)
    with session_factory() as db:
        before = {fact.id for fact in candidate_facts(db, candidate_id)}
        assert before, "the seeded candidate should already have facts"

        candidate = db.get(Candidate, candidate_id)
        artifact = record_artifact(
            db, url="https://games.roblox.com/v1/games?later=1", retrieval_method="test",
            content_type="application/json",
            payload={"data": [{"name": "Evidence Garden", "playing": 43}]},
            source_tier="primary",
        )
        observation = add_json_observation(
            db, artifact=artifact, candidate_id=candidate.id, metric="roblox_playing",
            pointer="/data/0/playing", unit="players",
        )
        fresh = create_fact(db, template_id="roblox_playing", slots={"value": observation})
        db.commit()

        after = {fact.id for fact in candidate_facts(db, candidate_id)}

    assert fresh.id in after, "a fact appended after the index was built went missing"
    assert before < after


def test_candidate_facts_returns_only_that_candidates_facts(session_factory):
    """Positive control: the index must not widen the result.

    Serving every fact in the ledger for every candidate would also make the
    staleness test above pass, so this pins the other side.
    """
    from app.evidence import candidate_facts

    _, first, *_ = seed(session_factory, universe="77")
    _, second, *_ = seed(session_factory, universe="88")
    with session_factory() as db:
        first_facts = {fact.id for fact in candidate_facts(db, first)}
        second_facts = {fact.id for fact in candidate_facts(db, second)}

    assert first_facts and second_facts
    assert not (first_facts & second_facts), "facts leaked across candidates"


def test_facts_sharing_a_timestamp_are_ordered_by_id():
    """This machine writes batched facts with identical microsecond stamps.

    Leaving those ties to set-iteration order made the evidence packet reorder
    itself between processes, so one candidate rendered its facts in a
    different order on each restart. The ledger is append-only, so the tie
    cannot be fixed up after the fact -- the ordering rule has to break it.
    """
    from types import SimpleNamespace

    from app.evidence import _fact_order

    stamp = datetime.now(UTC)
    tied = [SimpleNamespace(id="b-second", created_at=stamp),
            SimpleNamespace(id="a-first", created_at=stamp)]
    assert [item.id for item in sorted(tied, key=_fact_order)] == ["a-first", "b-second"]


def test_facts_order_across_naive_and_aware_timestamps():
    """A fact created in this session is aware; one read back from SQLite is not.

    Sorting them together raised TypeError and took the whole runs endpoint
    with it, so ordering must normalise before comparing.
    """
    from types import SimpleNamespace

    from app.evidence import _fact_order

    now = datetime.now(UTC)
    from_session = SimpleNamespace(id="new", created_at=now)
    from_sqlite = SimpleNamespace(id="old", created_at=(now - timedelta(hours=1)).replace(tzinfo=None))
    assert [item.id for item in sorted([from_session, from_sqlite], key=_fact_order)] == ["old", "new"]
