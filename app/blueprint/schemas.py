"""The typed stages between an idea and a build.

Three shapes, in order:

  `Blueprint`        what the user and the architect are still negotiating:
                     suggestions with selections, configuration, systems.
                     Mutable, revisioned.

  `GameBuildSpecification`
                     what the Engineer is handed. Compiled from a blueprint at
                     a moment in time, carries its own revision, and is not
                     edited afterwards -- a change means a new revision, so a
                     build can always be traced to exactly what was asked for.

  `BuildStatus`      where a build is, as one value rather than a handful of
                     booleans that can disagree with each other.

Every enum here is a closed set on purpose. A status the frontend has never
heard of is worse than a status it can render, and pydantic refuses it at the
boundary rather than letting it reach the UI.
"""

from __future__ import annotations

import enum
import hashlib
import json
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from ..engineer.doctrine import GameplayPath, PlayerJourney

SPEC_VERSION = 1


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- vocabulary -----------------------------------------------------------

class Complexity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Priority(str, enum.Enum):
    """How much a feature belongs in the first playable, not how good it is."""

    ESSENTIAL = "essential"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Scope(str, enum.Enum):
    QUICK_PROTOTYPE = "quick_prototype"
    VERTICAL_SLICE = "vertical_slice"
    EXPANDED_PROTOTYPE = "expanded_prototype"


class Persistence(str, enum.Enum):
    SESSION_ONLY = "session_only"
    SAVED_PROGRESSION = "saved_progression"


class WorldShape(str, enum.Enum):
    SINGLE_ARENA = "single_arena"
    SMALL_WORLD = "small_world"
    MULTIPLE_ZONES = "multiple_zones"
    PROCEDURAL = "procedural"


class Platform(str, enum.Enum):
    DESKTOP = "desktop"
    MOBILE = "mobile"
    CONSOLE = "console"


class BuildTarget(str, enum.Enum):
    """Where the build lands. Never guessed: overwriting a place someone has
    been working in is not recoverable by apologising."""

    CLEAN_PROTOTYPE = "clean_prototype"
    CURRENT_PLACE = "current_place"


class SystemLayer(str, enum.Enum):
    SERVER = "server"
    CLIENT = "client"
    SHARED = "shared"


class BuildStatus(str, enum.Enum):
    """The state machine. Transitions are in `transitions.py`."""

    DRAFT = "draft"
    BLUEPRINTING = "blueprinting"
    READY_TO_BUILD = "ready_to_build"
    QUEUED = "queued"
    PLANNING = "planning"
    GENERATING = "generating"
    VALIDATING = "validating"
    WAITING_FOR_STUDIO = "waiting_for_studio"
    SYNCING = "syncing"
    BUILDING = "building"
    PLAYTESTING = "playtesting"
    REPAIRING = "repairing"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # Stopped without finishing and without failing: the process running it
    # went away, or its task was cancelled. Distinct from FAILED because
    # nothing about the build was found wanting -- it was simply not finished.
    INTERRUPTED = "interrupted"


TERMINAL_STATUSES = frozenset({BuildStatus.SUCCEEDED, BuildStatus.PARTIAL,
                               BuildStatus.FAILED, BuildStatus.CANCELLED,
                               BuildStatus.INTERRUPTED})


# ---- what the architect proposes ------------------------------------------

class FeatureSuggestion(Strict):
    """One proposed addition, with everything needed to judge it.

    `selected` is tri-state on purpose: None means the user has not looked at
    it yet, which is different from having rejected it. A rejected feature goes
    into the specification's `excluded_features` and must not come back.
    """

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=2000)
    reason: str = Field(min_length=10, max_length=2000)
    player_value: str = Field(default="", max_length=1000)
    implementation_complexity: Complexity = Complexity.MEDIUM
    mvp_priority: Priority = Priority.MEDIUM
    retention_impact: Complexity = Complexity.MEDIUM
    risk: str = Field(default="", max_length=1000)
    dependencies: list[str] = Field(default_factory=list, max_length=12)
    required_systems: list[str] = Field(default_factory=list, max_length=12)
    selected: bool | None = None
    origin: str = Field(default="architect", pattern="^(architect|user)$")


