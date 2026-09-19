"""Build order, and the validation that refuses a plan which cannot work.

Two things here, and both exist because a model's opinion is not good enough.

BUILD ORDER IS COMPUTED, NOT ASKED FOR. The architect proposes systems and
their dependencies; the order they are built in is derived from that graph
here. A model asked for an order gives a plausible one -- and a plausible order
put a system that reads the inventory before the inventory existed. Topology
decides, and priority class only breaks ties between systems that are equally
ready. That way an ordering mistake is a dependency someone declared wrongly,
which is visible, rather than a preference nobody can see.

VALIDATION RUNS BEFORE ANY CODE. The expensive failure is not a bad plan; it is
a bad plan discovered eight systems later. The check that matters most is the
one about rewards: if the player is given something and no system stores it,
the reward disappears and the player learns the game is broken. That is a plan
failure, findable in milliseconds, and it fails the build before the Engineer
writes a line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .doctrine import GameplayPath, PlayerJourney, PriorityClass


class PlanInvalid(ValueError):
    """A plan that could not produce the experience it describes."""


@dataclass(frozen=True)
class PlannedSystem:
    """A system as the planner sees it: what it needs, and how urgent it is."""

    name: str
    depends_on: tuple[str, ...] = ()
    priority: PriorityClass = PriorityClass.SECONDARY
    core_loop_blocker: bool = False
    required_for_vertical_slice: bool = False
    player_flow_index: int = 999
    risk: str = "medium"

    @property
    def sort_key(self) -> tuple:
        """Among systems whose dependencies are all satisfied, which first.

        Vertical-slice work outranks everything, then core-loop blockers, then
        the priority class, then where the player meets it. The name is last so
        the order is stable: two runs of the same specification must produce
        the same build, or nothing downstream is reproducible.
        """
        return (
            0 if self.required_for_vertical_slice else 1,
            0 if self.core_loop_blocker else 1,
            self.priority.rank,
            self.player_flow_index,
            self.name,
        )


def build_order(systems: list[PlannedSystem]) -> list[str]:
    """Dependencies first, then the most urgent of whatever is ready.

    Kahn's algorithm with the doctrine's priorities as the tie-break. Topology
    is absolute: a system never appears before something it depends on, whatever
    its priority class says. Priority only chooses between systems that could
    equally be built next.

    A dependency on a system that is not in the plan is ignored rather than
    treated as unsatisfiable -- it is already reported by validation, with a
    better message than a build order that silently omits half the systems.
    """
    known = {system.name: system for system in systems}
    remaining = {
        system.name: {need for need in system.depends_on if need in known}
        for system in systems
    }

    order: list[str] = []
    while remaining:
        ready = [name for name, needs in remaining.items() if not needs]
        if not ready:
            circle = " -> ".join(sorted(remaining))
            raise PlanInvalid(
                "these systems depend on each other in a circle, so there is no order "
                f"to build them in: {circle}")
        chosen = min(ready, key=lambda name: known[name].sort_key)
        order.append(chosen)
        del remaining[chosen]
        for needs in remaining.values():
            needs.discard(chosen)
    return order


@dataclass
class Validation:
    """What was checked, and everything that was wrong with it."""

    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict:
        return {"ok": self.ok, "problems": list(self.problems),
                "warnings": list(self.warnings)}


# Words a system uses to say it keeps something for the player. Deliberately
# broad: the question is whether ANY system can hold a reward, and a plan that
# calls it a Satchel rather than an Inventory is still a plan that works.
_WORDS = re.compile(r"[a-z]+")
# Words that name no feature on their own, so matching on them would flag
# every system against every rejected feature.
_GENERIC_WORDS = frozenset({
    "system", "service", "manager", "handler", "controller", "module",
    "game", "player", "basic", "simple", "core", "main", "the", "and",
})

_STORAGE_WORDS = (
    "inventory", "wallet", "currency", "bank", "storage", "collection",
    "satchel", "backpack", "pack", "purse", "vault", "ledger", "holdall",
    "equipment", "loadout", "profile", "state", "stash", "hold", "treasury",
    "score", "progress", "tally", "catalogue", "catalog", "log", "record",
)


def _significant(text: str) -> set[str]:
    """The words in a name that actually identify a feature.

    Splits CamelCase as well as spaces, so PetService and "Pet System" both
    reduce to {"pet"} once the words that name nothing on their own are
    dropped.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return {word for word in _WORDS.findall(spaced.lower())
            if word not in _GENERIC_WORDS and len(word) >= 3}


