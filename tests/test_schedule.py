"""Systems written at once, and the order that still holds between them.

The claims that matter are about what can never happen -- a system starting
before its dependency finished, more running than the limit allows, a layer
waiting on the slowest system of the layer before it -- so where it can, a
test here is built to deadlock if the claim is false, and a timeout turns the
deadlock into a failure. That is deterministic in a way timing is not.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from app.engineer.schedule import Unschedulable, in_dependency_order

# Ascent's specification as compiled: its real dependencies, in its real build
# order. Five layers, six wide at the widest.
ASCENT = {
    "TowerService": [], "SessionLifecycleService": [],
    "PlayerSpawnService": ["TowerService", "SessionLifecycleService"],
    "TelemetryService": ["SessionLifecycleService"],
    "PlatformPhysicsService": ["TowerService"],
    "LocomotionService": ["PlayerSpawnService"],
    "DashAbilityService": ["SessionLifecycleService"],
    "CheckpointService": ["TowerService", "SessionLifecycleService", "DashAbilityService"],
    "VoidKillPlaneService": ["SessionLifecycleService", "CheckpointService",
                             "TelemetryService", "DashAbilityService"],
    "HazardService": ["TowerService"],
    "RouteManagementService": ["TowerService"],
    "MovingPlatformService": ["TowerService", "RouteManagementService"],
    "GhostCollisionService": ["PlayerSpawnService"],
    "TelemetryHUD": ["TelemetryService"],
    "SpineVisualService": ["TowerService", "CheckpointService"],
    "SummitService": ["TowerService", "SessionLifecycleService", "TelemetryService",
                      "SpineVisualService"],
}
ORDER = list(ASCENT)


def bounded(coroutine, seconds: float = 3.0):
    """A deadlock becomes a failure rather than a hung suite."""
    return asyncio.wait_for(coroutine, seconds)


async def test_a_system_never_starts_before_its_dependencies_finish():
    log: list[tuple[str, str]] = []
    rng = random.Random(7)

    async def work(name: str) -> None:
        log.append(("start", name))
        await asyncio.sleep(rng.uniform(0, 0.01))
        log.append(("finish", name))

    await bounded(in_dependency_order(ORDER, ASCENT, limit=6, work=work))

    position = {event: index for index, event in enumerate(log)}
    for name, needs in ASCENT.items():
        for need in needs:
            assert position[("finish", need)] < position[("start", name)], (name, need)
    assert {name for kind, name in log if kind == "finish"} == set(ORDER)


async def test_never_more_than_the_limit_at_once():
    running, most = 0, 0

    async def work(_name: str) -> None:
        nonlocal running, most
        running += 1
        most = max(most, running)
        await asyncio.sleep(0.005)
        running -= 1

    await bounded(in_dependency_order([f"S{n}" for n in range(8)], {}, limit=3, work=work))

    assert most == 3


async def test_systems_that_do_not_depend_on_each_other_really_overlap():
    # Each waits until all three are inside at once. Run one at a time, the
    # first could never get through, and the timeout says so.
    together = asyncio.Barrier(3)

    async def work(_name: str) -> None:
        await together.wait()

    await bounded(in_dependency_order(["A", "B", "C"], {}, limit=3, work=work))


async def test_a_system_starts_when_its_own_dependencies_are_done_not_its_layers():
    # A -> C and B -> D. In waves, D waits for A because A is in its layer. Here
    # A refuses to finish until D has started, so waves would deadlock.
    d_started = asyncio.Event()

    async def work(name: str) -> None:
        if name == "D":
            d_started.set()
        if name == "A":
            await d_started.wait()

    await bounded(in_dependency_order(["A", "B", "C", "D"], {"C": ["A"], "D": ["B"]},
                                      limit=4, work=work))


async def test_one_at_a_time_is_exactly_the_build_order():
    # The line the build walked before, unchanged, when only one may run.
    started: list[str] = []

    async def work(name: str) -> None:
        started.append(name)
        await asyncio.sleep(0)

    await bounded(in_dependency_order(ORDER, ASCENT, limit=1, work=work))

    assert started == ORDER


async def test_the_build_order_decides_who_goes_first_when_slots_are_scarce():
    started: list[str] = []
    release = asyncio.Event()

    async def work(name: str) -> None:
        started.append(name)
        await release.wait()

    running = asyncio.create_task(in_dependency_order(["P", "Q", "R", "S"], {}, limit=2,
                                                      work=work))
    await asyncio.sleep(0.01)
    assert started == ["P", "Q"]
    release.set()
    await bounded(running)
    assert started == ["P", "Q", "R", "S"]


async def test_a_dependency_the_project_already_has_holds_nothing_up():
    done: list[str] = []

    async def work(name: str) -> None:
        done.append(name)

    await bounded(in_dependency_order(["A"], {"A": ["AlreadyInTheProject"]}, limit=2, work=work))

    assert done == ["A"]


async def test_a_circle_is_refused_rather_than_waited_on():
    async def work(_name: str) -> None:
        return None

    with pytest.raises(Unschedulable, match="A .needs B."):
        await bounded(in_dependency_order(["A", "B"], {"A": ["B"], "B": ["A"]}, limit=2,
                                          work=work))


async def test_an_unexpected_failure_stops_everything_still_running():
    cancelled: list[str] = []
    started: list[str] = []

    async def work(name: str) -> None:
        started.append(name)
        if name == "Broken":
            await asyncio.sleep(0)
            raise RuntimeError("the landing lock was lost")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(name)
            raise

    with pytest.raises(RuntimeError, match="landing lock"):
        await bounded(in_dependency_order(["Slow", "Broken", "Later"], {"Later": ["Broken"]},
                                          limit=2, work=work))

    assert cancelled == ["Slow"]
    assert "Later" not in started


async def test_cancelling_the_build_cancels_every_system_in_flight():
    cancelled: list[str] = []
    inside = asyncio.Barrier(3)

    async def work(name: str) -> None:
        try:
            await inside.wait()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(name)
            raise

    running = asyncio.create_task(in_dependency_order(["A", "B"], {}, limit=2, work=work))
    await bounded(inside.wait())
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert sorted(cancelled) == ["A", "B"]
