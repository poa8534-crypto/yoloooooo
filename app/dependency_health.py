"""What each external dependency was last observed to do, and when.

The health page reported `tavily_configured: true` and the dashboard rendered
that as "Authenticated". It meant only that a key was present in the settings:
an expired key, a revoked one, or a provider that was down all morning read
exactly the same. The same applied to "Healthy" for a package that was merely
installed.

Configuration and observation are separate facts here. Configuration is what
the settings say; observation is what actually happened on the last request,
with the time it happened. Neither is inferred from the other, and a
dependency nothing has called yet is `unknown` rather than optimistic.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .models import SystemState

PREFIX = "dependency:"

# Only providers we actually call. A URL belonging to none of them is not
# recorded rather than filed under a guess.
PROVIDERS = (
    ("api.tavily.com", "tavily"),
    ("127.0.0.1:8888", "searxng"),
    ("localhost:8888", "searxng"),
    ("apis.roblox.com/search-api", "roblox_search"),
    ("googleapis.com/youtube", "youtube"),
    ("roblox.com", "roblox"),
)


def provider_for(url: str) -> str | None:
    lowered = (url or "").lower()
    return next((name for fragment, name in PROVIDERS if fragment in lowered), None)


def record(factory, url: str, *, ok: bool, detail: str = "") -> None:
    """Record the outcome of one request against whichever provider it hit.

    Failures never propagate: a health note is not worth losing a captured
    result over, and the caller is in the middle of real work.
    """
    provider = provider_for(url)
    if provider is None or factory is None:
        return
    try:
        with factory() as db:
            key = PREFIX + provider
            row = db.get(SystemState, key)
            if row is None:
                row = SystemState(key=key)
                db.add(row)
            row.value_json = {
                "state": "last_request_succeeded" if ok else "last_request_failed",
                "at": datetime.now(UTC).isoformat(),
                "detail": detail[:200],
            }
            row.updated_at = datetime.now(UTC)
            db.commit()
    except Exception:
        return


def observations(db) -> dict[str, dict]:
    return {
        row.key.removeprefix(PREFIX): row.value_json
        for row in db.query(SystemState).filter(SystemState.key.like(PREFIX + "%")).all()
    }


def describe(name: str, *, configured: bool, observed: dict | None, reachable: bool | None = None) -> dict:
    """One dependency, with configuration and observation kept apart.

    `reachable` is for dependencies this process can probe cheaply and without
    spending anything -- a local model server. For a metered third-party API
    there is no free probe, so the last real request is the only honest
    evidence available, and "configured" alone is never reported as healthy.
    """
    if not configured:
        state, detail = "not_configured", "No credential or endpoint is configured."
        checked_at = None
    elif reachable is True:
        state, detail = "reachable", "Answered a status request just now."
        checked_at = datetime.now(UTC).isoformat()
    elif reachable is False:
        state, detail = "unreachable", "Did not answer a status request just now."
        checked_at = datetime.now(UTC).isoformat()
    elif observed:
        state = observed.get("state", "unknown")
        detail = observed.get("detail") or (
            "The last request succeeded." if state == "last_request_succeeded"
            else "The last request failed."
        )
        checked_at = observed.get("at")
    else:
        state = "unknown"
        detail = "Configured, but nothing has called it yet in a way this service recorded."
        checked_at = None
    return {"name": name, "configured": configured, "state": state,
            "detail": detail, "checked_at": checked_at}
