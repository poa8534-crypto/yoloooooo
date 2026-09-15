import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.audit_jobs import AuditJobs, JobBudget, JobConflict
from app.models import Candidate, ResearchRun


def candidate(factory):
    with factory() as db:
        run = ResearchRun(niche="job test")
        db.add(run)
        db.flush()
        item = Candidate(run_id=run.id, external_id="77")
        db.add(item)
        db.commit()
        return item.id


class Worker:
    def __init__(self):
        self.release = asyncio.Event()
        self.calls = []

    async def audit(self, cid, pid, *, budget, operation, on_event):
        self.calls.append((cid, pid, operation))
        budget.model_attempt("fake-local")
        on_event("draft_started", "Drafting")
        await self.release.wait()
        return {"audit_id": "saved", "proposal": {"design": "speculative"}}


@pytest.mark.asyncio
async def test_deduplicates_before_queue_and_persists_result(session_factory):
    cid = candidate(session_factory)
    worker = Worker()
    jobs = AuditJobs(session_factory, worker)
    first = jobs.start(cid, "analyze_game")
    second = jobs.start(cid, "analyze_game")
    assert first["id"] == second["id"]
    worker.release.set()
    await jobs.tasks[first["id"]]
    result = AuditJobs(session_factory, worker).get(first["id"])
    assert result["status"] == "complete" and result["audit_id"] == "saved"
    assert result["model_attempts"] == 1 and len(worker.calls) == 1
    assert [e["sequence"] for e in result["events"]] == list(range(1, len(result["events"]) + 1))


@pytest.mark.asyncio
async def test_cancel_does_not_leave_running_job(session_factory):
    jobs = AuditJobs(session_factory, Worker())
    job = jobs.start(candidate(session_factory), "analyze_game")
    await asyncio.sleep(0)
    result = await jobs.cancel(job["id"])
    assert result["status"] == "cancelled" and not jobs.tasks


@pytest.mark.asyncio
async def test_restart_preserves_budget_and_requires_explicit_resume(session_factory):
    jobs = AuditJobs(session_factory, Worker())
    job = jobs.start(candidate(session_factory), "analyze_game")
    await asyncio.sleep(0)
    await jobs.close()
    old = jobs.get(job["id"])
    assert old["status"] == "interrupted"
    worker = Worker()
    worker.release.set()
    restarted = AuditJobs(session_factory, worker)
    restarted.reconcile()
    assert not worker.calls
    resumed = restarted.resume(job["id"])
    await restarted.tasks[job["id"]]
    assert resumed["created_at"] == old["created_at"]
    assert restarted.get(job["id"])["model_attempts"] == old["model_attempts"] + 1


@pytest.mark.asyncio
async def test_deadline_and_attempt_caps_cannot_reset(session_factory):
    jobs = AuditJobs(session_factory, Worker())
    job = jobs.start(candidate(session_factory), "analyze_game")
    await jobs.close()
    jobs.update(job["id"], created_at=(datetime.now(UTC) - timedelta(minutes=31)).isoformat(), status="interrupted")
    with pytest.raises(JobConflict):
        jobs.resume(job["id"])
    with pytest.raises(TimeoutError):
        JobBudget(jobs, job["id"]).model_attempt("fake")


@pytest.mark.asyncio
async def test_audit_idea_requires_exact_proposal(session_factory):
    jobs = AuditJobs(session_factory, Worker())
    with pytest.raises(ValueError):
        jobs.start(candidate(session_factory), "audit_idea", "unknown")
    assert not jobs.tasks
