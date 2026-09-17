"""What the Engineering Agent page is allowed to show.

The page exists to make a build legible while it runs, which is exactly the
situation where an invented number is most convincing and least noticed. So
these tests are mostly about refusal: no duration for a system that has not
finished, no sync figures before a batch was sent, no accepted source for a
system the gate refused.
"""

from __future__ import annotations

import pytest

from app.blueprint.api import _explorer, _studio_location, _sync_state, _system_durations


def event(stage: str, detail: str, at: str, **extra) -> dict:
    return {"stage": stage, "detail": detail, "at": at, **extra}


# ---- measured durations ----------------------------------------------------

def test_a_system_is_timed_from_the_events_that_recorded_it():
    events = [
        event("generating", "ResearchService: asking the Engineer", "2026-09-17T10:00:00+00:00"),
        event("system_built", "ResearchService: accepted on engineer/x",
              "2026-09-17T10:02:08+00:00"),
    ]

    assert _system_durations(events) == {"ResearchService": 128.0}


def test_a_refused_system_is_still_timed():
    """Six attempts took fifteen minutes whether or not anything was kept, and
    hiding that would make the build look faster than it was."""
    events = [
        event("generating", "InfectedService: asking the Engineer", "2026-09-17T10:00:00+00:00"),
        event("system_refused", "InfectedService: All 6 attempts were refused",
              "2026-09-17T10:15:15+00:00"),
    ]

    assert _system_durations(events) == {"InfectedService": 915.0}


def test_a_system_still_running_has_no_duration():
    """The one that matters. Counting "how long it has been going" as a
    duration makes the average climb the longer you watch, so a build looks
    like it is slowing down when nothing has changed."""
    events = [
        event("generating", "WaveService: asking the Engineer", "2026-09-17T10:00:00+00:00"),
    ]

    assert _system_durations(events) == {}


def test_an_unparseable_timestamp_is_skipped_rather_than_guessed():
    events = [
        event("generating", "WaveService: asking the Engineer", "not a time"),
        event("system_built", "WaveService: accepted", "2026-09-17T10:02:00+00:00"),
    ]

    assert _system_durations(events) == {}


# ---- what Studio was actually sent -----------------------------------------

def test_nothing_is_reported_as_synced_before_a_batch_is_sent():
    state = _sync_state([event("generating", "A: asking the Engineer", "2026-09-17T10:00:00")])

    assert state["batch_id"] == ""
    assert state["operations"] == 0
    # None, not zero: "Studio applied none of them" and "Studio has not been
    # asked yet" are different, and zero would read as the first.
    assert state["applied"] is None


def test_the_sent_batch_and_what_studio_reported_are_both_kept():
    state = _sync_state([
        event("syncing", "21 operation(s) queued for Studio", "2026-09-17T10:20:00",
              batch_id="batch-7", operations=21),
        event("studio_result", "21 applied, 0 unchanged, 0 failed", "2026-09-17T10:20:30",
              result={"applied": 21, "skipped": 0, "failed_count": 0}),
    ])

    assert state["batch_id"] == "batch-7"
    assert state["operations"] == 21
    assert (state["applied"], state["skipped"], state["failed"]) == (21, 0, 0)


def test_a_failure_in_studio_is_carried_through_as_a_failure():
    state = _sync_state([
        event("syncing", "3 queued", "2026-09-17T10:20:00", batch_id="b", operations=3),
        event("studio_result", "1 applied, 0 unchanged, 2 failed", "2026-09-17T10:20:30",
              result={"applied": 1, "skipped": 0, "failed_count": 2}),
    ])

    assert state["failed"] == 2


# ---- the DataModel tree ----------------------------------------------------

def node(name: str, source: str, state: str) -> dict:
    location, script_class = _studio_location(source)
    return {"id": name, "state": state, "studio_path": location, "studio_class": script_class}


def test_the_explorer_nests_by_the_path_the_sync_would_use():
    rows = _explorer([
        node("ResearchService", "src/server/ResearchService.luau", "built"),
        node("HudController", "src/client/HudController.luau", "waiting"),
    ])

    names = {row["name"] for row in rows}
    assert names == {"ServerScriptService", "StarterPlayer"}
    server = next(row for row in rows if row["name"] == "ServerScriptService")
    assert server["children"][0]["name"] == "Server"
    assert server["children"][0]["children"][0]["name"] == "ResearchService"


def test_every_leaf_carries_the_state_of_the_system_it_came_from():
    """A folder of waiting modules must not look like a folder of applied
    ones: the tree is read as though it were Studio's own explorer."""
    rows = _explorer([
        node("ResearchService", "src/server/ResearchService.luau", "built"),
        node("InfectedService", "src/server/InfectedService.luau", "refused"),
    ])

    leaves = rows[0]["children"][0]["children"]
    assert {leaf["name"]: leaf["state"] for leaf in leaves} == {
        "ResearchService": "built", "InfectedService": "refused"}
    assert all(leaf["class"] == "ModuleScript" for leaf in leaves)


def test_a_file_that_maps_nowhere_is_left_out_rather_than_placed_somewhere():
    assert _explorer([node("Odd", "docs/notes.luau", "waiting")]) == []


# ---- the endpoints ---------------------------------------------------------

@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_the_graph_of_a_build_that_does_not_exist_is_a_404(client):
    assert client.get("/api/builds/no-such-build/graph").status_code == 404


def test_source_for_a_build_that_does_not_exist_is_a_404(client):
    assert client.get("/api/builds/nope/systems/AService/source").status_code == 404


def test_opening_a_script_needs_the_bridge_token(client):
    """It is a real operation over the real bridge, so it cannot be done
    anonymously any more than a build can."""
    response = client.post("/api/builds/nope/systems/AService/open", json={})

    assert response.status_code == 422


def test_source_is_refused_for_a_system_the_gate_never_accepted(session_factory, monkeypatch):
    """The last rejected attempt is not this system's source. Showing it in a
    tab labelled with the system's name would present refused code as built."""
    from app.blueprint import api
    from app.blueprint.builds import BuildRecord
    from app.blueprint.schemas import Blueprint, BlueprintConfig, GameBuildSpecification

    plan = Blueprint(id="bp", project_id="p", audit_id="a", title="Lab")
    spec = GameBuildSpecification(spec_id="s", project_id="p", blueprint_id="bp",
                                  idea_id="a", title="Lab", config=BlueprintConfig())
    record = BuildRecord(session_factory, "build-refused")
    record.create(plan, spec)
    record.update(systems={"InfectedService": {"status": "refused", "attempts": 6,
                                               "reason": "All 6 attempts were refused",
                                               "branch": "engineer/infected"}})
    monkeypatch.setattr(api, "SessionLocal", session_factory, raising=False)

    import app.db

    monkeypatch.setattr(app.db, "SessionLocal", session_factory)
    answer = api.system_source("build-refused", "InfectedService")

    assert answer["state"] == "refused"
    assert answer["source"] == ""
    assert answer["detail"] == "All 6 attempts were refused"
