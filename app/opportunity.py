"""Model 2: deterministic opportunity arithmetic over the measured pillars.

This is the layer that was asked for explicitly: not an LLM asked which idea it
likes, but arithmetic over measured variables, with fixed weights, reproducible
from the ledger. No model is called anywhere in this module.

What it does and does not claim:

* It **ranks**. Given the same rows it always produces the same order, and the
  contribution of every component is reported next to the total so a reader can
  disagree with one input rather than with a single number.
* It does **not** state a probability. `COMPONENTS` are normalised to 0..1 and
  weighted, which produces a comparable index, and an index is not a chance of
  anything. Turning this into P(CCU >= target) requires outcomes to calibrate
  against, which is `app.calibration_outcomes`, and until that has enough data
  every score here is marked `uncalibrated`.
* It **refuses rather than guesses**. A component whose pillar abstained
  contributes nothing and is named in `missing`. A candidate with too few
  measured components gets no score at all -- `None`, with the reason --
  because a score computed from one of five inputs looks exactly like a score
  computed from five.

The weights are a stated prior, not a fitted result. They are recorded in the
output so a score can be re-derived, and `OPPORTUNITY_VERSION` changes whenever
they do, so an old score is never silently compared against a new one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import pillars as pillars_module

OPPORTUNITY_VERSION = "opportunity-v1"

# At least this many of the weighted components must be measured before a
# score is emitted at all.
MIN_COMPONENTS = 3

# A player count where "this niche clearly has demand" saturates. Above it,
# more players stop counting as more evidence of demand and start counting as
# a wall of incumbents, which `saturation` handles separately.
DEMAND_REFERENCE = 20_000.0
MOMENTUM_REFERENCE = 500.0       # players/hour that reads as a strong climb
ACCELERATION_REFERENCE = 1_000.0  # players/hour^2 that reads as steepening


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _saturating(value: float, reference: float) -> float:
    """Diminishing returns without a cliff: 0 at 0, 0.5 at the reference."""
    if reference <= 0:
        return 0.0
    return _clamp(value / (value + reference)) if value > 0 else 0.0


def _signed(value: float, reference: float) -> float:
    """A rate that can be negative, mapped to 0..1 with 0.5 as flat."""
    if reference <= 0:
        return 0.5
    return _clamp(0.5 + 0.5 * _clamp(value / reference, -1.0, 1.0))


def _demand(value: float) -> float:
    return _saturating(value, DEMAND_REFERENCE)


def _momentum(value: float) -> float:
    return _signed(value, MOMENTUM_REFERENCE)


def _acceleration(value: float) -> float:
    return _signed(value, ACCELERATION_REFERENCE)


def _reception(value: float) -> float:
    """Approval below half is actively bad, so the usable range is 0.5..1."""
    return _clamp((value - 0.5) * 2.0)


def _headroom(value: float) -> float:
    """Genre saturation inverted: an empty shelf is room, a full one is not.

    Deliberately not a verdict. A crowded genre is proven demand as much as it
    is competition, and this only expresses the crowding as room to enter.
    """
    return _clamp(1.0 - value)


# key -> (pillar key, label, weight, transform, what a high value means)
#
# The label is the component's own, not the pillar's. `headroom` inverts
# `saturation`, so borrowing the pillar's label printed "Genre saturation 60%"
# beside a bar meaning 60% room -- the exact opposite of what it showed.
COMPONENTS: tuple[tuple[str, str, str, float, object, str], ...] = (
    ("demand", "demand", "Demand", 0.30, _demand,
     "how many people are playing this kind of game right now"),
    ("momentum", "momentum", "Momentum", 0.25, _momentum,
     "whether that audience is growing or draining"),
    ("acceleration", "acceleration", "Acceleration", 0.10, _acceleration,
     "whether the growth itself is steepening"),
    ("reception", "reception", "Reception", 0.20, _reception,
     "how the audience rates what already exists"),
    ("headroom", "saturation", "Genre headroom", 0.15, _headroom,
     "how much room the genre still has on Roblox's shelves"),
)


@dataclass(frozen=True)
class Component:
    key: str
    label: str
    weight: float
    measured: bool
    raw: float | None
    normalised: float | None
    contribution: float | None
    meaning: str
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Opportunity:
    version: str
    universe_id: str
    score: float | None
    state: str
    reason: str
    calibrated: bool
    components: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def score_from_pillars(reading: dict) -> Opportunity:
    """Rank one game from an already-computed pillar reading.

    Takes the reading rather than the database so the arithmetic is testable
    on its own and cannot quietly acquire a second source of truth.
    """
    by_key = {entry["key"]: entry for entry in reading.get("pillars", [])}
    components: list[Component] = []
    missing: list[str] = []
    weighted = 0.0
    available = 0.0

    for key, pillar_key, label, weight, transform, meaning in COMPONENTS:
        pillar = by_key.get(pillar_key) or {}
        if pillar.get("state") != pillars_module.MEASURED or pillar.get("value") is None:
            missing.append(key)
            components.append(Component(key, label, weight, False, None, None, None,
                                        meaning, pillar.get("detail", "not measured")))
            continue
        raw = float(pillar["value"])
        normalised = float(transform(raw))
        weighted += normalised * weight
        available += weight
        components.append(Component(key, label, weight, True, raw, normalised,
                                    normalised * weight, meaning,
                                    pillar.get("detail", "")))

    measured = len(COMPONENTS) - len(missing)
    if measured < MIN_COMPONENTS or available <= 0:
        return Opportunity(
            OPPORTUNITY_VERSION, reading.get("universe_id", ""), None,
            "insufficient_evidence",
            f"{measured} of {len(COMPONENTS)} components measured; at least "
            f"{MIN_COMPONENTS} are required before a score means anything",
            False, [component.as_dict() for component in components], missing,
            "A score computed from one input looks exactly like a score computed "
            "from five, so none is given.",
        )

    # Re-weighted over what was actually measured, so a game missing one
    # component is not silently penalised for the absence.
    return Opportunity(
        OPPORTUNITY_VERSION, reading.get("universe_id", ""), weighted / available,
        "ranked", f"{measured} of {len(COMPONENTS)} components measured", False,
        [component.as_dict() for component in components], missing,
        "A comparable ranking index, not a probability. Calibration against "
        "observed outcomes is what turns a ranking into a chance of anything.",
    )


def score(db, universe_id: str) -> Opportunity:
    return score_from_pillars(pillars_module.pillars_for(db, universe_id))


def rank(db, universe_ids) -> list[Opportunity]:
    """Score several games, best first, unscoreable ones last.

    Ordering is total and deterministic: ties break on the universe ID so the
    same ledger always produces the same list.
    """
    scored = [score(db, universe_id) for universe_id in dict.fromkeys(universe_ids)]
    return sorted(scored, key=lambda item: (item.score is None,
                                            -(item.score or 0.0),
                                            item.universe_id))
