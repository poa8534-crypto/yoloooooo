"""Putting an accepted system into the project.

The bug this closes was invisible for thirty-three branches: the Engineer
wrote a system, six checks accepted it, Studio got the file, and the repository
never gained it -- so the next build of the same specification wrote it again.

Two dangers replace it, and the tests here are mostly about those. Landing a
whole branch would carry files the branch happens to hold, including one real
case of a refused system's code riding along on an accepted system's branch.
And writing into a real checkout can destroy work someone has open.
"""

from __future__ import annotations

import subprocess

import pytest

from app.engineer.land import current_branch, dirty_paths, land


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "game"
    (root / "src" / "server").mkdir(parents=True)
    git(root, "init", "-b", "master")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("game\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-m", "first")
    return root


def committed_files(repo) -> set[str]:
    listing = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=repo,
                             capture_output=True, check=True)
    return set(listing.stdout.decode().splitlines())


# ---- the gap it closes -----------------------------------------------------

def test_an_accepted_system_reaches_the_project(repo):
    result = land(repo, {"src/server/WaveService.luau": "--!strict\nreturn {}\n"},
                  message="feat(WaveService): accepted by the gate")

    assert result.committed is True
    assert "src/server/WaveService.luau" in committed_files(repo)
    assert (repo / "src/server/WaveService.luau").read_text(encoding="utf-8").startswith("--!strict")


def test_only_the_files_it_was_given_are_landed(repo):
    """The reason this takes files rather than a branch.

    A real accepted branch carried a ninety-one line InfectedService.luau from
    a system the gate had REFUSED. Landing the branch would have put refused
    code on master under an accepted build's name.
    """
    land(repo, {"src/server/AirlockService.luau": "--!strict\nreturn {}\n"},
         message="feat(AirlockService): accepted")

    assert "src/server/InfectedService.luau" not in committed_files(repo)


def test_the_commit_message_says_where_the_code_came_from(repo):
    land(repo, {"src/server/A.luau": "--!strict\n"},
         message="feat(A): accepted by the gate\n\nFrom build build-1, revision 4.")

    log = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=repo,
                         capture_output=True, check=True).stdout.decode()
    assert "build-1" in log


# ---- never over someone's work ---------------------------------------------

def test_a_local_edit_is_left_alone_rather_than_overwritten(repo):
    """The repository is a real checkout that may be open in an editor."""
    target = repo / "src" / "server" / "WaveService.luau"
    target.write_text("--!strict\n-- mine\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "hand written")
    target.write_text("--!strict\n-- mine, edited and not committed\n", encoding="utf-8")

    result = land(repo, {"src/server/WaveService.luau": "--!strict\n-- generated\n"},
                  message="feat: accepted")

    assert result.committed is False
    assert "uncommitted changes" in result.detail
    assert "-- mine, edited" in target.read_text(encoding="utf-8")


def test_nothing_is_committed_from_another_branch(repo):
    """A build must not quietly commit onto whatever happens to be checked out."""
    git(repo, "checkout", "-b", "experiment")

    result = land(repo, {"src/server/A.luau": "--!strict\n"}, message="feat: accepted")

    assert result.committed is False
    assert "experiment" in result.detail
    assert "engineer/ branch" in result.detail


def test_an_untracked_file_does_not_block_landing(repo):
    """A file left behind by a previous run that was never committed is exactly
    what this exists to fix, so treating it as "someone's work" would make the
    gap permanent."""
    target = repo / "src" / "server" / "WaveService.luau"
    target.write_text("--!strict\n-- from a run that never landed\n", encoding="utf-8")

    result = land(repo, {"src/server/WaveService.luau": "--!strict\n-- new\n"},
                  message="feat: accepted")

    assert result.committed is True
    assert "-- new" in target.read_text(encoding="utf-8")


def test_a_directory_that_is_not_a_repository_is_refused(tmp_path):
    result = land(tmp_path, {"src/server/A.luau": "x"}, message="feat: accepted")

    assert result.committed is False
    assert "not a git repository" in result.detail


# ---- no noise --------------------------------------------------------------

def test_landing_the_same_content_twice_adds_no_second_commit(repo):
    source = "--!strict\nreturn {}\n"
    land(repo, {"src/server/A.luau": source}, message="feat: accepted")
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                            capture_output=True, check=True).stdout

    result = land(repo, {"src/server/A.luau": source}, message="feat: accepted again")

    after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                           capture_output=True, check=True).stdout
    assert result.committed is False
    assert result.detail == "already in the project, unchanged"
    assert before == after


