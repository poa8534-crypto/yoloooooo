"""Live activity for a running Venture Scout audit.

An audit takes minutes and reports intermediate progress while it runs:
every question about what it is doing -- which pass, which model, which
validation gate failed -- is emitted as a structured milestone.

The feed is backed by a dual-write architecture:
1. An in-memory queue and asyncio.Event for zero-latency live SSE streaming.
2. A durable SQLite write-through ledger (scout_audit_runs and audit_activity_events)
   so that a service restart or page reload never loses the chronological record of
   what happened. On startup, interrupted runs are automatically reconciled.

It carries descriptions of the work, never model output -- so nothing untrusted
reaches the page through this channel.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

# Enough to hold a long audit's chatter without letting a stuck one grow
# without bound.
MAX_EVENTS = 200

_session_factory: Callable[[], Session] | None = None


def set_session_factory(factory: Callable[[], Session] | None) -> None:
    global _session_factory
    _session_factory = factory


def _get_session(feed: _Feed | None = None) -> Session | None:
    factory = (feed.session_factory if feed else None) or _session_factory
    if factory is not None:
        try:
            return factory()
        except Exception:
            pass
    try:
        from .db import SessionLocal
        return SessionLocal()
    except Exception:
        return None


@dataclass
class _Feed:
    events: deque = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))
    updated: asyncio.Event = field(default_factory=asyncio.Event)
    running: bool = False
    sequence: int = 0
    run_id: str | None = None
    session_factory: Callable[[], Session] | None = None


_feeds: dict[str, _Feed] = {}


def _feed(candidate_id: str) -> _Feed:
    return _feeds.setdefault(candidate_id, _Feed())


def reset(candidate_id: str | None = None) -> None:
    """Clear in-memory feed state, simulating a service restart."""
    if candidate_id:
        _feeds.pop(candidate_id, None)
    else:
        _feeds.clear()


def start(candidate_id: str, session_factory: Callable[[], Session] | None = None) -> None:
    """Begin a new audit's feed, discarding whatever the last in-memory run left."""
    if session_factory is not None:
        set_session_factory(session_factory)
    feed = _feed(candidate_id)
    feed.events.clear()
    feed.running = True
    feed.sequence = 0
    if session_factory is not None:
        feed.session_factory = session_factory

    from .models import Candidate, ScoutAuditRun, uid
    feed.run_id = uid()
    feed.updated.set()

    session = _get_session(feed)
    if session is not None:
        try:
            with session:
                if session.get(Candidate, candidate_id) is not None:
                    scout_run = ScoutAuditRun(
                        id=feed.run_id,
                        candidate_id=candidate_id,
                        status="running",
                        message="Venture Scout audit requested",
                        created_at=datetime.now(UTC),
                    )
                    session.add(scout_run)
                    session.commit()
        except Exception:
            pass

    emit(candidate_id, "started", "Venture Scout audit requested")


# The one stage whose detail is written by the model rather than by this
# pipeline. It is displayed as reasoning and never read back as evidence.
UNTRUSTED_STAGE = "reasoning"


def emit(candidate_id: str, stage: str, detail: str = "", **extra) -> None:
    """Record one thing the audit just did.

    Details are written by the pipeline, with one deliberate exception: a
    `reasoning` event carries the model's own thinking so an operator can read
    what it was working through. That text is untrusted -- it is redacted
    before it arrives, marked with its stage, and nothing downstream reads it
    back as evidence or lets it influence a decision.
    """
    feed = _feed(candidate_id)
    feed.sequence += 1
    now = datetime.now(UTC)
    feed.events.append({
        "sequence": feed.sequence,
        "stage": stage,
        "untrusted": stage == UNTRUSTED_STAGE,
        "detail": detail,
        "at": now.isoformat(),
        **extra,
    })
    feed.updated.set()

    if feed.run_id:
        session = _get_session(feed)
        if session is not None:
            try:
                with session:
                    from .models import AuditActivityEvent, Candidate
                    if session.get(Candidate, candidate_id) is not None:
                        row = AuditActivityEvent(
                            candidate_id=candidate_id,
                            run_id=feed.run_id,
                            sequence=feed.sequence,
                            stage=stage,
                            detail=detail,
                            extra_json=dict(extra),
                            created_at=now,
                        )
                        session.add(row)
                        session.commit()
            except Exception:
                pass


def finish(candidate_id: str, stage: str, detail: str = "", **extra) -> None:
    emit(candidate_id, stage, detail, **extra)
    feed = _feed(candidate_id)
    feed.running = False
    feed.updated.set()

    if feed.run_id:
        session = _get_session(feed)
        if session is not None:
            try:
                with session:
                    from .models import ScoutAuditRun
                    scout_run = session.get(ScoutAuditRun, feed.run_id)
                    if scout_run is not None:
                        scout_run.status = "complete" if stage == "stored" else stage
                        scout_run.completed_at = datetime.now(UTC)
                        session.commit()
            except Exception:
                pass


def snapshot(candidate_id: str, session_factory: Callable[[], Session] | None = None) -> dict:
    if session_factory is not None:
        set_session_factory(session_factory)
    feed = _feed(candidate_id)
    if session_factory is not None:
        feed.session_factory = session_factory
    if feed.events or feed.running:
        return {"running": feed.running, "events": list(feed.events)}

    # If in-memory feed is empty (e.g. following a restart or fresh request),
    # rehydrate from the durable SQLite ledger.
    session = _get_session(feed)
    if session is not None:
        try:
            with session:
                from .models import AuditActivityEvent, ScoutAuditRun
                latest_run = session.scalar(
                    select(ScoutAuditRun)
                    .where(ScoutAuditRun.candidate_id == candidate_id)
                    .order_by(desc(ScoutAuditRun.created_at))
                )
                if latest_run is not None:
                    event_rows = list(session.scalars(
                        select(AuditActivityEvent)
                        .where(AuditActivityEvent.run_id == latest_run.id)
                        .order_by(AuditActivityEvent.sequence)
                    ))
                    replayed = [
                        {
                            "sequence": row.sequence,
                            "stage": row.stage,
                            "detail": row.detail,
                            "at": row.created_at.isoformat(),
                            **(row.extra_json or {}),
                        }
                        for row in event_rows
                    ]
                    feed.run_id = latest_run.id
                    feed.running = (latest_run.status == "running")
                    feed.sequence = replayed[-1]["sequence"] if replayed else 0
                    feed.events.clear()
                    feed.events.extend(replayed[-MAX_EVENTS:])
                    return {"running": feed.running, "events": list(feed.events)}
        except Exception:
            pass

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
