"""A restart must not throw away a run that was nearly finished.

This cost two real runs. Both reached their last step -- round four, 29 games
inspected, concepts about to be written -- and both died because the service
was restarted underneath them. The checkpoint survived each time and was worth
minutes of model time and a hundred-odd API calls, and both runs then sat
marked "interrupted" waiting for a person to notice and press a button.

Deep runs checkpoint, so they resume. A quick run has nothing to resume from
and is still recorded as interrupted rather than pretended over.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import ResearchCheckpoint, ResearchRun, RunStatus


def seeded_run(session_factory, *, status, mode="deep", checkpoint=True,
               completed_at=None):
    with session_factory() as db:
        run = ResearchRun(niche="cooperative cozy farming", status=status,
                          completed_at=completed_at)
        db.add(run)
        db.flush()
        if checkpoint:
            db.add(ResearchCheckpoint(run_id=run.id, state={"mode": mode, "round": 4}))
        db.commit()
        return run.id


async def restart(session_factory, monkeypatch, started):
    """Run the startup half of the lifespan against this ledger."""
    from app import main as main_module

    monkeypatch.setattr(main_module, "SessionLocal", session_factory)

    class Orchestrator:
        async def research(self, run_id):
            started.append(run_id)

        async def close(self):
            return None
        associations = None

    class Jobs:
        def reconcile(self): return None
        async def close(self): return None

    monkeypatch.setattr(main_module, "ResearchOrchestrator", lambda *a, **k: Orchestrator())
    monkeypatch.setattr(main_module, "AuditJobs", lambda *a, **k: Jobs())
    monkeypatch.setattr(main_module, "start_scheduler", lambda: type("S", (), {"shutdown": lambda *a, **k: None})())

    async def nothing():
        return None
    monkeypatch.setattr(main_module, "catch_up_if_needed", nothing)

    import asyncio

    async with main_module.lifespan(main_module.app):
        # The resume tasks are created, not awaited. Without yielding to the
        # loop the context exits and shutdown cancels them before they run.
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [RunStatus.RUNNING.value, RunStatus.QUEUED.value])
async def test_a_deep_run_caught_by_a_restart_is_resumed(session_factory, settings,
                                                          monkeypatch, status):
    # A previous shutdown stamped a finish time on it, which is exactly what
    # has to be cleared: a run that is running again must not read as over.
    from datetime import UTC, datetime

    run_id = seeded_run(session_factory, status=status,
                        completed_at=datetime.now(UTC))
    started: list[str] = []

    await restart(session_factory, monkeypatch, started)

    assert started == [run_id], "the run was left stopped after a restart"
    with session_factory() as db:
        run = db.get(ResearchRun, run_id)
    assert run.status == RunStatus.QUEUED.value
    assert run.completed_at is None, "a resuming run must not look finished"
    assert "Resuming" in run.message


@pytest.mark.asyncio
async def test_a_quick_run_has_nothing_to_resume_from_and_says_so(session_factory,
                                                                  settings, monkeypatch):
    run_id = seeded_run(session_factory, status=RunStatus.RUNNING.value, mode="quick")
    started: list[str] = []

    await restart(session_factory, monkeypatch, started)

    assert started == [], "a quick run was resumed from a checkpoint it does not have"
    with session_factory() as db:
        run = db.get(ResearchRun, run_id)
    assert run.status == "interrupted"
    assert "no evidence was fabricated" in run.message


@pytest.mark.asyncio
async def test_a_run_with_no_checkpoint_is_not_resumed(session_factory, settings,
                                                        monkeypatch):
    """Resuming from nothing would restart the work while claiming to continue
    it, and the run's own record would disagree with what happened."""
    run_id = seeded_run(session_factory, status=RunStatus.RUNNING.value, checkpoint=False)
    started: list[str] = []

    await restart(session_factory, monkeypatch, started)

    assert started == []
    with session_factory() as db:
        assert db.get(ResearchRun, run_id).status == "interrupted"


@pytest.mark.asyncio
async def test_a_finished_run_is_left_alone(session_factory, settings, monkeypatch):
    run_id = seeded_run(session_factory, status="complete")
    started: list[str] = []

    await restart(session_factory, monkeypatch, started)

    assert started == [], "a completed run was started again"
    with session_factory() as db:
        assert db.get(ResearchRun, run_id).status == "complete"


@pytest.mark.asyncio
async def test_several_interrupted_runs_all_resume(session_factory, settings, monkeypatch):
    ids = [seeded_run(session_factory, status=RunStatus.RUNNING.value) for _ in range(3)]
    started: list[str] = []

    await restart(session_factory, monkeypatch, started)

    assert sorted(started) == sorted(ids)


@pytest.mark.asyncio
async def test_startup_does_not_wait_for_the_runs_it_restarts(session_factory, settings,
                                                               monkeypatch):
    """A run takes minutes. Awaiting one would hold the service down, and the
    dashboard would be unreachable for the whole of it."""
    import asyncio

    from app import main as main_module

    run_id = seeded_run(session_factory, status=RunStatus.RUNNING.value)
    monkeypatch.setattr(main_module, "SessionLocal", session_factory)

    class SlowOrchestrator:
        async def research(self, _run_id):
            await asyncio.sleep(30)

        async def close(self): return None
        associations = None

    monkeypatch.setattr(main_module, "ResearchOrchestrator", lambda *a, **k: SlowOrchestrator())
    monkeypatch.setattr(main_module, "AuditJobs",
                        lambda *a, **k: type("J", (), {"reconcile": lambda s: None,
                                                       "close": lambda s: asyncio.sleep(0)})())
    monkeypatch.setattr(main_module, "start_scheduler",
                        lambda: type("S", (), {"shutdown": lambda *a, **k: None})())

    async def nothing(): return None
    monkeypatch.setattr(main_module, "catch_up_if_needed", nothing)

    async def start():
        async with main_module.lifespan(main_module.app):
            return run_id

    assert await asyncio.wait_for(start(), timeout=5) == run_id