def test_landing_nothing_does_nothing(repo):
    assert land(repo, {}, message="feat: accepted").detail == "nothing to land"


# ---- the small helpers -----------------------------------------------------

def test_the_branch_is_read_from_the_repository(repo):
    assert current_branch(repo) == "master"


def test_dirty_paths_ignores_files_it_was_not_asked_about(repo):
    (repo / "README.md").write_text("changed\n", encoding="utf-8")

    assert dirty_paths(repo, ["src/server/A.luau"]) == []


# ---- through the build loop ------------------------------------------------

def test_a_refused_system_is_never_landed(session_factory, tmp_path, monkeypatch):
    """End to end through run_build: the one that would be worst to get wrong."""
    import asyncio

    from app.blueprint import builds as module
    from app.blueprint.builds import BuildFailed, run_build
    from app.blueprint.schemas import GameSystem, SystemLayer
    from app.blueprint.store import BlueprintStore

    root = tmp_path / "game"
    (root / "src" / "server").mkdir(parents=True)
    git(root, "init", "-b", "master")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("game\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-m", "first")
    monkeypatch.setattr(module, "game_repo", lambda _settings: root)

    store = BlueprintStore(session_factory)
    plan = store.create(audit_id="audit-1", title="Lab")
    plan = store.save(plan.model_copy(update={
        "user_intent": "Survivors run a bunker.",
        "systems": [GameSystem(id="waveservice", name="WaveService", layer=SystemLayer.SERVER,
                               purpose="Run the waves.",
                               acceptance_criteria=["A bad amount is refused."])],
    }))

    class Refused:
        status = "refused"
        reason = "the gate refused every attempt"
        branch = "engineer/wave-service-1"
        commit = None
        attempts = 6

    async def stand_in(_task, **_kwargs):
        return Refused()

    monkeypatch.setattr(module, "run_task", stand_in)
    # If this were ever called for a refused system it would land something.
    monkeypatch.setattr(module, "files_on_branch",
                        lambda *_a, **_k: {"src/server/WaveService.luau": "--!strict\n"})

    async def go():
        try:
            await run_build(plan.id, settings=object(), factory=session_factory,
                            token="t", play=False, build_id="build-refuse")
        except BuildFailed:
            pass

    asyncio.run(go())

    assert "src/server/WaveService.luau" not in committed_files(root)


# ---- accepted alone is not accepted together -------------------------------
#
# Six systems that had each passed the gate in their own worktree were landed
# together and turned the project red: one called a function another did not
# export, one required a system the gate had REFUSED, and two used services the
# project's Services module does not carry. None of that is visible from inside
# a single worktree, so the question asked before keeping a file is about the
# project, not the file.


def test_a_system_that_breaks_the_project_is_not_kept(repo):
    result = land(repo, {"src/server/A.luau": "--!strict\nreturn {}\n"},
                  message="feat(A): accepted",
                  verify=lambda _root: (False, "A.luau: TypeError: Key 'Has' not found"))

    assert result.committed is False
    assert "does not build with it" in result.detail
    assert "Key 'Has' not found" in result.detail


def test_a_failed_check_leaves_the_working_tree_exactly_as_it_was(repo):
    """Nothing is committed and then undone, and no file is left behind for the
    next run to trip over."""
    target = repo / "src" / "server" / "A.luau"

    land(repo, {"src/server/A.luau": "--!strict\nreturn {}\n"},
         message="feat(A): accepted", verify=lambda _root: (False, "broken"))

    assert not target.exists()
    assert subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                          capture_output=True, check=True).stdout == b""


def test_a_failed_check_restores_a_file_that_was_already_there(repo):
    target = repo / "src" / "server" / "A.luau"
    target.write_text("--!strict\n-- the good one\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "first version")

    land(repo, {"src/server/A.luau": "--!strict\n-- the broken one\n"},
         message="feat(A): accepted", verify=lambda _root: (False, "broken"))

    assert target.read_text(encoding="utf-8") == "--!strict\n-- the good one\n"
    assert subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                          capture_output=True, check=True).stdout == b""


