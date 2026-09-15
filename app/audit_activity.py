"""Live activity for a running Venture Scout audit.

An audit takes minutes and, until now, reported nothing at all while it ran:
the button said "Auditing…" and the next visible thing was either a brief or a
one-line failure. Every question about what it was doing -- which pass, which
model, why an attempt was refused -- could only be answered from the service
log afterwards.

This is a deliberately small in-process broker. Audits run in the same process
that serves the dashboard, so nothing needs a queue or a table. It is *not*
durable: a restart loses the feed, which is why the audit record in the ledger
stays the source of truth and this only describes work in flight.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

# Enough to hold a long audit's chatter without letting a stuck one grow
# without bound.
MAX_EVENTS = 200


@dataclass
class _Feed:
    events: deque = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))
    updated: asyncio.Event = field(default_factory=asyncio.Event)
    running: bool = False
    sequence: int = 0


_feeds: dict[str, _Feed] = {}


def _feed(candidate_id: str) -> _Feed:
    return _feeds.setdefault(candidate_id, _Feed())


def start(candidate_id: str) -> None:
    """Begin a new audit's feed, discarding whatever the last one left."""
    feed = _feed(candidate_id)
    feed.events.clear()
    feed.running = True
    feed.sequence = 0
    feed.updated.set()
    emit(candidate_id, "started", "Venture Scout audit requested")


def emit(candidate_id: str, stage: str, detail: str = "", **extra) -> None:
    """Record one thing the audit just did.

    Callers pass plain description, never model output: the feed is a progress
    report, not a channel for untrusted text.
    """
    feed = _feed(candidate_id)
    feed.sequence += 1
    feed.events.append({
        "sequence": feed.sequence,
        "stage": stage,
        "detail": detail,
        "at": datetime.now(UTC).isoformat(),
        **extra,
    })
    feed.updated.set()


def finish(candidate_id: str, stage: str, detail: str = "", **extra) -> None:
    emit(candidate_id, stage, detail, **extra)
    feed = _feed(candidate_id)
    feed.running = False
    feed.updated.set()


def snapshot(candidate_id: str) -> dict:
    feed = _feed(candidate_id)
    return {"running": feed.running, "events": list(feed.events)}


async def wait(candidate_id: str, timeout: float) -> None:
    """Block until something new happens, or the timeout expires.

    The timeout is what keeps a finished or idle feed from holding a request
    open forever; the caller re-reads the snapshot either way.
    """
    feed = _feed(candidate_id)
    feed.updated.clear()
    try:
        await asyncio.wait_for(feed.updated.wait(), timeout=timeout)
    except (TimeoutError, asyncio.TimeoutError):
        pass
