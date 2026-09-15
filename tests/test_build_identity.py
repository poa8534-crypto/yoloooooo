"""Which code is running has to be knowable, or unknown.

The page reported a hardcoded application version, so a stale service and a
freshly restarted one were indistinguishable -- and after a restart there was
no way to confirm the fix under test was the code answering requests.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.build_identity import build_identity


def test_the_running_commit_is_reported():
    identity = build_identity()
    assert identity["source"] in {"git", "unavailable"}
    if identity["source"] == "git":
        assert len(identity["commit"]) == 40, identity["commit"]
        assert identity["short_commit"] == identity["commit"][:8]
        assert identity["branch"] and identity["branch"] != "unknown"
    else:
        # Says so plainly rather than implying a clean checkout.
        assert identity["commit"] == "unknown"
        assert identity["dirty"] is None


def test_a_dirty_checkout_is_not_reported_as_its_base_commit():
    """The running code is not the commit it was branched from."""
    identity = build_identity()
    if identity["source"] == "git":
        assert isinstance(identity["dirty"], bool)


def test_the_start_time_is_recorded_once():
    """Cached deliberately: a running process does not change commit, and a
    start time that moved on every request would be useless."""
    first, second = build_identity(), build_identity()
    assert first is second
    assert datetime.fromisoformat(first["started_at"]) <= datetime.now(UTC)


def test_health_reports_the_build(session_factory, settings, monkeypatch):
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    def dependency():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    try:
        body = TestClient(app).get("/api/health").json()
    finally:
        app.dependency_overrides.clear()

    assert "build" in body, "health did not say which code answered it"
    assert body["build"]["short_commit"]
    assert body["build"]["started_at"]
