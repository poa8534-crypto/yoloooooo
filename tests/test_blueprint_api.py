"""The Blueprint stage over HTTP.

The frontend is an interface to real capabilities, so the tests that matter
most are the ones about telling the truth: a rejected feature stays rejected
through the API, readiness is counted rather than claimed, a contradictory
system selection is refused rather than accepted, and the build endpoint says
it is not implemented instead of returning something that looks like progress.
"""

from __future__ import annotations

import pytest

from app.blueprint.schemas import Blueprint, BuildStatus, FeatureSuggestion, GameSystem, SystemLayer
from app.blueprint.store import BlueprintStore

CRITERION = "Spending more resources than the player holds is refused and changes nothing."


@pytest.fixture
def store(session_factory):
    return BlueprintStore(session_factory)


def suggestion(identifier: str, **overrides) -> FeatureSuggestion:
    fields = {"id": identifier, "title": f"Feature {identifier}",
              "description": "something concrete about this game",
              "reason": "why it helps this game specifically"}
    return FeatureSuggestion(**{**fields, **overrides})


def system(name: str, **overrides) -> GameSystem:
    fields = {"id": name.lower(), "name": name, "layer": SystemLayer.SERVER,
              "purpose": "Track what each survivor is carrying.",
              "acceptance_criteria": [CRITERION]}
    return GameSystem(**{**fields, **overrides})


# ---- persistence -----------------------------------------------------------

def test_a_blueprint_survives_a_round_trip(store):
    created = store.create(audit_id="audit-1", title="Zombie Quarantine Lab")

    read = store.get(created.id)

    assert read.title == "Zombie Quarantine Lab"
    assert read.audit_id == "audit-1"
    assert read.status is BuildStatus.DRAFT


def test_every_save_bumps_the_revision(store):
    created = store.create(audit_id="audit-1", title="Lab")

    once = store.save(created.model_copy(update={"user_intent": "a bunker"}))
    twice = store.save(once.model_copy(update={"user_intent": "a bunker, co-op"}))

    assert (created.revision, once.revision, twice.revision) == (1, 2, 3)


def test_an_earlier_revision_can_still_be_read(store):
    """A build is traced to the revision it was compiled from, so "what did I
    approve?" has to survive later edits."""
    created = store.create(audit_id="audit-1", title="Lab")
    store.save(created.model_copy(update={"user_intent": "first thought"}))
    store.save(store.get(created.id).model_copy(update={"user_intent": "second thought"}))

    assert store.revision(created.id, 1).user_intent == ""
    assert store.revision(created.id, 2).user_intent == "first thought"
    assert store.get(created.id).user_intent == "second thought"


def test_saving_through_an_illegal_transition_is_refused(store):
    from app.blueprint.transitions import IllegalTransition

    created = store.create(audit_id="audit-1", title="Lab")

    with pytest.raises(IllegalTransition):
        store.save(created, status=BuildStatus.PLAYTESTING)


def test_proceeding_twice_on_one_idea_finds_the_work_in_progress(store):
    """Otherwise a second Proceed silently starts a new blueprint beside the
    one the person was already filling in."""
    first = store.create(audit_id="audit-1", title="Lab")
    store.create(audit_id="audit-2", title="Something else")

    assert store.for_audit("audit-1").id == first.id
    assert store.for_audit("audit-never") is None


# ---- the view the frontend renders -----------------------------------------

def view_of(blueprint: Blueprint) -> dict:
    from app.blueprint.api import _view

    return _view(blueprint)


def a_blueprint(**overrides) -> Blueprint:
    fields = {"id": "bp", "project_id": "proj", "audit_id": "audit-1", "title": "Lab",
              "user_intent": "A research bunker.", "systems": [system("ResourceService")]}
    return Blueprint(**{**fields, **overrides})


def test_the_view_counts_what_the_person_chose():
    view = view_of(a_blueprint(suggestions=[
        suggestion("a", selected=True), suggestion("b", selected=False), suggestion("c")]))

    assert view["counts"] == {"selected_features": 1, "rejected_features": 1,
                              "undecided_features": 1, "systems": 1}


def test_the_view_reports_readiness_that_was_counted_not_claimed():
    view = view_of(a_blueprint(suggestions=[suggestion("a")]))  # undecided

    assert view["readiness"]["ready"] is False
    assert any(item["key"] == "features_decided" for item in view["readiness"]["missing"])
    assert 0 < view["readiness"]["percent"] < 100


def test_the_view_offers_only_controls_that_make_sense_here():
    view = view_of(a_blueprint(status=BuildStatus.READY_TO_BUILD))

    assert "build" in view["controls"]
    assert "stop_playtest" not in view["controls"]


def test_readiness_is_recomputed_rather_than_remembered(store):
    """A stored number can disagree with the blueprint it describes, and the
    disagreement is invisible."""
    created = store.create(audit_id="audit-1", title="Lab")
    filled = store.save(created.model_copy(update={
        "user_intent": "a bunker", "systems": [system("ResourceService")]}))

    assert view_of(filled)["readiness"]["ready"] is True

    broken = store.save(filled.model_copy(update={"systems": []}))
    assert view_of(broken)["readiness"]["ready"] is False


# ---- what the API refuses --------------------------------------------------

@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_a_blueprint_that_does_not_exist_is_a_404(client):
    assert client.get("/api/blueprints/nope").status_code == 404


def test_proceeding_on_an_idea_with_no_design_is_refused(client):
    response = client.post("/api/blueprints/proceed", json={"audit_id": "no-such-audit"})

    assert response.status_code == 400
    assert "audit" in response.text.lower()


def test_building_an_unknown_blueprint_is_a_404(client):
    response = client.post("/api/builds", json={"blueprint_id": "nope", "token": "t"})
    assert response.status_code == 404


def test_a_build_needs_a_bridge_token(client):
    """Without it the bridge refuses, and a build that fails at the last step
    wastes everything before it."""
    response = client.post("/api/builds", json={"blueprint_id": "bp"})
    assert response.status_code == 422


def test_reading_a_result_needs_the_token_too(client):
    response = client.get("/api/builds/some-batch")
    assert response.status_code == 400
    assert "token" in response.text


def test_studio_reports_the_bridge_being_offline_rather_than_erroring(client):
    """Three states have three different fixes, so they are told apart."""
    response = client.get("/api/studio")

    assert response.status_code == 200
    body = response.json()
    assert body["bridge"] in ("offline", "online", "unknown")
    assert body["plugin_connected"] in (True, False)
    if body["bridge"] == "offline":
        assert "python -m app.bridge.run" in body["detail"]
