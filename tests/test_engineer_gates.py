"""Dependency gates, playability gates, and targeted re-planning.

All three answer questions the build could not answer before: what can be
worked on, how far a player could actually get, and what a mid-build change
breaks. The tests are mostly about the answers being derived rather than
asserted -- a gate that passes because nothing claims it, or a change that
invalidates everything, are both worse than no answer.
"""

from __future__ import annotations

from app.engineer.doctrine import GameplayPath, GateName, PathNode
from app.engineer.gates import TaskState, gate_states, invalidated_by, playability


def path(*nodes: tuple[str, GateName, list[str]]) -> GameplayPath:
    return GameplayPath(nodes=[
        PathNode(id=label.lower().replace(" ", "-"), label=label, gate=gate, systems=systems)
        for label, gate, systems in nodes])


# ---- what can be worked on -------------------------------------------------

def test_a_system_whose_dependencies_are_done_is_ready():
    states = {s.name: s for s in gate_states(
        order=["ItemDefinition", "InventoryService"],
        depends={"InventoryService": ["ItemDefinition"]},
        outcomes={"ItemDefinition": "built"})}

    assert states["InventoryService"].state is TaskState.READY
    assert states["InventoryService"].waiting_for == ()


def test_a_system_whose_dependency_is_missing_is_blocked_and_says_by_what():
    states = {s.name: s for s in gate_states(
        order=["ItemDefinition", "InventoryService"],
        depends={"InventoryService": ["ItemDefinition"]},
        outcomes={})}

    assert states["InventoryService"].state is TaskState.BLOCKED
    assert states["InventoryService"].waiting_for == ("ItemDefinition",)


def test_a_refusal_blocks_everything_downstream_of_it():
    """The reason BLOCKED and "waiting its turn" are different words. A refusal
    partway through leaves the rest of the queue looking like it is still
    progressing."""
    states = {s.name: s for s in gate_states(
        order=["ItemDefinition", "InventoryService", "ShopService"],
        depends={"InventoryService": ["ItemDefinition"],
                 "ShopService": ["InventoryService"]},
        outcomes={"ItemDefinition": "refused"})}

    assert states["ItemDefinition"].state is TaskState.FAILED
    assert states["InventoryService"].state is TaskState.BLOCKED
    assert states["ShopService"].state is TaskState.BLOCKED


def test_the_system_being_written_is_building_rather_than_ready():
    states = {s.name: s for s in gate_states(
        order=["A", "B"], depends={}, outcomes={}, current="A")}

    assert states["A"].state is TaskState.BUILDING
    assert states["B"].state is TaskState.READY


def test_a_dependency_outside_the_build_does_not_block():
    """It is already in the project, or it is reported elsewhere -- either way
    a name the build does not own must not stall the build forever."""
    states = {s.name: s for s in gate_states(
        order=["InventoryService"],
        depends={"InventoryService": ["SomethingElse"]}, outcomes={})}

    assert states["InventoryService"].state is TaskState.READY


# ---- how far a player could get --------------------------------------------

def test_a_gate_passes_only_when_every_system_behind_it_is_built():
    plan = path(("Spawn on the dock", GateName.SPAWNABLE, ["PierService"]),
                ("Cast", GateName.CORE_ACTION, ["FishingService", "RodService"]))

    gates = {g.name: g for g in playability(plan, {"PierService": "built",
                                                   "FishingService": "built"})}

    assert gates[GateName.SPAWNABLE].passed
    assert not gates[GateName.CORE_ACTION].passed
    assert gates[GateName.CORE_ACTION].needs == ("RodService",)


def test_a_gate_nothing_claims_does_not_pass_by_default():
    """An empty requirement is not an achievement. A build with no UI planned
    must not report that the player can understand it."""
    gates = {g.name: g for g in playability(
        path(("Spawn", GateName.SPAWNABLE, ["PierService"])), {"PierService": "built"})}

    assert not gates[GateName.UNDERSTANDABLE].passed
    assert "nothing in the plan reaches" in gates[GateName.UNDERSTANDABLE].detail


def test_mvp_cannot_be_reached_while_an_earlier_gate_is_not():
    """It is the whole slice, so it cannot be ahead of the ladder below it."""
    plan = path(("Spawn", GateName.SPAWNABLE, ["PierService"]),
                ("Play", GateName.MVP_PLAYABLE, ["FishingService"]))

    gates = {g.name: g for g in playability(plan, {"FishingService": "built"})}

    assert gates[GateName.MVP_PLAYABLE].passed is False
    assert "spawnable" in gates[GateName.MVP_PLAYABLE].detail


def test_a_finished_build_reaches_its_gates():
    plan = path(("Spawn", GateName.SPAWNABLE, ["PierService"]),
                ("Cast", GateName.CORE_ACTION, ["FishingService"]),
                ("See the catch", GateName.UNDERSTANDABLE, ["HudController"]))

    gates = {g.name: g for g in playability(
        plan, {"PierService": "built", "FishingService": "built",
               "HudController": "built"})}

    assert gates[GateName.SPAWNABLE].passed
    assert gates[GateName.CORE_ACTION].passed
    assert gates[GateName.UNDERSTANDABLE].passed


# ---- what a change breaks --------------------------------------------------

DEPENDS = {
    "FishCatalog": ["ItemDefinition"],
    "FishSelector": ["FishCatalog"],
    "FishingService": ["FishSelector", "InventoryService"],
    "InventoryService": ["ItemDefinition"],
    "ShopService": ["InventoryService", "WalletService"],
    "WalletService": [],
    "ItemDefinition": [],
}


def test_a_change_reaches_everything_downstream_of_it():
    reached = invalidated_by({"FishSelector"}, DEPENDS)

    assert reached == {"FishSelector", "FishingService"}


def test_a_change_does_not_reach_what_does_not_depend_on_it():
    """The point of doing this at all: rebuilding everything throws away work
    that was fine. Rare fish appearing only at night does not touch the wallet."""
    reached = invalidated_by({"FishSelector"}, DEPENDS)

    assert "WalletService" not in reached
    assert "ShopService" not in reached
    assert "InventoryService" not in reached


def test_a_change_to_a_foundation_reaches_far():
    reached = invalidated_by({"ItemDefinition"}, DEPENDS)

    assert reached == {"ItemDefinition", "FishCatalog", "FishSelector",
                       "FishingService", "InventoryService", "ShopService"}


def test_a_change_to_a_leaf_reaches_only_itself():
    assert invalidated_by({"ShopService"}, DEPENDS) == {"ShopService"}


def test_changing_nothing_invalidates_nothing():
    assert invalidated_by(set(), DEPENDS) == set()
