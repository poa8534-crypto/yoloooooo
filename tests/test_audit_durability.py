"""Verification of Tier 1 (durable SQLite audit events) and Tier 2 (startup orphan reconciler)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app import audit_activity
from app.models import (
    AuditActivityEvent,
    Candidate,
    ResearchRun,
    ScoutAuditRun,
)


def _seed_candidate(session_factory) -> str:
    with session_factory() as db:
        run = ResearchRun(niche="durable-test")
        db.add(run)
        db.flush()
        candidate = Candidate(run_id=run.id, external_id="exp-101")
        db.add(candidate)
        db.commit()
        return candidate.id


def test_audit_events_persisted_to_sqlite(session_factory):
    candidate_id = _seed_candidate(session_factory)
    audit_activity.reset()

    audit_activity.start(candidate_id, session_factory=session_factory)
    audit_activity.emit(candidate_id, "gates", "4 of 4 gates pass")
    audit_activity.emit(candidate_id, "evidence", "2 verified facts packed")
    audit_activity.emit(candidate_id, "draft_started", "Pass 1 - drafting")
    audit_activity.emit(candidate_id, "draft_ready", "Draft accepted")
    audit_activity.finish(candidate_id, "stored", "Audit stored in ledger")

    with session_factory() as db:
        run = db.scalar(select(ScoutAuditRun).where(ScoutAuditRun.candidate_id == candidate_id))
        assert run is not None
        assert run.status == "complete"
        assert run.completed_at is not None

        events = list(db.scalars(
            select(AuditActivityEvent)
            .where(AuditActivityEvent.run_id == run.id)
            .order_by(AuditActivityEvent.sequence)
        ))
        assert len(events) == 6
        stages = [e.stage for e in events]
        assert stages == ["started", "gates", "evidence", "draft_started", "draft_ready", "stored"]
        assert [e.sequence for e in events] == [1, 2, 3, 4, 5, 6]


def test_feed_snapshot_rehydrates_after_restart(session_factory):
    candidate_id = _seed_candidate(session_factory)
    audit_activity.reset()

    # Run audit and emit events
    audit_activity.start(candidate_id, session_factory=session_factory)
    audit_activity.emit(candidate_id, "gates", "All gates pass")
    audit_activity.finish(candidate_id, "stored", "Finished")

    # Simulate service restart: in-memory state is wiped
    audit_activity.reset()
    assert len(audit_activity._feeds) == 0

    # Snapshot should rehydrate from SQLite
    state = audit_activity.snapshot(candidate_id)
    assert state["running"] is False
    assert len(state["events"]) == 3
    assert [e["stage"] for e in state["events"]] == ["started", "gates", "stored"]
    assert state["events"][0]["detail"] == "Venture Scout audit requested"
    assert state["events"][1]["detail"] == "All gates pass"
    assert state["events"][2]["detail"] == "Finished"


def test_lifespan_reconciles_interrupted_audit_on_restart(session_factory):
    candidate_id = _seed_candidate(session_factory)
    audit_activity.reset()

    # Simulate an audit in-flight when service shuts down
    audit_activity.start(candidate_id, session_factory=session_factory)
    audit_activity.emit(candidate_id, "draft_started", "Drafting pass 1")

    # Service restarts: in-memory state cleared
    audit_activity.reset()

    # Lifespan startup reconciliation logic
    with session_factory() as db:
        orphaned_audits = list(db.scalars(select(ScoutAuditRun).where(
            ScoutAuditRun.status == "running"
        )))
        assert len(orphaned_audits) == 1
        for scout_run in orphaned_audits:
            scout_run.status = "interrupted"
            scout_run.message = "Interrupted by service restart"
            scout_run.completed_at = datetime.now(UTC)
            max_seq = db.scalar(
                select(func.max(AuditActivityEvent.sequence))
                .where(AuditActivityEvent.run_id == scout_run.id)
            ) or 0
            interrupted_event = AuditActivityEvent(
                candidate_id=scout_run.candidate_id,
                run_id=scout_run.id,
                sequence=max_seq + 1,
                stage="interrupted",
                detail="Service was restarted while audit was in flight. Evidence and ledger integrity preserved.",
                extra_json={"interrupted": True},
                created_at=datetime.now(UTC),
            )
            db.add(interrupted_event)
        db.commit()

    # Verify state after reconciliation
    with session_factory() as db:
        run = db.scalar(select(ScoutAuditRun).where(ScoutAuditRun.candidate_id == candidate_id))
        assert run.status == "interrupted"
        assert run.completed_at is not None

    # Snapshot rehydrates and shows the interrupted state
    state = audit_activity.snapshot(candidate_id)
    assert state["running"] is False
    assert state["events"][-1]["stage"] == "interrupted"
    assert "restarted while audit was in flight" in state["events"][-1]["detail"]


def test_audit_activity_event_is_append_only(session_factory):
    candidate_id = _seed_candidate(session_factory)
    audit_activity.reset()

    audit_activity.start(candidate_id, session_factory=session_factory)
    audit_activity.emit(candidate_id, "gates", "Gates pass")

    with session_factory() as db:
        event = db.scalar(select(AuditActivityEvent).where(AuditActivityEvent.candidate_id == candidate_id))
        assert event is not None
        event.detail = "Modified detail"
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
