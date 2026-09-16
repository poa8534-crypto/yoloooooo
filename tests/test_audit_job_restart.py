"""Restart and Resume are different actions and must stay different.

Resume continues a job on its *original* deadline and attempt count, which is
right for something the service interrupted and wrong for something that ran
out of budget or failed on its own terms. Restart is a new job with a fresh
budget. Collapsing the two would either strand a failed audit forever or hand
a runaway one an unlimited allowance, and a single button could not tell the
operator which they were getting.
"""

from __future__ import annotations

import pytest

from app.audit_jobs import ACTIVE, AuditJobs, JobConflict


class Recorder(AuditJobs):
    """Real bookkeeping, no asyncio task and no model."""

    def __init__(self, factory, orchestrator=None):
        super().__init__(factory, orchestrator)
        self.launched: list[str] = []

    def _launch(self, job_id):
        self.launched.append(job_id)


@pytest.fixture
def jobs(session_factory, settings, monkeypatch):
    manager = Recorder(session_factory)
    # `start` and `resume` both create a task; neither should reach an event
    # loop in these tests.
    monkeypatch.setattr("app.audit_jobs.asyncio.create_task",
                        lambda coro: (coro.close(), manager.launched.append("task"))[0])
    return manager


@pytest.fixture
def seeded(session_factory):
    from tests.test_deep_research import seed

    _run, candidate_id, _artifact, proposal_id = seed(session_factory)
    return candidate_id, proposal_id


def finish(manager, job_id, status):
    manager.update(job_id, status=status, completed_at="2026-09-16T00:00:00+00:00")


def test_restart_makes_a_new_job_rather_than_reusing_the_old_one(jobs, seeded):
    candidate_id, proposal_id = seeded
    first = jobs.start(candidate_id, "audit_idea", proposal_id)
    finish(jobs, first["id"], "failed")

    second = jobs.restart(first["id"])

    assert second["id"] != first["id"], "restart reused the exhausted job"
    assert second["status"] == "queued"
    assert second["candidate_id"] == candidate_id
    assert second["proposal_id"] == proposal_id


def test_restart_starts_the_same_operation_on_the_same_concept(jobs, seeded):
    candidate_id, proposal_id = seeded
    first = jobs.start(candidate_id, "analyze_game")
    finish(jobs, first["id"], "timed_out")

    second = jobs.restart(first["id"])

    assert second["operation"] == "analyze_game"
    assert second["proposal_id"] is None


def test_restart_gives_a_fresh_budget_where_resume_does_not(jobs, seeded):
    """The whole reason both buttons exist."""
    candidate_id, proposal_id = seeded
    first = jobs.start(candidate_id, "audit_idea", proposal_id)
    jobs.update(first["id"], model_attempts=24)
    finish(jobs, first["id"], "failed")

    second = jobs.restart(first["id"])

    assert second["model_attempts"] == 0


@pytest.mark.parametrize("status", sorted(ACTIVE))
def test_a_running_audit_is_not_restarted_underneath_itself(jobs, seeded, status):
    candidate_id, proposal_id = seeded
    first = jobs.start(candidate_id, "audit_idea", proposal_id)
    jobs.update(first["id"], status=status)

    with pytest.raises(JobConflict, match="still running"):
        jobs.restart(first["id"])


@pytest.mark.parametrize("status", ["failed", "timed_out", "cancelled", "interrupted"])
def test_every_finished_failure_can_be_restarted(jobs, seeded, status):
    candidate_id, proposal_id = seeded
    first = jobs.start(candidate_id, "audit_idea", proposal_id)
    finish(jobs, first["id"], status)

    assert jobs.restart(first["id"])["status"] == "queued"


def test_resume_still_refuses_a_job_that_did_not_get_interrupted(jobs, seeded):
    """Restart exists precisely so this stays refused."""
    candidate_id, proposal_id = seeded
    first = jobs.start(candidate_id, "audit_idea", proposal_id)
    finish(jobs, first["id"], "failed")

    with pytest.raises(JobConflict, match="Only interrupted jobs"):
        jobs.resume(first["id"])


def test_recent_lists_newest_first_and_names_what_each_button_can_act_on(jobs, seeded):
    candidate_id, proposal_id = seeded
    first = jobs.start(candidate_id, "audit_idea", proposal_id)
    finish(jobs, first["id"], "interrupted")
    second = jobs.start(candidate_id, "audit_idea", proposal_id)
    finish(jobs, second["id"], "complete")

    listed = jobs.recent()

    assert [job["id"] for job in listed][:2] == [second["id"], first["id"]]
    interrupted = [job["id"] for job in listed if job["status"] == "interrupted"]
    assert interrupted == [first["id"]]


def test_a_completed_audit_is_not_offered_for_resume(jobs, seeded):
    candidate_id, proposal_id = seeded
    first = jobs.start(candidate_id, "audit_idea", proposal_id)
    finish(jobs, first["id"], "complete")

    assert [job for job in jobs.recent() if job["status"] == "interrupted"] == []
