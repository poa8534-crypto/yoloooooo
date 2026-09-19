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


def test_reading_a_batch_result_needs_the_token_too(client):
    """Namespaced under /batches/ so it cannot be shadowed by the build record
    route, which takes a build id in the same position."""
    response = client.get("/api/builds/batches/some-batch")
    assert response.status_code == 400
    assert "token" in response.text


def test_a_build_that_does_not_exist_is_a_404(client):
    assert client.get("/api/builds/no-such-build").status_code == 404


def test_studio_reports_the_bridge_being_offline_rather_than_erroring(client):
    """Three states have three different fixes, so they are told apart."""
    response = client.get("/api/studio")

    assert response.status_code == 200
    body = response.json()
    assert body["bridge"] in ("offline", "online", "unknown")
    assert body["plugin_connected"] in (True, False)
    if body["bridge"] == "offline":
        assert "python -m app.bridge.run" in body["detail"]


# ---- the build orchestrator ------------------------------------------------

def test_a_build_of_an_unready_blueprint_fails_the_request(client, store, monkeypatch):
    """Rather than failing inside a background task nobody is watching yet."""
    from app.blueprint import api

    created = store.create(audit_id="audit-1", title="Lab")
    monkeypatch.setattr(api, "_store", lambda: (store, store.factory))

    response = client.post("/api/builds", json={"blueprint_id": created.id, "token": "t"})

    assert response.status_code == 409
    assert "ready" in response.text


def test_build_history_is_newest_first_and_carries_the_spec_it_came_from(session_factory):
    """Reproducibility: a build says which specification revision and content
    hash it was made from, so two builds can be told apart."""
    from app.blueprint.builds import BuildRecord, list_builds
    from app.blueprint.schemas import Blueprint, GameBuildSpecification, BlueprintConfig

    plan = Blueprint(id="bp", project_id="p", audit_id="a", title="Lab")
    spec = GameBuildSpecification(spec_id="s1", project_id="p", blueprint_id="bp",
                                  idea_id="a", title="Lab", config=BlueprintConfig(),
                                  revision=7, content_hash="abc123")
    for identifier in ("build-1", "build-2"):
        BuildRecord(session_factory, identifier).create(plan, spec)

    history = list_builds(session_factory)

    assert len(history) == 2
    assert history[0]["spec_revision"] == 7
    assert history[0]["content_hash"] == "abc123"


def test_a_build_moves_only_through_legal_states(session_factory):
    from app.blueprint.builds import BuildRecord
    from app.blueprint.schemas import Blueprint, BlueprintConfig, BuildStatus, GameBuildSpecification
    from app.blueprint.transitions import IllegalTransition

    plan = Blueprint(id="bp", project_id="p", audit_id="a", title="Lab")
    spec = GameBuildSpecification(spec_id="s", project_id="p", blueprint_id="bp",
                                  idea_id="a", title="Lab", config=BlueprintConfig())
    record = BuildRecord(session_factory, "build-x")
    record.create(plan, spec)

    record.move(BuildStatus.PLANNING)
    with pytest.raises(IllegalTransition):
        record.move(BuildStatus.SUCCEEDED)


def test_every_recorded_event_came_from_something_that_happened(session_factory):
    """Section 28: no log lines written to make the UI look alive."""
    from app.blueprint.builds import BuildRecord
    from app.blueprint.schemas import Blueprint, BlueprintConfig, BuildStatus, GameBuildSpecification

    plan = Blueprint(id="bp", project_id="p", audit_id="a", title="Lab")
    spec = GameBuildSpecification(spec_id="s", project_id="p", blueprint_id="bp",
                                  idea_id="a", title="Lab", config=BlueprintConfig())
    record = BuildRecord(session_factory, "build-y")
    assert record.create(plan, spec)["events"] == []

    record.move(BuildStatus.PLANNING, "working it out")

    events = record.read()["events"]
    assert [event["stage"] for event in events] == ["planning"]
    assert events[0]["detail"] == "working it out"


def test_files_are_read_from_the_commit_the_gate_accepted(tmp_path):
    """Not from the worktree, which is deleted when a run ends -- and the commit
    is the thing six checks were run against."""
    import subprocess

    from app.blueprint.builds import files_on_branch

    repo = tmp_path / "game"
    (repo / "src" / "server").mkdir(parents=True)
    (repo / "src" / "server" / "WaveService.luau").write_text("--!strict\nreturn {}\n",
                                                              encoding="utf-8")
    (repo / "notes.txt").write_text("not luau", encoding="utf-8")
    for command in (["init", "-b", "main"], ["add", "-A"],
                    ["-c", "user.name=t", "-c", "user.email=t@t.invalid",
                     "commit", "-m", "first"]):
        subprocess.run(["git", *command], cwd=repo, capture_output=True, check=True)

    found = files_on_branch(repo, "main")

    assert list(found) == ["src/server/WaveService.luau"]
    assert found["src/server/WaveService.luau"].startswith("--!strict")


def test_a_branch_that_does_not_exist_yields_nothing_rather_than_raising(tmp_path):
    import subprocess

    from app.blueprint.builds import files_on_branch

    repo = tmp_path / "game"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True, check=True)

    assert files_on_branch(repo, "no-such-branch") == {}


