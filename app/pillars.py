"""Named, measured dimensions of a game, each traceable to the rows it came from.

A pillar is deliberately not a score. Five numbers that each say what they
measure, next to the evidence they were computed from, let a reader disagree
with one of them. A single blended figure cannot be disagreed with -- it can
only be believed -- and the calibration gate exists precisely to stop this
system emitting numbers of that kind before they have been validated.

Three rules hold the line, and each is enforced by a test:

1. **Nothing here sums pillars.** There is no total, no weighted blend, no
   "overall". Combining them is the opportunity model's job, under the
   calibration gate, in a later phase.
2. **Absent evidence is never zero.** A pillar with nothing behind it reports
   `insufficient_evidence` and says what is missing. A velocity of zero means
   measured and flat; it never means unmeasured.
3. **A rate needs a real interval.** Two samples ten seconds apart would
   extrapolate a noise difference into a spectacular hourly rate, so a span
   shorter than `MIN_SPAN_SECONDS` reports insufficient rather than fast.

Version the rules, not just the code: `PILLARS_VERSION` changes whenever a
definition changes, so an old reading is never silently compared against a new
one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select

from .models import MarketSample

PILLARS_VERSION = "pillars-v1"

# Below this, the interval is too short for a difference to be signal rather
# than the market breathing. Two samples half an hour apart is the design.
MIN_SPAN_SECONDS = 15 * 60

MEASURED = "measured"
INSUFFICIENT = "insufficient_evidence"


@dataclass(frozen=True)
class Pillar:
    key: str
    label: str
    unit: str
    # None whenever state is INSUFFICIENT. Readers must branch on state, not
    # on truthiness: 0.0 is a real, measured value.
    value: float | None
    state: str
    detail: str
    observations: int = 0
    basis: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _unmeasured(key, label, unit, detail, observations=0) -> Pillar:
    return Pillar(key=key, label=label, unit=unit, value=None, state=INSUFFICIENT,
                  detail=detail, observations=observations)


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def series(db, universe_id: str) -> list[MarketSample]:
    """Every census row for one game, oldest first.

    A game on several shelves is sampled several times per capture; those are
    the same measurement of the same game, so they are collapsed to one point
    per capture time and the shelf is not part of this pillar.
    """
    rows = list(db.scalars(
        select(MarketSample).where(MarketSample.universe_id == str(universe_id))
        .order_by(MarketSample.captured_at)
    ))
    by_capture: dict[datetime, MarketSample] = {}
    for row in rows:
        by_capture.setdefault(_utc(row.captured_at), row)
    return [by_capture[key] for key in sorted(by_capture)]


def demand(points: list[MarketSample]) -> Pillar:
    if not points:
        return _unmeasured("demand", "Demand", "players",
                           "this game has not appeared in a sampled shelf")
    latest = points[-1]
    return Pillar("demand", "Demand", "players", float(latest.player_count), MEASURED,
                  f"Roblox reported {latest.player_count} players at "
                  f"{_utc(latest.captured_at).isoformat(timespec='minutes')}",
                  observations=len(points), basis=[latest.id])


def momentum(points: list[MarketSample]) -> Pillar:
    """Players gained per hour between the first and last observation."""
    if len(points) < 2:
        return _unmeasured("momentum", "Momentum", "players/hour",
                           f"a rate needs two observations of this game; there "
                           f"{'is 1' if points else 'are none'} so far",
                           observations=len(points))
    first, last = points[0], points[-1]
    span = (_utc(last.captured_at) - _utc(first.captured_at)).total_seconds()
    if span < MIN_SPAN_SECONDS:
        return _unmeasured("momentum", "Momentum", "players/hour",
                           f"the observations are {int(span)}s apart; a shorter "
                           f"interval than {MIN_SPAN_SECONDS}s turns noise into a rate",
                           observations=len(points))
    rate = (last.player_count - first.player_count) / (span / 3600)
    return Pillar("momentum", "Momentum", "players/hour", rate, MEASURED,
                  f"{last.player_count - first.player_count:+d} players over "
                  f"{span / 3600:.1f}h",
                  observations=len(points), basis=[first.id, last.id])


def acceleration(points: list[MarketSample]) -> Pillar:
    """Change in the rate itself: is the rise steepening or flattening?"""
    if len(points) < 3:
        return _unmeasured("acceleration", "Acceleration", "players/hour²",
                           f"a change in rate needs three observations; there are "
                           f"{len(points)} so far", observations=len(points))
    middle = points[len(points) // 2]
    early, late = momentum([points[0], middle]), momentum([middle, points[-1]])
    if early.state != MEASURED or late.state != MEASURED:
        return _unmeasured("acceleration", "Acceleration", "players/hour²",
                           "the two halves of the series are too close together "
                           "to compare rates", observations=len(points))
    span = (_utc(points[-1].captured_at) - _utc(points[0].captured_at)).total_seconds()
    return Pillar("acceleration", "Acceleration", "players/hour²",
                  (late.value - early.value) / (span / 3600), MEASURED,
                  f"rate moved from {early.value:+.0f} to {late.value:+.0f} players/hour",
                  observations=len(points), basis=[points[0].id, middle.id, points[-1].id])


def reception(points: list[MarketSample]) -> Pillar:
    """Approval share from Roblox's own vote totals.

    These are first-party integers, which is why judging how a game is
    received needs no sentiment model reading what people wrote about it.
    """
    if not points:
        return _unmeasured("reception", "Reception", "approval",
                           "this game has not appeared in a sampled shelf")
    latest = points[-1]
    total = latest.up_votes + latest.down_votes
    if total <= 0:
        return _unmeasured("reception", "Reception", "approval",
                           "Roblox reported no votes for this game",
                           observations=len(points))
    return Pillar("reception", "Reception", "approval", latest.up_votes / total, MEASURED,
                  f"{latest.up_votes} up, {latest.down_votes} down",
                  observations=len(points), basis=[latest.id])


def saturation(db, genre: str, at: datetime | None = None) -> Pillar:
    """Share of the most recent census that this game's genre already holds.

    High saturation is not automatically bad and this does not say it is: it
    measures how crowded the shelf space is, and the reader decides whether
    that reads as proven demand or as a wall of incumbents.
    """
    if not genre:
        return _unmeasured("saturation", "Genre saturation", "share of ranked games",
                           "Roblox did not report a genre for this game")
    latest = db.scalar(select(MarketSample.captured_at)
                       .order_by(MarketSample.captured_at.desc()).limit(1))
    if latest is None:
        return _unmeasured("saturation", "Genre saturation", "share of ranked games",
                           "no census has been sampled yet")
    rows = list(db.scalars(select(MarketSample).where(MarketSample.captured_at == latest)))
    universes = {row.universe_id for row in rows}
    in_genre = {row.universe_id for row in rows if row.genre == genre}
    if not universes:
        return _unmeasured("saturation", "Genre saturation", "share of ranked games",
                           "the latest census holds no games")
    return Pillar("saturation", "Genre saturation", "share of ranked games",
                  len(in_genre) / len(universes), MEASURED,
                  f"{len(in_genre)} of {len(universes)} ranked games are {genre}",
                  observations=len(rows))


def visibility(points: list[MarketSample]) -> Pillar:
    """How many of Roblox's own shelves currently carry this game."""
    if not points:
        return _unmeasured("visibility", "Shelf visibility", "shelves",
                           "this game has not appeared in a sampled shelf")
    latest_at = _utc(points[-1].captured_at)
    return Pillar("visibility", "Shelf visibility", "shelves", float(len(points)), MEASURED,
                  f"carried by {len(points)} shelf(s) at "
                  f"{latest_at.isoformat(timespec='minutes')}",
                  observations=len(points), basis=[points[-1].id])