class GameSystem(Strict):
    """One system the Engineer will be asked to build.

    `acceptance_criteria` is the specification for that system: it is what the
    behaviour specs are written from, so a criterion no test could fail is a
    hole with a tick next to it.
    """

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(pattern=r"^[A-Z][A-Za-z0-9]{2,48}$")
    layer: SystemLayer
    purpose: str = Field(min_length=10, max_length=2000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    complexity: Complexity = Complexity.MEDIUM
    essential: bool = True
    # What the planner orders by. The architect declares these; the ORDER is
    # computed from them rather than asked for, because a model asked for an
    # order gives a plausible one. See app/engineer/instruction.md.
    priority_class: str = Field(default="P5", pattern=r"^P[0-7]$")
    core_loop_blocker: bool = False
    required_for_vertical_slice: bool = False
    player_flow_index: int = Field(default=999, ge=0, le=999)
    # The one system that makes the place appear. The build runs it in Studio
    # after syncing, so the world is there in edit mode rather than only once
    # somebody presses Play.
    builds_world: bool = False
    from_feature: str | None = Field(default=None, max_length=64)


class BlueprintConfig(Strict):
    """The choices that change what gets engineered.

    Deliberately short. A control that does not change the generated code is a
    field the user has to fill in for nothing.
    """

    scope: Scope = Scope.VERTICAL_SLICE
    min_players: int = Field(default=1, ge=1, le=100)
    max_players: int = Field(default=4, ge=1, le=100)
    platforms: list[Platform] = Field(default_factory=lambda: [Platform.DESKTOP], min_length=1)
    persistence: Persistence = Persistence.SESSION_ONLY
    world: WorldShape = WorldShape.SINGLE_ARENA
    build_target: BuildTarget = BuildTarget.CLEAN_PROTOTYPE
    tone: str = Field(default="", max_length=600)
    # 0-100 each. They bias which systems the architect proposes, nothing else:
    # a slider that quietly rewrites code would be worse than no slider.
    emphasis: dict[str, int] = Field(default_factory=dict)


class Blueprint(Strict):
    """A build being negotiated. Mutable; every change bumps `revision`."""

    id: str = Field(min_length=1, max_length=64)
    project_id: str = Field(min_length=1, max_length=64)
    audit_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    revision: int = Field(default=1, ge=1)
    user_intent: str = Field(default="", max_length=8000)
    summary: str = Field(default="", max_length=4000)
    suggestions: list[FeatureSuggestion] = Field(default_factory=list, max_length=24)
    systems: list[GameSystem] = Field(default_factory=list, max_length=40)
    config: BlueprintConfig = Field(default_factory=BlueprintConfig)
    asset_requirements: list[str] = Field(default_factory=list, max_length=40)
    # What the player experiences, and the actions they take, decided before
    # any system is planned. The systems are derived from these rather than the
    # other way round -- see app/engineer/instruction.md.
    player_journey: PlayerJourney | None = None
    gameplay_path: GameplayPath | None = None
    status: BuildStatus = BuildStatus.DRAFT
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    def selected_features(self) -> list[FeatureSuggestion]:
        return [s for s in self.suggestions if s.selected is True]

    def rejected_features(self) -> list[FeatureSuggestion]:
        return [s for s in self.suggestions if s.selected is False]

    def undecided_features(self) -> list[FeatureSuggestion]:
        return [s for s in self.suggestions if s.selected is None]

    def active_systems(self) -> list[GameSystem]:
        """Systems that survive the user's feature choices.

        A system that exists only because of a rejected feature is dropped:
        building it would be adding a feature nobody approved.
        """
        rejected = {s.id for s in self.rejected_features()}
        return [system for system in self.systems
                if system.from_feature is None or system.from_feature not in rejected]


# ---- what the Engineer is handed ------------------------------------------

class SpecSystem(Strict):
    """A system as the Engineer sees it: no provenance, no maybes."""

    name: str = Field(pattern=r"^[A-Z][A-Za-z0-9]{2,48}$")
    layer: SystemLayer
    path: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=2000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    priority_class: str = Field(default="P5", pattern=r"^P[0-7]$")
    core_loop_blocker: bool = False
    required_for_vertical_slice: bool = False
    player_flow_index: int = Field(default=999, ge=0, le=999)
    builds_world: bool = False


class GameBuildSpecification(Strict):
    """What the Engineer builds, and the only thing it is allowed to build.

    Compiled from a blueprint and then left alone. `content_hash` covers the
    parts that determine the build, so two specs that would produce the same
    project are recognisably the same, and a build can be traced to the exact
    text that asked for it.
    """

    spec_version: int = SPEC_VERSION
    revision: int = Field(default=1, ge=1)
    spec_id: str = Field(min_length=1, max_length=64)
    project_id: str = Field(min_length=1, max_length=64)
    blueprint_id: str = Field(min_length=1, max_length=64)
    idea_id: str = Field(min_length=1, max_length=64)

    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    core_loop: str = Field(default="", max_length=2000)

    config: BlueprintConfig
    systems: list[SpecSystem] = Field(default_factory=list, max_length=40)
    build_order: list[str] = Field(default_factory=list, max_length=40)
    player_journey: PlayerJourney | None = None
    gameplay_path: GameplayPath | None = None

    # Named so the Engineer cannot mistake them for suggestions. A feature here
    # was considered and refused, and putting it in the build anyway is the
    # failure mode this field exists to make unambiguous.
    excluded_features: list[str] = Field(default_factory=list, max_length=40)
    included_features: list[str] = Field(default_factory=list, max_length=40)

    asset_requirements: list[str] = Field(default_factory=list, max_length=40)
    technical_constraints: list[str] = Field(default_factory=list, max_length=40)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=200)

    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    content_hash: str = Field(default="", max_length=64)

    def fingerprint(self) -> str:
        """A hash of what determines the build, ignoring identity and time.

        Two specs with the same fingerprint ask for the same project, which is
        what makes "nothing changed, do not rebuild" a decidable question
        rather than a guess.
        """
        material = {
            "title": self.title,
            "core_loop": self.core_loop,
            "config": self.config.model_dump(mode="json"),
            "systems": [system.model_dump(mode="json") for system in self.systems],
            "build_order": self.build_order,
            "excluded": sorted(self.excluded_features),
            "constraints": sorted(self.technical_constraints),
        }
        return hashlib.sha256(
            json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
