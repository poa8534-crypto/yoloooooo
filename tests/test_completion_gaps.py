import json

import httpx
import pytest
from sqlalchemy import select
from test_deep_research import VALID, seed

from app.llm import LLMUnavailable, OllamaProposalClient
from app.models import Candidate, Fact, ResearchReport
from app.research_evidence import verified_fact_packet


@pytest.mark.parametrize("now,last,expected", [
    ("2026-09-15T01:00:00+05:30", "2026-09-13T02:00:00+05:30", True),
    ("2026-09-15T01:00:00+05:30", "2026-09-14T03:00:00+05:30", False),
    ("2026-09-15T02:00:00+05:30", "2026-09-14T03:00:00+05:30", True),
    ("2026-09-15T03:00:00+05:30", "2026-09-15T02:01:00+05:30", False),
    ("2026-09-15T01:00:00+05:30", None, True),
])
def test_snapshot_restart_catches_latest_due_slot(now, last, expected):
    from datetime import datetime

    from app.scheduler import snapshot_is_due
    assert snapshot_is_due(datetime.fromisoformat(now), datetime.fromisoformat(last) if last else None, 2, 0) is expected


def test_redaction_preserves_uvicorn_access_formatter_arguments(settings):
    import logging

    from uvicorn.logging import AccessFormatter

    from app.security import install_log_redaction
    install_log_redaction()
    logger = logging.getLogger("uvicorn.access")
    record = logger.makeRecord("uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:123", "GET", "/api?key=" + settings.youtube_api_key, "1.1", 200), None)
    output = AccessFormatter('%(client_addr)s %(request_line)s %(status_code)s', use_colors=False).format(record)
    assert "200" in output and settings.youtube_api_key not in output


@pytest.mark.asyncio
async def test_deep_decode_schema_requires_supplied_citation(settings):
    fid = "00000000-0000-0000-0000-000000000001"
    schemas = []
    def handler(request):
        schemas.append(json.loads(request.content)["format"])
        return httpx.Response(200, json={"message": {"content": json.dumps(VALID)}})
    client = OllamaProposalClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(LLMUnavailable):
        await client.generate(agent="Meta Hunter", niche="farming", sourced_name="garden", fact_ids=[fid], require_citations=True)
    assert len(schemas) == 4
    assert schemas[0]["properties"]["supporting_fact_ids"]["items"]["enum"] == [fid]
    assert schemas[0]["properties"]["supporting_fact_ids"]["minItems"] == 1


@pytest.mark.asyncio
async def test_complete_scout_schema_and_citation_are_accepted(settings):
    fid = "00000000-0000-0000-0000-000000000001"
    payload = {**VALID, "supporting_fact_ids": [fid], "essential_features": ["Planting"],
               "excluded_features": ["Trading"], "dependencies": ["Studio"], "validation_tasks": ["Playtest the loop"]}
    client = OllamaProposalClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"message": {"content": json.dumps(payload)}}))))
    result = await client.generate(agent="Venture Scout", niche="farming", sourced_name="garden", fact_ids=[fid], require_citations=True)
    assert result.payload.supporting_fact_ids == [fid]


def test_old_report_keeps_exact_valid_fact_after_newer_capture(session_factory, settings):
    from app.main import get_report
    from app.research_evidence import evidence_packet
    run_id, cid, _, _ = seed(session_factory)
    with session_factory() as db:
        original = evidence_packet(db, cid)
        db.add(ResearchReport(run_id=run_id, payload={"comparison": [{"candidate_id": cid, "facts": original}], "questions": []}))
        db.commit()
    seed(session_factory)
    with session_factory() as db:
        report = get_report(run_id, db)
        assert {f["id"] for f in report["comparison"][0]["facts"]} == {f["id"] for f in original}
        for fact in db.scalars(select(Fact)):
            assert verified_fact_packet(db, fact)


def test_legacy_recommendation_cannot_appear_as_active(session_factory, settings):
    from app.main import _candidate_view
    from app.models import DecisionRecord
    _, cid, _, _ = seed(session_factory)
    with session_factory() as db:
        db.add(DecisionRecord(candidate_id=cid, kind="recommend", rationale_codes=["legacy"]))
        db.commit()
        view = _candidate_view(db, db.get(Candidate, cid))
        assert view.decision == "collection_only" and view.score is None and view.confidence is None


@pytest.mark.asyncio
async def test_direct_api_audit_is_blocked_without_evidence(session_factory, settings, monkeypatch):
    from fastapi.testclient import TestClient
    from test_deep_research import FakeLLM, orchestrator

    from app.db import get_db
    from app.main import app
    _, cid, _, _ = seed(session_factory, age=3)
    llm = FakeLLM()
    monkeypatch.setattr(app.state, "orchestrator", orchestrator(session_factory, llm), raising=False)
    def dependency():
        with session_factory() as db: yield db
    app.dependency_overrides[get_db] = dependency
    try:
        client = TestClient(app)
        result = client.post(f"/api/candidates/{cid}/audit")
        assert result.status_code == 200
        assert result.json()["evidence_state"] == "blocked" and not llm.calls
        assert client.get("/api/audits/" + result.json()["audit_id"]).status_code == 200
    finally:
        app.dependency_overrides.clear()
