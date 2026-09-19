"""Proof that the doctrine is loaded and obeyed, not merely written down.

The brief that asked for instruction.md was explicit: do not create it and
leave it unused. These are the tests that would fail if it became decorative --
if the Engineer stopped being given it, if the build order went back to being
whatever a model preferred, or if a refused feature could return under a
system's name.
"""

from __future__ import annotations

import pytest

from app.blueprint.compile import NotReady, compile_spec
from app.blueprint.schemas import (
    Blueprint,
    FeatureSuggestion,
    GameSystem,
    SystemLayer,
)

CRITERION = "Spending more than the player holds is refused and changes nothing."


def system(name: str, *, layer=SystemLayer.SERVER, depends=(), priority="P5",
           blocker=False, slice_=False, flow=999) -> GameSystem:
    return GameSystem(
        id=name.lower(), name=name, layer=layer,
        purpose=f"Look after what {name} is for.",
        acceptance_criteria=[CRITERION], depends_on=list(depends),
        priority_class=priority, core_loop_blocker=blocker,
        required_for_vertical_slice=slice_, player_flow_index=flow)


def blueprint(systems, **overrides) -> Blueprint:
    fields = {
        "id": "bp", "project_id": "p", "audit_id": "a", "title": "A Game",
        "user_intent": "Players do a thing and are rewarded for it.",
        "systems": systems,
    }
    return Blueprint(**{**fields, **overrides})


# ---- the Engineer is actually given it -------------------------------------

def test_the_engineers_prompt_contains_the_doctrine():
    """The one that fails if instruction.md becomes decoration."""
    from app.engineer.prompts import SYSTEM

    assert "PLAYER EXPERIENCE" in SYSTEM
    assert "Where does that reward go?" in SYSTEM
    # And the placeholder was actually substituted rather than shipped.
    assert "{DOCTRINE}" not in SYSTEM


def test_the_prompt_says_the_specification_outranks_the_doctrine():
    """It must never let the Engineer talk itself out of what was approved."""
    from app.engineer.prompts import SYSTEM

    assert "the specification" in SYSTEM
    assert "always wins" in SYSTEM


def test_the_doctrine_is_read_from_the_file_rather_than_duplicated():
    """One copy, so editing instruction.md changes what the Engineer is told."""
    from app.engineer.doctrine import DOCTRINE_FILE, doctrine

    assert DOCTRINE_FILE.name == "instruction.md"
    assert DOCTRINE_FILE.is_file()
    assert doctrine().startswith("# Engineering Agent doctrine")


# ---- the build order obeys it ----------------------------------------------

def test_the_specification_orders_by_dependency_not_by_the_order_given():
    """The whole point. Systems are listed in a deliberately wrong order and
    the specification comes out right."""
    spec = compile_spec(blueprint([
        system("ShopService", depends=("InventoryService", "WalletService"),
               priority="P3", blocker=True, slice_=True, flow=6),
        system("FishingService", depends=("FishCatalog", "InventoryService"),
               priority="P2", blocker=True, slice_=True, flow=5),
        system("InventoryService", depends=("ItemDefinition",),
               priority="P1", blocker=True, slice_=True, flow=4),
        system("QuestService", depends=("InventoryService",), priority="P5"),
        system("ItemDefinition", layer=SystemLayer.SHARED, priority="P0",
               blocker=True, slice_=True, flow=0),
        system("FishCatalog", depends=("ItemDefinition",), layer=SystemLayer.SHARED,
               priority="P0", blocker=True, slice_=True, flow=2),
        system("WalletService", priority="P1", blocker=True, slice_=True, flow=3),
    ]))

    order = spec.build_order
    assert order[0] == "ItemDefinition", "definitions first: everything references them"
    assert order.index("InventoryService") < order.index("FishingService"), \
        "the fish needs somewhere to go before it can be caught"
    assert order.index("FishCatalog") < order.index("FishingService")
    assert order.index("FishingService") < order.index("ShopService")
    assert order[-1] == "QuestService", "nothing depends on quests; nothing waits for them"


def test_priority_never_beats_a_dependency():
    """A P0 that needs a P5 still comes second. Topology is absolute."""
    spec = compile_spec(blueprint([
        system("Config", depends=("Legacy",), priority="P0", slice_=True),
        system("Legacy", priority="P5"),
    ]))

    assert spec.build_order == ["Legacy", "Config"]


def test_the_vertical_slice_is_built_before_equally_ready_work():
    spec = compile_spec(blueprint([
        system("Decoration", priority="P0"),
        system("CoreThing", priority="P5", slice_=True),
    ]))

    assert spec.build_order == ["CoreThing", "Decoration"]


def test_the_same_specification_orders_the_same_way_twice():
    systems = [system("Beta"), system("Alpha"), system("Gamma")]

    first = compile_spec(blueprint(systems)).build_order
    second = compile_spec(blueprint(list(reversed(systems)))).build_order

    assert first == second


def test_a_dependency_circle_refuses_to_compile():
    with pytest.raises(NotReady) as raised:
        compile_spec(blueprint([
            system("Alpha", depends=("Beta",)), system("Beta", depends=("Alpha",))]))

    assert "circle" in str(raised.value)


# ---- the task knows why it is being built now ------------------------------

def test_a_task_is_told_where_it_sits_and_what_waits_on_it():
    """A system built as though nothing else existed is how a build ends up as
    a folder of disconnected features."""
    from app.engineer.from_spec import tasks_from

    spec = compile_spec(blueprint([
        system("ItemDefinition", layer=SystemLayer.SHARED, priority="P0",
               blocker=True, slice_=True, flow=0),
        system("InventoryService", depends=("ItemDefinition",), priority="P1",
               blocker=True, slice_=True, flow=1),
    ]))
    tasks = {task.system: task for task in tasks_from(spec)}

    notes = " ".join(tasks["ItemDefinition"].notes)
    assert "system 1 of 2" in notes
    assert "VERTICAL SLICE" in notes
    assert "core loop CANNOT complete without it" in notes
    assert "InventoryService" in notes, "it must know what is waiting on it"


def test_a_system_nothing_depends_on_is_told_to_keep_its_surface_small():
    from app.engineer.from_spec import tasks_from

    spec = compile_spec(blueprint([system("LonelyService", priority="P5")]))
    task = tasks_from(spec)[0]

    assert "keep its surface small" in " ".join(task.notes)


# ---- what the person refused stays refused ---------------------------------

def test_a_rejected_feature_is_still_carried_into_every_task():
    """The specification decides what is built, and a refusal is part of it."""
    from app.engineer.from_spec import tasks_from

    plan = blueprint(
        [system("InventoryService", priority="P1", slice_=True)],
        suggestions=[
            FeatureSuggestion(id="pets", title="Pet System",
                              description="Companions that follow the player.",
                              reason="Players like pets.", selected=False),
        ])
    task = tasks_from(compile_spec(plan))[0]

    notes = " ".join(task.notes)
    assert "REFUSED" in notes
    assert "Pet System" in notes
