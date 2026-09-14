from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.connectors import ConnectorResult
from app.db import Base
from app.evidence import candidate_facts, render_fact
from app.llm import GeneratedProposal
from app.models import Candidate, DecisionRecord, ResearchRun
from app.schemas import ProposalPayload
from app.workflows import ResearchOrchestrator


class FakeConnectors:
    async def tavily_search(self, _query):
        return ConnectorResult(
            "https://api.tavily.com/search",
            {"results": [{"url": "https://www.roblox.com/games/12345/Example"}]},
        )

    async def universe_for_place(self, _place_id):
        return ConnectorResult("https://apis.roblox.com/universes/v1/places/12345/universe", {"universeId": 77})

    async def roblox_games(self, _ids):
        return ConnectorResult(
            "https://games.roblox.com/v1/games?universeIds=77",
            {"data": [{
                "id": 77,
                "name": "Evidence Garden",
                "playing": 42,
                "visits": 900,
                "favoritedCount": 80,
                "updated": "2026-09-14T00:00:00Z",
            }]},
        )

    async def youtube_search(self, _query):
        return ConnectorResult("https://www.googleapis.com/youtube/v3/search", {"items": []})

    async def close(self):
        return None


class FakeLLM:
    async def generate(self, **_kwargs):
        return GeneratedProposal(
            ProposalPayload(
                concept_title="Lantern Garden",
                core_loop="Grow unusual plants and reveal cooperative crafting paths.",
                differentiator="Players shape a shared garden through visible transformations.",
                build_steps=["Create the planting interaction"],
                risks=["The interaction may need more variety"],
                questions=[],
                supporting_fact_ids=[],
            ),
            "fake-model",
        )

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_research_workflow_collects_facts_but_does_not_score(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    monkeypatch.setattr("app.evidence.get_settings", lambda: SimpleNamespace(artifact_dir=artifact_dir))
    monkeypatch.setattr("app.workflows.load_artifact", lambda: None)
    with factory() as db:
        run = ResearchRun(niche="cozy gardening")
        db.add(run)
        db.commit()
        run_id = run.id
    orchestrator = ResearchOrchestrator(factory, connectors=FakeConnectors(), llm=FakeLLM())
    await orchestrator.research(run_id)
    with factory() as db:
        run = db.get(ResearchRun, run_id)
        candidate = db.scalar(select(Candidate).where(Candidate.run_id == run_id))
        decision = db.scalar(select(DecisionRecord).where(DecisionRecord.candidate_id == candidate.id))
        facts = candidate_facts(db, candidate.id)
        rendered = [render_fact(db, fact) for fact in facts]
        assert run.status == "complete"
        assert candidate.external_id == "77"
        assert decision.kind == "collection_only"
        assert any("42 concurrent players" in text for text in rendered)
        assert any("900 lifetime visits" in text for text in rendered)

