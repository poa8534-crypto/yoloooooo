"""Revising systems the project already has.

A build skips any system the project already holds, which is right for a
patch and wrong when the world a system lives in has moved: the old version
would be kept forever. `--revise` appends new criteria to named systems and
rebuilds them. These cases hold the appending to what it promises.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import autobuild  # noqa: E402


class _System(SimpleNamespace):
    def model_copy(self, update):
        return _System(**{**self.__dict__, **update})


class _Blueprint(SimpleNamespace):
    def model_copy(self, update):
        return _Blueprint(**{**self.__dict__, **update})


def _blueprint(criteria=4):
    return _Blueprint(systems=[
        _System(name="IslandService", purpose="Builds the island.", acceptance_criteria=[f"c{i}" for i in range(criteria)]),
        _System(name="ShopView", purpose="Shows the shop.", acceptance_criteria=["s"]),
    ])


def test_a_revision_adds_its_purpose_and_criteria_to_that_system_only():
    revised, touched = autobuild.apply_revisions(
        _blueprint(), {"IslandService": {"purpose": "Moved.", "criteria": ["plots in the west"]}})

    island, shop = revised.systems
    assert touched == ["IslandService"]
    assert island.purpose.endswith("Moved.")
    assert island.acceptance_criteria[-1] == "plots in the west"
    assert shop.acceptance_criteria == ["s"]


def test_applying_the_same_revision_twice_does_not_grow_the_specification():
    change = {"IslandService": {"purpose": "Moved.", "criteria": ["plots in the west"]}}
    once, _ = autobuild.apply_revisions(_blueprint(), change)
    twice, _ = autobuild.apply_revisions(once, change)

    assert twice.systems[0].purpose == once.systems[0].purpose
    assert twice.systems[0].acceptance_criteria == once.systems[0].acceptance_criteria


def test_a_marked_revision_replaces_the_last_one_instead_of_stacking_beside_it():
    first, _ = autobuild.apply_revisions(
        _blueprint(), {"IslandService": {"purpose": "Moved.", "criteria": ["spawn at 150"]}}, marker="layout")
    second, _ = autobuild.apply_revisions(
        first, {"IslandService": {"purpose": "Moved again.", "criteria": ["spawn at 290"]}}, marker="layout")

    island = second.systems[0]
    assert "Moved." not in island.purpose and "Moved again. [layout]" in island.purpose
    assert "spawn at 150 [layout]" not in island.acceptance_criteria
    assert island.acceptance_criteria[-1] == "spawn at 290 [layout]"
    assert island.acceptance_criteria[:4] == ["c0", "c1", "c2", "c3"]


def test_a_revision_for_a_system_the_blueprint_lacks_is_refused():
    with pytest.raises(ValueError, match="NoSuchService"):
        autobuild.apply_revisions(_blueprint(), {"NoSuchService": {"criteria": ["x"]}})


def test_criteria_past_the_schema_limit_are_refused_not_cut():
    with pytest.raises(ValueError, match="over 20"):
        autobuild.apply_revisions(_blueprint(criteria=19), {"IslandService": {"criteria": ["a", "b"]}})
