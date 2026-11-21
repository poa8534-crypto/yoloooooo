"""Where the census has holes, measured from the census itself.

The sampler runs only while the service runs, and the service runs only while
the PC is awake. Every gap is a stretch of player counts that nobody can go
back and collect. This module does not fill gaps; it names them, so a rate or
an outcome that would straddle one can refuse instead of looking normal.

Coverage is derived from the distinct capture times already in
`market_samples`, so it needs no table of its own and is exact for the whole
history, including the months before this module existed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .models import MarketSample

# A pause longer than this many sampling intervals counts as a gap.
GAP_INTERVALS = 2.0


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Gap:
    start: str
    end: str
    hours: float

    def as_dict(self) -> dict:
        return asdict(self)


def capture_times(db) -> list[datetime]:
    return sorted(_utc(moment) for moment in
                  db.scalars(select(MarketSample.captured_at).distinct()))


def gaps(times: list[datetime], interval_minutes: int) -> list[Gap]:
    limit = interval_minutes * 60 * GAP_INTERVALS
    found = []
    for before, after in zip(times, times[1:]):
        span = (after - before).total_seconds()
        if span > limit:
            found.append(Gap(before.isoformat(timespec="minutes"),
                             after.isoformat(timespec="minutes"), round(span / 3600, 1)))
    return found


def daily(times: list[datetime], interval_minutes: int, days: int,
          now: datetime | None = None) -> list[dict]:
    """Share of expected samples taken, per UTC day, newest last.

    The current day is measured against the part of it that has elapsed.
    """
    now = _utc(now or datetime.now(UTC))
    expected_per_day = 24 * 60 / interval_minutes
    counts: dict[str, int] = {}
    for moment in times:
        key = moment.date().isoformat()
        counts[key] = counts.get(key, 0) + 1
    result = []
    for offset in range(days - 1, -1, -1):
        day = (now - timedelta(days=offset)).date()
        expected = expected_per_day
        if offset == 0:
            start = datetime(day.year, day.month, day.day, tzinfo=UTC)
            expected = max(1.0, (now - start).total_seconds() / (interval_minutes * 60))
        taken = counts.get(day.isoformat(), 0)
        result.append({"day": day.isoformat(), "samples": taken,
                       "coverage": round(min(1.0, taken / expected), 3)})
    return result


def report(db, interval_minutes: int, days: int = 14,
           now: datetime | None = None) -> dict:
    times = capture_times(db)
    if not times:
        return {"state": "never_sampled", "captures": 0, "gaps": [], "daily": [],
                "note": "No census has been recorded yet."}
    found = gaps(times, interval_minutes)
    span_hours = (times[-1] - times[0]).total_seconds() / 3600
    missing = sum(gap.hours for gap in found)
    return {
        "state": "measured",
        "captures": len(times),
        "first": times[0].isoformat(timespec="minutes"),
        "last": times[-1].isoformat(timespec="minutes"),
        "interval_minutes": interval_minutes,
        "gap_count": len(found),
        "hours_in_gaps": round(missing, 1),
        "share_of_history_in_gaps": round(missing / span_hours, 3) if span_hours else None,
        "gaps": [gap.as_dict() for gap in found[-50:]],
        "daily": daily(times, interval_minutes, days, now),
        "note": "A gap is a stretch with no census at all, almost always the PC "
                "asleep. Rates and outcomes that would straddle one are refused, "
                "not interpolated.",
    }
