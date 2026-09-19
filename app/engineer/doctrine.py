"""The Engineer's operating doctrine, and the plan shapes it asks for.

`instruction.md` beside this file is the doctrine itself: how the Engineer
thinks about a game, in prose, for a model to read. This module is the part
that has to be true rather than persuasive -- the priority classes, the build
order, and the validation that refuses a plan which cannot produce the player
experience it claims.

The split matters. A doctrine a model is merely shown is a doctrine it follows
on good days. Everything here that can be *enforced* is enforced in code:
build order is computed from the dependency graph rather than taken from the
model, an excluded feature cannot reach a task, and a reward with nowhere to go
fails validation before any code is written.

Three artefacts, in the order they are produced:

    PlayerJourney   what the player experiences, from joining to returning
    GameplayPath    the actions they take, in order
    SystemGraph     the software that implements them

The specification still decides WHAT is built. This decides in what order, and
refuses plans that could not work.
"""

from __future__ import annotations

import enum
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DOCTRINE_FILE = Path(__file__).resolve().parent / "instruction.md"


@lru_cache(maxsize=1)
def doctrine() -> str:
    """The doctrine text, read once.

    Missing is not an error the Engineer should die of: a build without the
    doctrine is worse, not impossible. It returns "" and the caller says so,
    rather than the whole pipeline stopping because a markdown file moved.
    """
    try:
        return DOCTRINE_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


def doctrine_summary(limit: int = 4000) -> str:
    """The doctrine, trimmed for a prompt that also has to carry a task.

    Whole sections are dropped rather than cutting mid-sentence, because half
    a rule reads as a different rule.
    """
    text = doctrine()
    if len(text) <= limit:
        return text
    kept: list[str] = []
    total = 0
    for block in text.split("\n---\n"):
        if total + len(block) > limit:
            break
        kept.append(block)
        total += len(block)
    return "\n---\n".join(kept)


class PriorityClass(str, enum.Enum):
    """What a system is for, which decides when it gets built.

    Ordered deliberately: comparing two of these compares their urgency, and
    the build order uses that directly.
    """

    FOUNDATION = "P0"            # configuration, shared types, data models
    CORE_INFRASTRUCTURE = "P1"   # inventory, wallet, health, interaction
    PRIMARY_GAMEPLAY = "P2"      # the mechanic the player came for
    PROGRESSION = "P3"           # selling, XP, upgrades, unlocks
    COMMUNICATION = "P4"         # HUD and the UI the loop needs
    SECONDARY = "P5"             # quests, collections, crafting
    POLISH = "P6"                # VFX, SFX, camera, decoration
    META = "P7"                  # passes, daily rewards, monetisation

    @property
    def rank(self) -> int:
        return int(self.value[1:])


class GateName(str, enum.Enum):
    """The playability gates, in the order a player meets them."""

    SPAWNABLE = "spawnable"
    INTERACTABLE = "interactable"
    CORE_ACTION = "core_action"
    REWARDABLE = "rewardable"
    LOOPABLE = "loopable"
    UNDERSTANDABLE = "understandable"
    PERSISTENT = "persistent"
    MVP_PLAYABLE = "mvp_playable"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlayerJourney(_Strict):
    """What the player experiences, from joining to coming back.

    Written before any system exists. Its purpose is to make the question
    "where does the first reward go?" unavoidable -- that answer is what
    reveals the systems that must exist before the reward does.
    """

    entry_state: str = Field(default="", max_length=600)
    spawn_context: str = Field(default="", max_length=600)
    immediate_visuals: list[str] = Field(default_factory=list, max_length=12)
    first_affordance: str = Field(default="", max_length=400)
    first_action: str = Field(default="", max_length=400)
    first_feedback: str = Field(default="", max_length=400)
    first_reward: str = Field(default="", max_length=400)
    # The field this whole model exists for.
    reward_destination: str = Field(default="", max_length=400)
    next_decision: str = Field(default="", max_length=400)
    core_loop: list[str] = Field(default_factory=list, max_length=16)
    progression_loop: list[str] = Field(default_factory=list, max_length=16)
    failure_state: str = Field(default="", max_length=400)
    recovery_path: str = Field(default="", max_length=400)
    session_end: str = Field(default="", max_length=400)
    return_state: str = Field(default="", max_length=400)


class PathNode(_Strict):
    """One thing the player does, and what it takes to make it possible."""

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=600)
    systems: list[str] = Field(default_factory=list, max_length=12)
    data: list[str] = Field(default_factory=list, max_length=12)
    ui: list[str] = Field(default_factory=list, max_length=8)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=8)
    gate: GateName | None = None


class GameplayPath(_Strict):
    """The player's actions in order: spawn, equip, act, reward, spend, repeat.

    Different from the system graph on purpose. This is what the player does;
    the graph is what implements it. Keeping them apart is what makes it
    possible to ask whether every step a player takes is actually supported.
    """

    nodes: list[PathNode] = Field(default_factory=list, max_length=32)

    def node(self, node_id: str) -> PathNode | None:
        return next((node for node in self.nodes if node.id == node_id), None)

    @property
    def systems(self) -> set[str]:
        return {name for node in self.nodes for name in node.systems}
