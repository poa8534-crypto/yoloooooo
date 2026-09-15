"""Configuration is not health.

The page reported `tavily_configured: true` and rendered it as "Authenticated".
An expired key, a revoked key, and a provider that had been down all morning
all produced exactly that. These tests keep the two facts apart.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app import dependency_health
from app.connectors import ConnectorError, Connectors
from app.models import SystemState


class Meter:
    """Stands in for the quota meter, which is where the factory comes from."""

    def __init__(self, factory):
        self.factory = factory

    def reserve(self, provider, cost):
        return None


def test_a_configured_dependency_nobody_has_called_is_unknown():
    described = dependency_health.describe("Tavily search", configured=True, observed=None)
    assert described["state"] == "unknown", described
    assert described["configured"] is True
    assert described["checked_at"] is None
    assert "nothing has called it" in described["detail"]


def test_an_unconfigured_dependency_is_not_reported_as_failing():
    described = dependency_health.describe("Tavily search", configured=False, observed=None)
    assert described["state"] == "not_configured"


def test_a_probe_result_outranks_an_old_observation():
    """A local server can be probed for free, so the probe is the better fact."""
    stale = {"state": "last_request_failed", "at": "2026-01-01T00:00:00+00:00", "detail": "old"}
    assert dependency_health.describe("Ollama", configured=True, observed=stale,
                                      reachable=True)["state"] == "reachable"
    assert dependency_health.describe("Ollama", configured=True, observed=stale,
                                      reachable=False)["state"] == "unreachable"


def test_an_observation_carries_when_it_was_made():
    observed = {"state": "last_request_succeeded", "at": "2026-09-15T10:00:00+00:00", "detail": "fine"}
    described = dependency_health.describe("YouTube Data API", configured=True, observed=observed)
    assert described["state"] == "last_request_succeeded"
    assert described["checked_at"] == "2026-09-15T10:00:00+00:00"


@pytest.mark.parametrize("url,expected", [
    ("https://api.tavily.com/search", "tavily"),
    ("https://www.googleapis.com/youtube/v3/videos", "youtube"),
    ("https://games.roblox.com/v1/games", "roblox"),
    ("https://example.com/anything", None),
])
def test_a_url_is_attributed_to_the_right_provider(url, expected):
    assert dependency_health.provider_for(url) == expected


def test_an_unknown_host_is_not_filed_under_a_guess(session_factory):
    dependency_health.record(session_factory, "https://example.com/x", ok=True)
    with session_factory() as db:
        assert dependency_health.observations(db) == {}


@pytest.mark.asyncio
async def test_a_failed_request_is_observed_as_a_failure(session_factory, settings):
    """The whole point: a key can be configured and the provider still broken."""
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "bad key"}))
    connectors = Connectors(settings, httpx.AsyncClient(transport=transport),
                            quota_meter=Meter(session_factory))
    with pytest.raises(ConnectorError):
        await connectors._json("GET", "https://api.tavily.com/search")
    await connectors.close()

    with session_factory() as db:
        seen = dependency_health.observations(db)
    assert seen["tavily"]["state"] == "last_request_failed", seen
    assert seen["tavily"]["at"]

    described = dependency_health.describe("Tavily search", configured=True, observed=seen["tavily"])
    assert described["configured"] is True
    assert described["state"] == "last_request_failed", "a configured but broken key read as healthy"


@pytest.mark.asyncio
async def test_a_successful_request_is_observed_as_success(session_factory, settings):
    """Positive control: recording every call as a failure would also pass above."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    connectors = Connectors(settings, httpx.AsyncClient(transport=transport),
                            quota_meter=Meter(session_factory))
    await connectors._json("GET", "https://api.tavily.com/search")
    await connectors.close()

    with session_factory() as db:
        assert dependency_health.observations(db)["tavily"]["state"] == "last_request_succeeded"


