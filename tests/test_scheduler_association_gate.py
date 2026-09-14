"""The daily snapshot measures a video only through an approved association."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import scheduler as scheduler_module
from app.association import AssociationService, MatchCandidateView, MatchSubjectView
from app.association.materialize import apply_association
from app.connectors import ConnectorResult
from app.evidence import record_artifact
from app.models import Candidate, Observation, ResearchRun, TrackedVideo

VIDEO_PAYLOAD = {"items": [
    {"id": "vid-exact", "statistics": {"viewCount": "51000"},
     "snippet": {"title": "The autumn season is here"}},
    {"id": "vid-fuzzy", "statistics": {"viewCount": "22000"},
     "snippet": {"title": "Evidence Garden beginner guide"}},
]}


class SnapshotConnectors:
    def __init__(self):
        self.requested_videos: list[str] = []

    async def roblox_games(self, _ids):
        return ConnectorResult(
            "https://games.roblox.com/v1/games?universeIds=77",
            {"data": [{"id": 77, "playing": 51, "visits": 1000, "favoritedCount": 90}]},
        )

    async def youtube_videos(self, video_ids):
        self.requested_videos.extend(video_ids)
        return ConnectorResult(
            "https://www.googleapis.com/youtube/v3/videos",
            {"items": [
                item for item in VIDEO_PAYLOAD["items"] if item["id"] in set(video_ids)
            ]},
        )

    async def close(self):
        return None


@pytest.fixture
def ledger(session_factory, tmp_path: Path, monkeypatch):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    monkeypatch.setattr(
        "app.evidence.get_settings", lambda: SimpleNamespace(artifact_dir=artifact_dir)
    )
    monkeypatch.setattr(scheduler_module, "SessionLocal", session_factory)

    service = AssociationService(shadow_mode=True)
    with session_factory() as db:
        run = ResearchRun(niche="cozy gardening")
        db.add(run)
        db.flush()
        candidate = Candidate(run_id=run.id, external_id="77")
        db.add(candidate)
        db.flush()
        pool = [MatchCandidateView(
            candidate_id=candidate.id, universe_id="77", place_ids=("12345",),
            raw_name="Evidence Garden", creator_name="Lantern Studio",
        )]
        artifact = record_artifact(
            db,
            url="https://www.googleapis.com/youtube/v3/videos",
            retrieval_method="youtube_videos_api",
            content_type="application/json",
            payload=VIDEO_PAYLOAD,
            source_tier="primary",
            owner="googleapis.com",
        )
        rows = {}
        for index, (video_id, title, description) in enumerate((
            ("vid-exact", "The autumn season is here",
             "Play it: https://www.roblox.com/games/12345/Evidence-Garden"),
            ("vid-fuzzy", "Evidence Garden beginner guide", "Everything a new player needs."),
        )):
            decision = service.associate(
                db,
                MatchSubjectView(
                    subject_id=f"yt:{video_id}", subject_type="youtube_video",
                    external_id=video_id, raw_title=title, raw_description=description,
                    creator_name="CozyCraftTV",
                    source_artifact_id=artifact.id,
                    source_artifact_sha256=artifact.sha256,
                    extraction_method="youtube_videos_api", source_tier="primary",
                    pointer_prefix=f"/items/{index}",
                ),
                pool, niche="cozy gardening",
                candidate_row_ids={candidate.id: candidate.id},
            )
            rows[video_id] = decision.record.id
            apply_association(db, decision.record)
        # The unapproved one is still tracked by hand, to prove the snapshot
        # refuses it rather than trusting the tracking row.
        db.add(TrackedVideo(candidate_id=candidate.id, video_id="vid-fuzzy"))
        db.commit()
    return service, session_factory, rows


@pytest.mark.asyncio
async def test_the_snapshot_skips_a_video_with_no_approved_association(ledger):
    _service, session_factory, records = ledger
    connectors = SnapshotConnectors()
    counts = await scheduler_module.snapshot_all(connectors)
    assert connectors.requested_videos == ["vid-exact"]
    assert counts["youtube"] == 1
    with session_factory() as db:
        views = list(db.scalars(
            select(Observation).where(
                Observation.metric == "youtube_views",
                Observation.extraction_method == "json_pointer",
            )
        ))
        assert {row.association_id for row in views} == {records["vid-exact"]}


@pytest.mark.asyncio
async def test_an_approved_review_brings_its_video_into_the_snapshot(ledger):
    service, session_factory, records = ledger
    with session_factory() as db:
        service.record_review(
            db, records["vid-fuzzy"], verdict="approved",
            reason="Confirmed by hand against the captured description.",
        )
        db.commit()
    connectors = SnapshotConnectors()
    await scheduler_module.snapshot_all(connectors)
    assert sorted(connectors.requested_videos) == ["vid-exact", "vid-fuzzy"]
    with session_factory() as db:
        associations = {
            row.association_id for row in db.scalars(
                select(Observation).where(Observation.metric == "youtube_views")
            )
        }
        assert associations == set(records.values())


@pytest.mark.asyncio
async def test_a_rejected_association_stops_being_measured(ledger):
    service, session_factory, records = ledger
    with session_factory() as db:
        service.record_review(
            db, records["vid-exact"], verdict="rejected",
            reason="That link is a sponsor link, not the subject of the video.",
        )
        db.commit()
    connectors = SnapshotConnectors()
    counts = await scheduler_module.snapshot_all(connectors)
    assert connectors.requested_videos == []
    assert counts["youtube"] == 0


@pytest.mark.asyncio
async def test_roblox_metrics_need_no_association(ledger):
    """A Roblox metric is keyed by the experience's own universe ID."""
    _service, session_factory, _records = ledger
    await scheduler_module.snapshot_all(SnapshotConnectors())
    with session_factory() as db:
        roblox = list(db.scalars(
            select(Observation).where(Observation.metric == "roblox_playing")
        ))
        assert roblox
        assert all(row.association_id is None for row in roblox)
