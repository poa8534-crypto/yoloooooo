"""Where generated code may land, how it is written, and the worktree it lives in."""

from __future__ import annotations

import subprocess

import pytest

from app.engineer.luau_guard import render_services_module
from app.engineer.workspace import (
    SERVICES_PATH,
    UnsafePath,
    Worktree,
    normalize_source,
    services_in,
    validate_path,
)


@pytest.mark.parametrize("path", [
    "src/server/CombatService.luau",
    "src/shared/Types/Player.luau",
    "src/server/init.server.luau",
])
def test_paths_inside_the_writable_roots_are_accepted(path):
    assert validate_path(path) == path


@pytest.mark.parametrize("path", [
    "../evil.luau", "src/server/../../x.luau", "/etc/x.luau", "C:/x.luau", "src\\server\\x.luau",
    "src/server/x.lua", "src/server/x.txt", "default.project.json", "src/serverx/a.luau",
    SERVICES_PATH, "src/shared/services.luau", "src/server//x.luau", "src/server/./x.luau",
    "src/server/CON.luau", "src/server/nul.server.luau", "src/server/a b.luau", "src/server/.hidden.luau",
    "src/client/Controller.luau",  # client code is not enabled; see WRITABLE_ROOTS
])
def test_unsafe_or_unsupported_paths_are_refused(path):
    with pytest.raises(UnsafePath):
        validate_path(path)


def test_sources_are_written_without_bom_and_with_lf():
    assert normalize_source("\ufeff--!strict\r\nlocal x = 1\rreturn x") == b"--!strict\nlocal x = 1\nreturn x\n"


def test_service_names_are_read_back_from_a_generated_module():
    assert services_in(render_services_module(["Players", "ReplicatedStorage"])) == {"Players", "ReplicatedStorage"}


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def game_repo(tmp_path):
    repo = tmp_path / "game"
    (repo / "src" / "shared").mkdir(parents=True)
    git("init", "-q", "-b", "master", cwd=repo)
    (repo / "src" / "shared" / "Hello.luau").write_bytes(b"--!strict\nreturn {}\n")
    (repo / "default.project.json").write_text('{"name": "game"}\n')
    git("add", "-A", cwd=repo)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base", cwd=repo)
    return repo


def test_worktree_commits_to_its_own_branch_and_leaves_master_alone(game_repo, tmp_path):
    tree = Worktree.create(game_repo, "master", tmp_path / "worktrees", "data-service-abcd1234")
    assert tree.read_existing_sources() == {"src/shared/Hello.luau": "--!strict\nreturn {}\n"}
    assert tree.project_file() == '{"name": "game"}\n'

    written = tree.write({"src/server/DataService.luau": "--!strict\r\nreturn {}"}, {"Players"})
    commit = tree.commit(written, "feat: data")
    tree.remove(delete_branch=False)

    assert not tree.path.exists()
    assert git("rev-parse", "engineer/data-service-abcd1234", cwd=game_repo) == commit
    assert git("show", f"{commit}:src/server/DataService.luau", cwd=game_repo) == "--!strict\nreturn {}"
    assert "Players" in git("show", f"{commit}:{SERVICES_PATH}", cwd=game_repo)
    assert git("log", "--format=%s", "master", cwd=game_repo) == "base"
    assert not (game_repo / "src" / "server").exists()


def test_reset_discards_a_previous_attempt(game_repo, tmp_path):
    tree = Worktree.create(game_repo, "master", tmp_path / "worktrees", "combat-abcd1234")
    tree.write({"src/server/Old.luau": "--!strict\n"}, set())
    tree.reset()
    assert not (tree.path / "src" / "server" / "Old.luau").exists()
    assert not (tree.path / SERVICES_PATH).exists()
    tree.remove(delete_branch=True)
    assert "engineer/combat-abcd1234" not in git("branch", "--list", cwd=game_repo)


def test_worktree_name_must_be_a_slug(game_repo, tmp_path):
    with pytest.raises(ValueError):
        Worktree.create(game_repo, "master", tmp_path, "../escape")
