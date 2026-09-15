"""Authentication, and the rule that a wider bind requires it.

This is the file where a mistake is worst: a hole here exposes the whole
ledger, the review queue and the override controls to anyone who finds the
port. So the guards are about the failure modes, not the happy path -- a token
that is absent, wrong, short, leaked into a URL, or bypassed by asking for the
frontend instead of the API.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.requests import Request

from app import access


def request(path="/api/health", headers=None, cookies=None):
    raw = dict(headers or {})
    if cookies:
        raw["cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    scope = {"type": "http", "method": "GET", "path": path, "query_string": b"",
             "headers": Headers(raw).raw, "scheme": "http",
             "server": ("testserver", 80), "root_path": ""}
    return Request(scope)


TOKEN = "a-long-enough-token-for-real-use-000"


# --- binding ---------------------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_needs_no_credential(host):
    assert access.assert_bindable(host, "") is None


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.4", "example.com", ""])
def test_a_wider_bind_without_a_token_is_refused(host):
    """The whole point. A deployment that forgets the token must fail at
    startup, not serve one request first."""
    with pytest.raises(RuntimeError, match="no authentication"):
        access.assert_bindable(host, "")


def test_a_token_unlocks_a_wider_bind():
    assert access.assert_bindable("0.0.0.0", TOKEN) is None


def test_a_short_token_is_refused_rather_than_accepted_as_a_credential():
    """"admin" is not a password, and accepting it would make the guard a
    formality that reports itself as satisfied."""
    with pytest.raises(RuntimeError, match="not a credential"):
        access.assert_bindable("0.0.0.0", "admin")


def test_the_refusal_never_prints_the_token():
    with pytest.raises(RuntimeError) as caught:
        access.assert_bindable("0.0.0.0", "short")
    assert "short" not in str(caught.value).replace("shorter", "")


# --- checking requests -----------------------------------------------------

def test_with_no_token_configured_nothing_is_checked():
    """Loopback-only is already enforced at bind time, so every caller is on
    this machine and there is nothing to prove."""
    assert access.check(request(), "") is None


def test_a_request_with_no_credential_is_refused():
    with pytest.raises(HTTPException) as caught:
        access.check(request(), TOKEN)
    assert caught.value.status_code == 401


def test_a_wrong_credential_is_refused():
    with pytest.raises(HTTPException):
        access.check(request(headers={"authorization": "Bearer wrong-token-entirely"}), TOKEN)


def test_a_wrong_and_a_missing_credential_give_the_same_answer():
    """Telling an attacker which of the two they got is free information."""
    with pytest.raises(HTTPException) as absent:
        access.check(request(), TOKEN)
    with pytest.raises(HTTPException) as wrong:
        access.check(request(headers={"authorization": "Bearer nope-nope-nope"}), TOKEN)
    assert absent.value.detail == wrong.value.detail
    assert absent.value.status_code == wrong.value.status_code


@pytest.mark.parametrize("headers", [
    {"authorization": f"Bearer {TOKEN}"},
    {"authorization": f"bearer {TOKEN}"},
    {"x-dashboard-token": TOKEN},
])
def test_the_right_credential_is_accepted_however_it_is_presented(headers):
    assert access.check(request(headers=headers), TOKEN) is None


def test_a_cookie_works_so_the_browser_can_hold_it():
    assert access.check(request(cookies={"dashboard_token": TOKEN}), TOKEN) is None


def test_a_token_in_the_query_string_is_not_accepted():
    """URLs reach logs, proxy records and browser history, and a credential in
    one of those outlives every attempt to remove it."""
    scope = {"type": "http", "method": "GET", "path": "/api/health",
             "query_string": f"token={TOKEN}".encode(), "headers": Headers({}).raw,
             "scheme": "http", "server": ("testserver", 80), "root_path": ""}
    with pytest.raises(HTTPException):
        access.check(Request(scope), TOKEN)


def test_only_the_liveness_probe_is_open():
    """A platform health check holds no token, and its failure would take the
    service down. Nothing else gets that exemption."""
    assert access.check(request(path="/api/health/live"), TOKEN) is None
    for path in ("/api/health", "/", "/index.html", "/api/research-runs",
                 "/api/evidence/anything"):
        with pytest.raises(HTTPException):
            access.check(request(path=path), TOKEN)


def test_the_frontend_itself_is_behind_the_credential():
    """Gating only /api would serve the application shell to anyone and leave
    the token as the only thing between them and a running agent."""
    for path in ("/", "/index.html", "/assets/index.js"):
        with pytest.raises(HTTPException):
            access.check(request(path=path), TOKEN)


def test_a_prefix_of_the_token_is_not_enough():
    with pytest.raises(HTTPException):
        access.check(request(headers={"authorization": f"Bearer {TOKEN[:-1]}"}), TOKEN)


def test_comparison_does_not_leak_through_an_exception_message():
    with pytest.raises(HTTPException) as caught:
        access.check(request(headers={"x-dashboard-token": "guess"}), TOKEN)
    assert TOKEN not in str(caught.value.detail)
    assert "guess" not in str(caught.value.detail)