def validate_plan(*, systems: list[PlannedSystem], journey: PlayerJourney,
                  path: GameplayPath, excluded_features: list[str],
                  session_only: bool = False) -> Validation:
    """Everything worth knowing before the first system is written.

    Ordered by how expensive the mistake is to find later. A reward with
    nowhere to go is first because it is invisible in code review, survives
    every unit test, and only shows up when a person plays the game and their
    fish vanishes.
    """
    result = Validation()
    known = {system.name for system in systems}

    if not systems:
        result.problems.append("the plan has no systems, so it cannot build anything")
        return result

    # --- the reward has to go somewhere -----------------------------------
    if journey.first_reward and not journey.reward_destination:
        result.problems.append(
            f"the player is given {journey.first_reward!r} and the plan does not say where "
            "it goes. A reward with no destination disappears, and the player learns the "
            "game is broken.")
    elif journey.reward_destination:
        # Only the SYSTEMS are asked. The destination naming a satchel proves
        # the plan has a word for where the fish goes, not that anything in it
        # can hold one -- and that difference is the entire check.
        holder = any(word in name.lower() for name in known for word in _STORAGE_WORDS)
        if not holder:
            result.problems.append(
                f"the first reward goes to {journey.reward_destination!r} and no system in "
                "the plan holds anything for the player. Build the thing that stores it "
                "before the thing that gives it.")

    # --- the loop has to close --------------------------------------------
    if not journey.core_loop:
        result.problems.append(
            "the plan has no core loop, so there is nothing for the player to repeat")
    elif len(journey.core_loop) < 3:
        result.problems.append(
            "a core loop of fewer than three steps is not a loop: it needs at least an "
            f"action, a result and something that leads back round. Got: {journey.core_loop}")

    for required, label in ((journey.first_action, "first action"),
                            (journey.entry_state, "entry state")):
        if not required:
            result.problems.append(f"the plan does not say what the player's {label} is")

    # --- every step the player takes needs something behind it ------------
    if not path.nodes:
        result.problems.append("the plan has no gameplay path, so no player action is supported")
    for node in path.nodes:
        if not node.systems:
            result.problems.append(
                f"the player does {node.label!r} and no system implements it")
        for name in node.systems:
            if name not in known:
                result.problems.append(
                    f"{node.label!r} needs {name}, which is not in the plan")

    # --- dependencies have to exist, and not circle -----------------------
    for system in systems:
        for need in system.depends_on:
            if need not in known:
                result.problems.append(
                    f"{system.name} depends on {need}, which is not in the plan")
    try:
        build_order(systems)
    except PlanInvalid as exc:
        result.problems.append(str(exc))

    # --- nothing the person refused ---------------------------------------
    for feature in excluded_features:
        # Matched on the feature's own words rather than its whole name.
        # "Pet System" must catch PetService: squashing it to "petsystem" and
        # looking for that found nothing, which is the check passing while the
        # refused feature sits in the plan under a tidier name.
        words = _significant(feature)
        for name in known:
            # Whole words on both sides, not substrings. "Pet System" has to
            # catch PetService, and "Car Racing" must NOT catch Carrier --
            # which a substring test does, and then refuses a legitimate plan.
            if words and words & _significant(name):
                result.problems.append(
                    f"{name} looks like {feature!r}, which was considered and REFUSED. "
                    "A rejected feature must not come back under a system's name.")

    if session_only:
        for name in known:
            if "datastore" in name.lower() or "persistence" in name.lower():
                result.problems.append(
                    f"{name} persists data, and this build is session only. Roblox games "
                    "usually save; this one was told not to.")

    # --- the slice has to be reachable ------------------------------------
    slice_systems = [s for s in systems if s.required_for_vertical_slice]
    if not slice_systems:
        result.warnings.append(
            "no system is marked as part of the vertical slice, so nothing says which "
            "work makes the game playable first")
    else:
        for system in slice_systems:
            for need in system.depends_on:
                if need in known and not any(
                        other.name == need and other.required_for_vertical_slice
                        for other in systems):
                    result.warnings.append(
                        f"{system.name} is in the vertical slice and depends on {need}, "
                        "which is not -- the slice cannot be finished without it")

    return result
