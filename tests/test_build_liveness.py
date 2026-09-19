"""A build is running only while something is running it.

Five build records claimed otherwise: `generating`, `planning` or `building`
for up to seventeen hours after the process behind them had gone, each drawn
in the dashboard as work in progress. Three stopped mid-system with the next
build starting minutes later -- killed, which writes nothing. Two stopped
exactly where a transition the state machine then refused would have raised,
in a dashboard that went on serving.

So the build holds a lock for as long as it runs and names it on the record,
and a reader asks the lock rather than believing the record. These tests use a
lock that is free, held, or on another machine, and a real build that crashes
or is cancelled partway.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from app.blueprint import builds as module
from app.blueprint.builds import BuildRecord, list_builds, read_build, run_build
from app.blueprint.compile import compile_spec
from app.blueprint.schemas import BuildStatus, GameSystem, SystemLayer
from app.blueprint.store import BlueprintStore
from app.blueprint.transitions import RUNNING, can
from app.ownership import Held, is_held

ASKED = "PlayerSpawnService: asking the Engineer"


def system(name: str, depends: list[str] | None = None) -> GameSystem:
    return GameSystem(id=name.lower(), name=name, layer=SystemLayer.SERVER,
                      purpose=f"Look after what {name} is for.",
                      acceptance_criteria=["A bad request is refused and changes nothing."],
                      depends_on=depends or [])


def a_plan(session_factory):
    store = BlueprintStore(session_factory)
    plan = store.create(audit_id="audit-1", title="Checkpoint Ascent")
    return store.save(plan.model_copy(update={
        "user_intent": "A solo tower climb where progress is the reward.",
        "systems": [system("TowerService"), system("PlayerSpawnService", ["TowerService"])],
    }))


def a_build_in_flight(session_factory, build_id: str, owner: dict | None) -> BuildRecord:
    """A record as a build leaves it when its process stops mid-system."""
    plan = a_plan(session_factory)
    record = BuildRecord(session_factory, build_id)
    record.create(plan, compile_spec(plan), owner=owner)
    record.move(BuildStatus.PLANNING, "working out what still has to be built")
    record.move(BuildStatus.GENERATING, "2 system(s) to write")
    record.event("generating", ASKED)
    return record


def owned_by(lock, host: str | None = None) -> dict:
    return {"pid": 4242, "host": host or socket.gethostname(),
            "since": "2026-09-19T06:01:00+00:00", "lock": str(lock)}


def released(path):
    """A lock file its holder has let go of -- which is what the operating
    system does for a holder that dies."""
    lock = Held(path)
    assert lock.acquire()
    lock.release()
    return path


def test_a_build_nothing_is_running_is_reported_as_interrupted(session_factory, tmp_path):
    record = a_build_in_flight(session_factory, "build-dead",
                               owned_by(released(tmp_path / "dead.lock")))
    last_recorded = record.read()["events"][-1]["at"]

    answer = read_build(session_factory, "build-dead")

    assert answer["status"] == BuildStatus.INTERRUPTED.value
    said = answer["events"][-1]
    assert said["stage"] == "interrupted"
    assert "process 4242" in said["detail"] and ASKED in said["detail"]
    # When it stopped is unknown; when it was last heard from is measured.
    # Now would add every hour it sat unnoticed to how long it took.
    assert answer["completed_at"] == last_recorded


def test_asking_twice_says_it_once(session_factory, tmp_path):
    a_build_in_flight(session_factory, "build-dead",
                      owned_by(released(tmp_path / "dead.lock")))

    first = read_build(session_factory, "build-dead")
    [listed] = list_builds(session_factory)

    assert listed["status"] == BuildStatus.INTERRUPTED.value
    assert len(listed["events"]) == len(first["events"])


def test_a_build_whose_lock_is_held_is_left_running(session_factory, tmp_path):
    holder = Held(tmp_path / "live.lock")
    assert holder.acquire()
    try:
        record = a_build_in_flight(session_factory, "build-live", owned_by(holder.path))
        before = record.read()

        answer = read_build(session_factory, "build-live")

        assert answer["status"] == BuildStatus.GENERATING.value
        assert answer["events"] == before["events"]
    finally:
        holder.release()


def test_a_lock_on_another_machine_is_not_asked_about(session_factory, tmp_path):
    # Nothing here can tell whether a process on another machine is alive. A
    # free file on this one says nothing about it.
    a_build_in_flight(session_factory, "build-elsewhere",
                      owned_by(released(tmp_path / "dead.lock"), host="another-machine"))

    assert read_build(session_factory, "build-elsewhere")["status"] == BuildStatus.GENERATING.value


def test_a_record_that_names_no_lock_is_left_saying_what_it_says(session_factory):
    # Written before builds named their lock. Nothing can be asked, so nothing
    # is guessed.
    a_build_in_flight(session_factory, "build-old", owner=None)

    assert read_build(session_factory, "build-old")["status"] == BuildStatus.GENERATING.value


def test_the_dashboard_draws_nothing_in_flight_for_an_interrupted_build(
        session_factory, tmp_path, monkeypatch):
    # The symptom itself: "Working on PlayerSpawnService", two hours after the
    # process that was working on it had gone.
    import app.db
    from app.blueprint import api

    monkeypatch.setattr(app.db, "SessionLocal", session_factory)
    a_build_in_flight(session_factory, "build-dead",
                      owned_by(released(tmp_path / "dead.lock")))

    graph = api.build_graph("build-dead")

    assert graph["status"] == BuildStatus.INTERRUPTED.value
    assert graph["current"] is None
    assert not [node for node in graph["nodes"] if node["state"] == "building"]


def test_every_running_state_can_be_interrupted_and_retried():
    for state in RUNNING:
        assert can(state, BuildStatus.INTERRUPTED), state
    assert can(BuildStatus.INTERRUPTED, BuildStatus.QUEUED)


# ---- a real build that stops partway ---------------------------------------

@pytest.fixture
def plan(session_factory, tmp_path, monkeypatch):
    repo = tmp_path / "game"
    (repo / "src" / "server").mkdir(parents=True)
    monkeypatch.setattr(module, "game_repo", lambda _settings: repo)
    return a_plan(session_factory)


async def test_a_crash_inside_the_build_is_recorded_as_a_failure(plan, session_factory,
                                                                 monkeypatch,
                                                                 build_settings):
    def broken(*_args, **_kwargs):
        raise KeyError("TowerService")

    monkeypatch.setattr(module, "tasks_from", broken)

    with pytest.raises(KeyError):
        await run_build(plan.id, settings=build_settings, factory=session_factory,
                        token="t", play=False, build_id="build-crashed")

    record = read_build(session_factory, "build-crashed")
    assert record["status"] == BuildStatus.FAILED.value
    assert "KeyError" in record["events"][-1]["detail"]
    assert record["completed_at"]
    assert not is_held(record["owner"]["lock"])


async def test_a_run_that_fails_never_rewrites_a_record_it_did_not_create(
        plan, session_factory, tmp_path, build_settings):
    # Same id, record already there: creating it fails, and the stop is this
    # run's to report, not the other record's to suffer.
    theirs = owned_by(tmp_path / "theirs.lock")
    a_build_in_flight(session_factory, "build-taken", theirs)

    with pytest.raises(Exception):  # noqa: B017 - the duplicate key, whatever the driver calls it
        await run_build(plan.id, settings=build_settings, factory=session_factory,
                        token="t", play=False, build_id="build-taken")

    untouched = BuildRecord(session_factory, "build-taken").read()
    assert untouched["status"] == BuildStatus.GENERATING.value
    assert untouched["owner"] == theirs


async def test_a_cancelled_build_is_recorded_as_interrupted(plan, session_factory,
                                                            monkeypatch, build_settings):
    async def never_answers(_task, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(module, "run_task", never_answers)
    running = asyncio.create_task(run_build(
        plan.id, settings=build_settings, factory=session_factory, token="t", play=False,
        build_id="build-cancelled"))
    record = BuildRecord(session_factory, "build-cancelled")
    for _ in range(200):
        await asyncio.sleep(0.01)
        try:
            if record.read()["events"][-1]["detail"].endswith("asking the Engineer"):
                break
        except (KeyError, IndexError):
            continue

    # Held by this very process, and still the true answer.
    assert read_build(session_factory, "build-cancelled")["status"] == "generating"

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    stopped = read_build(session_factory, "build-cancelled")
    assert stopped["status"] == BuildStatus.INTERRUPTED.value
    assert "CancelledError" in stopped["events"][-1]["detail"]
    assert not is_held(stopped["owner"]["lock"])
