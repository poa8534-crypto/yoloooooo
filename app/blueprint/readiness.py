"""Whether a blueprint can be built yet, and how large it has become.

Both numbers here are COUNTED, never asked of a model. "82% ready" has to mean
something a person can check -- so it is the fraction of hard requirements
satisfied, and every missing one is named. A percentage a model made up would
read identically and mean nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import Blueprint, Complexity, Priority, Scope

# What a scope can carry before the first playable stops being first or
# playable. Counted in systems, because that is what the Engineer builds one of
# at a time and what every build so far has been measured in.
SCOPE_BUDGET: dict[Scope, int] = {
    Scope.QUICK_PROTOTYPE: 5,
    Scope.VERTICAL_SLICE: 10,
    Scope.EXPANDED_PROTOTYPE: 16,
}
COMPLEXITY_WEIGHT: dict[Complexity, float] = {
    Complexity.LOW: 0.6,
    Complexity.MEDIUM: 1.0,
    Complexity.HIGH: 1.8,
}


@dataclass(frozen=True)
class Requirement:
    key: str
    satisfied: bool
    prompt: str


@dataclass
class Readiness:
    requirements: list[Requirement] = field(default_factory=list)

    @property
    def missing(self) -> list[Requirement]:
        return [requirement for requirement in self.requirements if not requirement.satisfied]

    @property
    def percent(self) -> int:
        if not self.requirements:
            return 0
        met = len(self.requirements) - len(self.missing)
        return round(100 * met / len(self.requirements))

    @property
    def ready(self) -> bool:
        return bool(self.requirements) and not self.missing

    def as_dict(self) -> dict:
        return {"percent": self.percent, "ready": self.ready,
                "missing": [{"key": r.key, "prompt": r.prompt} for r in self.missing],
                "satisfied": [r.key for r in self.requirements if r.satisfied]}


def assess(blueprint: Blueprint) -> Readiness:
    """The hard requirements, each one a thing the Engineer cannot proceed without."""
    config = blueprint.config
    active = blueprint.active_systems()
    requirements = [
        Requirement("intent", bool(blueprint.user_intent.strip()),
                    "Say what you want this game to be, in your own words"),
        Requirement("systems", len(active) >= 1,
                    "Keep at least one system; there is nothing to build otherwise"),
        Requirement("features_decided", not blueprint.undecided_features(),
                    "Decide on every suggested feature: a feature nobody chose is not a plan"),
        Requirement("player_count", config.max_players >= config.min_players,
                    "Maximum players cannot be below minimum players"),
        Requirement("platforms", len(config.platforms) >= 1,
                    "Choose at least one platform"),
        # Not a taste question: it decides whether the Engineer writes DataStore
        # code at all, and adding it afterwards changes every system that holds
        # player state.
        Requirement("persistence", config.persistence is not None,
                    "Choose whether progress is saved or lasts one session"),
        Requirement("acceptance", all(system.acceptance_criteria for system in active),
                    "Every system needs acceptance criteria; they are what the build is judged by"),
    ]
    return Readiness(requirements)


@dataclass
class ScopeVerdict:
    systems: int
    budget: int
    weighted: float
    verdict: str  # "lean" | "balanced" | "too_large"
    suggested_cut: list[str]

    def as_dict(self) -> dict:
        return {"systems": self.systems, "budget": self.budget,
                "weighted": round(self.weighted, 1), "verdict": self.verdict,
                "suggested_cut": self.suggested_cut}


def scope_of(blueprint: Blueprint) -> ScopeVerdict:
    """How large the blueprint has become against what its scope can carry.

    The suggested cut is the least essential work first: systems that came from
    a feature rather than the core idea, lowest MVP priority first. It is a
    proposal, never applied automatically -- silently dropping a system the
    user chose is the same sin as silently adding one.
    """
    active = blueprint.active_systems()
    budget = SCOPE_BUDGET[blueprint.config.scope]
    weighted = sum(COMPLEXITY_WEIGHT[system.complexity] for system in active)

    if weighted <= budget * 0.6:
        verdict = "lean"
    elif weighted <= budget:
        verdict = "balanced"
    else:
        verdict = "too_large"

    cut: list[str] = []
    if verdict == "too_large":
        priority = {s.id: s.mvp_priority for s in blueprint.suggestions}
        order = {Priority.LOW: 0, Priority.MEDIUM: 1, Priority.HIGH: 2, Priority.ESSENTIAL: 3}
        optional = [system for system in active if not system.essential or system.from_feature]
        optional.sort(key=lambda system: (
            order.get(priority.get(system.from_feature or "", Priority.MEDIUM), 1),
            -COMPLEXITY_WEIGHT[system.complexity]))
        remaining = weighted
        for system in optional:
            if remaining <= budget:
                break
            cut.append(system.name)
            remaining -= COMPLEXITY_WEIGHT[system.complexity]

    return ScopeVerdict(systems=len(active), budget=budget, weighted=weighted,
                        verdict=verdict, suggested_cut=cut)
