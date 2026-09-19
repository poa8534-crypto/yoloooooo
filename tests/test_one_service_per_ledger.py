"""A second copy of the service must not touch the first one's ledger.

Startup marks every research run and audit in flight as interrupted by a
previous shutdown. That is only true when no other service is running them.
The watchdog launched a second copy every five minutes for an afternoon while
the first was serving; each copy ran startup in full and only then failed to
bind its port, because uvicorn binds after startup. Nothing was in flight that
day. Anything that had been would have been recorded as interrupted while it
was still running.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base, ledger_lock
from app.migrations import install_append_only_triggers
from app.models import ResearchRun, RunStatus
from app.ownership import is_held


def file_ledger(path):
    engine = create_engine(f"sqlite:///{path.as_posix()}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    install_append_only_triggers(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def a_running_run(factory) -> str:
    with factory() as db:
        run = ResearchRun(niche="cooperative cozy farming", status=RunStatus.RUNNING.value)
        db.add(run)
        db.commit()
        return run.id


def status_of(factory, run_id: str) -> str:
    with factory() as db:
        return db.scalar(select(ResearchRun.status).where(ResearchRun.id == run_id))


def quiet_startup(monkeypatch, factory):
    """The lifespan against this ledger, with nothing that reaches outside it."""
    from app import main as main_module

    monkeypatch.setattr(main_module, "SessionLocal", factory)

    class Orchestrator:
        associations = None

        async def research(self, run_id):
            return None

        async def close(self):
            return None

    class Jobs:
        def reconcile(self):
            return None

        async def close(self):
            return None

    async def nothing():
        return None

    monkeypatch.setattr(main_module, "ResearchOrchestrator", lambda *a, **k: Orchestrator())
    monkeypatch.setattr(main_module, "AuditJobs", lambda *a, **k: Jobs())
    monkeypatch.setattr(main_module, "start_scheduler",
                        lambda: type("S", (), {"shutdown": lambda *a, **k: None})())
    monkeypatch.setattr(main_module, "catch_up_if_needed", nothing)
    return main_module


@pytest.mark.asyncio
async def test_a_second_service_stops_before_touching_the_first_ones_runs(tmp_path, monkeypatch):
    factory = file_ledger(tmp_path / "ledger.db")
    run_id = a_running_run(factory)
    main_module = quiet_startup(monkeypatch, factory)

    first = ledger_lock(factory)
    assert first.acquire(describe=True)
    try:
        with pytest.raises(main_module.AnotherService) as refused:
            async with main_module.lifespan(main_module.app):
                pass
    finally:
        first.release()

    # Still running: the first service is still running it.
    assert status_of(factory, run_id) == RunStatus.RUNNING.value
    # And the refusal names who it lost to, so the log says why.
    assert f"pid {os.getpid()}" in str(refused.value)


@pytest.mark.asyncio
async def test_the_next_service_starts_once_the_first_has_stopped(tmp_path, monkeypatch):
    factory = file_ledger(tmp_path / "ledger.db")
    run_id = a_running_run(factory)
    main_module = quiet_startup(monkeypatch, factory)

    async with main_module.lifespan(main_module.app):
        assert is_held(ledger_lock(factory).path)

    # This one really was the only service, so the run it found in flight was
    # left behind by a shutdown and is recorded as such.
    assert status_of(factory, run_id) == "interrupted"
    assert not is_held(ledger_lock(factory).path)


def test_an_in_memory_ledger_needs_no_lock():
    # Private to its process: nothing else can reach it to collide with.
    assert ledger_lock(sessionmaker(bind=create_engine("sqlite://"))) is None


def test_two_spellings_of_one_database_share_one_lock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    relative = sessionmaker(bind=create_engine("sqlite:///./ledger.db"))
    absolute = sessionmaker(bind=create_engine(f"sqlite:///{(tmp_path / 'ledger.db').as_posix()}"))

    assert ledger_lock(relative).path == ledger_lock(absolute).path
