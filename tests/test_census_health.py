"""A stopped sampler has to be visible, because it fails silently by design.

`sample_market` swallows its own errors so one bad census cannot stop the
next, and an interval job that never fires logs nothing at all. That is the
right behaviour for the sampler and the wrong behaviour for the operator: it
means a sampler that stopped a week ago looks exactly like one that is
working, and the first symptom is that every rate quietly reports insufficient
evidence forever.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.evidence import record_artifact
from app.models import MarketSample


@pytest.fixture
def client(session_factory, settings, monkeypatch):
    from app import main as main_module

    def override():
        with session_factory() as db:
            yield db

    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    main_module.app.dependency_overrides[main_module.get_db] = override
    yield TestClient(main_module.app)
    main_module.app.dependency_overrides.clear()


def census_at(session_factory, moment):
    with session_factory() as db:
        artifact = record_artifact(
            db, url="https://apis.roblox.com/explore-api/v1/get-sorts",
            retrieval_method="scheduled_market_sample", content_type="application/json",
            payload={"sorts": []}, source_tier="primary", owner="roblox.com")
        db.add(MarketSample(artifact_id=artifact.id, captured_at=moment,
                            sort_id="up-and-coming", rank=0, universe_id="1",
                            name="Game", player_count=100, up_votes=9, down_votes=1,
                            genre="Simulation"))
        db.commit()


def row(client, name="Market census sampler"):
    body = client.get("/api/health").json()
    return next((entry for entry in body["dependencies"] if entry["name"] == name), None)


def test_the_sampler_appears_on_the_health_page_at_all(client):
    assert row(client) is not None, "a silent component with no health row is unmonitored"


def test_a_sampler_that_has_never_run_is_not_reported_as_healthy(client):
    reading = row(client)

    assert reading["state"] == "never_sampled"
    assert "No market census" in reading["detail"]


def test_a_recent_census_reads_as_working(client, session_factory):
    census_at(session_factory, datetime.now(UTC) - timedelta(minutes=5))

    reading = row(client)

    assert reading["state"] == "last_request_succeeded"
    assert "5 minutes ago" in reading["detail"], "the age is the useful part"


def test_a_census_older_than_several_intervals_reads_as_stale(client, session_factory,
                                                              settings, monkeypatch):
    """This is the case the whole row exists for: the last sample succeeded, so
    nothing recorded a failure, and the sampler has been dead ever since."""
    monkeypatch.setattr(settings, "market_sample_minutes", 30, raising=False)
    census_at(session_factory, datetime.now(UTC) - timedelta(hours=6))

    reading = row(client)

    assert reading["state"] == "stale", (
        "a six-hour-old census under a thirty-minute schedule read as healthy"
    )


def test_a_census_just_inside_the_allowance_is_not_called_stale(client, session_factory,
                                                                settings, monkeypatch):
    """The threshold is three intervals, which tolerates one missed sample and
    a slow one without crying wolf."""
    monkeypatch.setattr(settings, "market_sample_minutes", 30, raising=False)
    census_at(session_factory, datetime.now(UTC) - timedelta(minutes=80))

    assert row(client)["state"] == "last_request_succeeded"


def test_disabling_the_sampler_reads_as_not_configured_rather_than_broken(
        client, session_factory, settings, monkeypatch):
    monkeypatch.setattr(settings, "roblox_charts_enabled", False, raising=False)
    census_at(session_factory, datetime.now(UTC) - timedelta(days=9))

    assert row(client)["state"] == "not_configured"
