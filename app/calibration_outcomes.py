"""Phase 4: measure P(CCU >= target) from outcomes actually observed.

The goal stated for this system is to maximise P(CCU >= 5000). That is a real,
measurable quantity and this module measures it -- but only from outcomes the
ledger has actually watched happen. It computes nothing from belief.

How the measurement works. Every game in the census is observed repeatedly. A
game is an *outcome* once it has been watched for at least `HORIZON_HOURS`:
the opportunity score is computed from the part of its history up to the start
of the window, and the question asked of the rest of the window is simply "did
it reach the target". That is a real cohort, assembled from rows, with no
model involved.

What it refuses to do. Until `MIN_OUTCOMES` games have completed a window,
there is no rate to report and every call says so. The number would otherwise
be a base rate over three games presented as a probability, which is worse
than no number: a reader can discount an absence, but they cannot discount a
figure that looks computed. The sampler produces outcomes on its own, so this
becomes measurable by waiting, not by adjusting anything here.

Nothing here is a forecast for a game that does not exist yet. It reports what
share of *observed* games in a score band reached the target, which is a base
rate for that band and is the honest ceiling on what this data can support.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from . import opportunity as opportunity_module
from . import pillars as pillars_module
from .market_pulse import TRACKED_SORT
from .models import MarketSample

CALIBRATION_VERSION = "calibration-v1"

DEFAULT_TARGET_CCU = 5_000
# How long a game is watched before its outcome counts. Shorter than this and
# "reached the target" mostly measures the time of day it was first seen.
HORIZON_HOURS = 24.0
# Below this many completed windows there is no rate worth reporting.
MIN_OUTCOMES = 30
# Score bands the rate is reported per. Coarse on purpose: finer bands over a
# small cohort produce confident-looking noise.
BANDS = ((0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0))


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Outcome:
    universe_id: str
    score: float
    peak_ccu: int
    reached: bool
    observed_hours: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Calibration:
    version: str
    target_ccu: int
    horizon_hours: float
    state: str
    reason: str
    outcomes: int
    required: int
    bands: list[dict] = field(default_factory=list)
    overall_rate: float | None = None
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _history_before(rows: list[MarketSample], cutoff: datetime) -> list[MarketSample]:
    return [row for row in rows if _utc(row.captured_at) <= cutoff]


def collect_outcomes(db, target_ccu: int = DEFAULT_TARGET_CCU,
                     horizon_hours: float = HORIZON_HOURS) -> list[Outcome]:
    """Every game watched long enough to have an answer.

    The score is computed from the history *before* the window opens, so it
    never sees the outcome it is being judged against. Getting that wrong is
    how a calibration reports itself as excellent.
    """
    universes = list(db.scalars(select(MarketSample.universe_id).distinct()))
    outcomes: list[Outcome] = []
    for universe in universes:
        rows = list(db.scalars(
            select(MarketSample).where(MarketSample.universe_id == universe)
            .order_by(MarketSample.captured_at)))
        if len(rows) < 2:
            continue
        first, last = _utc(rows[0].captured_at), _utc(rows[-1].captured_at)
        observed = (last - first).total_seconds() / 3600
        # Belt and braces: the `len(prior) < 2` guard below already rejects
        # every series shorter than the horizon, because the cutoff then falls
        # before the first row. Kept because the intent is not obvious from
        # that guard alone.
        if observed < horizon_hours:
            continue
        cutoff = last - timedelta(hours=horizon_hours)
        prior = _history_before(rows, cutoff)
        if len(prior) < 2:
            continue
        window = [row for row in rows if _utc(row.captured_at) > cutoff]
        if not window:
            continue
        reading = _reading_from(prior, universe)
        ranked = opportunity_module.score_from_pillars(reading)
        if ranked.score is None:
            continue
        peak = max(row.player_count for row in window)
        outcomes.append(Outcome(universe, ranked.score, peak, peak >= target_ccu,
                                observed))
    return outcomes


def _reading_from(rows: list[MarketSample], universe_id: str) -> dict:
    """A pillar reading built from a fixed slice of history.

    `pillars_for` reads the whole series from the database, which would let
    the future leak into a score meant to predict it. This composes the same
    pillars over an explicit list of rows instead.
    """
    by_capture: dict[datetime, MarketSample] = {}
    for row in rows:
        by_capture.setdefault(_utc(row.captured_at), row)
    points = [by_capture[key] for key in sorted(by_capture)]
    genre = points[-1].genre if points else ""
    shelves = [row for row in rows
               if _utc(row.captured_at) == _utc(points[-1].captured_at)
               and row.sort_id != TRACKED_SORT] if points else []
    computed = [
        pillars_module.demand(points),
        pillars_module.momentum(points),
        pillars_module.acceleration(points),
        pillars_module.reception(points),
        # Saturation needs the whole census at a moment, which a per-game slice
        # cannot supply without reaching past the cutoff. Left unmeasured so
        # the score is re-weighted over what is genuinely known here.
        pillars_module._unmeasured("saturation", "Genre saturation",
                                   "share of ranked games",
                                   "not computed inside a historical slice"),
        pillars_module.visibility(shelves, census_taken=True),
    ]
    return {"version": pillars_module.PILLARS_VERSION, "universe_id": universe_id,
            "genre": genre, "observations": len(points),
            "pillars": [pillar.as_dict() for pillar in computed]}


def calibrate(db, target_ccu: int = DEFAULT_TARGET_CCU,
              horizon_hours: float = HORIZON_HOURS) -> Calibration:
    """The observed rate per score band, or a clear refusal."""
    outcomes = collect_outcomes(db, target_ccu, horizon_hours)
    if len(outcomes) < MIN_OUTCOMES:
        return Calibration(
            CALIBRATION_VERSION, target_ccu, horizon_hours, "insufficient_outcomes",
            f"{len(outcomes)} game(s) have been watched for a full "
            f"{horizon_hours:.0f}h window; {MIN_OUTCOMES} are required before a "
            f"rate means anything",
            len(outcomes), MIN_OUTCOMES,
            note="The sampler produces outcomes on its own. This becomes "
                 "measurable by waiting, not by lowering the bar.",
        )
    bands = []
    for low, high in BANDS:
        inside = [item for item in outcomes if low <= item.score < high or
                  (high == 1.0 and item.score == 1.0)]
        bands.append({
            "from": low, "to": high, "games": len(inside),
            "reached": sum(item.reached for item in inside),
            # A band nobody landed in has no rate; it does not have a rate of 0.
            "rate": (sum(item.reached for item in inside) / len(inside)) if inside else None,
        })
    return Calibration(
        CALIBRATION_VERSION, target_ccu, horizon_hours, "measured",
        f"measured over {len(outcomes)} completed {horizon_hours:.0f}h windows",
        len(outcomes), MIN_OUTCOMES, bands,
        sum(item.reached for item in outcomes) / len(outcomes),
        note=f"The share of observed games in each score band that reached "
             f"{target_ccu:,} concurrent players. A base rate for games already "
             f"on the platform, not a forecast for a game that does not exist yet.",
    )


def probability_for(db, universe_id: str, target_ccu: int = DEFAULT_TARGET_CCU) -> dict:
    """The band rate a game's current score falls into, when one exists."""
    ranked = opportunity_module.score(db, universe_id)
    calibration = calibrate(db, target_ccu)
    if ranked.score is None or calibration.state != "measured":
        return {"universe_id": str(universe_id), "score": ranked.score,
                "probability": None, "state": calibration.state,
                "reason": calibration.reason if ranked.score is not None
                else ranked.reason}
    band = next((entry for entry in calibration.bands
                 if entry["from"] <= ranked.score < entry["to"]
                 or (entry["to"] == 1.0 and ranked.score == 1.0)), None)
    return {"universe_id": str(universe_id), "score": ranked.score,
            "probability": (band or {}).get("rate"), "state": "measured",
            "band": band, "target_ccu": target_ccu,
            "reason": f"observed rate for games scoring {band['from']}-{band['to']}"
                      if band else "no band matched"}
