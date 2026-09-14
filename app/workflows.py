from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .calibration import current_features, load_artifact, score_features
from .connectors import ConnectorError, Connectors, extract_roblox_place_ids
from .evidence import (
    add_json_observation,
    candidate_facts,
    create_fact,
    evidence_conflicts,
    latest_metric,
    record_artifact,
)
from .llm import LLMUnavailable, OllamaProposalClient
from .matching import AssociationMatcher, LocalEmbeddingSearch
from .models import (
    Candidate,
    ConfidenceRecord,
    DecisionKind,
    DecisionRecord,
    Observation,
    Proposal,
    ResearchRun,
    RunStatus,
    ScoreRecord,
    TrackedVideo,
)


def _roblox_facts(db: Session, candidate: Candidate, artifact, index: int) -> list[str]:
    specs = [
        ("name", "roblox_name", None),
        ("playing", "roblox_playing", "players"),
        ("visits", "roblox_visits", "visits"),
        ("favoritedCount", "roblox_favorites", "favorites"),
        ("updated", "roblox_updated", None),
    ]
    fact_ids: list[str] = []
    for field, template, unit in specs:
        try:
            observation = add_json_observation(
                db,
                artifact=artifact,
                candidate_id=candidate.id,
                metric=template,
                pointer=f"/data/{index}/{field}",
                unit=unit,
            )
        except (KeyError, IndexError, ValueError):
            continue
        fact = create_fact(db, template_id=template, slots={"value": observation})
        fact_ids.append(fact.id)
        if template == "roblox_name":
            candidate.display_name_observation_id = observation.id
    return fact_ids


def _youtube_facts(db: Session, candidate: Candidate, artifact, index: int) -> list[str]:
    fact_ids: list[str] = []
    for pointer, metric, template, unit in (
        (f"/items/{index}/snippet/title", "youtube_title", "youtube_title", None),
        (f"/items/{index}/statistics/viewCount", "youtube_views", "youtube_views", "views"),
    ):
        try:
            observation = add_json_observation(
                db,
                artifact=artifact,
                candidate_id=candidate.id,
                metric=metric,
                pointer=pointer,
                unit=unit,
            )
        except (KeyError, IndexError, ValueError):
            continue
        fact_ids.append(create_fact(db, template_id=template, slots={"value": observation}).id)
    return fact_ids


def evidence_confidence(db: Session, candidate_id: str) -> tuple[float, dict[str, float], list[str]]:
    required = ["roblox_playing", "roblox_visits", "roblox_favorites", "youtube_views"]
    rows = [latest_metric(db, candidate_id, metric) for metric in required]
    coverage = sum(row is not None for row in rows) / len(required)
    artifacts = [row.artifact for row in rows if row is not None]
    authority = min((1.0 if artifact and artifact.source_tier == "primary" else 0.8 for artifact in artifacts), default=0.0)
    now = datetime.now(UTC)
    ages = []
    for row in rows:
        if row is None:
            continue
        observed_at = row.observed_at
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        ages.append(max(0.0, (now - observed_at).total_seconds() / 3600.0))
    freshness = min((max(0.0, 1.0 - age / 48.0) for age in ages), default=0.0)
    independence = 1.0 if artifacts else 0.0
    conflicts = evidence_conflicts(db, candidate_id)
    consistency = 0.0 if conflicts else 1.0
    parts = {
        "coverage": coverage,
        "authority": authority,
        "freshness": freshness,
        "independence": independence,
        "consistency": consistency,
    }
    failures = [name for name, value in parts.items() if value < 0.80]
    failures.extend(f"conflict:{metric}" for metric in conflicts)
    return round(100.0 * min(parts.values()), 3), parts, failures


