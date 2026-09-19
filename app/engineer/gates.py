"""Dependency gates, playability gates, and what a steering change invalidates.

Three questions a build has to answer that nothing was answering.

WHAT CAN BE WORKED ON. A system whose dependencies are not built is BLOCKED,
and saying so is different from saying it is merely waiting its turn. The
distinction matters when something fails: a refusal partway through the order
leaves everything downstream blocked on it, and a person looking at the build
should see that rather than a queue that appears to be progressing.

HOW FAR THE GAME ACTUALLY IS. Eleven systems built is not a measure of anything
a player would recognise. The playability gates are: can they join, can they
act, does the mechanic work, do they get the reward, does the loop close, can
they tell what happened. Those are derived from which systems are done, so the
answer cannot be more optimistic than the build.

WHAT A CHANGE BREAKS. When the person steers mid-build, rebuilding everything
throws away work that was fine, and rebuilding nothing ships a game that
contradicts what they just asked for. Only the systems downstream of the change
are invalidated, and that set is computed from the dependency graph rather than
guessed.
"""

from __future__ import annotations

import enum
from collections.abc import Collection
from dataclasses import dataclass

from .doctrine import GameplayPath, GateName


class TaskState(str, enum.Enum):
    """Where one system stands, from the build's point of view."""

    READY = "ready"        # its dependencies are done; it could be built now
    BLOCKED = "blocked"    # something it needs is not done
    BUILDING = "building"
    TESTING = "testing"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class SystemState:
    name: str
    state: TaskState
    waiting_for: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"name": self.name, "state": self.state.value,
                "waiting_for": list(self.waiting_for)}


def gate_states(*, order: list[str], depends: dict[str, list[str]],
                outcomes: dict[str, str], in_flight: Collection[str] = ()) -> list[SystemState]:
    """Every system's state, derived from what has actually happened.

    `outcomes` is the build record's own view: built, refused, error. Nothing
    here decides an outcome, it only works out what those outcomes imply for
    everything else -- which is why a system can be BLOCKED by a refusal five
    places earlier without anyone writing that down. `in_flight` is every
    system the Engineer has been asked for and not yet answered: several, when
    systems that do not depend on each other are written at once.
    """
    in_flight = set(in_flight)
    done = {name for name, status in outcomes.items() if status == "built"}
    broken = {name for name, status in outcomes.items() if status in ("refused", "error")}

    states: list[SystemState] = []
    for name in order:
        needs = [need for need in depends.get(name, []) if need in set(order)]
        unmet = tuple(need for need in needs if need not in done)

        if name in broken:
            state = TaskState.FAILED
        elif name in done:
            state = TaskState.DONE
        elif name in in_flight:
            state = TaskState.BUILDING
        elif unmet:
            state = TaskState.BLOCKED
        else:
            state = TaskState.READY
        states.append(SystemState(name=name, state=state, waiting_for=unmet))
    return states


# Which gate each kind of work opens. A gate is only as true as the systems
# behind it, so this maps the path's own declarations onto the ladder.
GATE_ORDER: tuple[GateName, ...] = (
    GateName.SPAWNABLE, GateName.INTERACTABLE, GateName.CORE_ACTION,
    GateName.REWARDABLE, GateName.LOOPABLE, GateName.UNDERSTANDABLE,
    GateName.PERSISTENT, GateName.MVP_PLAYABLE,
)


@dataclass(frozen=True)
class Gate:
    name: GateName
    passed: bool
    needs: tuple[str, ...]
    detail: str

    def as_dict(self) -> dict:
        return {"gate": self.name.value, "passed": self.passed,
                "needs": list(self.needs), "detail": self.detail}


def playability(path: GameplayPath, outcomes: dict[str, str]) -> list[Gate]:
    """How far a player could actually get, from what is built.

    Each gate is claimed by the path's nodes, and passes only when every system
    those nodes need is built. A gate with nothing claiming it is reported as
    not passed with "nothing in the plan reaches it" rather than quietly
    passing -- an empty requirement is not an achievement.
    """
    done = {name for name, status in outcomes.items() if status == "built"}

    gates: list[Gate] = []
    for gate_name in GATE_ORDER:
        claimed = [node for node in path.nodes if node.gate is gate_name]
        if not claimed:
            gates.append(Gate(gate_name, False, (),
                              "nothing in the plan reaches this gate"))
            continue
        needed = sorted({system for node in claimed for system in node.systems})
        missing = tuple(system for system in needed if system not in done)
        if missing:
            gates.append(Gate(gate_name, False, missing,
                              "waiting on " + ", ".join(missing)))
        else:
            labels = ", ".join(node.label for node in claimed)
            gates.append(Gate(gate_name, True, (), f"reached: {labels}"))

    # MVP is the whole slice, so it cannot be ahead of the gates below it.
    if gates and gates[-1].name is GateName.MVP_PLAYABLE:
        earlier = [gate for gate in gates[:-1] if gate.name is not GateName.PERSISTENT]
        if gates[-1].passed and not all(gate.passed for gate in earlier):
            blocked = tuple(gate.name.value for gate in earlier if not gate.passed)
            gates[-1] = Gate(GateName.MVP_PLAYABLE, False, blocked,
                             "an earlier gate has not been reached: " + ", ".join(blocked))
    return gates


def invalidated_by(changed: set[str], depends: dict[str, list[str]]) -> set[str]:
    """The systems a change reaches: the changed ones and everything downstream.

    Transitive, because a system that calls a system that calls the one that
    changed is just as wrong. Nothing upstream is touched -- rebuilding
    everything throws away work that was fine, which is the failure this is
    here to avoid.

    "Rare fish only appear at night" reaches fish selection and whatever reads
    it. It does not reach the wallet.
    """
    dependents: dict[str, set[str]] = {}
    for name, needs in depends.items():
        for need in needs:
            dependents.setdefault(need, set()).add(name)

    reached = set(changed)
    frontier = list(changed)
    while frontier:
        name = frontier.pop()
        for dependent in dependents.get(name, ()):
            if dependent not in reached:
                reached.add(dependent)
                frontier.append(dependent)
    return reached