def shelf_placements(db, universe_id: str, at: datetime | None = None) -> list[MarketSample]:
    """Every shelf carrying this game in one census (not collapsed)."""
    latest = at or db.scalar(select(MarketSample.captured_at)
                             .order_by(MarketSample.captured_at.desc()).limit(1))
    if latest is None:
        return []
    return list(db.scalars(
        select(MarketSample)
        .where(MarketSample.universe_id == str(universe_id),
               MarketSample.captured_at == latest)
        .order_by(MarketSample.rank)))


def pillars_for(db, universe_id: str) -> dict:
    """Every pillar for one game. Never returns a combined figure."""
    points = series(db, universe_id)
    genre = points[-1].genre if points else ""
    placements = shelf_placements(db, universe_id)
    computed = [demand(points), momentum(points), acceleration(points),
                reception(points), saturation(db, genre), visibility(placements)]
    return {
        "version": PILLARS_VERSION,
        "universe_id": str(universe_id),
        "genre": genre,
        "observations": len(points),
        "shelves": [row.sort_id for row in placements],
        # Deliberately a list, not a mapping with a total. Adding one would be
        # scoring, which stays locked behind calibration.
        "pillars": [pillar.as_dict() for pillar in computed],
        "measured": sum(pillar.state == MEASURED for pillar in computed),
        "note": "Measured dimensions, not a ranking. These are never combined "
                "into a single score; that requires calibration, which is locked.",
    }
