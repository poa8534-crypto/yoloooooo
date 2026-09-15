"""Routing is automatic; running is not.

Meta Hunter drafts concepts and the Venture Scout audits them, and the two
were joined only by hand: reaching a concept meant opening the Idea Panel,
finding the right candidate and starting one audit. Concepts nobody happened
to open were never audited and nothing said so.

The queue closes that gap without taking the decision away. An audit spends
several minutes of a model that serves one request at a time, so a queue that
ran itself would be deciding how the next half hour of the machine is spent.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import scout_queue
from app.models import AuditRecord, Candidate, Proposal, ResearchRun


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
    """Two Hunter concepts with evidence, one of them already audited."""
    from tests.test_deep_research import seed

    run_id, candidate_id, _artifact_id, proposal_id = seed(session_factory)
    with session_factory() as db:
        second = Candidate(run_id=run_id, external_id="88")
        db.add(second)
        db.flush()
        other = Proposal(candidate_id=second.id, agent="meta_hunter",
                         payload={"concept_title": "Second concept",
                                  "core_loop": "Do the thing repeatedly."},
                         model_name="fake")
        db.add(other)
        db.commit()
        return {"run_id": run_id, "candidate_id": candidate_id,
                "proposal_id": proposal_id, "second_candidate": second.id,
                "second_proposal": other.id}


def test_every_unaudited_hunter_concept_reaches_the_queue_unasked(session_factory, seeded):
    with session_factory() as db:
        rows = scout_queue.pending(db)

    assert {row["proposal_id"] for row in rows} == {seeded["proposal_id"], seeded["second_proposal"]}


def test_an_audited_concept_leaves_the_queue(session_factory, seeded):
    with session_factory() as db:
        db.add(AuditRecord(candidate_id=seeded["candidate_id"],
                           proposal_id=seeded["proposal_id"], payload={"risks": []}))
        db.commit()
        rows = scout_queue.pending(db)

    assert [row["proposal_id"] for row in rows] == [seeded["second_proposal"]]


def test_a_concept_whose_game_is_already_being_audited_is_shown_not_hidden(session_factory,
                                                                          seeded):
    """Vanishing from the queue the moment something else touches the same game
    is how an operator loses track of what is actually waiting."""
    with session_factory() as db:
        rows = scout_queue.pending(db, active_candidates={seeded["candidate_id"]})

    blocked = next(row for row in rows if row["proposal_id"] == seeded["proposal_id"])
    assert blocked["available"] is False
    assert "already active" in blocked["unavailable_reason"]
    assert len(rows) == 2, "it was removed from the queue instead of being marked"


def test_a_concept_with_no_admissible_evidence_is_not_offered(session_factory, seeded):
    """It would fail the binding gate after the operator had already committed
    the model to it."""
    with session_factory() as db:
        rows = scout_queue.pending(db)

    starved = next(row for row in rows if row["proposal_id"] == seeded["second_proposal"])
    assert starved["facts"] == 0
    assert starved["available"] is False
    assert "No admissible evidence" in starved["unavailable_reason"]


def test_the_queue_endpoint_reports_the_count_the_button_shows(client, seeded):
    body = client.get("/api/scout/queue").json()

    assert body["runnable"] + body["blocked"] == len(body["queued"])
    assert body["runnable"] == 1, "only the concept with evidence can run"


def test_running_an_empty_selection_is_refused_rather_than_silently_doing_nothing(client):
    response = client.post("/api/scout/queue/run", json={"proposal_ids": []})

    assert response.status_code == 422


def test_a_selection_larger_than_the_cap_is_refused(client):
    response = client.post("/api/scout/queue/run",
                           json={"proposal_ids": [f"p{n}" for n in range(26)]})

    assert response.status_code == 422, "a 26-concept selection is a mistake, not an instruction"


def test_an_unstartable_concept_does_not_cancel_the_rest(client, seeded, monkeypatch):
    """The caller asked for the set. Dropping part of it silently leaves them
    waiting for a result that was never coming."""
    from app import main as main_module

    started = []

    class Jobs:
        def start(self, candidate_id, operation, proposal_id=None):
            if proposal_id == seeded["second_proposal"]:
                raise ValueError("no evidence")
            started.append((candidate_id, operation, proposal_id))
            return {"id": "job-1", "status": "queued", "candidate_id": candidate_id}

    monkeypatch.setattr(main_module.app.state, "audit_jobs", Jobs(), raising=False)
    body = client.post("/api/scout/queue/run", json={
        "proposal_ids": [seeded["second_proposal"], seeded["proposal_id"]]}).json()

    assert len(body["started"]) == 1
    assert [entry["reason"] for entry in body["skipped"]] == ["no evidence"]
    assert started == [(seeded["candidate_id"], "audit_idea", seeded["proposal_id"])]


def test_a_concept_selected_twice_starts_one_job(client, seeded, monkeypatch):
    from app import main as main_module

    calls = []

    class Jobs:
        def start(self, candidate_id, operation, proposal_id=None):
            calls.append(proposal_id)
            return {"id": f"job-{len(calls)}", "status": "queued"}

    monkeypatch.setattr(main_module.app.state, "audit_jobs", Jobs(), raising=False)
    client.post("/api/scout/queue/run", json={
        "proposal_ids": [seeded["proposal_id"], seeded["proposal_id"]]})

    assert calls == [seeded["proposal_id"]]


def test_an_unknown_proposal_is_reported_rather_than_started(client, seeded, monkeypatch):
    from app import main as main_module

    class Jobs:
        def start(self, *_args, **_kwargs):
            raise AssertionError("an unknown proposal reached the job runner")

    monkeypatch.setattr(main_module.app.state, "audit_jobs", Jobs(), raising=False)
    body = client.post("/api/scout/queue/run", json={"proposal_ids": ["nope"]}).json()

    assert body["started"] == []
    assert "Not in the queue" in body["skipped"][0]["reason"]


def test_finished_audits_come_back_as_cards_newest_first(client, session_factory, seeded):
    from datetime import UTC, datetime, timedelta

    with session_factory() as db:
        for minutes, title in ((10, "older"), (1, "newer")):
            db.add(AuditRecord(candidate_id=seeded["candidate_id"],
                               proposal_id=seeded["proposal_id"],
                               created_at=datetime.now(UTC) - timedelta(minutes=minutes),
                               payload={"evidence_state": "source_backed", "risks": ["one"],
                                        "proposal": {"concept_title": title,
                                                     "core_loop": "loop"}}))
            db.commit()

    cards = client.get("/api/scout/results").json()["cards"]

    assert [card["concept_title"] for card in cards] == ["newer", "older"]
    assert cards[0]["risks"] == 1
    assert cards[0]["evidence_state"] == "source_backed"
