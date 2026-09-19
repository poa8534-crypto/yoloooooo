"""What the models have actually been asked to do, and what is left.

Two different questions, and only one of them has an honest answer from the
provider.

WHAT WAS SPENT is measured. Every model call records its tokens against the day
and the hour it happened in (`app/engineer/runs.py`), so the totals here are a
sum over records that were written when the call returned. Nothing is
estimated.

WHAT IS LEFT is not something any provider tells us. Gemini answers 429 when a
quota is gone; `agy` refuses when its subscription is spent. Neither returns a
remaining count, so a "73% of your hourly limit" bar would be a number this
process invented. Instead the limit comes from configuration -- the person
knows their plan -- and remaining is arithmetic on a figure they supplied. With
no limit configured, usage is reported and remaining is null, which the UI
shows as "no limit set" rather than as a full tank.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .engineer.runs import USAGE_PREFIX
from .models import SystemState

# Token fields a provider may report. Summed into one number for the strip, and
# kept apart underneath, because a cached prompt token is not priced or
# rationed like a generated one.
TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens",
                "prompt_tokens", "completion_tokens", "cached_tokens")


def _days(factory, back: int) -> list[tuple[str, dict]]:
    """The usage rows for the last `back` days, oldest first."""
    today = datetime.now(UTC).date()
    wanted = [(today - timedelta(days=offset)).isoformat() for offset in range(back)]
    rows: list[tuple[str, dict]] = []
    with factory() as db:
        for day in reversed(wanted):
            row = db.get(SystemState, USAGE_PREFIX + day)
            if row is not None:
                rows.append((day, dict(row.value_json)))
    return rows


def _tokens(entry: dict) -> int:
    """One token number for an entry, without counting the same tokens twice."""
    if entry.get("total_tokens"):
        return int(entry["total_tokens"])
    return int(entry.get("input_tokens", 0)) + int(entry.get("output_tokens", 0))


def _window(rows: list[tuple[str, dict]], *, since: datetime | None) -> dict:
    """Totals across every provider key, optionally only for recent hours.

    `rate_limited` is the one number here a provider actually told us about its
    own limits: a 429 is the quota answering. It is counted per day rather than
    per hour, because the hour buckets record what was spent, not what was
    refused.
    """
    calls = 0
    tokens = 0
    refused = 0
    # Whether anything in range was recorded with hour buckets at all. A
    # process that started before hourly recording existed keeps writing daily
    # totals only, and reporting that as "0 calls this hour" would be a
    # confident wrong answer rather than a missing one.
    measured = since is None
    models: dict[str, int] = {}
    for _day, value in rows:
        for entry in value.values():
            if not isinstance(entry, dict):
                continue
            refused += int(entry.get("rate_limited", 0))
            if since is None:
                calls += int(entry.get("calls", 0))
                tokens += _tokens(entry)
            else:
                for hour, bucket in (entry.get("hours") or {}).items():
                    measured = True
                    try:
                        at = datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=UTC)
                    except ValueError:
                        continue
                    if at >= since:
                        calls += int(bucket.get("calls", 0))
                        tokens += _tokens(bucket)
            if since is None:
                # Model counts are recorded per day, so they belong to the week
                # window. Showing them beside an hour's totals would put 97
                # next to "0 calls" and let the eye pick whichever it liked.
                for model, count in (entry.get("models") or {}).items():
                    models[model] = models.get(model, 0) + int(count)
    return {"calls": calls, "tokens": tokens, "models": models,
            "rate_limited": refused, "measured": measured}


def _remaining(used: int, limit: int) -> dict:
    """What is left, or nulls when nothing was configured to be left of."""
    if limit <= 0:
        return {"limit": None, "remaining": None, "percent_used": None}
    return {
        "limit": limit,
        "remaining": max(0, limit - used),
        "percent_used": min(100, round(used * 100 / limit)),
    }


def report(factory, *, hourly_limit: int = 0, weekly_limit: int = 0) -> dict:
    """Usage for the last hour and the last seven days.

    The rate-limiter's live cooldowns are deliberately not here. They live in
    the build process's memory, and the dashboard is a different process: it
    could only report them by guessing. The 429s that caused them were recorded
    when they happened, so those are reported instead.
    """
    now = datetime.now(UTC)
    hour = _window(_days(factory, 2), since=now - timedelta(hours=1))
    week = _window(_days(factory, 7), since=None)
    return {
        "generated_at": now.isoformat(),
        "hour": {**hour, **_remaining(hour["calls"], hourly_limit)},
        "week": {**week, **_remaining(week["calls"], weekly_limit)},
        # Measured, never predicted: no "you will run out in 20 minutes".
        "limits_configured": bool(hourly_limit or weekly_limit),
    }
