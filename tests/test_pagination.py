"""Lists have to say how much they are not showing.

Sources, history and matching reviews returned a capped slice with no total,
so a search box filtered whatever happened to be in the first hundred rows and
reported nothing for everything past them.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.evidence import record_artifact
from app.main import app


@pytest.fixture
def client(session_factory, settings):
    def dependency():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def seed_sources(factory, count, owner="roblox.com"):
    with factory() as db:
        for index in range(count):
            record_artifact(db, url=f"https://games.roblox.com/v1/games?i={index}&o={owner}",
                            retrieval_method="scheduled_roblox_snapshot",
                            content_type="application/json", payload={"i": index},
                            source_tier="primary", owner=owner)
        db.commit()


def test_a_page_reports_the_total_behind_it(client, session_factory):
    seed_sources(session_factory, 12)
    body = client.get("/api/sources?paged=true&limit=5").json()
    assert body["total"] == 12, body
    assert len(body["items"]) == 5
    assert body["next_offset"] == 5


def test_the_last_page_has_no_next_offset(client, session_factory):
    seed_sources(session_factory, 7)
    body = client.get("/api/sources?paged=true&limit=5&offset=5").json()
    assert len(body["items"]) == 2
    assert body["next_offset"] is None, body


def test_paging_walks_every_record_exactly_once(client, session_factory):
    seed_sources(session_factory, 11)
    seen, offset = [], 0
    while offset is not None:
        body = client.get(f"/api/sources?paged=true&limit=4&offset={offset}").json()
        seen += [item["id"] for item in body["items"]]
        offset = body["next_offset"]
    assert len(seen) == 11
    assert len(set(seen)) == 11, "a record was returned on two different pages"


def test_filtering_happens_in_the_database_not_the_page(client, session_factory):
    """The point of the finding: a filter applied to one page is not a search."""
    seed_sources(session_factory, 8, owner="roblox.com")
    seed_sources(session_factory, 3, owner="youtube.com")
    body = client.get("/api/sources?paged=true&limit=2&q=youtube.com").json()
    assert body["total"] == 3, "the filter matched only what fitted on a page"
    assert len(body["items"]) == 2
    assert all("youtube.com" in item["url"] for item in body["items"])


def test_an_unmatched_filter_reports_zero_rather_than_the_unfiltered_page(client, session_factory):
    seed_sources(session_factory, 5)
    body = client.get("/api/sources?paged=true&q=nothing-matches-this").json()
    assert body["total"] == 0 and body["items"] == []


def test_the_bare_array_shape_still_works_for_existing_callers(client, session_factory):
    """Positive control: migrating the dashboard must not break what exists."""
    seed_sources(session_factory, 3)
    body = client.get("/api/sources").json()
    assert isinstance(body, list) and len(body) == 3


def test_history_totals_count_the_ledger_not_the_page(client, session_factory, monkeypatch):
    from tests.test_deep_research import FakeLLM, orchestrator, seed

    _, candidate_id, *_ = seed(session_factory)
    monkeypatch.setattr(app.state, "orchestrator", orchestrator(session_factory, FakeLLM()), raising=False)
    assert client.post(f"/api/candidates/{candidate_id}/audit").status_code == 200

    counts = client.get("/api/agent-runs/count").json()
    assert counts["venture_scout"] == 1
    assert counts["meta_hunter"] == 1
    assert counts["total"] == 2
    assert client.get("/api/agent-runs/count?kind=venture_scout").json()["total"] == 1
    assert client.get("/api/agent-runs/count?kind=meta_hunter").json()["total"] == 1


def test_matching_review_totals_count_the_queue_not_the_page(client, session_factory):
    """A reviewer could not tell fifty outstanding from five hundred."""
    from app.models import AssociationRecord, MatchSubject

    with session_factory() as db:
        for index in range(7):
            subject = MatchSubject(subject_type="youtube_video")
            db.add(subject)
            db.flush()
            db.add(AssociationRecord(
                subject_id=subject.id, outcome="review_required" if index < 5 else "auto_associate",
                matcher_version="assoc-v1", feature_schema_version="features-v1",
                normalization_version="norm-v2", threshold_version="thresholds-v1",
            ))
        db.commit()

    counts = client.get("/api/matching/reviews/count").json()
    assert counts["pending"] == 5, counts
    assert counts["all_associations"] == 7
    assert counts["total"] == 5
    assert client.get("/api/matching/reviews/count?include_resolved=true").json()["total"] == 7


def test_review_paging_reaches_the_second_page(client, session_factory):
    """Without an offset the queue could only ever show its first page."""
    from app.models import AssociationRecord, MatchSubject

    with session_factory() as db:
        for _ in range(6):
            subject = MatchSubject(subject_type="youtube_video")
            db.add(subject)
            db.flush()
            db.add(AssociationRecord(
                subject_id=subject.id, outcome="auto_associate",
                matcher_version="assoc-v1", feature_schema_version="features-v1",
                normalization_version="norm-v2", threshold_version="thresholds-v1",
            ))
        db.commit()

    first = client.get("/api/matching/reviews?include_resolved=true&limit=4&offset=0").json()
    second = client.get("/api/matching/reviews?include_resolved=true&limit=4&offset=4").json()
    assert len(first) == 4 and len(second) == 2
    ids = {row["association_id"] for row in first} | {row["association_id"] for row in second}
    assert len(ids) == 6, "paging returned the same records twice"


def _seed_runs(factory, audits: int, concepts: int):
    from app.models import AuditRecord, Proposal
    from tests.test_deep_research import VALID, seed

    _, candidate_id, *_ = seed(factory)
    with factory() as db:
        for index in range(audits):
            db.add(AuditRecord(candidate_id=candidate_id, proposal_id=None, payload={
                "candidate_id": candidate_id, "proposal": None, "gates": [],
                "risks": [f"r{index}"], "evidence_state": "blocked",
                "decision": "collection_only", "note": "n"}))
        for _ in range(concepts):
            db.add(Proposal(candidate_id=candidate_id, agent="meta_hunter",
                            payload=VALID, model_name="fake"))
        db.commit()
    return candidate_id


def test_history_total_counts_the_ledger_not_the_fetched_window(client, session_factory):
    """A total measured from the rows already fetched reports the page size."""
    _seed_runs(session_factory, audits=7, concepts=5)
    body = client.get("/api/agent-runs?paged=true&limit=3").json()
    # One Hunter proposal comes from the seed itself.
    assert body["total"] == 13, body["total"]
    assert len(body["items"]) == 3
    assert body["next_offset"] == 3


def test_history_paging_walks_every_run_once(client, session_factory):
    _seed_runs(session_factory, audits=4, concepts=4)
    seen, offset = [], 0
    while offset is not None:
        body = client.get(f"/api/agent-runs?paged=true&limit=3&offset={offset}").json()
        seen += [f"{run['kind']}:{run['id']}" for run in body["items"]]
        offset = body["next_offset"]
    assert len(seen) == 9
    assert len(set(seen)) == 9, "a run appeared on two pages"


def test_history_keeps_the_bare_array_for_existing_callers(client, session_factory):
    _seed_runs(session_factory, audits=2, concepts=0)
    body = client.get("/api/agent-runs").json()
    assert isinstance(body, list) and len(body) == 3
