"""Remember what a search returned, so the same question is not asked twice.

Public search engines suspend an instance that queries them in bursts -- during
testing, five of seven were blocked at once and the survivor ignored the site
operator, so discovery found nothing. Rounds and runs repeat queries often
enough that caching removes a real share of the outbound traffic.

The trade is staleness: a cached answer is the web as it was, not as it is.
That is acceptable for discovery, which only nominates games to look at. Every
measurement is fetched fresh from Roblox's API afterwards, and nothing cached
here becomes evidence.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from .models import SystemState

PREFIX = "search-cache:"


def _key(method: str, query: str) -> str:
    digest = hashlib.sha256(json.dumps([method, query], sort_keys=True).encode()).hexdigest()
    return PREFIX + digest[:32]


def get(factory, method: str, query: str, ttl_seconds: float) -> dict | None:
    """The stored payload, or None when absent or too old to trust."""
    if factory is None or ttl_seconds <= 0:
        return None
    try:
        with factory() as db:
            row = db.get(SystemState, _key(method, query))
            if row is None:
                return None
            stored = row.value_json
            at = datetime.fromisoformat(stored["at"])
            if datetime.now(UTC) - at > timedelta(seconds=ttl_seconds):
                return None
            return {"url": stored["url"], "payload": stored["payload"]}
    except Exception:
        # A cache that cannot be read is a cache miss, never an error.
        return None


def put(factory, method: str, query: str, url: str, payload) -> None:
    if factory is None:
        return
    try:
        with factory() as db:
            key = _key(method, query)
            row = db.get(SystemState, key)
            if row is None:
                row = SystemState(key=key)
                db.add(row)
            row.value_json = {
                "method": method, "query": query, "url": url,
                "payload": payload, "at": datetime.now(UTC).isoformat(),
            }
            row.updated_at = datetime.now(UTC)
            db.commit()
    except Exception:
        return
