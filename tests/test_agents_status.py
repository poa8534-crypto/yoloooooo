"""The "Running" badge has to be able to stop saying Running.

It was read out of `/api/research-runs`, which builds a full candidate view for
every candidate of the last twenty-five runs: an evidence packet and a citation
re-verification each. Measured on the real ledger that is **seven seconds** of
database work, and the page polled it every five, so the badge always described
the run as it had been several seconds ago and a finished run went on claiming
to be running until the next slow answer arrived.

This endpoint reads the run rows and nothing else.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.models import AuditRecord, Candidate, Proposal, ResearchRun


@pytest.fixture
def client(session_factory, settings, monkeypatch):
    from app import main as main_module

    def override():
        with session_factory() as db:
            yield db

    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    main_module.app.dependency_overrides[main_module.get_db] = override
    yield TestClient(main_module.app)
    main_module.app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def no_jobs(monkeypatch):
    from app import main as main_module

    class Jobs:
        def recent(self, limit=20):
            return []

    monkeypatch.setattr(main_module.app.state, "audit_jobs", Jobs(), raising=False)


def a_run(session_factory, status, niche="cozy farming"):
    with session_factory() as db:
        run = ResearchRun(niche=niche, status=status)
        db.add(run)
        db.commit()
        return run.id


def test_a_finished_run_is_not_reported_as_running(client, session_factory):
    a_run(session_factory, "complete")

    hunter = client.get("/api/agents/status").json()["hunter"]

    assert hunter["running"] is False
    assert hunter["runs"] == []


@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_working_run_is_reported_with_what_it_is_doing(client, session_factory, status):
    run_id = a_run(session_factory, status)

    hunter = client.get("/api/agents/status").json()["hunter"]

    assert hunter["running"] is True
    assert [run["id"] for run in hunter["runs"]] == [run_id]
    assert hunter["runs"][0]["niche"] == "cozy farming"
    assert "stage" in hunter["runs"][0], "the badge can say Running but not what it is doing"


@pytest.mark.parametrize("status", ["complete", "partial", "failed", "interrupted", "cancelled"])
def test_no_finished_status_counts_as_working(client, session_factory, status):
    """`interrupted` is the one that caused this: it is finished, it is not
    running, and anything that treats it as active strands the badge on."""
    a_run(session_factory, status)

    assert client.get("/api/agents/status").json()["hunter"]["running"] is False


def test_the_latest_run_is_reported_even_when_nothing_is_working(client, session_factory):
    """So the page can say what happened last rather than going blank."""
    run_id = a_run(session_factory, "partial", niche="pet hatching")

    latest = client.get("/api/agents/status").json()["hunter"]["latest"]

    assert latest["id"] == run_id
    assert latest["niche"] == "pet hatching"
    assert latest["status"] == "partial"


def test_no_runs_at_all_reports_nothing_rather_than_failing(client):
    body = client.get("/api/agents/status").json()

    assert body["hunter"] == {"running": False, "runs": [], "latest": None}


def test_an_active_scout_job_is_reported(client, session_factory, monkeypatch):
    from app import main as main_module

    class Jobs:
        def recent(self, limit=20):
            return [{"id": "job-1", "status": "running", "candidate_id": "c1"},
                    {"id": "job-2", "status": "complete", "candidate_id": "c2"}]

    monkeypatch.setattr(main_module.app.state, "audit_jobs", Jobs(), raising=False)
    scout = client.get("/api/agents/status").json()["scout"]

    assert scout["running"] is True
    assert [job["id"] for job in scout["jobs"]] == ["job-1"], "a finished job read as active"


def test_the_answer_is_stamped_so_the_page_can_tell_how_old_it_is(client):
    assert client.get("/api/agents/status").json()["observed_at"]


def test_it_does_not_pack_evidence_for_a_single_candidate(client, session_factory,
                                                          monkeypatch):
    """The whole point. If this ever starts touching candidates it becomes the
    seven-second call it was written to replace, and it is polled every three
    seconds."""
    from app import main as main_module

    def refuse(*_args, **_kwargs):
        raise AssertionError("the status endpoint packed an evidence packet")

    monkeypatch.setattr(main_module, "_candidate_view", refuse)
    monkeypatch.setattr(main_module, "evidence_packet", refuse, raising=False)
    a_run(session_factory, "running")

    assert client.get("/api/agents/status").status_code == 200


def test_it_answers_fast_enough_to_poll(client, session_factory):
    """A budget, not a benchmark: the page asks every three seconds."""
    for index in range(12):
        run_id = a_run(session_factory, "complete", niche=f"niche {index}")
        with session_factory() as db:
            for _ in range(8):
                candidate = Candidate(run_id=run_id, external_id="77")
                db.add(candidate)
                db.flush()
                db.add(Proposal(candidate_id=candidate.id, agent="meta_hunter",
                                payload={"concept_title": "t"}, model_name="fake"))
                db.add(AuditRecord(candidate_id=candidate.id, payload={"risks": []}))
            db.commit()

    client.get("/api/agents/status")  # warm the connection
    start = time.perf_counter()
    client.get("/api/agents/status")
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"the status poll took {elapsed:.2f}s with 96 candidates"
