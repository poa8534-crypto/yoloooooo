"""A running audit has to be reachable by name, not by identifier.

The page listed each started job as `2681a175 — complete` in a grey box, three
lines deep and not clickable. The operator had asked for those audits by
concept name, and the only way back to what one produced was to find it again
in a different strip further down the page.

A card needs the concept's title, the game it is about and the niche it was
drafted for. All three already exist on the Hunter proposal the job points at,
so the job payload carries them. Nothing here invents a label: a job whose
proposal or candidate has gone reports empty fields and the page falls back to
the identifier it does have.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import scout_queue
from app.models import Candidate, Observation


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


@pytest.fixture
def seeded(session_factory):
    from tests.test_deep_research import seed

    run_id, candidate_id, _artifact, proposal_id = seed(session_factory)
    with session_factory() as db:
        # The display name is an observation pointing into a hashed artifact,
        # which is the only place a candidate's name is allowed to come from.
        name = db.query(Observation).filter(Observation.metric == "roblox_name").first()
        candidate = db.get(Candidate, candidate_id)
        candidate.display_name_observation_id = name.id if name else None
        db.commit()
    return {"run_id": run_id, "candidate_id": candidate_id, "proposal_id": proposal_id}


def job(seeded, **overrides):
    return {"id": "job-1", "status": "running", "candidate_id": seeded["candidate_id"],
            "proposal_id": seeded["proposal_id"], "operation": "audit_idea",
            "audit_id": None, "error": None, **overrides}


def test_a_job_is_named_by_the_concept_it_is_auditing(session_factory, seeded):
    with session_factory() as db:
        labelled, = scout_queue.label(db, [job(seeded)])

    assert labelled["concept_title"] == "Lantern Garden"
    assert labelled["core_loop"].startswith("Plant strange seeds")
    assert labelled["niche"] == "cozy farming"


def test_the_game_name_comes_from_the_observation_not_from_anywhere_else(session_factory,
                                                                        seeded):
    with session_factory() as db:
        labelled, = scout_queue.label(db, [job(seeded)])

    assert labelled["game_name"] == "Evidence Garden"
    assert labelled["universe_id"] == "77"


def test_an_unnamed_candidate_stays_unnamed(session_factory, seeded):
    """A placeholder name on a card that everything else on the page traces
    back to a hashed artifact would be the one unsourced string on screen."""
    with session_factory() as db:
        db.get(Candidate, seeded["candidate_id"]).display_name_observation_id = None
        db.commit()
        labelled, = scout_queue.label(db, [job(seeded)])

    assert labelled["game_name"] == ""


def test_a_job_with_no_proposal_reports_no_title_rather_than_a_guess(session_factory,
                                                                    seeded):
    """`analyze_game` has no Hunter proposal to be named after."""
    with session_factory() as db:
        labelled, = scout_queue.label(
            db, [job(seeded, proposal_id=None, operation="analyze_game")])

    assert labelled["concept_title"] == ""
    assert labelled["core_loop"] == ""
    # The game is still known, because the candidate is.
    assert labelled["game_name"] == "Evidence Garden"


def test_a_job_whose_candidate_has_gone_is_still_returned(session_factory, seeded):
    """Dropping it would remove a card for work that really ran."""
    with session_factory() as db:
        labelled, = scout_queue.label(db, [job(seeded, candidate_id="missing",
                                               proposal_id=None)])

    assert labelled["id"] == "job-1"
    assert labelled["game_name"] == "" and labelled["universe_id"] == ""


def test_labelling_keeps_every_field_the_page_polls_on(session_factory, seeded):
    """The card renders status, error and audit id from the same object."""
    original = job(seeded, status="complete", audit_id="audit-9", error=None)

    with session_factory() as db:
        labelled, = scout_queue.label(db, [original])

    assert {key: labelled[key] for key in original} == original


def test_labelling_does_not_mutate_what_it_was_given(session_factory, seeded):
    original = job(seeded)
    before = dict(original)

    with session_factory() as db:
        scout_queue.label(db, [original])

    assert original == before, "the job manager's own dict was edited in place"


def test_the_job_list_endpoint_labels_what_it_returns(client, seeded, monkeypatch):
    from app import main as main_module

    class Jobs:
        def recent(self, limit=20):
            return [job(seeded, status="complete", audit_id="audit-9")]

    monkeypatch.setattr(main_module.app.state, "audit_jobs", Jobs(), raising=False)
    body = client.get("/api/audit-jobs").json()

    assert body["jobs"][0]["concept_title"] == "Lantern Garden"
    assert body["jobs"][0]["game_name"] == "Evidence Garden"


def test_starting_the_queue_returns_cards_that_can_be_rendered_immediately(
        client, seeded, monkeypatch):
    """Without this the strip shows identifiers until the next refresh lands."""
    from app import main as main_module

    class Jobs:
        def start(self, candidate_id, operation, proposal_id=None):
            return {"id": "job-1", "status": "queued", "candidate_id": candidate_id,
                    "proposal_id": proposal_id, "operation": operation}

    monkeypatch.setattr(main_module.app.state, "audit_jobs", Jobs(), raising=False)
    body = client.post("/api/scout/queue/run",
                       json={"proposal_ids": [seeded["proposal_id"]]}).json()

    assert body["started"][0]["concept_title"] == "Lantern Garden"
    assert body["started"][0]["niche"] == "cozy farming"


def test_the_polling_endpoint_stays_cheap(client, seeded, monkeypatch):
    """It runs every 1.5 seconds per job. The page keeps the label it was
    already given rather than paying for it on every poll."""
    from app import main as main_module

    class Jobs:
        def get(self, job_id):
            return job(seeded, id=job_id)

    monkeypatch.setattr(main_module.app.state, "audit_jobs", Jobs(), raising=False)
    body = client.get("/api/audit-jobs/job-1").json()

    assert "concept_title" not in body
