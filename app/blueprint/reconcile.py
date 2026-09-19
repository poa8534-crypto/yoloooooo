"""Automatic reconciliation of missing gameplay-path and dependency systems.

The doctrine (app/engineer/instruction.md) requires every gameplay path node
and every system dependency to be backed by an active system in the build.
When an LLM architect generates a player journey and gameplay path in pass 1,
and then generates systems in pass 2, it can omit systems referenced by
the path nodes or by depends_on.

This module detects those omissions and synthesizes legal, testable GameSystem
specifications so that the plan can be compiled, reviewed, and built without
hitting NotReady refusals.
"""

from __future__ import annotations

import secrets
from typing import NamedTuple

from .schemas import Blueprint, Complexity, GameSystem, SystemLayer


class MissingSystem(NamedTuple):
    name: str
    needed_for: str
    is_early_flow: bool
    flow_index: int


def find_missing_systems(blueprint: Blueprint) -> list[MissingSystem]:
    """Find systems referenced by the gameplay path or depends_on that are not in the plan.

    "In the plan" means `active_systems()`, which is what the specification is
    compiled from -- not every system on the blueprint. A system that came from
    a feature the user rejected is still in `blueprint.systems` and is dropped
    from the build, so reading the whole list here answered "nothing is
    missing" about a system the compile then refused for being missing, with
    nothing on screen to explain it or fix it.
    """
    active = blueprint.active_systems()
    known = {system.name for system in active}
    missing: dict[str, MissingSystem] = {}

    if blueprint.gameplay_path and blueprint.gameplay_path.nodes:
        for idx, node in enumerate(blueprint.gameplay_path.nodes):
            for name in node.systems:
                if name not in known and name not in missing:
                    missing[name] = MissingSystem(
                        name=name,
                        needed_for=node.label,
                        is_early_flow=idx < 3,
                        flow_index=idx,
                    )

    for system in active:
        for need in system.depends_on:
            if need not in known and need not in missing:
                missing[need] = MissingSystem(
                    name=need,
                    needed_for=f"Dependency of {system.name}",
                    is_early_flow=False,
                    flow_index=999,
                )

    return list(missing.values())


def infer_layer(name: str) -> SystemLayer:
    """Infer the Roblox architecture layer from PascalCase naming conventions."""
    name_lower = name.lower()
    client_indicators = ("controller", "gui", "hud", "ui", "screen", "view", "camera", "input", "client", "vfx")
    shared_indicators = ("util", "utils", "config", "types", "constants", "shared", "math", "resolver")

    if any(ind in name_lower for ind in client_indicators):
        return SystemLayer.CLIENT
    if any(ind in name_lower for ind in shared_indicators):
        return SystemLayer.SHARED
    return SystemLayer.SERVER


def synthesize_system(missing: MissingSystem) -> GameSystem:
    """Create a fully-specified, testable GameSystem meeting architect doctrine."""
    name = missing.name
    layer = infer_layer(name)
    purpose = f"Implements {missing.needed_for} and coordinates runtime state for the player experience."

    if layer == SystemLayer.CLIENT:
        criteria = [
            f"Mounts and updates client UI and presentation elements for {name} based on state changes.",
            f"Captures player interactions for {name} and dispatches validated network signals to the server.",
        ]
    elif layer == SystemLayer.SHARED:
        criteria = [
            f"Exports pure data structures, configuration definitions, and validation logic for {name}.",
            f"Evaluates deterministic algorithms for {name} identically across client and server environments.",
        ]
    else:
        criteria = [
            f"Initializes authoritative state for {name} on player join and releases resources on disconnect.",
            f"Validates incoming client requests for {name} and updates game state without uncaught exceptions.",
        ]

    priority = "P1" if missing.is_early_flow else ("P2" if missing.flow_index < 6 else "P3")

    return GameSystem(
        id=f"sys_{secrets.token_hex(6)}",
        name=name,
        layer=layer,
        purpose=purpose,
        acceptance_criteria=criteria,
        depends_on=[],
        complexity=Complexity.MEDIUM,
        essential=True,
        priority_class=priority,
        core_loop_blocker=missing.is_early_flow,
        required_for_vertical_slice=True,
        player_flow_index=missing.flow_index,
        builds_world=False,
    )


def reconcile_missing_systems(blueprint: Blueprint) -> tuple[Blueprint, list[GameSystem]]:
    """Synthesize and append all missing systems to the blueprint, returning updated blueprint and added systems."""
    missing = find_missing_systems(blueprint)
    if not missing:
        return blueprint, []

    new_systems = [synthesize_system(m) for m in missing]
    updated_systems = list(blueprint.systems) + new_systems
    updated_blueprint = blueprint.model_copy(update={"systems": updated_systems})
    return updated_blueprint, new_systems
