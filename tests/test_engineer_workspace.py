"""Where generated code may land, how it is written, and the worktree it lives in."""

from __future__ import annotations

import subprocess

import pytest

from app.engineer.luau_guard import render_services_module
from app.engineer.workspace import (
    CLIENT_SERVICES_PATH,
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


# ---- inputs git does not carry ---------------------------------------------

def test_the_worktree_gets_the_files_the_checks_need(tmp_path):
    """`globalTypes.None.d.luau` is generated and gitignored, so a fresh
    worktree has none. luau-lsp answers a missing definitions file with an
    ERROR line and then exits 0, and `Services.Players:MadeUp()` drew no
    diagnostic at all in that state -- the one mistake the Services module
    exists to catch. verify.ps1 now refuses when it is absent, so without this
    copy every engineer run stops at preflight.
    """
    repo = tmp_path / "game"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    git("config", "user.email", "t@t.invalid", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    (repo / ".gitignore").write_text("globalTypes.None.d.luau\nroblox.yml\n", encoding="utf-8")
    (repo / "keep.txt").write_text("tracked\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "base", cwd=repo)
    # Generated after the commit, exactly as the real ones are.
    (repo / "globalTypes.None.d.luau").write_text("declare extern type X\n", encoding="utf-8")
    (repo / "roblox.yml").write_text("base: lua51\n", encoding="utf-8")

    tree = Worktree.create(repo, "master", tmp_path / "trees", "probe-run")

    assert (tree.path / "globalTypes.None.d.luau").is_file(), "the type check has no definitions"
    assert (tree.path / "roblox.yml").is_file()
    assert (tree.path / "globalTypes.None.d.luau").read_text(encoding="utf-8") == "declare extern type X\n"


def test_a_reset_between_attempts_keeps_them(tmp_path):
    """`reset()` runs `git clean -fd`, which leaves ignored files alone. If
    that ever changes, every attempt after the first loses its type
    definitions and the gate starts refusing for the wrong reason."""
    repo = tmp_path / "game"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    git("config", "user.email", "t@t.invalid", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    (repo / ".gitignore").write_text("globalTypes.None.d.luau\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "base", cwd=repo)
    (repo / "globalTypes.None.d.luau").write_text("definitions\n", encoding="utf-8")

    tree = Worktree.create(repo, "master", tmp_path / "trees", "probe-reset")
    (tree.path / "src").mkdir(parents=True, exist_ok=True)
    (tree.path / "src" / "Junk.luau").write_text("--!strict\nreturn {}\n", encoding="utf-8")

    tree.reset()

    assert not (tree.path / "src" / "Junk.luau").exists(), "the attempt's files survived the reset"
    assert (tree.path / "globalTypes.None.d.luau").is_file(), "the reset removed the definitions"


def test_a_repo_without_them_still_builds_a_worktree(tmp_path):
    """Copying is best effort: the gate reports the absence with the command
    to fix it, which is a better message than a crash in worktree creation."""
    repo = tmp_path / "game"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    git("config", "user.email", "t@t.invalid", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    (repo / "keep.txt").write_text("tracked\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "base", cwd=repo)

    tree = Worktree.create(repo, "master", tmp_path / "trees", "probe-bare")

    assert tree.path.is_dir()
    assert not (tree.path / "globalTypes.None.d.luau").exists()


def test_sources_are_read_back_with_the_line_endings_they_were_written_with(game_repo, tmp_path):
    """Git for Windows defaults core.autocrlf to true at system level, so a
    worktree checkout returns CRLF for a file committed with LF. Reading it
    raw showed the model CRLF sources while `.gitattributes` forces LF and
    stylua.toml declares Unix endings -- a contradiction it could not see.
    Writes were already normalised; only the read was asymmetric.
    """
    git("config", "core.autocrlf", "true", cwd=game_repo)
    tree = Worktree.create(game_repo, "master", tmp_path / "worktrees", "eol-abcd1234")
    try:
        sources = tree.read_existing_sources()
    finally:
        tree.remove(delete_branch=True)

    assert sources == {"src/shared/Hello.luau": "--!strict\nreturn {}\n"}
    assert all("\r" not in text for text in sources.values())


def test_client_code_is_writable_now_that_its_require_form_is_pinned_down():
    """Client scripts are copied into Player.PlayerScripts at run time, so only
    requires that stay inside the copied folder are safe. That is a guard rule
    (`client-require-escapes`), not a reason to refuse the path."""
    assert validate_path("src/client/Controller.luau") == "src/client/Controller.luau"


def test_the_generated_client_services_module_cannot_be_written_by_the_model():
    with pytest.raises(UnsafePath, match="generated"):
        validate_path(CLIENT_SERVICES_PATH)
