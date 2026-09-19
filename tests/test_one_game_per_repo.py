"""One repository is one game.

Which repository a build writes into is one line in .env. Left pointing at the
last game, a new game's build writes its systems in among the old game's, lands
them on its master and sends the mixture to Studio. It nearly happened a third
time: NeuroMine was one click from being built into ascent's repository.

Every landed system's commit names its build, and every build names its
blueprint, so the repository itself can say whose it is.
"""

from __future__ import annotations

import subprocess

import pytest

from app.blueprint import builds as module
from app.blueprint.builds import BuildFailed, BuildRecord, other_games, read_build, run_build
from app.blueprint.compile import compile_spec
from app.blueprint.schemas import GameSystem, SystemLayer
from app.blueprint.store import BlueprintStore


def git(root, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def a_game(session_factory, title: str):
    store = BlueprintStore(session_factory)
    plan = store.create(audit_id=f"audit-{title}", title=title)
    return store.save(plan.model_copy(update={
        "user_intent": f"{title}, as its designers described it.",
        "systems": [GameSystem(id="towerservice", name="TowerService", layer=SystemLayer.SERVER,
                               purpose="Build the tower.",
                               acceptance_criteria=["A bad floor number is refused."])],
    }))


def built(session_factory, build_id: str, game) -> None:
    BuildRecord(session_factory, build_id).create(game, compile_spec(game))


def landed(root, build_id: str, name: str = "TowerService") -> None:
    (root / "src" / "server" / f"{name}.luau").write_text(f"-- {build_id}\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", f"feat({name}): accepted by the gate\n\n"
        f"Generated for build {build_id} from specification abc revision 1, accepted on "
        f"engineer/x after 1 attempt(s).")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "game"
    (root / "src" / "server").mkdir(parents=True)
    git(root, "init", "-q", "-b", "master")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("game\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: empty project, toolchain only")
    return root


def test_a_repository_holding_another_games_systems_says_whose_they_are(repo, session_factory):
    ascent = a_game(session_factory, "Checkpoint Ascent")
    neuromine = a_game(session_factory, "NeuroMine")
    built(session_factory, "build-a5c3", ascent)
    landed(repo, "build-a5c3")

    assert other_games(repo, neuromine.id, session_factory) == {ascent.id: "Checkpoint Ascent"}


def test_its_own_systems_are_not_another_game(repo, session_factory):
    # A rebuild, or a revised specification of the same game, lands here too.
    ascent = a_game(session_factory, "Checkpoint Ascent")
    built(session_factory, "build-a5c3", ascent)
    landed(repo, "build-a5c3")

    assert other_games(repo, ascent.id, session_factory) == {}


def test_a_build_this_ledger_never_recorded_is_not_held_against_it(repo, session_factory):
    neuromine = a_game(session_factory, "NeuroMine")
    landed(repo, "build-0ld0")

    assert other_games(repo, neuromine.id, session_factory) == {}


def test_a_repository_with_no_commits_holds_nothing(tmp_path, session_factory):
    root = tmp_path / "fresh"
    root.mkdir()
    git(root, "init", "-q", "-b", "master")

    assert other_games(root, "any-blueprint", session_factory) == {}


async def test_a_build_into_another_games_repository_stops_before_the_engineer_is_asked(
        repo, session_factory, monkeypatch, build_settings):
    ascent = a_game(session_factory, "Checkpoint Ascent")
    neuromine = a_game(session_factory, "NeuroMine")
    built(session_factory, "build-a5c3", ascent)
    landed(repo, "build-a5c3")
    monkeypatch.setattr(module, "game_repo", lambda _settings: repo)
    asked: list[str] = []

    async def engineer(task, **_kwargs):
        asked.append(task.system)

    monkeypatch.setattr(module, "run_task", engineer)

    with pytest.raises(BuildFailed, match="One repository is one game"):
        await run_build(neuromine.id, settings=build_settings, factory=session_factory,
                        token="t", play=False, build_id="build-n3ur0")

    record = read_build(session_factory, "build-n3ur0")
    assert asked == []
    assert record["status"] == "failed"
    assert "Checkpoint Ascent" in record["events"][-1]["detail"]
    # Failed, and when: a failed build without an end time was drawn with an
    # elapsed time still counting.
    assert record["completed_at"]


def test_pressing_build_is_refused_on_the_spot(repo, session_factory, monkeypatch):
    from fastapi.testclient import TestClient

    from app import db
    from app.blueprint import api
    from app.engineer import runs
    from app.main import app as service

    ascent = a_game(session_factory, "Checkpoint Ascent")
    neuromine = a_game(session_factory, "NeuroMine")
    built(session_factory, "build-a5c3", ascent)
    landed(repo, "build-a5c3")
    store = BlueprintStore(session_factory)
    monkeypatch.setattr(api, "_store", lambda: (store, session_factory))
    monkeypatch.setattr(db, "SessionLocal", session_factory)
    monkeypatch.setattr(api, "_toolchain", lambda: {"ready": True, "missing": []})
    monkeypatch.setattr(runs, "game_repo", lambda _settings: repo)
    started: list[str] = []
    monkeypatch.setattr("app.blueprint.builds.run_build",
                        lambda *a, **k: started.append("run_build"))

    with TestClient(service) as client:
        response = client.post("/api/builds", json={"blueprint_id": neuromine.id, "token": "t"})

    assert response.status_code == 409
    assert "Checkpoint Ascent" in response.json()["detail"]
    assert started == []
