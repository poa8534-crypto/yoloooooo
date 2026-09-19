"""Build order and plan validation.

The Engineer used to be handed a list of systems and an order a model had
chosen, and the order was plausible rather than correct. These are the tests
that make it correct: topology decides, priority only breaks ties, and a plan
that could not produce the player experience it describes is refused before
anything is written.
"""

from __future__ import annotations

import pytest

from app.engineer.doctrine import (
    GameplayPath,
    PathNode,
    PlayerJourney,
    PriorityClass,
    doctrine,
    doctrine_summary,
)
from app.engineer.planning import PlanInvalid, PlannedSystem, build_order, validate_plan


def system(name: str, *depends: str, priority=PriorityClass.SECONDARY,
           blocker: bool = False, slice_: bool = False, flow: int = 999,
           purpose: str = "") -> PlannedSystem:
    return PlannedSystem(name=name, depends_on=depends, priority=priority,
                         core_loop_blocker=blocker, required_for_vertical_slice=slice_,
                         player_flow_index=flow, purpose=purpose)


# ---- the doctrine exists and says what it must -----------------------------

def test_the_doctrine_is_readable():
    text = doctrine()

    assert "PLAYER EXPERIENCE" in text
    assert "Where does that reward go?" in text


def test_the_doctrine_states_the_priority_hierarchy():
    """It must never let the Engineer overrule the person."""
    text = doctrine()

    assert "approved GameBuildSpecification" in text
    assert "never overrides a decision the person made" in text


def test_the_doctrine_forbids_features_nobody_asked_for():
    # Whitespace-normalised: the document is wrapped for reading, and a rule
    # must not stop counting because it fell across two lines.
    text = " ".join(doctrine().lower().split())

    assert "do not build pets" in text
    assert "do not add persistence because roblox games usually have it" in text


def test_the_summary_cuts_whole_sections_rather_than_sentences():
    """Half a rule reads as a different rule."""
    trimmed = doctrine_summary(limit=2000)

    assert len(trimmed) <= 2000
    assert not trimmed.endswith((",", "and", "the"))


# ---- build order is computed, not preferred --------------------------------

def test_dependencies_are_built_before_what_needs_them():
    order = build_order([
        system("FishingService", "FishCatalog", "InventoryService"),
        system("InventoryService", "ItemDefinition"),
        system("FishCatalog", "ItemDefinition"),
        system("ItemDefinition"),
    ])

    assert order.index("ItemDefinition") < order.index("InventoryService")
    assert order.index("InventoryService") < order.index("FishingService")
    assert order.index("FishCatalog") < order.index("FishingService")


def test_topology_beats_priority():
    """The rule that stops a plausible order being a wrong one. A P0 that
    depends on a P5 still comes after it -- priority chooses between systems
    that are equally ready, never between a system and its dependency."""
    order = build_order([
        system("Foundation", "Secondary", priority=PriorityClass.FOUNDATION, slice_=True),
        system("Secondary", priority=PriorityClass.SECONDARY),
    ])

    assert order == ["Secondary", "Foundation"]


def test_priority_breaks_ties_between_systems_that_are_both_ready():
    order = build_order([
        system("Polish", priority=PriorityClass.POLISH),
        system("Foundation", priority=PriorityClass.FOUNDATION),
        system("Gameplay", priority=PriorityClass.PRIMARY_GAMEPLAY),
    ])

    assert order == ["Foundation", "Gameplay", "Polish"]


def test_the_vertical_slice_outranks_everything_else_that_is_ready():
    order = build_order([
        system("Decoration", priority=PriorityClass.FOUNDATION),
        system("CoreThing", priority=PriorityClass.SECONDARY, slice_=True),
    ])

    assert order == ["CoreThing", "Decoration"]


def test_a_core_loop_blocker_outranks_an_equal_system_that_is_not_one():
    order = build_order([
        system("Nice", priority=PriorityClass.PRIMARY_GAMEPLAY),
        system("Needed", priority=PriorityClass.PRIMARY_GAMEPLAY, blocker=True),
    ])

    assert order == ["Needed", "Nice"]


def test_the_order_is_the_same_every_time():
    """Two runs of one specification must build the same thing, or nothing
    downstream is reproducible."""
    systems = [system("B"), system("A"), system("C")]

    assert build_order(systems) == build_order(list(reversed(systems)))


def test_a_circle_is_refused_rather_than_broken_arbitrarily():
    with pytest.raises(PlanInvalid) as raised:
        build_order([system("A", "B"), system("B", "A")])

    assert "circle" in str(raised.value)


def test_the_worked_example_comes_out_in_dependency_order():
    """The exact reasoning the doctrine asks for: definitions, then somewhere
    to put a reward, then what can be caught, then the tool, then the mechanic,
    then the economy -- with quests last because nothing needs them."""
    order = build_order([
        system("InventoryService", "ItemDefinition",
               priority=PriorityClass.CORE_INFRASTRUCTURE, blocker=True, slice_=True, flow=4),
        system("ShopService", "InventoryService", "CurrencyService",
               priority=PriorityClass.PROGRESSION, blocker=True, slice_=True, flow=6),
        system("ItemDefinition", priority=PriorityClass.FOUNDATION,
               blocker=True, slice_=True, flow=0),
        system("QuestService", "InventoryService", priority=PriorityClass.SECONDARY),
        system("FishingService", "FishCatalog", "RodService", "InventoryService",
               priority=PriorityClass.PRIMARY_GAMEPLAY, blocker=True, slice_=True, flow=5),
        system("FishCatalog", "ItemDefinition", priority=PriorityClass.FOUNDATION,
               blocker=True, slice_=True, flow=2),
        system("RodService", "ItemDefinition", priority=PriorityClass.PRIMARY_GAMEPLAY,
               blocker=True, slice_=True, flow=4),
        system("CurrencyService", priority=PriorityClass.CORE_INFRASTRUCTURE,
               blocker=True, slice_=True, flow=3),
    ])

    assert order[0] == "ItemDefinition", "definitions first: everything references them"
    assert order.index("InventoryService") < order.index("FishingService"), \
        "a caught fish needs somewhere to go before it can be caught"
    assert order.index("RodService") < order.index("FishingService")
    assert order.index("FishingService") < order.index("ShopService")
    assert order[-1] == "QuestService", "nothing depends on quests, and nothing should wait"


