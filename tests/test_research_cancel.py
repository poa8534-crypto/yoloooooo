"""Stopping a research run the operator no longer wants.

The interesting part is not the status column. It is that the task actually
stops: a Stop button that marks a row and leaves the work running would spend
the rest of the budget, keep writing to the ledger, and look like it had
worked.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.main import RUN_TASKS, cancel_research_run
from app.models import ResearchRun, RunStatus


def _queued(db, niche="cooperative cozy farming"):
    run = ResearchRun(niche=niche, status=RunStatus.RUNNING.value, message="Investigating round 1")
    db.add(run)
    db.commit()
    return run


async def test_a_running_task_is_cancelled_and_awaited(db):
    """The endpoint does not return while the work is still going."""
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            stopped.set()
            raise

    run = _queued(db)
    task = asyncio.create_task(work())
    RUN_TASKS[run.id] = task
    try:
        await started.wait()
        view = await cancel_research_run(run.id, db)
    finally:
        RUN_TASKS.pop(run.id, None)

    assert view.status == RunStatus.CANCELLED.value
    # Awaited, not merely requested: by the time the caller has an answer the
    # task has already unwound.
    assert task.done()
    assert stopped.is_set()


async def test_the_record_says_it_was_stopped_on_purpose(db):
    run = _queued(db)
    view = await cancel_research_run(run.id, db)

    assert view.status == RunStatus.CANCELLED.value
    assert "operator" in view.message.lower()
    assert view.completed_at is not None


async def test_a_run_with_no_live_task_is_still_closed(db):
    """A restart leaves rows claiming to be running with no task behind them.

    Refusing to close those would leave the page with a Stop button that
    cannot work and three others that stay disabled behind a run that is
    never coming back.
    """
    run = _queued(db)
    assert run.id not in RUN_TASKS

    view = await cancel_research_run(run.id, db)
    assert view.status == RunStatus.CANCELLED.value


@pytest.mark.parametrize("status", ["complete", "failed", "cancelled", "interrupted"])
async def test_only_a_live_run_can_be_stopped(db, status):
    run = _queued(db)
    run.status = status
    db.commit()

    with pytest.raises(HTTPException) as refused:
        await cancel_research_run(run.id, db)
    assert refused.value.status_code == 409
    assert status in refused.value.detail


async def test_an_unknown_run_is_not_found(db):
    with pytest.raises(HTTPException) as refused:
        await cancel_research_run("no-such-run", db)
    assert refused.value.status_code == 404


@pytest.mark.parametrize(("status", "reason"), [
    (RunStatus.CANCELLED.value, "operator_cancelled"),
    (RunStatus.RUNNING.value, "service_shutdown"),
])
async def test_the_deep_controller_records_which_cancellation_this_was(session_factory, status, reason):
    """A shutdown and a Stop both cancel the task; they are not the same event.

    The endpoint writes the status before cancelling, so the row is what tells
    them apart. Filing an operator's decision as `service_shutdown` would put a
    wrong reason in the report.
    """
    from types import SimpleNamespace

    from app.deep_research import DeepResearch
    from app.research_budget import RunBudget

    with session_factory() as db:
        run = ResearchRun(niche="cozy farming", status=status)
        db.add(run)
        db.commit()
        run_id = run.id

    controller = DeepResearch(SimpleNamespace(session_factory=session_factory), run_id)
    controller.b.save(stage="Investigating round 2")

    async def cancelled():
        raise asyncio.CancelledError

    controller.investigate = cancelled
    with pytest.raises(asyncio.CancelledError):
        await controller.run()

    assert RunBudget(session_factory, run_id).state["stop_reason"] == reason


async def test_stopping_keeps_the_stage_the_run_reached(session_factory):
    """The message belongs to the endpoint, the stage to the run.

    `RunBudget.save` rewrites `run.message` whenever it is handed a stage, so
    the operator-cancelled path passes none: otherwise the reason the endpoint
    just recorded would be overwritten by a stage name.
    """
    from types import SimpleNamespace

    from app.deep_research import DeepResearch

    with session_factory() as db:
        run = ResearchRun(niche="cozy farming", status=RunStatus.CANCELLED.value,
                          message="Stopped by the operator; everything it had already collected is kept")
        db.add(run)
        db.commit()
        run_id = run.id

    controller = DeepResearch(SimpleNamespace(session_factory=session_factory), run_id)
    controller.b.state["stage"] = "Investigating round 2"

    async def cancelled():
        raise asyncio.CancelledError

    controller.investigate = cancelled
    with pytest.raises(asyncio.CancelledError):
        await controller.run()

    with session_factory() as db:
        assert "operator" in db.get(ResearchRun, run_id).message.lower()


async def test_cancelled_is_not_offered_a_resume(db):
    """Stop and interrupt are different endings.

    `resume_run` only accepts "interrupted", so a run stopped on purpose is
    finished rather than something to pick back up. Restart is the way back to
    that niche, and it starts from nothing, which is what was asked for.
    """
    from app.main import resume_run

    run = _queued(db)
    await cancel_research_run(run.id, db)

    with pytest.raises(HTTPException) as refused:
        await resume_run(run.id, db)
    assert refused.value.status_code == 409
