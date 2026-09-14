from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.evidence import (
    EvidenceError,
    accept_web_claim,
    add_json_observation,
    create_fact,
    record_artifact,
    render_fact,
)
from app.models import SourceArtifact


@pytest.fixture(autouse=True)
def artifact_dir(tmp_path: Path, monkeypatch):
    settings = SimpleNamespace(artifact_dir=tmp_path / "artifacts")
    settings.artifact_dir.mkdir()
    monkeypatch.setattr("app.evidence.get_settings", lambda: settings)


def test_fact_is_rendered_only_from_hashed_json_pointer(db):
    artifact = record_artifact(
        db,
        url="https://games.roblox.com/v1/games?universeIds=1",
        retrieval_method="test",
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
    assert render_fact(db, fact) == "Roblox reported 321 concurrent players at capture time."


def test_tampered_artifact_blocks_render(db):
    artifact = record_artifact(
        db, url="https://games.roblox.com/v1/games", retrieval_method="test",
        content_type="application/json", payload={"data": [{"visits": 12}]},
        source_tier="primary", owner="roblox.com",
    )
    observation = add_json_observation(
        db, artifact=artifact, candidate_id=None, metric="roblox_visits",
        pointer="/data/0/visits", unit="visits",
    )
    fact = create_fact(db, template_id="roblox_visits", slots={"value": observation})
    Path(artifact.raw_path).write_text('{"data":[{"visits":99}]}', encoding="utf-8")
    with pytest.raises(EvidenceError, match="hash mismatch"):
        render_fact(db, fact)


def test_discovery_snippet_cannot_become_observation(db):
    artifact = record_artifact(
        db, url="https://api.tavily.com/search", retrieval_method="tavily_search",
        content_type="application/json", payload={"results": [{"score": 0.9}]},
        source_tier="discovery", discovery_only=True, owner="tavily.com",
    )
    with pytest.raises(EvidenceError, match="discovery-only"):
        add_json_observation(
            db, artifact=artifact, candidate_id=None, metric="search_score",
            pointer="/results/0/score",
        )


def _page(db, url: str, text: str, tier: str = "secondary") -> SourceArtifact:
    return record_artifact(
        db, url=url, retrieval_method="page_capture", content_type="text/plain",
        payload=text, source_tier=tier,
    )


def test_primary_page_exact_passage_is_admissible(db):
    page = _page(db, "https://create.roblox.com/example", "The feature is available.", "primary")
    fact = accept_web_claim(
        db, candidate_id=None, claim_value="The feature is available.", metric="availability",
        evidence=[(page, "The feature is available.")],
    )
    assert fact.verification_state == "primary"


def test_two_independent_secondary_pages_are_required(db):
    one = _page(db, "https://publisher-a.com/report", "Report A. Interest is growing.")
    duplicate_owner = _page(db, "https://publisher-a.com/copy", "Copy A. Interest is growing.")
    with pytest.raises(EvidenceError, match="two independent"):
        accept_web_claim(
            db, candidate_id=None, claim_value="Interest is growing.", metric="trend",
            evidence=[(one, "Interest is growing."), (duplicate_owner, "Interest is growing.")],
        )
    two = _page(db, "https://publisher-b.net/report", "Independent report B. Interest is growing.")
    fact = accept_web_claim(
        db, candidate_id=None, claim_value="Interest is growing.", metric="trend",
        evidence=[(one, "Interest is growing."), (two, "Interest is growing.")],
    )
    assert fact.verification_state == "corroborated"


def test_append_only_evidence_refuses_updates(db):
    artifact = _page(db, "https://publisher.example/report", "Original")
    db.commit()
    artifact.source_tier = "primary"
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
