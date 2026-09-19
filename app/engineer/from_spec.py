"""GameBuildSpecification -> EngineeringTasks.

The missing link. The Engineer consumes an `EngineeringTask` -- one system, a
goal, acceptance criteria -- and until now a person wrote those by hand. This
turns an approved specification into that list, in an order that builds
dependencies first.

Nothing is invented here. Every task's criteria come from the spec's criteria
for that system, and the spec's technical constraints are carried into every
task's notes, because a constraint the Engineer is not shown is a constraint it
will break. Where the specification says NO DATASTORE, every task says so.
"""

from __future__ import annotations

from pathlib import Path

from ..blueprint.schemas import GameBuildSpecification, SpecSystem, SystemLayer
from .interfaces import interface_note
from .schemas import EngineeringTask

LAYER_NOTE: dict[SystemLayer, str] = {
    SystemLayer.SERVER: "This is a server system: it holds state and is authoritative. "
                        "Write it under src/server.",
    SystemLayer.CLIENT: "This is a client system: it draws and reads input, and decides nothing. "
                        "Write it under src/client, and require only its own siblings.",
    SystemLayer.SHARED: "This is a shared module: pure logic, reachable by both sides, touching "
                        "no Roblox service. Write it under src/shared.",
}


class SpecUnusable(ValueError):
    pass


def order_systems(spec: GameBuildSpecification) -> list[SpecSystem]:
    """The systems in build order.

    `build_order` was computed when the spec was compiled, so this follows it
    rather than sorting again -- two orderings that could disagree is one
    ordering too many. A spec whose order does not name every system is refused
    rather than half-built.
    """
    index = {system.name: system for system in spec.systems}
    ordered = [index[name] for name in spec.build_order if name in index]
    missing = sorted(set(index) - set(spec.build_order))
    if missing:
        raise SpecUnusable(
            "these systems are in the specification but not in its build order, so there is no "
            "order to build them in: " + ", ".join(missing))
    return ordered


def _place_in_the_plan(spec: GameBuildSpecification, system: SpecSystem) -> str:
    """Where this system sits in the build, and what is waiting on it.

    The Engineer is given one system at a time, which is what makes a build
    coherent -- and also what makes it easy to write a system as though nothing
    else existed. This is the context it would otherwise lack: the class of
    work, whether the loop is blocked on it, what the player has already done
    by the time they meet it, and which systems cannot be finished until it is.
    """
    position = spec.build_order.index(system.name) + 1 if system.name in spec.build_order else 0
    dependents = sorted(other.name for other in spec.systems
                        if system.name in other.depends_on)

    parts = [f"This is system {position} of {len(spec.build_order)} in the build order."]
    if system.required_for_vertical_slice:
        parts.append("It is part of the VERTICAL SLICE: the smallest playable path needs it, "
                     "so build it completely rather than leaving it half-wired.")
    if system.core_loop_blocker:
        parts.append("The core loop CANNOT complete without it.")
    parts.append(f"Priority class {system.priority_class}.")
    if dependents:
        parts.append("These systems are waiting on it and will call into it: "
                     + ", ".join(dependents) + ". Export what they need.")
    else:
        parts.append("Nothing else depends on it, so keep its surface small.")
    return " ".join(parts)


def task_for(spec: GameBuildSpecification, system: SpecSystem,
             repo: Path | None = None) -> EngineeringTask:
    """One system, as the Engineer's loop already understands it.

    `repo` is the project as it stands. Given it, the task carries the real
    signatures of the dependencies already in it -- see interfaces.py for the
    two systems that were accepted by six checks and could not be landed,
    because each had called a function on a sibling that nobody wrote.
    """
    notes = [
        LAYER_NOTE[system.layer],
        f"Write it at {system.path}. Return only the files you create; "
        "do not return a file you are not changing.",
    ]
    notes.extend(spec.technical_constraints)
    if system.depends_on:
        notes.append("These systems are built before this one. Require them rather than "
                     "reimplementing them: " + ", ".join(system.depends_on))
        if repo is not None:
            known = interface_note(
                repo, system.depends_on,
                {other.name: other.path for other in spec.systems})
            if known:
                notes.append(known)
    # Why this system, why now. The doctrine asks the Engineer to build for a
    # player experience rather than a file list, and a task that arrives with
    # no place in that experience gets built as a file list.
    place = _place_in_the_plan(spec, system)
    if place:
        notes.append(place)

    if spec.excluded_features:
        # The contract, restated per task. The Engineer never sees the blueprint,
        # and a feature it was not told was refused is one it may add helpfully.
        notes.append("These were considered and REFUSED. Do not build them, under any name: "
                     + ", ".join(spec.excluded_features))
    return EngineeringTask(
        audit_id=spec.idea_id,
        system=system.name,
        goal=f"{system.purpose}\n\nPart of {spec.title}. {spec.summary}".strip(),
        acceptance_criteria=list(system.acceptance_criteria),
        notes=notes[:20],
    )


def tasks_from(spec: GameBuildSpecification,
               already_built: set[str] | None = None,
               repo: Path | None = None) -> list[EngineeringTask]:
    """Every system still to build, dependencies first.

    `already_built` is what the project already has. A system that exists is
    skipped rather than rebuilt, which is what makes a second build of a
    revised specification a patch rather than a regeneration.
    """
    existing = {name.lower() for name in (already_built or set())}
    tasks = [task_for(spec, system, repo) for system in order_systems(spec)
             if system.name.lower() not in existing]
    if not tasks:
        raise SpecUnusable("every system in this specification already exists in the project")
    return tasks


def plan_summary(spec: GameBuildSpecification,
                 already_built: set[str] | None = None) -> dict:
    """What the build will do, before it does it."""
    existing = {name.lower() for name in (already_built or set())}
    ordered = order_systems(spec)
    building = [system.name for system in ordered if system.name.lower() not in existing]
    skipping = [system.name for system in ordered if system.name.lower() in existing]
    return {
        "spec_id": spec.spec_id,
        "revision": spec.revision,
        "content_hash": spec.content_hash,
        "building": building,
        "skipping_because_they_exist": skipping,
        "constraints": spec.technical_constraints,
        "excluded_features": spec.excluded_features,
    }