class ResearchOrchestrator:
    def __init__(
        self,
        session_factory: sessionmaker,
        connectors: Connectors | None = None,
        llm: OllamaProposalClient | None = None,
    ):
        self.session_factory = session_factory
        self.connectors = connectors or Connectors()
        self.llm = llm or OllamaProposalClient()
        self.matcher = AssociationMatcher()
        self.embedder = LocalEmbeddingSearch()

    async def close(self) -> None:
        await self.connectors.close()
        await self.llm.close()

    async def research(self, run_id: str) -> None:
        with self.session_factory() as db:
            run = db.get(ResearchRun, run_id)
            if run is None:
                return
            run.status = RunStatus.RUNNING.value
            run.message = "Discovering source records"
            db.commit()
        try:
            await self._research(run_id)
        except Exception as exc:
            with self.session_factory() as db:
                run = db.get(ResearchRun, run_id)
                if run:
                    run.status = RunStatus.FAILED.value
                    run.message = f"Research stopped safely: {type(exc).__name__}: {exc}"
                    run.completed_at = datetime.now(UTC)
                    db.commit()

    async def _research(self, run_id: str) -> None:
        with self.session_factory() as db:
            run = db.get(ResearchRun, run_id)
            assert run is not None
            niche = run.niche
        try:
            search = await self.connectors.tavily_search(f"site:roblox.com/games {niche} Roblox")
        except ConnectorError as exc:
            with self.session_factory() as db:
                run = db.get(ResearchRun, run_id)
                assert run is not None
                run.status = RunStatus.COMPLETE.value
                run.message = f"Collection completed with no candidates: {exc}"
                run.completed_at = datetime.now(UTC)
                db.commit()
            return
        with self.session_factory() as db:
            record_artifact(
                db,
                url=search.url,
                retrieval_method="tavily_search",
                content_type=search.content_type,
                payload=search.payload,
                source_tier="discovery",
                discovery_only=True,
                owner="tavily.com",
            )
            db.commit()
        place_ids = extract_roblox_place_ids(search.payload)[:5]
        universe_ids: list[str] = []
        for place_id in place_ids:
            try:
                result = await self.connectors.universe_for_place(place_id)
                universe_id = str(result.payload.get("universeId") or result.payload.get("universe_id") or "")
                if universe_id:
                    universe_ids.append(universe_id)
            except ConnectorError:
                continue
        if not universe_ids:
            with self.session_factory() as db:
                run = db.get(ResearchRun, run_id)
                assert run is not None
                run.status = RunStatus.COMPLETE.value
                run.message = "No Roblox experience IDs could be verified from discovery results."
                run.completed_at = datetime.now(UTC)
                db.commit()
            return
        games = await self.connectors.roblox_games(universe_ids)
        candidate_info: list[tuple[str, str, list[str]]] = []
        with self.session_factory() as db:
            artifact = record_artifact(
                db,
                url=games.url,
                retrieval_method="roblox_games_api",
                content_type=games.content_type,
                payload=games.payload,
                source_tier="primary",
                owner="roblox.com",
            )
            for index, item in enumerate(games.payload.get("data", [])):
                universe_id = str(item.get("id", ""))
                if not universe_id:
                    continue
                candidate = Candidate(run_id=run_id, external_id=universe_id)
                db.add(candidate)
                db.flush()
                facts = _roblox_facts(db, candidate, artifact, index)
                candidate_info.append((candidate.id, str(item.get("name", "Sourced Roblox experience")), facts))
            run = db.get(ResearchRun, run_id)
            assert run is not None
            run.message = f"Captured primary evidence for {len(candidate_info)} candidates"
            db.commit()
        await self._attach_youtube(niche, candidate_info)
        for candidate_id, sourced_name, fact_ids in candidate_info:
            with self.session_factory() as db:
                fact_ids = [fact.id for fact in candidate_facts(db, candidate_id)]
            try:
                generated = await self.llm.generate(
                    agent="Meta Hunter",
                    niche=niche,
                    sourced_name=sourced_name,
                    fact_ids=fact_ids,
                )
                with self.session_factory() as db:
                    db.add(Proposal(
                        candidate_id=candidate_id,
                        agent="meta_hunter",
                        payload=generated.payload.model_dump(),
                        model_name=generated.model,
                    ))
                    db.commit()
            except LLMUnavailable:
                pass
            with self.session_factory() as db:
                self._record_decision(db, candidate_id)
                db.commit()
        with self.session_factory() as db:
            run = db.get(ResearchRun, run_id)
            assert run is not None
            run.status = RunStatus.COMPLETE.value
            run.message = (
                "Evidence collected. Scoring remains locked until the calibration benchmark passes."
                if not (load_artifact() or {}).get("active")
                else "Research complete; only evidence-gated recommendations are shown."
            )
            run.completed_at = datetime.now(UTC)
            db.commit()

    async def _attach_youtube(self, niche: str, candidates: list[tuple[str, str, list[str]]]) -> None:
        if not candidates:
            return
        try:
            search = await self.connectors.youtube_search(f"Roblox {niche}")
            video_ids = [
                item.get("id", {}).get("videoId") for item in search.payload.get("items", [])
                if item.get("id", {}).get("videoId")
            ]
            if not video_ids:
                return
            videos = await self.connectors.youtube_videos(video_ids)
        except ConnectorError:
            return
        names = [name for _, name, _ in candidates]
        with self.session_factory() as db:
            artifact = record_artifact(
                db,
                url=videos.url,
                retrieval_method="youtube_videos_api",
                content_type=videos.content_type,
                payload=videos.payload,
                source_tier="primary",
                owner="googleapis.com",
            )
            for index, item in enumerate(videos.payload.get("items", [])):
                title = str(item.get("snippet", {}).get("title", ""))
                dense_scores = await asyncio.to_thread(self.embedder.scores, title, names)
                match = self.matcher.match(title, names, dense_scores=dense_scores)
                if match.outcome != "auto_associate" or match.winner_index is None:
                    continue
                candidate_id = candidates[match.winner_index][0]
                video_id = str(item.get("id", ""))
                if not video_id:
                    continue
                if db.scalar(select(TrackedVideo).where(TrackedVideo.video_id == video_id)) is None:
                    db.add(TrackedVideo(candidate_id=candidate_id, video_id=video_id))
                _youtube_facts(db, db.get(Candidate, candidate_id), artifact, index)
            db.commit()

    def _record_decision(self, db: Session, candidate_id: str) -> DecisionRecord:
        artifact = load_artifact()
        if not artifact or not artifact.get("active"):
            decision = DecisionRecord(
                candidate_id=candidate_id,
                kind=DecisionKind.COLLECTION_ONLY.value,
                rationale_codes=["calibration_not_active"],
            )
            db.add(decision)
            return decision
        candidate = db.get(Candidate, candidate_id)
        features = current_features(db, candidate) if candidate else None
        if features is None:
            decision = DecisionRecord(
                candidate_id=candidate_id,
                kind=DecisionKind.RESEARCH_MORE.value,
                rationale_codes=["incomplete_7_day_input_window"],
                threshold_version=artifact["version"],
            )
            db.add(decision)
            return decision
        score = 100.0 * score_features(features, artifact)
        confidence, parts, failures = evidence_confidence(db, candidate_id)
        score_row = ScoreRecord(
            candidate_id=candidate_id,
            value=score,
            features=features,
            model_version=artifact["version"],
            threshold=100.0 * artifact["threshold"],
            dataset_hash=artifact["dataset_hash"],
        )
        confidence_row = ConfidenceRecord(
            candidate_id=candidate_id,
            value=confidence,
            coverage=parts["coverage"],
            authority=parts["authority"],
            freshness=parts["freshness"],
            independence=parts["independence"],
            consistency=parts["consistency"],
            failure_reasons=failures,
        )
        db.add_all([score_row, confidence_row])
        db.flush()
        conflicts = evidence_conflicts(db, candidate_id)
        if conflicts:
            kind, reasons = DecisionKind.BLOCKED_CONFLICT.value, [f"conflict:{item}" for item in conflicts]
        elif score >= 100.0 * artifact["threshold"] and confidence >= 80.0:
            kind, reasons = DecisionKind.RECOMMEND.value, ["score_pass", "evidence_confidence_pass"]
        else:
            kind, reasons = DecisionKind.RESEARCH_MORE.value, ["evidence_gate_not_met", *failures]
        decision = DecisionRecord(
            candidate_id=candidate_id,
            kind=kind,
            rationale_codes=reasons,
            score_id=score_row.id,
            confidence_id=confidence_row.id,
            threshold_version=artifact["version"],
        )
        db.add(decision)
        return decision

    async def audit(self, candidate_id: str) -> dict[str, Any]:
        with self.session_factory() as db:
            candidate = db.get(Candidate, candidate_id)
            if candidate is None:
                raise KeyError(candidate_id)
            run = db.get(ResearchRun, candidate.run_id)
            facts = candidate_facts(db, candidate_id)
            name_observation = db.get(Observation, candidate.display_name_observation_id) if candidate.display_name_observation_id else None
            sourced_name = str(name_observation.value_json) if name_observation else "Sourced Roblox experience"
            latest_decision = db.scalar(
                select(DecisionRecord).where(DecisionRecord.candidate_id == candidate_id).order_by(DecisionRecord.created_at.desc())
            )
            fact_ids = [fact.id for fact in facts]
        proposal_payload = None
        try:
            generated = await self.llm.generate(
                agent="Venture Scout",
                niche=run.niche if run else "Roblox",
                sourced_name=sourced_name,
                fact_ids=fact_ids,
            )
            proposal_payload = generated.payload
            with self.session_factory() as db:
                db.add(Proposal(
                    candidate_id=candidate_id,
                    agent="venture_scout",
                    payload=proposal_payload.model_dump(),
                    model_name=generated.model,
                ))
                db.commit()
        except LLMUnavailable:
            pass
        kind = latest_decision.kind if latest_decision else DecisionKind.COLLECTION_ONLY.value
        return {
            "candidate_id": candidate_id,
            "evidence_state": "conflicted" if kind == DecisionKind.BLOCKED_CONFLICT.value else "source_backed",
            "proposal": proposal_payload,
            "risks": proposal_payload.risks if proposal_payload else ["The local proposal model did not produce schema-valid output."],
            "decision": kind,
            "note": "This is a scoped proposal, not a prediction of game success.",
        }