def test_a_build_with_nothing_to_generate_can_still_reach_studio(session_factory):
    """The state machine forbade its own legal path.

    Every system in the specification already being in the project is the
    ordinary state of a re-sync: planning finds nothing to write and the build
    goes straight to validating what is there. That move was not in the table,
    so a finished project could not be pushed into Studio at all -- the build
    died with "a build cannot go from planning to validating".
    """
    from app.blueprint.builds import BuildRecord
    from app.blueprint.schemas import Blueprint, BlueprintConfig, BuildStatus, GameBuildSpecification

    plan = Blueprint(id="bp", project_id="p", audit_id="a", title="Lab")
    spec = GameBuildSpecification(spec_id="s", project_id="p", blueprint_id="bp",
                                  idea_id="a", title="Lab", config=BlueprintConfig())
    record = BuildRecord(session_factory, "build-resync")
    record.create(plan, spec)

    record.move(BuildStatus.PLANNING)
    record.move(BuildStatus.VALIDATING)
    record.move(BuildStatus.WAITING_FOR_STUDIO)

    assert record.read()["status"] == BuildStatus.WAITING_FOR_STUDIO.value


def test_a_sync_without_a_playtest_can_finish(session_factory):
    """The other half of the same gap.

    A build asked not to start a test session applies its operations and is
    done, but BUILDING could only go to PLAYTESTING or PARTIAL -- so a sync
    with play=False did every piece of real work and then died on "a build
    cannot go from building to succeeded", with the operations already applied
    in Studio and the record saying the build failed.
    """
    from app.blueprint.builds import BuildRecord
    from app.blueprint.schemas import Blueprint, BlueprintConfig, BuildStatus, GameBuildSpecification

    plan = Blueprint(id="bp", project_id="p", audit_id="a", title="Lab")
    spec = GameBuildSpecification(spec_id="s", project_id="p", blueprint_id="bp",
                                  idea_id="a", title="Lab", config=BlueprintConfig())
    record = BuildRecord(session_factory, "build-noplay")
    record.create(plan, spec)

    for status in (BuildStatus.PLANNING, BuildStatus.VALIDATING,
                   BuildStatus.WAITING_FOR_STUDIO, BuildStatus.SYNCING,
                   BuildStatus.BUILDING, BuildStatus.SUCCEEDED):
        record.move(status)

    assert record.read()["status"] == BuildStatus.SUCCEEDED.value


def test_build_history_counts_outcomes_rather_than_shipping_every_event():
    """A list of builds is read to choose between them.

    The counts are the backend's because a number worked out in the browser as
    well can disagree with the one on the build, and the full record carries
    every attempt of every system -- megabytes nobody reading a list needs.
    """
    from app.blueprint.api import _summary

    row = _summary({
        "id": "build-1", "blueprint_id": "bp", "title": "Checkpoint Ascent",
        "status": "succeeded", "spec_revision": 3, "content_hash": "aa11",
        "created_at": "2026-09-16T10:00:00+00:00",
        "completed_at": "2026-09-16T10:42:00+00:00",
        "systems": {
            "TowerService": {"status": "built", "attempts": 1},
            "DashService": {"status": "built", "attempts": 2},
            "HazardService": {"status": "refused", "attempts": 3},
        },
    })

    assert row["systems_attempted"] == 3
    assert row["systems_built"] == 2
    assert row["systems_refused"] == 1, "a refusal is the thing worth seeing in history"
    assert row["attempts_spent"] == 6
    assert row["duration_seconds"] == 2520
    assert "events" not in row and "systems" not in row


def test_a_build_still_running_has_no_duration_rather_than_a_guessed_one():
    from app.blueprint.api import _summary

    row = _summary({
        "id": "build-2", "status": "generating",
        "created_at": "2026-09-16T10:00:00+00:00", "completed_at": None,
        "systems": {"TowerService": {"status": "built", "attempts": 1}},
    })

    assert row["duration_seconds"] is None


class _UsageDb:
    """The usage rows, without a database. Keyed by day, as the recorder writes
    them, so the report is tested against the shape it actually reads."""

    def __init__(self, days: dict):
        self._days = days

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, _model, key: str):
        from app.engineer.runs import USAGE_PREFIX

        day = key[len(USAGE_PREFIX):]
        value = self._days.get(day)
        if value is None:
            return None
        return type("Row", (), {"value_json": value})()


def test_usage_reports_what_was_spent_and_no_remaining_without_a_limit():
    """No provider we use returns a remaining quota.

    Gemini answers 429 when it is gone; `agy` refuses when the subscription is
    spent. So with nothing configured the report says what was spent and leaves
    remaining null, rather than drawing a bar against an invented ceiling.
    """
    from app.usage import report

    answer = report(_UsageDb({
        "2026-09-19": {"key-a": {"calls": 40, "total_tokens": 900,
                                 "rate_limited": 3, "models": {"flash": 40},
                                 "hours": {"2026-09-19T06": {"calls": 9, "total_tokens": 120}}}},
    }))

    assert answer["week"]["calls"] == 40
    assert answer["week"]["rate_limited"] == 3, "a 429 is the quota itself answering"
    assert answer["week"]["limit"] is None
    assert answer["week"]["remaining"] is None
    assert answer["limits_configured"] is False


def test_usage_reports_remaining_against_a_limit_that_was_configured():
    from app.usage import report

    answer = report(_UsageDb({
        "2026-09-19": {"key-a": {"calls": 700, "models": {}, "hours": {}}},
    }), weekly_limit=1000)

    assert answer["week"]["remaining"] == 300
    assert answer["week"]["percent_used"] == 70
    assert answer["limits_configured"] is True


def test_an_hour_with_nothing_recorded_is_not_reported_as_an_hour_with_no_calls():
    """A build process started before hourly recording existed writes daily
    totals only, and "0 calls this hour" would be a confident wrong answer."""
    from app.usage import report

    answer = report(_UsageDb({
        "2026-09-19": {"key-a": {"calls": 40, "models": {}}},
    }))

    assert answer["hour"]["measured"] is False
    assert answer["week"]["measured"] is True