# ---- validation refuses a plan that cannot work ----------------------------

def journey(**overrides) -> PlayerJourney:
    fields = {
        "entry_state": "a wooden dock",
        "first_action": "cast a line",
        "first_reward": "a fish",
        "reward_destination": "the player's inventory",
        "core_loop": ["cast", "catch", "sell", "upgrade"],
    }
    return PlayerJourney(**{**fields, **overrides})


def path(*names: str) -> GameplayPath:
    return GameplayPath(nodes=[
        PathNode(id=name.lower(), label=name, systems=[f"{name}Service"]) for name in names])


def test_a_reward_with_nowhere_to_go_fails_before_any_code():
    """The check that matters most. It is invisible in code review, survives
    every unit test, and shows up when a person plays the game and their fish
    vanishes."""
    result = validate_plan(
        systems=[system("FishingService")], journey=journey(reward_destination=""),
        path=path("Fishing"), excluded_features=[])

    assert not result.ok
    assert any("disappears" in problem for problem in result.problems)


def test_a_reward_whose_destination_no_system_provides_fails():
    result = validate_plan(
        systems=[system("FishingService"), system("CastService")],
        journey=journey(reward_destination="the player's satchel"),
        path=path("Fishing"), excluded_features=[])

    assert not result.ok
    assert any("no system in the plan holds anything" in p for p in result.problems)


def test_a_reward_kept_somewhere_the_genre_has_no_noun_for_passes():
    """The storage nouns were written while thinking about one kind of game.

    A tower climb keeps the player's progress in a checkpoint, and refusing
    that plan is the check being wrong about the game rather than the plan
    being wrong about the player.
    """
    result = validate_plan(
        systems=[system("SessionLifecycleService"), system("CheckpointService"),
                 system("LocomotionService")],
        journey=journey(first_reward="reaching Floor 1",
                        reward_destination="the player's active session state, holding "
                                           "the checkpoint they last touched"),
        path=path("Locomotion"), excluded_features=[])

    assert result.ok, result.problems


def test_a_system_that_says_it_stores_the_reward_counts_as_the_holder():
    # The name is not the only place a plan can say what a system does.
    result = validate_plan(
        systems=[system("FishingService"),
                 system("CreelService", purpose="keeps every fish the player lands")],
        journey=journey(reward_destination="the player's creel"),
        path=path("Fishing"), excluded_features=[])

    assert result.ok, result.problems


def test_a_reward_destination_a_system_does_provide_passes():
    result = validate_plan(
        systems=[system("FishingService"), system("InventoryService")],
        journey=journey(), path=path("Fishing"), excluded_features=[])

    assert result.ok, result.problems


def test_a_player_action_with_no_system_behind_it_fails():
    result = validate_plan(
        systems=[system("InventoryService")], journey=journey(),
        path=GameplayPath(nodes=[PathNode(id="cast", label="Cast a line")]),
        excluded_features=[])

    assert not result.ok
    assert any("no system implements it" in p for p in result.problems)


def test_an_action_naming_a_system_that_is_not_in_the_plan_fails():
    result = validate_plan(
        systems=[system("InventoryService")], journey=journey(),
        path=path("Fishing"), excluded_features=[])

    assert not result.ok
    assert any("FishingService, which is not in the plan" in p for p in result.problems)


def test_a_refused_feature_cannot_come_back_as_a_system():
    """A rejected feature returning under a system's name is the failure the
    whole selection step exists to prevent."""
    result = validate_plan(
        systems=[system("InventoryService"), system("PetService")],
        journey=journey(), path=path("Inventory"),
        excluded_features=["Pet System"])

    assert not result.ok
    assert any("REFUSED" in p for p in result.problems)


def test_session_only_means_no_persistence_system():
    result = validate_plan(
        systems=[system("InventoryService"), system("DataStoreService")],
        journey=journey(), path=path("Inventory"),
        excluded_features=[], session_only=True)

    assert not result.ok
    assert any("session only" in p for p in result.problems)


def test_a_loop_too_short_to_repeat_fails():
    result = validate_plan(
        systems=[system("InventoryService")], journey=journey(core_loop=["cast"]),
        path=path("Inventory"), excluded_features=[])

    assert not result.ok
    assert any("not a loop" in p for p in result.problems)


def test_a_plan_with_no_systems_fails_immediately():
    result = validate_plan(systems=[], journey=journey(), path=path(),
                           excluded_features=[])

    assert not result.ok
    assert any("no systems" in p for p in result.problems)


def test_a_slice_depending_on_something_outside_it_is_a_warning_not_a_refusal():
    """It may be deliberate, so it is said rather than enforced."""
    result = validate_plan(
        systems=[system("InventoryService", "ItemDefinition", slice_=True),
                 system("ItemDefinition")],
        journey=journey(), path=path("Inventory"), excluded_features=[])

    assert result.ok
    assert any("cannot be finished without it" in w for w in result.warnings)
