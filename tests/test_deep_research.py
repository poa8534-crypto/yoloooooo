from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from app.association import AssociationService
from app.config import Settings
from app.connectors import ConnectorError, ConnectorResult, Connectors
from app.deep_research import discovery_queries
from app.evidence import add_json_observation, record_artifact
from app.llm import GeneratedProposal, OllamaProposalClient
from app.models import (
    Candidate,
    Proposal,
    ResearchCheckpoint,
    ResearchReport,
    ResearchRun,
    SourceArtifact,
)
from app.research_budget import BudgetExceeded, RunBudget
from app.research_evidence import history
from app.schemas import ProposalPayload
from app.security import install_log_redaction, redact, sanitize_url
from app.workflows import ResearchOrchestrator, _roblox_facts

VALID = {
    "concept_title": "Lantern Garden",
    "core_loop": "Plant strange seeds and share harvesting tasks.",
    "differentiator": "Shape the shared garden through cooperative crafting.",
    "build_steps": ["Build a planting interaction"],
    "risks": ["Scope needs a human playtest"],
    "questions": [], "supporting_fact_ids": [],
}


@pytest.fixture
def settings(tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    cfg = Settings(_env_file=None, artifact_dir=artifacts, model_dir=tmp_path / "models", youtube_api_key="SYNTHETIC_SECRET_12345", tavily_api_key="SYNTHETIC_TAVILY_12345")
    monkeypatch.setattr("app.config.get_settings", lambda: cfg)
    monkeypatch.setattr("app.evidence.get_settings", lambda: cfg)
    monkeypatch.setattr("app.workflows.load_artifact", lambda: None)
    return cfg


def seed(factory, age=0, universe="77", hunter=True):
    with factory() as db:
        run = ResearchRun(niche="cozy farming")
        db.add(run); db.flush()
        candidate = Candidate(run_id=run.id, external_id=universe)
        db.add(candidate); db.flush()
        art = record_artifact(db, url="https://games.roblox.com/v1/games", retrieval_method="test",
                              content_type="application/json", payload={"data": [{"name": "Evidence Garden", "playing": 42,
                              "visits": 900, "favoritedCount": 80, "updated": "source date"}]}, source_tier="primary",
                              captured_at=datetime.now(UTC) - timedelta(days=age))
        _roblox_facts(db, candidate, art, 0)
        proposal = Proposal(candidate_id=candidate.id, agent="meta_hunter", payload=VALID, model_name="fake") if hunter else None
        if proposal:
            db.add(proposal)
        db.commit()
        return run.id, candidate.id, art.id, proposal.id if proposal else None


class FakeLLM:
    def __init__(self): self.calls = []
    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("before_attempt"): kwargs["before_attempt"]("fake")
        return GeneratedProposal(ProposalPayload(**VALID), "fake")
    async def close(self): pass


class FakeConnectors:
    def __init__(self): self.calls = []
    async def tavily_search(self, query):
        self.calls.append(("tavily", query))
        return ConnectorResult("https://api.tavily.com/search", {"results": [{"url": "https://www.roblox.com/games/123/Test"}]})
    async def universe_for_place(self, place): return ConnectorResult("https://apis.roblox.com/resolve", {"universeId": 77})
    async def roblox_games(self, ids):
        return ConnectorResult("https://games.roblox.com/v1/games", {"data": [{"id": 77, "rootPlaceId": 123, "name": "Evidence Garden", "playing": 42, "visits": 900, "favoritedCount": 80, "updated": "source date", "description": "Plant seeds together."}]})
    async def youtube_search(self, query):
        self.calls.append(("youtube", query))
        return ConnectorResult("https://www.googleapis.com/youtube/v3/search", {"items": []})
    async def capture_page(self, url): return ConnectorResult(url, "Primary page without a matching passage.", "text/plain")
    async def close(self): pass


def orchestrator(factory, llm=None, connectors=None):
    return ResearchOrchestrator(factory, llm=llm or FakeLLM(), connectors=connectors or FakeConnectors(), associations=AssociationService())


def test_credentials_removed_from_urls_and_logs(settings, caplog):
    url = f"https://user:password@example.com/api?key={settings.youtube_api_key}&part=snippet#token"
    assert sanitize_url(url) == "https://example.com/api?part=snippet"
    install_log_redaction()
    logger = logging.getLogger("credential-redaction-test")
    with caplog.at_level(logging.INFO): logger.info("request %s", url)
    assert settings.youtube_api_key not in caplog.text
    assert settings.youtube_api_key not in redact(f"failure {url}")


@pytest.mark.asyncio
async def test_connector_success_and_failure_do_not_expose_secret(settings):
    for status in (200, 403):
        client = Connectors(settings, httpx.AsyncClient(transport=httpx.MockTransport(lambda _request, code=status: httpx.Response(code, json={"items": []}))))
        if status == 200:
            result = await client.youtube_search("cozy")
            assert "key=" not in result.url and settings.youtube_api_key not in result.url
        else:
            with pytest.raises(ConnectorError) as error: await client.youtube_search("cozy")
            assert settings.youtube_api_key not in str(error.value)


def test_security_migration_preserves_ids_hashes_and_is_idempotent(session_factory, settings):
    from app.migrations import (
        drop_append_only_triggers,
        install_append_only_triggers,
        redact_credential_urls,
    )
    _, _, aid, _ = seed(session_factory)
    engine = session_factory.kw["bind"]
    drop_append_only_triggers(engine)
    try:
        with engine.begin() as conn:
            before = conn.execute(text("SELECT id, sha256, raw_path FROM source_artifacts WHERE id=:id"), {"id": aid}).one()
            conn.execute(text("UPDATE source_artifacts SET url=:url WHERE id=:id"), {"id": aid, "url": "https://youtube.com/api?key=legacy-secret&part=snippet"})
        assert redact_credential_urls(engine) == 1
        assert redact_credential_urls(engine) == 0
        with engine.connect() as conn:
            assert conn.execute(text("SELECT id, sha256, raw_path FROM source_artifacts WHERE id=:id"), {"id": aid}).one() == before
            assert "legacy-secret" not in str(conn.execute(text("SELECT * FROM security_redactions")).all())
    finally: install_append_only_triggers(engine)


@pytest.mark.parametrize("field,value", [("core_loop", "Use 15 minutes per round"), ("score", 99), ("confidence", 80), ("verdict", "recommend"), ("sources", ["Invented source"]), ("supporting_fact_ids", ["fact-bogus"])])
def test_firewall_rejects_unauthorized_outputs(field, value):
    with pytest.raises(ValidationError): ProposalPayload(**{**VALID, field: value})


def test_numeric_design_assumptions_are_separate():
    payload = ProposalPayload(**VALID, design_assumptions=[{"kind": "session_length", "value": 15, "unit": "minutes"}])
    assert payload.design_assumptions[0].basis == "unverified_design_assumption"


@pytest.mark.asyncio
async def test_llm_receives_values_and_falls_back_with_attempt_accounting(settings):
    prompts, attempts = [], []
    def handler(req):
        body = json.loads(req.content)
        prompts.append(body)
        if body["model"] == settings.ollama_primary_model:
            return httpx.Response(200, json={"message": {"content": json.dumps({**VALID, "score": 99})}})
        return httpx.Response(200, json={"message": {"content": json.dumps(VALID)}})
    # One pass, so this stays a test of retry and fallback accounting.
    # Deliberation across passes has its own tests.
    single = settings.model_copy(update={"scout_deliberation_passes": 1})
    model = OllamaProposalClient(single, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await model.generate(agent="Venture Scout", niche="farming", sourced_name="garden", fact_ids=[],
                         evidence=[{"text": "Roblox reported 42 concurrent players", "slots": [{"value": 42}]}],
                         hunter_proposal=VALID, before_attempt=attempts.append)
    assert attempts == [single.ollama_primary_model] * 2 + [single.ollama_fallback_model]
    assert "42 concurrent players" in prompts[0]["messages"][-1]["content"]
    assert "EXACT supplied Hunter proposal" in prompts[0]["messages"][-1]["content"]
    assert "Previous response was invalid" in prompts[1]["messages"][-1]["content"]
    assert prompts[0]["think"] is True and prompts[0]["options"]["num_ctx"] == 16384


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["stale", "broken", "conflict"])
async def test_direct_audit_blocks_before_model(session_factory, settings, condition):
    _, cid, aid, _ = seed(session_factory, age=3 if condition == "stale" else 0)
    if condition == "broken":
        with session_factory() as db: Path(db.get(SourceArtifact, aid).raw_path).write_text("tampered")
    if condition == "conflict":
        with session_factory() as db:
            art = record_artifact(db, url="https://games.roblox.com/other", retrieval_method="test", content_type="application/json", payload={"ccu": 9999}, source_tier="primary")
            add_json_observation(db, artifact=art, candidate_id=cid, metric="roblox_playing", pointer="/ccu")
            db.commit()
    llm = FakeLLM()
    result = await orchestrator(session_factory, llm).audit(cid)
    assert result["evidence_state"] == "blocked" and result["audit_id"]
    assert llm.calls == []


@pytest.mark.asyncio
async def test_scout_binds_selected_hunter_and_roblox_only_is_explicit(session_factory, settings):
    _, cid, _, pid = seed(session_factory)
    llm = FakeLLM()
    result = await orchestrator(session_factory, llm).audit(cid, pid)
    assert result["proposal_id"] == pid
    assert llm.calls[0]["hunter_proposal"] == VALID
    assert any(g["state"] == "not_applicable" for g in result["gates"])
    assert any("42 concurrent players" in f["text"] for f in llm.calls[0]["evidence"])


def test_canonical_history_does_not_sum_repeated_discoveries(session_factory, settings):
    _, first, _, _ = seed(session_factory)
    seed(session_factory)
    with session_factory() as db:
        series = history(db, first)
        ccu = [m for p in series["points"] for m in p["measurements"] if m["metric"] == "roblox_playing"]
        assert len(ccu) == 1 and ccu[0]["value"] == 42
        assert series["trend"] is None


@pytest.mark.asyncio
async def test_deep_run_followups_report_and_distinct_agents(session_factory, settings):
    with session_factory() as db:
        run = ResearchRun(niche="cooperative cozy farming short social sessions")
        db.add(run); db.flush(); rid = run.id
        db.add(ResearchCheckpoint(run_id=rid, state={"mode": "deep"})); db.commit()
    llm, connectors = FakeLLM(), FakeConnectors()
    await orchestrator(session_factory, llm, connectors).research(rid)
    with session_factory() as db:
        run = db.get(ResearchRun, rid)
        report = db.scalar(select(ResearchReport).where(ResearchReport.run_id == rid))
        assert run.status == "complete", run.message
        assert report.payload["progress"]["stop_reason"] == "no_new_admissible_evidence_two_rounds"
        assert report.payload["passing_recommendations"] == []
        assert report.payload["concepts"] and report.payload["audits"]
        assert report.payload["api_usage"]["model_attempts"] == 2
        assert any(q["id"] == "creators" and q["state"] == "limited" for q in report.payload["questions"])
    assert [c["agent"] for c in llm.calls] == ["Meta Hunter", "Venture Scout"]
    assert len({q for name, q in connectors.calls if name == "tavily"}) > 1
    assert any('"Evidence Garden"' in q for name, q in connectors.calls if name == "youtube")


@pytest.mark.asyncio
async def test_request_cache_resume_does_not_spend_again(session_factory, settings):
    rid, *_ = seed(session_factory)
    connector = FakeConnectors()
    first = RunBudget(session_factory, rid)
    await first.call(connector, "tavily_search", "same query")
    second = RunBudget(session_factory, rid)
    await second.call(connector, "tavily_search", "same query")
    assert len(connector.calls) == 1 and second.state["usage"]["tavily_search"] == 1


@pytest.mark.asyncio
async def test_expired_run_finalizes_without_spending(session_factory, settings):
    with session_factory() as db:
        run = ResearchRun(niche="cozy farming", created_at=datetime.now(UTC) - timedelta(minutes=31))
        db.add(run); db.flush(); rid = run.id
        db.add(ResearchCheckpoint(run_id=rid, state={"mode": "deep"})); db.commit()
    llm, connectors = FakeLLM(), FakeConnectors()
    await orchestrator(session_factory, llm, connectors).research(rid)
    assert connectors.calls == [] and llm.calls == []
    with session_factory() as db: assert db.scalar(select(ResearchReport).where(ResearchReport.run_id == rid)) is not None


@pytest.mark.asyncio
async def test_budget_exhausted_before_request(session_factory, settings):
    rid, *_ = seed(session_factory)
    b = RunBudget(session_factory, rid)
    b.state["limits"]["tavily_search"] = 0
    connectors = FakeConnectors()
    with pytest.raises(BudgetExceeded): await b.call(connectors, "tavily_search", "never sent")
    assert connectors.calls == []


def test_queries_decompose_niche():
    queries = discovery_queries("cooperative cozy farming with short social sessions")
    assert 3 < len(queries) <= 12
    assert len(set(queries)) == len(queries)