@pytest.mark.asyncio
async def test_recording_health_never_breaks_a_capture(settings):
    """A health note is not worth losing a captured result over."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))

    class Broken:
        factory = staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("no database")))

        def reserve(self, provider, cost):
            return None

    connectors = Connectors(settings, httpx.AsyncClient(transport=transport), quota_meter=Broken())
    result = await connectors._json("GET", "https://api.tavily.com/search")
    await connectors.close()
    assert result.payload == {"ok": True}


def test_a_later_observation_replaces_an_earlier_one(session_factory):
    dependency_health.record(session_factory, "https://api.tavily.com/search", ok=False, detail="first")
    dependency_health.record(session_factory, "https://api.tavily.com/search", ok=True)
    with session_factory() as db:
        seen = dependency_health.observations(db)
        assert seen["tavily"]["state"] == "last_request_succeeded"
        assert len(db.query(SystemState).filter(SystemState.key.like("dependency:%")).all()) == 1
    assert datetime.fromisoformat(seen["tavily"]["at"]) <= datetime.now(UTC)


# --- Collection continuity: what was captured, not what is promised -------


def test_continuity_reports_captured_days_not_a_promised_matrix(session_factory, settings, monkeypatch):
    """The page showed a "dataset readiness" bar driven by complete_clusters /
    required_clusters. Niche-cluster calibration is not implemented, so that
    bar was always zero out of two hundred for a pipeline that does not exist.
    """
    from datetime import timedelta

    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.evidence import add_json_observation, record_artifact
    from app.main import app
    from tests.test_deep_research import seed

    _, candidate_id, *_ = seed(session_factory)
    with session_factory() as db:
        base = datetime.now(UTC)
        # Two consecutive days, then a gap, then one more.
        for offset in (5, 4, 1):
            artifact = record_artifact(
                db, url=f"https://games.roblox.com/v1/games?d={offset}", retrieval_method="test",
                content_type="application/json", payload={"data": [{"playing": 7}]},
                source_tier="primary", captured_at=base - timedelta(days=offset),
            )
            # observed_at follows the artifact's capture time; the ledger is
            # append-only, so it cannot be adjusted afterwards.
            add_json_observation(db, artifact=artifact, candidate_id=candidate_id,
                                 metric="roblox_playing", pointer="/data/0/playing", unit="players",
                                 observed_at=base - timedelta(days=offset))
        db.commit()

    def dependency():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    try:
        body = TestClient(app).get("/api/collection/continuity").json()
    finally:
        app.dependency_overrides.clear()

    assert body["niche_cluster_calibration"] == "not_implemented"
    assert body["captured_days"] == len(body["days"]) >= 3
    # The two adjacent days form a streak; the gap is not interpolated across.
    assert body["longest_consecutive_days"] == 2, body["days"]
    assert body["entities_tracked"] >= 1
    assert body["first_capture"] < body["last_capture"]


def test_continuity_is_empty_rather_than_optimistic_when_nothing_is_captured(session_factory, settings):
    """Positive control: an empty ledger must not report a streak."""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    def dependency():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    try:
        body = TestClient(app).get("/api/collection/continuity").json()
    finally:
        app.dependency_overrides.clear()

    assert body["days"] == []
    assert body["captured_days"] == 0
    assert body["longest_consecutive_days"] == 0
    assert body["first_capture"] is None and body["last_capture"] is None


@pytest.mark.asyncio
async def test_the_connector_marks_quota_exhaustion_as_planned(settings):
    """The flag has to be set where the refusal happens.

    Asserting on a ConnectorError built by hand proves nothing about the code
    path that raises it, which is how this survived a mutation.
    """
    from app.connectors import ConnectorError, Connectors
    from app.quotas import QuotaExceeded

    class Exhausted:
        factory = None

        def reserve(self, provider, cost):
            raise QuotaExceeded(f"{provider} configured daily allowance exhausted")

    connectors = Connectors(settings, httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))),
        quota_meter=Exhausted())
    with pytest.raises(ConnectorError) as caught:
        await connectors._json("GET", "https://api.tavily.com/search")
    await connectors.close()

    assert caught.value.planned is True, "an exhausted allowance was raised as a failure"


@pytest.mark.asyncio
async def test_a_transport_failure_is_not_marked_planned(settings):
    """Positive control for the pairing above."""
    from app.connectors import ConnectorError, Connectors

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    connectors = Connectors(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ConnectorError) as caught:
        await connectors._json("GET", "https://api.tavily.com/search")
    await connectors.close()

    assert caught.value.planned is False
