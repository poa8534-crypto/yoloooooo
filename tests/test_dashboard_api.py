from __future__ import annotations

from types import SimpleNamespace

from app.evidence import add_json_observation, create_fact, record_artifact
from app.main import (
    dashboard_summary,
    dashboard_timeline,
    get_source,
    list_research_runs,
    list_sources,
)
from app.models import ResearchRun


def test_dashboard_and_source_views_are_compiled_from_the_ledger(db, tmp_path, monkeypatch):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    monkeypatch.setattr(
        "app.evidence.get_settings",
        lambda: SimpleNamespace(artifact_dir=artifact_dir),
    )
    artifact = record_artifact(
        db,
        url="https://games.roblox.com/v1/games?universeIds=1",
        retrieval_method="roblox_games_api",
        content_type="application/json",
        payload={"data": [{"playing": 321}]},
        source_tier="primary",
        owner="roblox.com",
    )
    observation = add_json_observation(
        db,
        artifact=artifact,
        candidate_id=None,
        metric="roblox_playing",
        pointer="/data/0/playing",
        unit="players",
    )
    fact = create_fact(db, template_id="roblox_playing", slots={"value": observation})
    db.commit()

    summary = dashboard_summary(db)
    assert summary["counts"]["source_artifacts"] == 1
    assert summary["counts"]["observations"] == 1
    assert summary["counts"]["verified_facts"] == 1
    assert summary["counts"]["unique_publishers"] == 1
    assert summary["source_tiers"] == {"primary": 1}
    assert summary["collection_age_seconds"] >= 0

    timeline = dashboard_timeline(db)["points"]
    assert timeline[-1]["artifacts"] == 1
    assert timeline[-1]["observations"] == 1
    assert timeline[-1]["facts"] == 1

    sources = list_sources(db=db)
    assert sources[0]["id"] == artifact.id
    assert sources[0]["observation_count"] == 1
    assert sources[0]["raw_size"] > 0

    detail = get_source(artifact.id, db)
    assert detail["observations"][0]["id"] == observation.id
    assert detail["facts"][0]["id"] == fact.id
    assert detail["facts"][0]["text"] == (
        "Roblox reported 321 concurrent players at capture time."
    )


def test_research_run_listing_is_newest_first_and_bounded(db):
    older = ResearchRun(niche="older niche", status="complete", message="done")
    newer = ResearchRun(niche="newer niche", status="running", message="capturing")
    db.add_all([older, newer])
    db.flush()
    newer.created_at = older.created_at.replace(year=older.created_at.year + 1)
    db.commit()

    rows = list_research_runs(limit=500, db=db)

    assert [row.id for row in rows] == [newer.id, older.id]
