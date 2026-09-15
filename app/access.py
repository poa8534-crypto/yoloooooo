"""Authentication, and the rule that binding wider requires it.

The service has never had any. That was defensible while it refused to listen
on anything but loopback: the ledger, the review queue and the override
controls were reachable only from this machine. The moment it is asked to
serve a wider interface -- a container, a tunnel, a hosting platform -- that
argument disappears and the honest options are to add authentication or to
keep refusing.

So: `DASHBOARD_TOKEN` unlocks a wider bind, and nothing else does. No token,
no non-loopback interface, whatever the host setting says. A deployment that
forgets the token fails at startup with the reason rather than starting up
quietly open to the internet.

The token is compared in constant time, never logged, and never echoed back in
an error. A wrong token and a missing token get the same answer, because
telling an attacker which of the two they got is free information.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

# Paths that must answer before a caller can prove anything: a load balancer's
# health probe has no token and its failure would take the service down.
OPEN_PATHS = {"/api/health/live"}

MINIMUM_TOKEN_LENGTH = 24


def is_loopback(host: str) -> bool:
    return host in LOOPBACK_HOSTS


def assert_bindable(host: str, token: str) -> None:
    """Refuse a wider bind unless a usable token is configured.

    Raised at startup, deliberately, rather than serving one request first.
    """
    if is_loopback(host):
        return
    if not token:
        raise RuntimeError(
            f"refusing to bind {host}: this service has no authentication of its "
            "own, and the ledger, review queue and override controls are open to "
            "whoever reaches the port. Set DASHBOARD_TOKEN to serve a wider "
            "interface, or stay on loopback (127.0.0.1) and reach it through a "
            "tunnel that authenticates for you."
        )
    if len(token) < MINIMUM_TOKEN_LENGTH:
        raise RuntimeError(
            f"refusing to bind {host}: DASHBOARD_TOKEN is shorter than "
            f"{MINIMUM_TOKEN_LENGTH} characters, which is not a credential. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )


def _offered(request: Request) -> str:
    """The token the caller presented, from a header or a cookie.

    A query string is deliberately not read: URLs end up in logs, proxy
    records and browser history, and a credential in one of those outlives
    every attempt to remove it.
    """
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-dashboard-token", "") or request.cookies.get("dashboard_token", "")


def check(request: Request, token: str) -> None:
    """Refuse anything that cannot prove it holds the token.

    With no token configured the service is loopback-only (`assert_bindable`
    has already enforced that), so every caller is already on this machine and
    there is nothing to check.
    """
    if not token:
        return
    if request.url.path in OPEN_PATHS:
        return
    if not hmac.compare_digest(_offered(request), token):
        # The same answer either way: which of "absent" or "wrong" it was is
        # information an attacker does not need.
        raise HTTPException(401, "authentication required")