def test_the_check_sees_the_files_in_place(repo):
    """It has to run against the project WITH the new file, or it is checking
    the wrong thing."""
    seen = {}

    def verify(root):
        seen["content"] = (root / "src" / "server" / "A.luau").read_text(encoding="utf-8")
        return True, ""

    land(repo, {"src/server/A.luau": "--!strict\n-- new\n"},
         message="feat(A): accepted", verify=verify)

    assert "-- new" in seen["content"]


def test_a_check_that_raises_lands_nothing(repo):
    """A broken checker must not be read as a passing one."""
    def verify(_root):
        raise RuntimeError("rojo is not installed")

    result = land(repo, {"src/server/A.luau": "--!strict\n"},
                  message="feat(A): accepted", verify=verify)

    assert result.committed is False
    assert "could not run" in result.detail
    assert not (repo / "src" / "server" / "A.luau").exists()


def test_a_system_that_keeps_the_project_building_is_kept(repo):
    result = land(repo, {"src/server/A.luau": "--!strict\nreturn {}\n"},
                  message="feat(A): accepted", verify=lambda _root: (True, ""))

    assert result.committed is True
    assert "src/server/A.luau" in committed_files(repo)


# ---- the verifier the build actually uses ----------------------------------

def test_the_build_verifier_reports_what_the_gate_refused(session_factory, tmp_path,
                                                        monkeypatch):
    """Written because the first version called `GateReport.failed` as though
    it were a method. It is a property, so the call raised, `land` reported
    "the project check could not run", and every system that genuinely broke
    the project was skipped for the wrong reason with the real one never
    printed. The fakes in the tests above cannot catch that: only a real
    GateReport can.
    """
    import asyncio

    from app.blueprint import builds as module
    from app.blueprint.builds import BuildFailed, run_build
    from app.blueprint.schemas import GameSystem, SystemLayer
    from app.blueprint.store import BlueprintStore
    from app.engineer.gate import CheckResult, GateReport

    root = tmp_path / "game"
    (root / "src" / "server").mkdir(parents=True)
    git(root, "init", "-b", "master")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("game\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-m", "first")
    monkeypatch.setattr(module, "game_repo", lambda _settings: root)

    class Gate:
        def run(self, _root):
            return GateReport((
                CheckResult("guard", True, ""),
                CheckResult("luau-lsp", False,
                            "src/server/A.luau [game/X](274,27): TypeError: Key 'Has' "
                            "not found in table 'ResearchService'"),
            ))

    monkeypatch.setattr(module, "build_gate", lambda _settings: Gate())

    store = BlueprintStore(session_factory)
    plan = store.create(audit_id="audit-1", title="Lab")
    plan = store.save(plan.model_copy(update={
        "user_intent": "Survivors run a bunker.",
        "systems": [GameSystem(id="a", name="AService", layer=SystemLayer.SERVER,
                               purpose="Do a thing.",
                               acceptance_criteria=["A bad amount is refused."])],
    }))

    class Accepted:
        status = "complete"
        reason = ""
        branch = "engineer/a-service-1"
        commit = "abc123"
        attempts = 1

    async def stand_in(_task, **_kwargs):
        return Accepted()

    monkeypatch.setattr(module, "run_task", stand_in)
    monkeypatch.setattr(module, "files_on_branch",
                        lambda *_a, **_k: {"src/server/AService.luau": "--!strict\n"})

    async def go():
        try:
            await run_build(plan.id, settings=object(), factory=store.factory,
                            token="t", play=False, build_id="build-verify")
        except BuildFailed:
            pass

    asyncio.run(go())

    record = module.BuildRecord(store.factory, "build-verify").read()
    landed = record["systems"]["AService"]["landed"]
    assert landed["committed"] is False
    assert "Key 'Has' not found" in landed["detail"]
    assert "src/server/AService.luau" not in committed_files(root)


# ---- the Services module has to grow with the project ----------------------

def test_the_services_module_covers_what_the_incoming_system_uses(tmp_path):
    """The blocker that held back four of six systems.

    A run regenerates Services in its own worktree for exactly what that run
    used, so the project's copy is whatever the last landed system happened to
    require. A system using CollectionService then passed its own gate and
    failed in the project with "Key 'CollectionService' not found" -- nothing
    wrong with the system at all.
    """
    from app.engineer.workspace import SERVICES_PATH, services_for_project, services_in

    repo = tmp_path / "game"
    (repo / "src" / "server").mkdir(parents=True)
    (repo / SERVICES_PATH).parent.mkdir(parents=True, exist_ok=True)
    (repo / "src" / "server" / "Old.luau").write_text(
        "--!strict\nlocal p = Services.Players\nreturn {}\n", encoding="utf-8")

    modules = services_for_project(
        repo, {"src/server/New.luau": "--!strict\nlocal c = Services.CollectionService\n"},
        {"Players", "CollectionService", "ReplicatedStorage"})

    names = services_in(modules[SERVICES_PATH])
    assert "CollectionService" in names, "the incoming system's service"
    assert "Players" in names, "and the one a system already in the project uses"


