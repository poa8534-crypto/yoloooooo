"""Scout job guarantees that the first pass of tests did not defend.

Each test here follows a mutation check: the guard was deleted, the suite
stayed green, and the claim turned out to be unprotected. Codex flagged this
risk when it wrote the job code -- a passing total is not evidence that new
code is covered.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.audit_jobs import PREFIX, AuditJobs, JobConflict
from app.models import Candidate, Proposal, ResearchRun, SystemState
from tests.test_deep_research import VALID


def seed_candidate(factory, *, with_proposal: bool = False):
    with factory() as db:
        run = ResearchRun(niche="job guarantees")
        db.add(run)
        db.flush()
        candidate = Candidate(run_id=run.id, external_id="77")
        db.add(candidate)
        db.flush()
        proposal_id = None
        if with_proposal:
            proposal = Proposal(candidate_id=candidate.id, agent="meta_hunter",
                                payload=VALID, model_name="fake")
            db.add(proposal)
            db.flush()
            proposal_id = proposal.id
        db.commit()
        return candidate.id, proposal_id


class Idle:
    """An orchestrator that never finishes unless released."""

    def __init__(self, result=None):
        self.release = asyncio.Event()
        self.calls = []
        self.result = result if result is not None else {"audit_id": "saved", "proposal": {"d": 1}}

    async def audit(self, cid, pid, *, budget, operation, on_event):
        self.calls.append((cid, pid, operation))
        await self.release.wait()
        return self.result


# --- 1. Conflicting work is refused, not silently merged ------------------


@pytest.mark.asyncio
async def test_a_different_operation_on_the_same_game_is_refused(session_factory):
    """Returning the in-flight job would hand back an analysis when an audit
    was asked for, under a job id the caller believes is theirs."""
    candidate_id, proposal_id = seed_candidate(session_factory, with_proposal=True)
    worker = Idle()
    jobs = AuditJobs(session_factory, worker)
    first = jobs.start(candidate_id, "analyze_game")
    try:
        with pytest.raises(JobConflict):
            jobs.start(candidate_id, "audit_idea", proposal_id)
        assert len(worker.calls) <= 1
    finally:
        worker.release.set()
        await jobs.close()
    assert jobs.get(first["id"])["operation"] == "analyze_game"


@pytest.mark.asyncio
async def test_the_same_operation_is_still_deduplicated(session_factory):
    """Positive control: refusing everything would also pass the test above."""
    candidate_id, _ = seed_candidate(session_factory)
    worker = Idle()
    jobs = AuditJobs(session_factory, worker)
    try:
        first = jobs.start(candidate_id, "analyze_game")
        second = jobs.start(candidate_id, "analyze_game")
        assert first["id"] == second["id"]
    finally:
        worker.release.set()
        await jobs.close()


# --- 2. A restart tells the truth about what it found ---------------------


def _orphan_job(factory, candidate_id, status="running"):
    """A job row left behind by a process that died without unwinding."""
    with factory() as db:
        db.add(SystemState(key=PREFIX + "orphan", value_json={
            "id": "orphan", "candidate_id": candidate_id, "operation": "analyze_game",
            "proposal_id": None, "status": status,
            "created_at": "2026-09-15T00:00:00+00:00", "completed_at": None,
            "model_attempts": 3, "audit_id": None, "events": [], "error": None,
        }))
        db.commit()


def test_a_job_orphaned_by_a_restart_is_marked_interrupted(session_factory):
    """The existing restart test cancels the task, so the CancelledError
    handler sets the status and `reconcile` itself is never exercised. A
    process that is killed outright runs no handler at all.
    """
    candidate_id, _ = seed_candidate(session_factory)
    _orphan_job(session_factory, candidate_id)

    jobs = AuditJobs(session_factory, Idle())
    assert jobs.get("orphan")["status"] == "running"
    jobs.reconcile()

    recovered = jobs.get("orphan")
    assert recovered["status"] == "interrupted", "a dead job still looks alive after a restart"
    assert recovered["completed_at"], "an interrupted job was left with no end time"
    assert recovered["model_attempts"] == 3, "the restart reset consumed budget"


def test_a_restart_does_not_replay_model_work(session_factory):
    """Resuming has to be an explicit decision. Re-launching orphaned jobs on
    startup would spend quota and GPU time nobody asked for."""
    candidate_id, _ = seed_candidate(session_factory)
    _orphan_job(session_factory, candidate_id)

    worker = Idle()
    jobs = AuditJobs(session_factory, worker)
    jobs.reconcile()

    assert worker.calls == [], "a restart replayed model work on its own"
    assert jobs.tasks == {}, "a restart scheduled work without being asked"


def test_a_finished_job_is_untouched_by_reconcile(session_factory):
    """Positive control: marking everything interrupted would also satisfy the
    test above, and would rewrite history for jobs that really did finish."""
    candidate_id, _ = seed_candidate(session_factory)
    _orphan_job(session_factory, candidate_id, status="complete")

    jobs = AuditJobs(session_factory, Idle())
    jobs.reconcile()
    assert jobs.get("orphan")["status"] == "complete"


# --- 3. The operation contract is enforced at the door --------------------


def test_analyze_game_refuses_a_hunter_proposal(session_factory):
    """The two operations mean different things. Accepting a proposal here
    would quietly turn an evidence-only analysis into a critique of a design
    the caller never asked about."""
    candidate_id, proposal_id = seed_candidate(session_factory, with_proposal=True)
    jobs = AuditJobs(session_factory, Idle())
    with pytest.raises(ValueError):
        jobs.start(candidate_id, "analyze_game", proposal_id)
    assert jobs.tasks == {}


def test_an_unknown_operation_is_refused(session_factory):
    candidate_id, _ = seed_candidate(session_factory)
    jobs = AuditJobs(session_factory, Idle())
    with pytest.raises(ValueError):
        jobs.start(candidate_id, "summarise_everything")
    assert jobs.tasks == {}


def test_a_missing_candidate_is_refused(session_factory):
    jobs = AuditJobs(session_factory, Idle())
    with pytest.raises(KeyError):
        jobs.start("no-such-candidate", "analyze_game")
    assert jobs.tasks == {}


# --- 4. A job that produced nothing does not report success ---------------


@pytest.mark.asyncio
async def test_a_job_whose_audit_produced_no_design_reports_blocked(session_factory):
    """`complete` has to mean a design exists. A blocked audit reported as
    complete is the same lie the old "within the budget" message told."""
    candidate_id, _ = seed_candidate(session_factory)
    worker = Idle(result={"audit_id": "saved", "proposal": None})
    worker.release.set()
    jobs = AuditJobs(session_factory, worker)
    job = jobs.start(candidate_id, "analyze_game")
    await jobs.tasks[job["id"]]

    finished = jobs.get(job["id"])
    assert finished["status"] == "blocked", finished["status"]
    assert finished["audit_id"] == "saved", "the blocked audit record was not linked"


@pytest.mark.asyncio
async def test_a_job_whose_audit_produced_a_design_reports_complete(session_factory):
    """Positive control for the pairing above."""
    candidate_id, _ = seed_candidate(session_factory)
    worker = Idle()
    worker.release.set()
    jobs = AuditJobs(session_factory, worker)
    job = jobs.start(candidate_id, "analyze_game")
    await jobs.tasks[job["id"]]
    assert jobs.get(job["id"])["status"] == "complete"


# --- 5. Nothing secret reaches the progress feed --------------------------


def test_event_detail_is_redacted(session_factory, settings):
    """Progress events are written to the state store and served to the
    browser. A stage detail carrying a key would persist it in both places.
    """
    candidate_id, _ = seed_candidate(session_factory)
    # Written directly rather than started, so the test needs no event loop and
    # exercises only the event path.
    _orphan_job(session_factory, candidate_id)
    jobs = AuditJobs(session_factory, Idle())

    jobs.event("orphan", "attempt", f"called https://api.example.com/v3?key={settings.youtube_api_key}")

    detail = jobs.get("orphan")["events"][-1]["detail"]
    assert settings.youtube_api_key not in detail, detail
    with session_factory() as db:
        stored = json.dumps(db.get(SystemState, PREFIX + "orphan").value_json)
    assert settings.youtube_api_key not in stored, "a credential was persisted in the job feed"
