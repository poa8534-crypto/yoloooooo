"""A build writing several systems at once, through the real build loop.

Alpha and Beta depend on nothing; Gamma needs both. With room for three, Alpha
and Beta are written together -- each waits for the other to be inside, so
writing them one at a time would deadlock -- and Gamma is not asked for until
both have landed, because the project it is shown is the project as it stands
when it starts. Landings, which check the whole project in its own checkout,
never overlap.

The Engineer, the file read and the landing are stand-ins; the scheduling,
the locking and the record are the real ones. The build fails at the end,
where it would sync to Studio, because there is no bridge here -- by then the
half being tested has run.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from app.blueprint import api
from app.blueprint import builds as module
from app.blueprint.builds import BuildFailed, BuildRecord, run_build
from app.blueprint.schemas import GameSystem, SystemLayer
from app.blueprint.store import BlueprintStore
from app.engineer.land import Landed
from app.engineer.runs import NotConfigured, _held, provider_limits

ASKING = "asking the Engineer"


def system(name: str, depends: list[str] | None = None) -> GameSystem:
    return GameSystem(id=name.lower(), name=name, layer=SystemLayer.SERVER,
                      purpose=f"Look after what {name} is for.",
                      acceptance_criteria=["A bad request is refused and changes nothing."],
                      depends_on=depends or [])


def at_once(count: int, providers: str = "") -> SimpleNamespace:
    return SimpleNamespace(engineer_parallel_systems=count,
                           engineer_provider_concurrency=providers)


class Built:
    status = "complete"
    reason = "Every check passed"
    commit = "c0ffee"
    attempts = 1

    def __init__(self, name: str):
        self.branch = f"engineer/{name.lower()}"


class Stage:
    """The Engineer, the file read and the landing, standing in -- and a log
    of what happened in what order, from whichever thread it happened on."""

    def __init__(self, monkeypatch, tmp_path):
        self.log: list[tuple[str, str]] = []
        self.guard = threading.Lock()
        self.landing, self.most_landing = 0, 0
        self.together = asyncio.Barrier(2)
        self.release = asyncio.Event()
        self.release.set()
        self.slots_seen: list[object] = []

        repo = tmp_path / "game"
        (repo / "src" / "server").mkdir(parents=True)
        monkeypatch.setattr(module, "game_repo", lambda _settings: repo)
        monkeypatch.setattr(module, "run_task", self.engineer)
        monkeypatch.setattr(module, "files_on_branch", self.files)
        monkeypatch.setattr(module, "land", self.land)
        # Nowhere: a build that reaches the sync must fail there, never find a
        # real bridge that happens to be running on this machine.
        monkeypatch.setattr(api, "BRIDGE_URL", "http://127.0.0.1:9")

    def note(self, kind: str, name: str) -> None:
        with self.guard:
            self.log.append((kind, name))

    async def engineer(self, task, **kwargs):
        self.slots_seen.append(kwargs.get("slots"))
        self.note("asked", task.system)
        if task.system in ("AlphaService", "BetaService"):
            await self.together.wait()
            await self.release.wait()
        return Built(task.system)

    def files(self, _repo, branch: str) -> dict[str, str]:
        name = {"engineer/alphaservice": "AlphaService", "engineer/betaservice": "BetaService",
                "engineer/gammaservice": "GammaService"}[branch]
        return {f"src/server/{name}.luau": "--!strict\nreturn {}\n"}

    def land(self, _repo, files, *, message, verify=None, branch="master"):
        with self.guard:
            self.landing += 1
            self.most_landing = max(self.most_landing, self.landing)
        time.sleep(0.03)  # long enough for an overlapping landing to show
        name = next(iter(files)).split("/")[-1].removesuffix(".luau")
        self.note("landed", name)
        with self.guard:
            self.landing -= 1
        return Landed(True, commit="abc123def4567890", paths=tuple(files))


@pytest.fixture
def plan(session_factory):
    store = BlueprintStore(session_factory)
    plan = store.create(audit_id="audit-1", title="Parallel Lab")
    return store.save(plan.model_copy(update={
        "user_intent": "Three systems, two of which need nothing.",
        "systems": [system("AlphaService"), system("BetaService"),
                    system("GammaService", ["AlphaService", "BetaService"])],
    }))


async def build(plan, session_factory, settings, build_id: str) -> None:
    try:
        await asyncio.wait_for(run_build(plan.id, settings=settings, factory=session_factory,
                                         token="t", play=False, build_id=build_id), 10)
    except BuildFailed:
        pass  # at the sync, where there is no bridge


async def test_independent_systems_are_written_together_and_a_dependent_waits_for_both_to_land(
        plan, session_factory, monkeypatch, tmp_path):
    stage = Stage(monkeypatch, tmp_path)

    await build(plan, session_factory, at_once(3), "build-parallel")

    log = stage.log
    assert log.index(("asked", "BetaService")) < log.index(("landed", "AlphaService"))
    asked_gamma = log.index(("asked", "GammaService"))
    assert log.index(("landed", "AlphaService")) < asked_gamma
    assert log.index(("landed", "BetaService")) < asked_gamma
    assert stage.most_landing == 1

    record = BuildRecord(session_factory, "build-parallel").read()
    assert record["systems"].keys() == {"AlphaService", "BetaService", "GammaService"}
    assert all(entry["landed"]["committed"] for entry in record["systems"].values())
    assert "up to 3 at a time" in " ".join(event["detail"] for event in record["events"])


async def test_one_at_a_time_writes_them_in_build_order(plan, session_factory, monkeypatch,
                                                         tmp_path):
    stage = Stage(monkeypatch, tmp_path)
    stage.together = asyncio.Barrier(1)  # nobody to wait for

    await build(plan, session_factory, at_once(1), "build-serial")

    assert stage.log == [("asked", "AlphaService"), ("landed", "AlphaService"),
                         ("asked", "BetaService"), ("landed", "BetaService"),
                         ("asked", "GammaService"), ("landed", "GammaService")]


async def test_the_dashboard_shows_every_system_in_flight_and_steering_reaches_only_the_rest(
        plan, session_factory, monkeypatch, tmp_path):
    import app.db

    monkeypatch.setattr(app.db, "SessionLocal", session_factory)
    stage = Stage(monkeypatch, tmp_path)
    stage.release.clear()
    running = asyncio.create_task(build(plan, session_factory, at_once(3), "build-watched"))
    record = BuildRecord(session_factory, "build-watched")
    for _ in range(300):
        await asyncio.sleep(0.01)
        try:
            asked = [e for e in record.read()["events"] if e["detail"].endswith(ASKING)]
        except KeyError:
            continue
        if len(asked) == 2:
            break

    graph = api.build_graph("build-watched")
    states = {node["id"]: node["state"] for node in graph["nodes"]}

    assert graph["in_flight"] == ["AlphaService", "BetaService"]
    assert states == {"AlphaService": "building", "BetaService": "building",
                      "GammaService": "waiting"}
    assert graph["steering"]["reachable"] == ["GammaService"]
    plan_states = {state["name"]: state["state"] for state in graph["plan"]["states"]}
    assert plan_states["AlphaService"] == plan_states["BetaService"] == "building"

    stage.release.set()
    await running


async def test_a_provider_limit_is_one_set_of_slots_for_the_whole_build(
        plan, session_factory, monkeypatch, tmp_path):
    stage = Stage(monkeypatch, tmp_path)

    await build(plan, session_factory, at_once(3, "ollama=1"), "build-limited")

    [first, *rest] = stage.slots_seen
    assert isinstance(first["ollama"], asyncio.Semaphore)
    assert all(slots is first for slots in rest)


async def test_a_held_provider_never_has_more_calls_in_flight_than_its_slots():
    inside, most = 0, 0

    async def provider(_system, _prompt, _deadline):
        nonlocal inside, most
        inside += 1
        most = max(most, inside)
        await asyncio.sleep(0.01)
        inside -= 1
        return "{}", "local-model"

    held = _held(provider, asyncio.Semaphore(1))
    await asyncio.gather(*(held("", "", 0.0) for _ in range(4)))

    assert most == 1


@pytest.mark.parametrize("text, expected", [
    ("", {}),
    ("ollama=1", {"ollama": 1}),
    (" Ollama = 1 , gemini=2 ", {"ollama": 1, "gemini": 2}),
])
def test_provider_limits_are_read_as_written(text, expected):
    assert provider_limits(at_once(3, text)) == expected


@pytest.mark.parametrize("text", ["olama=1", "ollama=0", "ollama=many", "ollama"])
def test_a_provider_limit_that_would_silently_not_apply_is_refused(text):
    with pytest.raises(NotConfigured):
        provider_limits(at_once(3, text))
