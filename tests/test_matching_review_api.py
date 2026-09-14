"""The Matching Review workflow as the dashboard drives it over HTTP."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.association import AssociationService, MatchCandidateView, MatchSubjectView
from app.db import get_db

GARDEN = MatchCandidateView(
    candidate_id="c-garden", universe_id="1001", place_ids=("1101",),
    raw_name="Grow a Garden", creator_name="Lantern Studio",
)
TYCOON = MatchCandidateView(
    candidate_id="c-tycoon", universe_id="1003", place_ids=("1103",),
    raw_name="Garden Life Tycoon", creator_name="Mossbank",
)
POOL = [GARDEN, TYCOON]


def subject(title, description="a captured description", subject_id="s-1", external_id="vid-1"):
    return MatchSubjectView(
        subject_id=subject_id,
        subject_type="youtube_video",
        external_id=external_id,
        raw_title=title,
        raw_description=description,
        raw_url=f"https://www.youtube.com/watch?v={external_id}",
        creator_name="CozyCraftTV",
        niche="cozy gardening",
        source_artifact_sha256="a" * 64,
        extraction_method="youtube_videos_api",
        source_tier="primary",
        pointer_prefix="/items/0",
    )


@pytest.fixture
def client(session_factory):
    service = AssociationService(shadow_mode=True)

    def override_get_db():
        with session_factory() as session:
            yield session

    main_module.app.dependency_overrides[get_db] = override_get_db
    main_module.app.state.association_service = service
    # No lifespan: this exercises the routes, not the scheduler or connectors.
    with session_factory() as db:
        service.associate(db, subject("Grow a Garden full guide"), POOL, niche="cozy gardening")
        service.associate(
            db,
            subject(
                "Autumn season is here",
                "Play it https://www.roblox.com/games/1101/Grow-a-Garden",
                subject_id="s-2", external_id="vid-2",
            ),
            POOL, niche="cozy gardening",
        )
        db.commit()
    yield TestClient(main_module.app), service, session_factory
    main_module.app.dependency_overrides.clear()


def test_the_queue_lists_only_what_needs_a_human(client):
    http, _service, _factory = client
    response = http.get("/api/matching/reviews")
    assert response.status_code == 200
    rows = response.json()
    assert [row["subject_external_id"] for row in rows] == ["vid-1"]
    assert rows[0]["outcome"] == "review_required"


def test_a_review_row_carries_everything_a_reviewer_needs(client):
    http, _service, _factory = client
    row = http.get("/api/matching/reviews").json()[0]
    assert row["subject_title"] == "Grow a Garden full guide"
    assert row["subject_description"] == "a captured description"
    assert row["candidate"]["display_name"] == "Grow a Garden"
    assert row["runner_up"]["display_name"] == "Garden Life Tycoon"
    assert row["alternatives"]
    assert row["features"] and row["feature_order"]
    assert row["top_score"] >= row["runner_up_score"]
    assert row["margin"] == pytest.approx(row["top_score"] - row["runner_up_score"])
    assert row["matcher_version"] and row["normalization_version"]
    assert row["threshold_version"] and row["feature_schema_version"]
    assert row["conflict_warnings"] is not None
    assert row["usable_downstream"] is False


def test_status_reports_the_frozen_policy_and_the_queue_depth(client):
    http, _service, _factory = client
    status = http.get("/api/matching/status").json()
    assert status["shadow_mode"] is True
    assert status["fuzzy_auto_enabled"] is False
    assert status["pending_reviews"] == 1
    assert status["total_associations"] == 2
    assert status["outcome_counts"]["auto_associate"] == 1
    assert 0.0 < status["high_threshold"] <= 1.0
    assert status["matcher_version"]


def test_approving_records_the_review_and_leaves_the_verdict_alone(client):
    http, _service, _factory = client
    row = http.get("/api/matching/reviews").json()[0]
    response = http.post(
        f"/api/matching/reviews/{row['association_id']}",
        json={"verdict": "approved", "reason": "Watched it; this is the right experience."},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "approved"
    assert body["engine_outcome"] == "review_required"
    assert body["override_id"], "approving a review verdict is a disagreement"

    detail = http.get(f"/api/matching/reviews/{row['association_id']}").json()
    assert detail["outcome"] == "review_required"  # engine verdict untouched
    assert detail["review_verdict"] == "approved"
    assert detail["usable_downstream"] is True
    assert http.get("/api/matching/reviews").json() == []


def test_selecting_another_candidate_requires_a_reason(client):
    http, _service, _factory = client
    row = http.get("/api/matching/reviews").json()[0]
    response = http.post(
        f"/api/matching/reviews/{row['association_id']}",
        json={"verdict": "reassigned", "reason": "too short", "selected_candidate_id": "c-tycoon"},
    )
    assert response.status_code == 422


def test_selecting_another_candidate_is_recorded(client):
    http, _service, _factory = client
    row = http.get("/api/matching/reviews").json()[0]
    response = http.post(
        f"/api/matching/reviews/{row['association_id']}",
        json={
            "verdict": "reassigned",
            "reason": "The footage is from the tycoon experience, not this one.",
            "selected_candidate_id": "c-tycoon",
        },
    )
    assert response.status_code == 200
    assert response.json()["selected_candidate_id"] == "c-tycoon"
    detail = http.get(f"/api/matching/reviews/{row['association_id']}").json()
    assert detail["review_selected_candidate_id"] == "c-tycoon"


def test_rejecting_an_automatic_association_removes_it_from_downstream(client):
    http, _service, _factory = client
    automatic = next(
        row for row in http.get("/api/matching/reviews?include_resolved=true").json()
        if row["outcome"] == "auto_associate"
    )
    assert automatic["usable_downstream"] is True
    response = http.post(
        f"/api/matching/reviews/{automatic['association_id']}",
        json={
            "verdict": "rejected",
            "reason": "That is a sponsor link, not the subject of the video.",
        },
    )
    assert response.status_code == 200
    detail = http.get(f"/api/matching/reviews/{automatic['association_id']}").json()
    assert detail["outcome"] == "auto_associate"
    assert detail["usable_downstream"] is False


def test_reviewing_an_unknown_association_is_a_404(client):
    http, _service, _factory = client
    response = http.post(
        "/api/matching/reviews/does-not-exist",
        json={"verdict": "approved", "reason": "Trying to approve a missing record."},
    )
    assert response.status_code == 404


def test_an_unknown_verdict_is_rejected(client):
    http, _service, _factory = client
    row = http.get("/api/matching/reviews").json()[0]
    response = http.post(
        f"/api/matching/reviews/{row['association_id']}",
        json={"verdict": "definitely", "reason": "A verdict the schema does not allow."},
    )
    assert response.status_code == 422