def test_a_name_that_is_not_a_service_is_not_rendered(tmp_path):
    """The module is generated from source text, so it must not turn a typo or
    a local table named Services into a GetService call that cannot resolve."""
    from app.engineer.workspace import SERVICES_PATH, services_for_project, services_in

    repo = tmp_path / "game"
    (repo / "src" / "server").mkdir(parents=True)

    modules = services_for_project(
        repo, {"src/server/New.luau": "--!strict\nlocal x = Services.NotAService\n"},
        {"Players"})

    assert "NotAService" not in services_in(modules[SERVICES_PATH])


def test_the_client_gets_its_own_copy(tmp_path):
    from app.engineer.workspace import CLIENT_SERVICES_PATH, SERVICES_PATH, services_for_project

    repo = tmp_path / "game"
    (repo / "src" / "client").mkdir(parents=True)

    modules = services_for_project(
        repo, {"src/client/Hud.luau": "--!strict\nlocal r = Services.ReplicatedStorage\n"},
        {"ReplicatedStorage"})

    assert modules[CLIENT_SERVICES_PATH] == modules[SERVICES_PATH]


# ---- leaving nothing behind ------------------------------------------------

def test_a_failed_check_leaves_nothing_staged(repo):
    """A killed build left the project with a staged add of a file that was no
    longer there -- `AD src/server/BayService.luau` -- and the next build would
    have refused to land on top of a dirty tree. Putting the file back is only
    half of putting the tree back."""
    land(repo, {"src/server/A.luau": "--!strict\n"},
         message="feat(A): accepted", verify=lambda _root: (False, "broken"))

    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=repo,
                            capture_output=True, check=True).stdout
    assert staged == b""


def test_landing_unchanged_content_leaves_nothing_staged(repo):
    source = "--!strict\nreturn {}\n"
    land(repo, {"src/server/A.luau": source}, message="feat: accepted")

    land(repo, {"src/server/A.luau": source}, message="feat: accepted again")

    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=repo,
                            capture_output=True, check=True).stdout
    assert staged == b""


# ---- a repository made minutes ago -----------------------------------------

def test_landing_works_in_a_repository_with_no_identity_configured(tmp_path):
    """A brand-new project has no user.name or user.email, so `git commit`
    answers "Author identity unknown" -- and every system of a fresh project
    built, passed six checks, and then failed to reach the project for a
    setting that has nothing to do with the code.

    The Engineer's own commits never hit this because workspace.py passes the
    identity explicitly. Landing now does the same.
    """
    root = tmp_path / "fresh"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "master"], cwd=root, capture_output=True, check=True)
    # Deliberately no user.name / user.email, and no inherited global either.
    (root / "README.md").write_text("new\n", encoding="utf-8")
    subprocess.run(["git", "-c", "user.name=seed", "-c", "user.email=seed@example.com",
                    "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "-c", "user.name=seed", "-c", "user.email=seed@example.com",
                    "commit", "-m", "first"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "config", "--unset-all", "user.name"], cwd=root, capture_output=True)
    subprocess.run(["git", "config", "--unset-all", "user.email"], cwd=root, capture_output=True)

    result = land(root, {"src/server/A.luau": "--!strict\nreturn {}\n"},
                  message="feat(A): accepted by the gate")

    assert result.committed is True, result.detail
    assert "src/server/A.luau" in committed_files(root)


def test_the_commit_is_attributed_to_the_agent_that_wrote_it(repo):
    """An agent wrote this code. A commit carrying whoever configured the
    machine says otherwise, and the git history is the record of who did."""
    land(repo, {"src/server/A.luau": "--!strict\n"}, message="feat(A): accepted")

    author = subprocess.run(["git", "log", "-1", "--format=%an"], cwd=repo,
                            capture_output=True, check=True).stdout.decode().strip()
    assert author == "Roblox Engineer Agent"
