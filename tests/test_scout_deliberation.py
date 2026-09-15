"""Guarantees for the Venture Scout rebuild.

Scout used to refuse to run without a Meta Hunter proposal, answer in one
shot with reasoning switched off, and trust scraped page text that no
association had ever approved. Each test here pins one of those changes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.llm import LLMUnavailable, OllamaProposalClient
from app.research_evidence import (
    FIRST_PARTY_METRICS,
    admissible_observation,
    audit_readiness,
    evidence_packet,
    verify_citations,
)
from tests.test_deep_research import VALID, FakeLLM, orchestrator, seed

SCOUT_EXTRA = {
    "essential_features": ["A shared objective board"],
    "excluded_features": ["Monetisation"],
    "dependencies": ["One reusable interaction script"],
    "validation_tasks": ["Playtest the loop with two people"],
    "executive_summary": "A single shared objective is the smallest cooperative loop worth "
                         "building first, and it is the only part a solo beginner can finish.",
    "opportunity_gap": "The captured evidence shows what exists, not why anyone stays, so the "
                       "cooperative angle is an untested hypothesis rather than an established gap.",
}


def _scout(**overrides) -> dict:
    return {**VALID, **SCOUT_EXTRA, **overrides}


def _client(settings_obj, responses) -> tuple[OllamaProposalClient, list]:
    """A client that replies with `responses` in order, recording each request."""
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        body = responses[min(len(seen) - 1, len(responses) - 1)]
        if isinstance(body, int):
            return httpx.Response(body, json={"error": "nope"})
        return httpx.Response(200, json={"message": {"content": json.dumps(body)}})

    return OllamaProposalClient(
        settings=settings_obj,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ), seen


# --- 1. Scout no longer needs a Hunter proposal ----------------------------


def test_a_game_the_hunter_proposed_a_concept_for_is_auditable(session_factory, settings):
    """The other half of the rule. Restricting the Scout to Hunter output only
    helps if the games the Hunter did send still pass every gate."""
    _, candidate_id, *_ = seed(session_factory)
    with session_factory() as db:
        readiness = audit_readiness(db, candidate_id)
    hunter_gate = next(g for g in readiness.gates if g.label == "Selected Hunter proposal")
    assert hunter_gate.passed, hunter_gate.detail
    assert readiness.ready, [g.model_dump() for g in readiness.gates if not g.passed]


@pytest.mark.asyncio
async def test_a_game_the_hunter_never_proposed_anything_about_is_refused(session_factory,
                                                                          settings):
    """The Scout works on Hunter output and nothing else.

    Both operations used to be reachable for any captured game, so the
    candidate list ran 140 rows deep and mostly read "no Hunter proposal".
    That spent minutes of the model on games the Hunter had never judged
    worth proposing anything about.
    """
    _, candidate_id, *_ = seed(session_factory, hunter=False)
    llm = FakeLLM()

    with pytest.raises(ValueError, match="no Hunter proposal"):
        await orchestrator(session_factory, llm).audit(candidate_id)

    assert not llm.calls, "the model was asked about a game the Hunter never sent"


def test_a_proposal_belonging_to_another_candidate_is_still_refused(session_factory, settings):
    """Positive control: unbinding Scout must not make the binding meaningless."""
    _, first, _, proposal_id = seed(session_factory, universe="77")
    _, second, *_ = seed(session_factory, universe="88")
    with session_factory() as db:
        readiness = audit_readiness(db, second, proposal_id)
    hunter_gate = next(g for g in readiness.gates if g.label == "Selected Hunter proposal")
    assert hunter_gate.state == "fail"
    assert not readiness.ready
    assert first != second


@pytest.mark.asyncio
async def test_the_standalone_prompt_asks_for_its_own_analysis(settings):
    client, seen = _client(settings.model_copy(update={"scout_deliberation_passes": 1}), [_scout()])
    await client.generate(agent="Venture Scout", niche="farming", sourced_name="garden", fact_ids=[])
    prompt = seen[0]["messages"][-1]["content"]
    assert "No Hunter proposal was supplied" in prompt
    assert "EXACT supplied Hunter proposal" not in prompt


# --- 2. The audit deliberates ---------------------------------------------


@pytest.mark.asyncio
async def test_an_audit_drafts_critiques_and_revises(settings):
    """One shot returns whatever came out first. An audit has to read its own
    draft back and revise it, or the word audit is doing no work."""
    draft = _scout(concept_title="First Draft")
    critique = {"weaknesses": ["Scope is too wide for three days"],
                "missing_dependencies": [], "scope_risks": [], "unsupported_claims": []}
    revised = _scout(concept_title="Revised Draft")
    client, seen = _client(settings, [draft, critique, revised])

    generated = await client.generate(
        agent="Venture Scout", niche="farming", sourced_name="garden", fact_ids=[],
    )

    assert len(seen) == 3, "the audit did not run three passes"
    assert "Do not rewrite it here" in seen[1]["messages"][-1]["content"], "no critique pass"
    assert "your draft and your own critique" in seen[2]["messages"][-1]["content"], "no revision pass"
    assert generated.payload.concept_title == "Revised Draft", "the revision was discarded"


@pytest.mark.asyncio
async def test_reasoning_is_enabled_for_the_audit(settings):
    client, seen = _client(settings.model_copy(update={"scout_deliberation_passes": 1}), [_scout()])
    await client.generate(agent="Venture Scout", niche="farming", sourced_name="garden", fact_ids=[])
    assert seen[0]["think"] is True, "the audit was asked not to think"


@pytest.mark.asyncio
async def test_a_failed_revision_keeps_the_draft_that_already_passed(settings):
    """A later pass may not downgrade an answer that already satisfied every
    check. Returning the broken revision, or failing the whole audit, would
    both be worse than keeping the draft."""
    draft = _scout(concept_title="Survivor")
    critique = {"weaknesses": ["Too wide"], "missing_dependencies": [],
                "scope_risks": [], "unsupported_claims": []}
    broken = {**_scout(), "core_loop": "Visit https://example.com for details"}
    client, seen = _client(settings, [draft, critique, broken])

    generated = await client.generate(
        agent="Venture Scout", niche="farming", sourced_name="garden", fact_ids=[],
    )

    assert generated.payload.concept_title == "Survivor"
    assert len(seen) > 3, "the failed revision was never retried"


@pytest.mark.asyncio
async def test_a_hunter_concept_does_not_deliberate(settings):
    """Positive control: deliberation is the audit's job, not every call's."""
    client, seen = _client(settings, [VALID])
    await client.generate(agent="Meta Hunter", niche="farming", sourced_name="garden", fact_ids=[])
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_an_audit_still_fails_closed_when_every_pass_is_unusable(settings):
    client, _ = _client(settings, [500])
    with pytest.raises(LLMUnavailable):
        await client.generate(agent="Venture Scout", niche="farming", sourced_name="garden", fact_ids=[])


# --- 3. Every external claim resolves through an association --------------


def test_a_web_claim_without_an_association_is_inadmissible(session_factory, settings):
    """The gate used to cover YouTube metrics only, so scraped page text
    reached the model with nothing having matched it to this experience."""
    from app.evidence import add_passage_observation, record_artifact
    from app.models import Candidate

    _, candidate_id, *_ = seed(session_factory)
    with session_factory() as db:
        candidate = db.get(Candidate, candidate_id)
        artifact = record_artifact(
            db, url="https://www.roblox.com/games/1/evidence-garden", retrieval_method="primary_game_page",
            content_type="text/plain", payload="Header. Plant seeds together. Footer.",
            source_tier="primary", owner="roblox.com",
        )
        row = add_passage_observation(
            db, artifact=artifact, candidate_id=candidate.id, metric="web_description",
            exact_passage="Plant seeds together.", value="Plant seeds together.",
        )
        db.commit()
        assert row.association_id is None
        assert not admissible_observation(db, row), "an unassociated web claim was admitted"


def test_first_party_roblox_metrics_do_not_need_an_association(session_factory, settings):
    """Positive control: the gate must not refuse the API response that named
    the universe in the first place."""
    from app.models import Observation
    from sqlalchemy import select

    _, candidate_id, *_ = seed(session_factory)
    with session_factory() as db:
        rows = list(db.scalars(select(Observation).where(Observation.candidate_id == candidate_id)))
        assert rows
        for row in rows:
            assert row.metric in FIRST_PARTY_METRICS
            assert row.association_id is None
            assert admissible_observation(db, row), f"{row.metric} was refused"


# --- 4. Citations are re-checked when the brief renders -------------------


def test_a_citation_that_no_longer_resolves_is_withdrawn(session_factory, settings):
    """A proposal is stored once; associations get rejected and measurements go
    stale afterwards. What the brief shows as verified has to be re-resolved."""
    _, candidate_id, *_ = seed(session_factory)
    invented = str(uuid4())
    with session_factory() as db:
        real = [item["id"] for item in evidence_packet(db, candidate_id)]
        assert real
        kept, withdrawn = verify_citations(db, candidate_id, [real[0], invented])
    assert kept == [real[0]]
    assert withdrawn == [invented]


def test_verifying_citations_keeps_order_and_drops_duplicates(session_factory, settings):
    _, candidate_id, *_ = seed(session_factory)
    with session_factory() as db:
        kept, withdrawn = verify_citations(db, candidate_id, ["a", "b", "a"])
    assert kept == []
    assert withdrawn == ["a", "b"]


# --- 5. The schema must survive Ollama's grammar compiler -----------------


def test_the_grammar_schema_carries_no_max_length():
    """Ollama compiles the schema into a sampling grammar and some upper bounds
    break that compiler: `maxLength: 2000` fails every time while 1500 and 2500
    both work. Widening prose limits for a real brief hit exactly that value and
    every audit failed with a 400 before the model ran.
    """
    from app.llm import grammar_safe
    from app.schemas import AuditCritique, ProposalPayload

    def max_lengths(node):
        if isinstance(node, dict):
            return ("maxLength" in node) or any(max_lengths(v) for v in node.values())
        if isinstance(node, list):
            return any(max_lengths(item) for item in node)
        return False

    # ProposalPayload is the one that carries prose bounds, so it is also the
    # one that proves this test is not vacuous.
    assert max_lengths(ProposalPayload.model_json_schema()), "no bound to strip; this test is vacuous"
    for model in (ProposalPayload, AuditCritique):
        assert not max_lengths(grammar_safe(model.model_json_schema())),             f"{model.__name__} still carries maxLength"


def test_stripping_max_length_keeps_the_rest_of_the_schema():
    """Positive control: it must remove that one key, not flatten the schema."""
    from app.llm import grammar_safe

    source = {"type": "object", "properties": {
        "a": {"type": "string", "maxLength": 2000, "minLength": 80},
        "b": {"type": "array", "items": {"type": "string", "enum": ["x"]}, "minItems": 1, "maxItems": 12},
    }, "required": ["a", "b"]}
    cleaned = grammar_safe(source)
    assert cleaned["properties"]["a"] == {"type": "string", "minLength": 80}
    assert cleaned["properties"]["b"]["items"]["enum"] == ["x"]
    assert cleaned["properties"]["b"]["minItems"] == 1 and cleaned["properties"]["b"]["maxItems"] == 12
    assert cleaned["required"] == ["a", "b"]
    assert source["properties"]["a"]["maxLength"] == 2000, "the caller's schema was mutated"


# --- 6. The metric firewall refuses measurements, not ordinals ------------

BLOCKED_PROSE = [
    "Visit https://evil.example for 99% success odds.",
    "go to www.foo.com",
    "see example.com for details",
    "42 concurrent players",
    "1,200 visits",
    "339812 lifetime visits",
    "10k players",
    "5m views",
    "$40 revenue",
    "captured 2026-06-26",
    "on 26/06/2026",
    "players: 42",
    "retention 30 percent",
    "80% retention",
    "29983 favorites",
    # Design quantities belong in design_assumptions, not in prose.
    "Use 15 minutes per round",
    "a 90 minute session",
    "Ship a 72-hour MVP",
    "a 3 day prototype",
    "adds 5 features",
    # The finding that showed a blacklist of metric words could never win:
    # "sessions" was simply not on the list.
    "The game has 80 daily sessions.",
    "runs 5 sessions per day",
    "12 matches per hour",
    "a 20 wave gauntlet",
    # A trailing count is still a count, however the sentence starts.
    "Phase 2 of 3",
    "Day 1: build a map with 3 distinct zones",
]

ALLOWED_PROSE = [
    "Day 1: build the baseplate",
    "Phase 2",
    "Milestone 1",
    "Step 1 set up, Step 2 test",
    # The digit is part of the word, not a quantity.
    "a 2D scrolling map",
    "1v1 duels",
    "three distinct zones",
    "Two players share one plot",
    "Day 3: playtest and cut what does not land",
]


@pytest.mark.parametrize("text", BLOCKED_PROSE)
def test_measurement_shaped_prose_is_refused(text):
    from app.schemas import FORBIDDEN_PROPOSAL_TEXT

    assert FORBIDDEN_PROPOSAL_TEXT.search(text), f"a measurement got through: {text}"


@pytest.mark.parametrize("text", ALLOWED_PROSE)
def test_an_ordinal_is_not_a_measurement(text):
    """The firewall refused every digit, so a numbered milestone plan could
    never validate and every audit failed closed on build_steps."""
    from app.schemas import FORBIDDEN_PROPOSAL_TEXT

    assert not FORBIDDEN_PROPOSAL_TEXT.search(text), f"an ordinal was refused: {text}"


def test_a_numbered_build_plan_validates():
    """End to end through the payload, which is where this actually failed."""
    from app.schemas import ProposalPayload

    payload = ProposalPayload.model_validate(_scout(build_steps=[
        "Day 1: block out one arena and a single lane",
        "Day 2: add the pet follow behaviour",
        "Day 3: playtest with two people and cut what does not land",
    ]))
    assert len(payload.build_steps) == 3


def test_a_build_step_carrying_a_metric_is_still_refused():
    """Positive control for the loosened rule."""
    from pydantic import ValidationError

    from app.schemas import ProposalPayload

    with pytest.raises(ValidationError):
        ProposalPayload.model_validate(_scout(build_steps=["Day 1: match the 339812 visits leader"]))


# --- 7. A stored audit survives a reload ----------------------------------


def test_the_latest_audit_is_served_back_for_a_candidate(session_factory, settings, monkeypatch):
    """An audit costs minutes and is written to the ledger, but nothing served
    it back, so reopening the brief showed nothing and read as a failed run."""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    _, candidate_id, *_ = seed(session_factory)
    llm = FakeLLM()
    monkeypatch.setattr(app.state, "orchestrator", orchestrator(session_factory, llm), raising=False)

    def dependency():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    try:
        client = TestClient(app)
        assert client.get(f"/api/candidates/{candidate_id}/audit").status_code == 404
        created = client.post(f"/api/candidates/{candidate_id}/audit")
        assert created.status_code == 200 and created.json()["proposal"]
        fetched = client.get(f"/api/candidates/{candidate_id}/audit")
        assert fetched.status_code == 200
        assert fetched.json()["audit_id"] == created.json()["audit_id"]
        assert fetched.json()["proposal"]["concept_title"] == created.json()["proposal"]["concept_title"]
    finally:
        app.dependency_overrides.clear()


# --- 8. Survivors of the mutation pass ------------------------------------


@pytest.mark.asyncio
async def test_the_request_actually_sends_the_stripped_schema(settings):
    """grammar_safe existing is not the guarantee; using it on every request is.

    With the raw schema Ollama answered "failed to parse grammar" with a 400
    and no audit could run at all.
    """
    client, seen = _client(settings.model_copy(update={"scout_deliberation_passes": 1}), [_scout()])
    await client.generate(agent="Venture Scout", niche="farming", sourced_name="garden", fact_ids=[])

    def max_lengths(node):
        if isinstance(node, dict):
            return ("maxLength" in node) or any(max_lengths(v) for v in node.values())
        if isinstance(node, list):
            return any(max_lengths(item) for item in node)
        return False

    assert seen, "no request was sent"
    assert not max_lengths(seen[0]["format"]), "the request carried maxLength into the grammar"


@pytest.mark.asyncio
async def test_a_refusal_names_the_field_that_was_refused(settings):
    """"ValidationError" alone sent the investigation after the wrong cause:
    the audit reported a budget problem when the model had cited a bad field."""
    poisoned = _scout(build_steps=["Day 1: match the 339812 visits leader"])
    client, _ = _client(settings.model_copy(update={"scout_deliberation_passes": 1}), [poisoned])
    with pytest.raises(LLMUnavailable) as caught:
        await client.generate(agent="Venture Scout", niche="farming", sourced_name="garden", fact_ids=[])
    message = str(caught.value)
    assert "build_steps" in message, message
    assert "URLs or metric-like claims" in message, message


def test_a_stored_audit_is_re_verified_when_it_is_served_back(session_factory, settings, monkeypatch):
    """The stored payload records what was admissible when the audit ran.
    Replaying its citation list would show withdrawn evidence as verified."""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app
    from app.models import AuditRecord

    _, candidate_id, *_ = seed(session_factory)
    invented = str(uuid4())
    with session_factory() as db:
        db.add(AuditRecord(candidate_id=candidate_id, proposal_id=None, payload={
            "candidate_id": candidate_id, "proposal_id": None,
            "proposal": {**_scout(supporting_fact_ids=[invented])},
            "gates": [], "evidence_state": "source_backed_design_speculative",
            "decision": "collection_only", "risks": ["A risk"], "note": "n",
            # What the ledger said at the time; stale by definition.
            "cited_fact_ids": [invented], "withdrawn_fact_ids": [],
        }))
        db.commit()

    def dependency():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    try:
        body = TestClient(app).get(f"/api/candidates/{candidate_id}/audit").json()
    finally:
        app.dependency_overrides.clear()

    assert body["cited_fact_ids"] == [], "a citation that no longer resolves was shown as verified"
    assert body["withdrawn_fact_ids"] == [invented]


# --- 9. The audit says what it is doing while it runs ---------------------


def test_the_activity_feed_reports_each_pass(session_factory, settings, monkeypatch):
    """An audit takes minutes and reported nothing until it finished, so a
    working run and a stuck one were indistinguishable."""
    import asyncio

    from app import audit_activity

    _, candidate_id, *_ = seed(session_factory)
    asyncio.run(orchestrator(session_factory, FakeLLM()).audit(candidate_id))

    state = audit_activity.snapshot(candidate_id)
    stages = [event["stage"] for event in state["events"]]
    assert stages[0] == "started"
    for expected in ("gates", "evidence", "proposal_accepted", "citations", "stored"):
        assert expected in stages, f"{expected} never reported; saw {stages}"
    assert not state["running"], "the feed never closed"
    assert [event["sequence"] for event in state["events"]] == list(range(1, len(stages) + 1))


def test_a_blocked_audit_reports_the_gate_that_stopped_it(session_factory, settings):
    """Silence is the failure mode being fixed; a blocked audit must say why."""
    import asyncio

    from app import audit_activity

    _, candidate_id, *_ = seed(session_factory, age=3)  # stale evidence
    asyncio.run(orchestrator(session_factory, FakeLLM()).audit(candidate_id))

    state = audit_activity.snapshot(candidate_id)
    stages = [event["stage"] for event in state["events"]]
    assert "gate_blocked" in stages, stages
    assert stages[-1] == "blocked"
    assert not state["running"]
    blocked = next(e for e in state["events"] if e["stage"] == "gate_blocked")
    assert blocked["detail"], "the blocking gate was reported with no reason"


def test_a_new_audit_does_not_show_the_previous_run(session_factory, settings):
    """Positive control: a stale feed would be worse than none."""
    import asyncio

    from app import audit_activity

    _, candidate_id, *_ = seed(session_factory)
    asyncio.run(orchestrator(session_factory, FakeLLM()).audit(candidate_id))
    first = len(audit_activity.snapshot(candidate_id)["events"])
    assert first

    audit_activity.start(candidate_id)
    assert [e["stage"] for e in audit_activity.snapshot(candidate_id)["events"]] == ["started"]
    assert audit_activity.snapshot(candidate_id)["running"] is True


@pytest.mark.asyncio
async def test_a_refused_attempt_reaches_the_feed(settings):
    """The refusal reason is the most useful thing to watch; it says what the
    model has to change, and it is what the log used to hide."""
    seen: list[tuple[str, str]] = []
    poisoned = _scout(build_steps=["Day 1: match the 339812 visits leader"])
    client, _ = _client(settings.model_copy(update={"scout_deliberation_passes": 1}), [poisoned])
    with pytest.raises(LLMUnavailable):
        await client.generate(
            agent="Venture Scout", niche="farming", sourced_name="garden", fact_ids=[],
            on_event=lambda stage, detail, **extra: seen.append((stage, detail)),
        )
    refusals = [detail for stage, detail in seen if stage == "attempt_refused"]
    assert refusals, [stage for stage, _ in seen]
    assert "build_steps" in refusals[0], refusals[0]


def test_the_model_passes_reach_the_drawer(session_factory, settings):
    """The audit has to hand the model a way to report, or the drawer shows
    the setup steps and then nothing for the minutes that actually matter."""
    import asyncio

    from app import audit_activity
    from app.llm import GeneratedProposal
    from app.schemas import ProposalPayload

    class ReportingLLM:
        async def generate(self, **kwargs):
            report = kwargs.get("on_event")
            assert report is not None, "the audit gave the model no way to report progress"
            report("draft_started", "Pass one of 3: reading the evidence and drafting")
            report("attempt_refused", "model refused: build_steps", model="fake", attempt=1)
            report("revision_ready", "Revision accepted from fake", model="fake")
            return GeneratedProposal(ProposalPayload(**_scout()), "fake")

        async def close(self):
            pass

    _, candidate_id, *_ = seed(session_factory)
    asyncio.run(orchestrator(session_factory, ReportingLLM()).audit(candidate_id))

    events = audit_activity.snapshot(candidate_id)["events"]
    stages = [event["stage"] for event in events]
    for expected in ("draft_started", "attempt_refused", "revision_ready"):
        assert expected in stages, f"{expected} never reached the feed; saw {stages}"
    refused = next(event for event in events if event["stage"] == "attempt_refused")
    assert refused["model"] == "fake" and refused["attempt"] == 1, refused


# --- 10. A finished audit is findable -------------------------------------


def test_a_candidate_view_says_whether_an_audit_exists(session_factory, settings, monkeypatch):
    """With a hundred-odd candidates and no marker, the only way to find a
    finished brief was to open them one at a time."""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    _, candidate_id, *_ = seed(session_factory)
    monkeypatch.setattr(app.state, "orchestrator", orchestrator(session_factory, FakeLLM()), raising=False)

    def dependency():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    try:
        client = TestClient(app)

        def flag() -> bool:
            runs = client.get("/api/research-runs").json()
            candidate = next(c for run in runs for c in run["candidates"] if c["id"] == candidate_id)
            return candidate["has_audit"]

        assert flag() is False, "a candidate with no audit was marked as audited"
        assert client.post(f"/api/candidates/{candidate_id}/audit").status_code == 200
        assert flag() is True, "a stored audit left no trace on the candidate"
    finally:
        app.dependency_overrides.clear()


def test_the_audited_set_notices_an_audit_written_in_the_same_session(session_factory, settings):
    """The lookup is cached per session, which is only safe if it reloads.

    A request that audits and then re-reads would otherwise keep reporting the
    candidate as un-audited for the rest of that session.
    """
    from app.main import _audited_candidate_ids
    from app.models import AuditRecord

    _, candidate_id, *_ = seed(session_factory)
    with session_factory() as db:
        assert candidate_id not in _audited_candidate_ids(db)
        db.add(AuditRecord(candidate_id=candidate_id, proposal_id=None, payload={
            "candidate_id": candidate_id, "proposal": None, "gates": [], "risks": [],
            "evidence_state": "blocked", "decision": "collection_only", "note": "n",
        }))
        db.commit()
        assert candidate_id in _audited_candidate_ids(db), "a stale cache hid an audit written in this session"


# --- 11. Every agent run is findable afterwards ---------------------------


def _history_client(session_factory, monkeypatch):
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    monkeypatch.setattr(app.state, "orchestrator", orchestrator(session_factory, FakeLLM()), raising=False)

    def dependency():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    return TestClient(app), app


def test_both_agents_appear_in_the_history(session_factory, settings, monkeypatch):
    """Both write to the ledger and neither was listed anywhere, so a finished
    concept or audit could only be found by remembering its candidate."""
    _, candidate_id, _, proposal_id = seed(session_factory)  # seeds a Meta Hunter proposal
    client, app = _history_client(session_factory, monkeypatch)
    try:
        assert client.post(f"/api/candidates/{candidate_id}/audit").status_code == 200
        runs = client.get("/api/agent-runs").json()
        by_kind = {run["kind"]: run for run in runs}
        assert set(by_kind) == {"meta_hunter", "venture_scout"}, [run["kind"] for run in runs]
        assert by_kind["meta_hunter"]["id"] == proposal_id
        assert by_kind["venture_scout"]["outcome"] == "design"
        for run in runs:
            assert run["candidate_id"] == candidate_id
            assert run["candidate_name"] == "Evidence Garden", run["candidate_name"]
            assert run["niche"] == "cozy farming"
            assert run["title"], "a produced design was listed with no title"
        assert [run["created_at"] for run in runs] == sorted((r["created_at"] for r in runs), reverse=True)
    finally:
        app.dependency_overrides.clear()


def test_history_can_be_filtered_to_one_agent(session_factory, settings, monkeypatch):
    seed(session_factory)
    client, app = _history_client(session_factory, monkeypatch)
    try:
        assert {run["kind"] for run in client.get("/api/agent-runs?kind=meta_hunter").json()} == {"meta_hunter"}
        assert client.get("/api/agent-runs?kind=venture_scout").json() == []
    finally:
        app.dependency_overrides.clear()


def test_a_blocked_audit_is_listed_with_its_reason(session_factory, settings, monkeypatch):
    """A run that produced nothing is still a run, and the reason is the point."""
    _, candidate_id, *_ = seed(session_factory, age=3)  # stale evidence
    client, app = _history_client(session_factory, monkeypatch)
    try:
        client.post(f"/api/candidates/{candidate_id}/audit")
        scout = next(r for r in client.get("/api/agent-runs").json() if r["kind"] == "venture_scout")
        assert scout["outcome"] == "blocked"
        assert scout["payload"] is None
        assert scout["blocking_reasons"], "a blocked run was listed with no reason"
    finally:
        app.dependency_overrides.clear()


def test_history_citations_carry_their_text_and_are_re_verified(session_factory, settings, monkeypatch):
    """History re-resolves citations rather than replaying the stored list, and
    shows the claim rather than a column of identical-looking row ids."""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.llm import GeneratedProposal
    from app.main import app
    from app.schemas import ProposalPayload

    class CitingLLM:
        """Cites the first fact it was handed, the way a real answer does."""

        async def generate(self, **kwargs):
            supplied = kwargs["fact_ids"]
            assert supplied, "the audit handed the model no evidence to cite"
            return GeneratedProposal(
                ProposalPayload(**_scout(supporting_fact_ids=[supplied[0]])), "fake",
            )

        async def close(self):
            pass

    _, candidate_id, *_ = seed(session_factory)
    monkeypatch.setattr(app.state, "orchestrator", orchestrator(session_factory, CitingLLM()), raising=False)

    def dependency():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    client = TestClient(app)
    try:
        client.post(f"/api/candidates/{candidate_id}/audit")
        scout = next(r for r in client.get("/api/agent-runs").json() if r["kind"] == "venture_scout")
        assert scout["cited_facts"], "a cited run came back with no fact text"
        assert [fact["id"] for fact in scout["cited_facts"]] == scout["cited_fact_ids"]
        for fact in scout["cited_facts"]:
            assert fact["text"] and fact["text"] != fact["id"]
        assert scout["withdrawn_fact_ids"] == []
    finally:
        app.dependency_overrides.clear()


def test_history_is_ordered_newest_first(session_factory, settings, monkeypatch):
    """Timestamps written back to back on this machine can be identical, so an
    ordering assertion over one batch passes without the sort doing anything."""
    from app.models import AuditRecord

    _, candidate_id, *_ = seed(session_factory)
    stamps = [
        datetime(2026, 1, 3, 12, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
    ]
    with session_factory() as db:
        for created in stamps:
            db.add(AuditRecord(candidate_id=candidate_id, proposal_id=None, created_at=created, payload={
                "candidate_id": candidate_id, "proposal": None, "gates": [],
                "risks": [created.isoformat()], "evidence_state": "blocked",
                "decision": "collection_only", "note": "n",
            }))
        db.commit()

    client, app = _history_client(session_factory, monkeypatch)
    try:
        listed = [run["created_at"] for run in client.get("/api/agent-runs?kind=venture_scout").json()]
    finally:
        app.dependency_overrides.clear()

    assert len(listed) == 3
    assert listed == sorted(listed, reverse=True), listed
    assert listed[0].startswith("2026-01-03"), listed


def test_history_interleaves_the_two_agents_by_time(session_factory, settings, monkeypatch):
    """Each agent is queried in its own descending order, so only the merge can
    put a newer concept above an older audit. Without it the page shows every
    audit first and then every concept, which is not a history."""
    from app.models import AuditRecord, Proposal

    _, candidate_id, *_ = seed(session_factory, hunter=False)
    with session_factory() as db:
        db.add(AuditRecord(candidate_id=candidate_id, proposal_id=None,
                           created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC), payload={
                               "candidate_id": candidate_id, "proposal": None, "gates": [],
                               "risks": ["old audit"], "evidence_state": "blocked",
                               "decision": "collection_only", "note": "n"}))
        db.add(Proposal(candidate_id=candidate_id, agent="meta_hunter", model_name="fake",
                        created_at=datetime(2026, 1, 2, 12, 0, tzinfo=UTC), payload=VALID))
        db.add(AuditRecord(candidate_id=candidate_id, proposal_id=None,
                           created_at=datetime(2026, 1, 3, 12, 0, tzinfo=UTC), payload={
                               "candidate_id": candidate_id, "proposal": None, "gates": [],
                               "risks": ["new audit"], "evidence_state": "blocked",
                               "decision": "collection_only", "note": "n"}))
        db.commit()

    client, app = _history_client(session_factory, monkeypatch)
    try:
        runs = client.get("/api/agent-runs").json()
    finally:
        app.dependency_overrides.clear()

    assert [run["kind"] for run in runs] == ["venture_scout", "meta_hunter", "venture_scout"],         [(run["kind"], run["created_at"]) for run in runs]


def test_history_withdraws_a_citation_that_no_longer_resolves(session_factory, settings, monkeypatch):
    """Positive control for re-verification: replaying the stored list would
    show evidence as verified long after it stopped being admissible."""
    from app.models import AuditRecord

    _, candidate_id, *_ = seed(session_factory)
    invented = str(uuid4())
    with session_factory() as db:
        db.add(AuditRecord(candidate_id=candidate_id, proposal_id=None, payload={
            "candidate_id": candidate_id, "gates": [], "risks": ["r"], "note": "n",
            "evidence_state": "source_backed_design_speculative", "decision": "collection_only",
            "proposal": _scout(supporting_fact_ids=[invented]),
            "cited_fact_ids": [invented], "withdrawn_fact_ids": [],
        }))
        db.commit()

    client, app = _history_client(session_factory, monkeypatch)
    try:
        scout = next(r for r in client.get("/api/agent-runs").json() if r["kind"] == "venture_scout")
    finally:
        app.dependency_overrides.clear()

    assert scout["cited_fact_ids"] == [], "a citation that no longer resolves was listed as verified"
    assert scout["cited_facts"] == []
    assert scout["withdrawn_fact_ids"] == [invented]


# --- 12. A retained draft carries its unanswered critique -----------------


@pytest.mark.asyncio
async def test_a_draft_kept_after_a_failed_revision_carries_its_concerns(settings):
    """The critique was computed, used to decide nothing, and thrown away, so
    an unrevised draft was indistinguishable from a revised one."""
    draft = _scout(concept_title="Unrevised")
    critique = {"weaknesses": ["Scope is too wide for three days"],
                "missing_dependencies": ["A save system nobody scoped"],
                "scope_risks": [], "unsupported_claims": ["Assumes players want co-op"]}
    broken = {**_scout(), "core_loop": "Visit https://example.com for details"}
    client, _ = _client(settings, [draft, critique, broken])

    generated = await client.generate(
        agent="Venture Scout", niche="farming", sourced_name="garden", fact_ids=[],
    )

    assert generated.payload.concept_title == "Unrevised"
    assert generated.revision_applied is False, "a failed revision was reported as applied"
    assert generated.critique == critique, "the critique was discarded"


@pytest.mark.asyncio
async def test_a_revised_design_reports_its_revision_applied(settings):
    """Positive control: a design that answered its critique is finished work."""
    critique = {"weaknesses": ["Too wide"], "missing_dependencies": [],
                "scope_risks": [], "unsupported_claims": []}
    client, _ = _client(settings, [_scout(concept_title="Draft"), critique, _scout(concept_title="Revised")])
    generated = await client.generate(
        agent="Venture Scout", niche="farming", sourced_name="garden", fact_ids=[],
    )
    assert generated.payload.concept_title == "Revised"
    assert generated.revision_applied is True
    assert generated.critique == critique


def test_unresolved_concerns_are_empty_once_the_revision_lands():
    from types import SimpleNamespace

    from app.workflows import unresolved_concerns

    critique = {"weaknesses": ["w"], "unsupported_claims": ["u"],
                "missing_dependencies": ["m"], "scope_risks": ["s"]}
    assert unresolved_concerns(SimpleNamespace(critique=critique, revision_applied=True)) == []
    assert unresolved_concerns(SimpleNamespace(critique=None, revision_applied=False)) == []
    assert unresolved_concerns(SimpleNamespace(critique=critique, revision_applied=False)) == ["w", "u", "m", "s"]


@pytest.mark.asyncio
async def test_an_audit_with_unanswered_concerns_is_recorded_as_incomplete(session_factory, settings):
    """A schema-valid draft is not a completed quality audit, and the stored
    record is what every later view reads."""
    from app.llm import GeneratedProposal
    from app.schemas import ProposalPayload

    critique = {"weaknesses": ["Scope is too wide"], "missing_dependencies": [],
                "scope_risks": [], "unsupported_claims": ["Assumes co-op demand"]}

    class UnrevisedLLM:
        async def generate(self, **kwargs):
            return GeneratedProposal(ProposalPayload(**_scout()), "fake", critique, revision_applied=False)

        async def close(self):
            pass

    _, candidate_id, *_ = seed(session_factory)
    result = await orchestrator(session_factory, UnrevisedLLM()).audit(candidate_id)

    assert result["evidence_state"] == "source_backed_design_incomplete", result["evidence_state"]
    assert result["revision_applied"] is False
    assert result["unresolved_concerns"] == ["Scope is too wide", "Assumes co-op demand"]
    assert result["proposal"], "the design itself should still be kept and shown"


@pytest.mark.asyncio
async def test_an_answered_audit_is_recorded_as_speculative_not_incomplete(session_factory, settings):
    """Positive control: marking everything incomplete would also pass above."""
    from app.llm import GeneratedProposal
    from app.schemas import ProposalPayload

    class RevisedLLM:
        async def generate(self, **kwargs):
            return GeneratedProposal(ProposalPayload(**_scout()), "fake",
                                     {"weaknesses": ["w"], "missing_dependencies": [],
                                      "scope_risks": [], "unsupported_claims": []},
                                     revision_applied=True)

        async def close(self):
            pass

    _, candidate_id, *_ = seed(session_factory)
    result = await orchestrator(session_factory, RevisedLLM()).audit(candidate_id)
    assert result["evidence_state"] == "source_backed_design_speculative"
    assert result["unresolved_concerns"] == []


@pytest.mark.asyncio
async def test_two_pass_deliberation_still_carries_the_unanswered_critique(settings):
    """With two passes the audit criticises its draft and never revises it, so
    the concerns are unresolved by construction. That branch had no test, and a
    mutation making it claim the revision landed went unnoticed.
    """
    critique = {"weaknesses": ["Scope is too wide for three days"],
                "missing_dependencies": [], "scope_risks": [],
                "unsupported_claims": ["Assumes players want co-op"]}
    two_pass = settings.model_copy(update={"scout_deliberation_passes": 2})
    client, seen = _client(two_pass, [_scout(concept_title="Draft only"), critique])

    generated = await client.generate(
        agent="Venture Scout", niche="farming", sourced_name="garden", fact_ids=[],
    )

    assert len(seen) == 2, "a two-pass audit should draft and critique, and stop"
    assert generated.payload.concept_title == "Draft only"
    assert generated.revision_applied is False, "no revision ran, yet one was reported"
    assert generated.critique == critique, "the critique was discarded"


def test_a_sequel_numeral_in_another_games_name_is_still_refused():
    """A known, accepted cost of the whitelist.

    The rule cannot tell a sequel number in "Toilet World Roleplay 2" from a
    count, and special-casing capitalised names would reopen the hole the
    whitelist closed. The prompt tells the model to drop the numeral instead,
    so this is a retry, not a dead end.
    """
    from app.schemas import contains_unsupported_measurement

    assert contains_unsupported_measurement("social roleplay (Toilet World Roleplay 2)")
    assert not contains_unsupported_measurement("social roleplay (Toilet World Roleplay)")
    assert not contains_unsupported_measurement("its sequel focuses on roleplay")


# --- 13. The model's own reasoning is reported, and marked as untrusted ----


@pytest.mark.asyncio
async def test_model_reasoning_is_reported_as_its_own_event(settings):
    """An operator watching a three-minute audit could see stage names but not
    what the model was working through. Reasoning is real output from the model,
    so it is reported under its own stage rather than mixed into the pipeline's
    own progress descriptions.
    """
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {
            "content": json.dumps(_scout()),
            "thinking": "First I considered the shared boat, then the scope.",
        }})

    client = OllamaProposalClient(
        settings=settings.model_copy(update={"scout_deliberation_passes": 1}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await client.generate(agent="Venture Scout", niche="farming", sourced_name="garden",
                          fact_ids=[], on_event=lambda stage, detail, **extra: seen.append((stage, detail)))

    reasoning = [detail for stage, detail in seen if stage == "reasoning"]
    assert reasoning, [stage for stage, _ in seen]
    assert "shared boat" in reasoning[0]


@pytest.mark.asyncio
async def test_a_credential_in_the_reasoning_is_redacted(settings):
    """Reasoning is untrusted text that reaches the browser and the checkpoint."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {
            "content": json.dumps(_scout()),
            "thinking": f"I would call https://api.example.com/v3?key={settings.youtube_api_key} next.",
        }})

    seen: list[str] = []
    client = OllamaProposalClient(
        settings=settings.model_copy(update={"scout_deliberation_passes": 1}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await client.generate(agent="Venture Scout", niche="farming", sourced_name="garden",
                          fact_ids=[], on_event=lambda stage, detail, **extra: seen.append(detail))

    assert not any(settings.youtube_api_key in detail for detail in seen), seen


@pytest.mark.asyncio
async def test_reasoning_is_capped(settings):
    from app.llm import REASONING_LIMIT

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {
            "content": json.dumps(_scout()), "thinking": "x" * (REASONING_LIMIT * 3)}})

    seen: list[tuple[str, str]] = []
    client = OllamaProposalClient(
        settings=settings.model_copy(update={"scout_deliberation_passes": 1}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await client.generate(agent="Venture Scout", niche="farming", sourced_name="garden",
                          fact_ids=[], on_event=lambda stage, detail, **extra: seen.append((stage, detail)))
    reasoning = next(detail for stage, detail in seen if stage == "reasoning")
    assert len(reasoning) == REASONING_LIMIT


def test_the_feed_marks_which_events_the_model_wrote(session_factory, settings):
    """Everything else in the feed is written by the pipeline. The page has to
    be able to tell them apart to label one as reasoning and not as fact."""
    from app import audit_activity

    audit_activity.start("candidate-1")
    audit_activity.emit("candidate-1", "draft_started", "Pass one of 3")
    audit_activity.emit("candidate-1", "reasoning", "I weighed the scope against three days.")

    events = {event["stage"]: event for event in audit_activity.snapshot("candidate-1")["events"]}
    assert events["draft_started"]["untrusted"] is False
    assert events["reasoning"]["untrusted"] is True
