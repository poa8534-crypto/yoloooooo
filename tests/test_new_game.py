"""A new game's repository, made from the template rather than by hand."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("new_game", ROOT / "scripts" / "new_game.py")
new_game = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(new_game)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True,
                          text=True).stdout.strip()


@pytest.fixture
def old_game(tmp_path):
    """An existing game with the generated inputs a new one needs."""
    game = tmp_path / "ascent"
    game.mkdir()
    for name in new_game.GENERATED_INPUTS:
        (game / name).write_text(f"-- {name}\n", encoding="utf-8")
    return game


def test_the_template_becomes_one_commit_named_after_the_game(tmp_path, old_game):
    repo, missing = new_game.create("neuromine", tmp_path, old_game)

    template_files = sorted(p.relative_to(new_game.TEMPLATE).as_posix()
                            for p in new_game.TEMPLATE.rglob("*") if p.is_file())
    assert git(repo, "ls-files").splitlines() == template_files
    assert git(repo, "log", "--format=%s") == "chore: empty neuromine project, toolchain only"
    assert git(repo, "log", "--format=%an") == "Roblox Engineer Agent"
    assert git(repo, "status", "--porcelain") == ""
    assert missing == []
    # Rojo names the DataModel from this; a place wearing another game's name
    # is how the wrong project was noticed, twice.
    assert json.loads((repo / "default.project.json").read_text())["name"] == "neuromine"


def test_every_folder_rojo_maps_exists_even_while_empty(tmp_path, old_game):
    # src/server has nothing in it until the first system lands, git does not
    # carry an empty folder, and Rojo refuses a project whose $path is missing.
    repo, _missing = new_game.create("neuromine", tmp_path, old_game)

    project = json.loads((repo / "default.project.json").read_text())
    paths = []

    def walk(node):
        if isinstance(node, dict):
            if "$path" in node:
                paths.append(node["$path"])
            for value in node.values():
                walk(value)

    walk(project["tree"])
    assert paths
    for path in paths:
        assert (repo / path).is_dir(), path


def test_the_generated_inputs_are_copied_but_never_committed(tmp_path, old_game):
    repo, _missing = new_game.create("neuromine", tmp_path, old_game)

    for name in new_game.GENERATED_INPUTS:
        assert (repo / name).read_text(encoding="utf-8") == f"-- {name}\n"
        assert name not in git(repo, "ls-files").splitlines()


def test_sources_keep_the_line_endings_their_checks_demand(tmp_path, old_game):
    repo, _missing = new_game.create("neuromine", tmp_path, old_game)

    for path in (repo / "src").rglob("*.luau"):
        assert b"\r\n" not in path.read_bytes(), path


def test_it_says_which_inputs_it_could_not_find(tmp_path):
    empty = tmp_path / "nothing-here"
    empty.mkdir()

    _repo, missing = new_game.create("neuromine", tmp_path, empty)

    assert missing == list(new_game.GENERATED_INPUTS)


def test_it_never_writes_over_a_folder_with_something_in_it(tmp_path, old_game):
    taken = tmp_path / "ascent-two"
    taken.mkdir()
    (taken / "notes.txt").write_text("mine", encoding="utf-8")

    with pytest.raises(new_game.Refused, match="not empty"):
        new_game.create("ascent-two", tmp_path, old_game)
    assert [p.name for p in taken.iterdir()] == ["notes.txt"]


@pytest.mark.parametrize("name", ["NeuroMine", "neuro mine", "../escape", "9lives", "x"])
def test_a_name_that_cannot_be_a_folder_branch_and_place_is_refused(tmp_path, name):
    with pytest.raises(new_game.Refused):
        new_game.create(name, tmp_path, None)
