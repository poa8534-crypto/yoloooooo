from __future__ import annotations

import asyncio
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .association import (
    AssociationService,
    MatchCandidateView,
    MatchSubjectView,
)
from .association.materialize import apply_association
from .calibration import current_features, load_artifact, score_features
from . import audit_activity
from .config import get_settings
from .connectors import ConnectorError, Connectors, extract_roblox_place_ids
from .evidence import (
    add_json_observation,
    create_fact,
    evidence_conflicts,
    latest_metric,
    record_artifact,
)
from .llm import LLMUnavailable, OllamaProposalClient
from .matching import local_embedding_provider
from .models import (
    AuditRecord,
    Candidate,
    ConfidenceRecord,
    DecisionKind,
    DecisionRecord,
    Proposal,
    ResearchCheckpoint,
    ResearchRun,
    RunStatus,
    ScoreRecord,
)
from .research_evidence import audit_readiness, evidence_packet, verify_citations
from .security import redact


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


@dataclass
class CandidateContext:
    """What the association engine needs to know about one candidate."""

    candidate_id: str
    display_name: str
    universe_id: str
    fact_ids: list[str] = dc_field(default_factory=list)
    place_ids: tuple[str, ...] = ()
    creator_name: str = ""
    creator_external_id: str = ""
    description: str = ""

    def as_match_candidate(self) -> MatchCandidateView:
        return MatchCandidateView(
            candidate_id=self.candidate_id,
            universe_id=self.universe_id,
            place_ids=self.place_ids,
            raw_name=self.display_name,
            raw_description=self.description,
            creator_name=self.creator_name,
            creator_external_id=self.creator_external_id,
        )


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
        associations: AssociationService | None = None,
        shadow_mode: bool = True,
    ):
        self.session_factory = session_factory
        from .quotas import QuotaMeter
        self.connectors = connectors or Connectors(quota_meter=QuotaMeter(session_factory))
        self.llm = llm or OllamaProposalClient()
        # Every association goes through the service: it retrieves, scores,
        # applies the hard rules and writes the append-only record. Nothing in
        # this orchestrator decides a match on its own.
        self.associations = associations or AssociationService(
            embedder=local_embedding_provider(), shadow_mode=shadow_mode,
        )

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
            with self.session_factory() as db:
                checkpoint = db.get(ResearchCheckpoint, run_id)
                deep = checkpoint is not None and checkpoint.state.get("mode") == "deep"
            if deep:
                from .deep_research import DeepResearch
                await DeepResearch(self, run_id).run()
            else:
                await self._research(run_id)
        except Exception as exc:
            with self.session_factory() as db:
                run = db.get(ResearchRun, run_id)
                if run:
                    run.status = RunStatus.FAILED.value
                    run.message = f"Research stopped safely: {type(exc).__name__}"
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
        # Which place IDs resolved to which universe. The association engine
        # uses these to recognise a direct Roblox link in a video description.
        place_by_universe: dict[str, tuple[str, ...]] = {}
        for place_id in place_ids:
            try:
                result = await self.connectors.universe_for_place(place_id)
                universe_id = str(result.payload.get("universeId") or result.payload.get("universe_id") or "")
                if universe_id:
                    universe_ids.append(universe_id)
                    place_by_universe[universe_id] = place_by_universe.get(universe_id, ()) + (place_id,)
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
        candidate_info: list[CandidateContext] = []
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
                creator = item.get("creator") or {}
                candidate_info.append(CandidateContext(
                    candidate_id=candidate.id,
                    display_name=str(item.get("name", "Sourced Roblox experience")),
                    universe_id=universe_id,
                    fact_ids=facts,
                    place_ids=place_by_universe.get(universe_id, ())
                    + ((str(item["rootPlaceId"]),) if item.get("rootPlaceId") else ()),
                    creator_name=str(creator.get("name", "")),
                    creator_external_id=str(creator.get("id", "")),
                    description=str(item.get("description") or ""),
                ))
            run = db.get(ResearchRun, run_id)
            assert run is not None
            run.message = f"Captured primary evidence for {len(candidate_info)} candidates"
            db.commit()
        await self._attach_youtube(niche, candidate_info)
        for context in candidate_info:
            candidate_id = context.candidate_id
            with self.session_factory() as db:
                packet = evidence_packet(db, candidate_id)
                fact_ids = [fact["id"] for fact in packet]
            try:
                generated = await self.llm.generate(
                    agent="Meta Hunter",
                    niche=niche,
                    sourced_name=context.display_name,
                    fact_ids=fact_ids,
                    evidence=packet,
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

    async def _attach_youtube(self, niche: str, candidates: list[CandidateContext], *, budget=None, query=None) -> None:
        """Propose an association for every captured video and record it.

        Every proposal is written to the ledger. Only associations the service
        judges usable create observations; the rest wait for a human in the
        Matching Review queue and contribute nothing to scoring.
        """
        if not candidates:
            return
        try:
            query = query or f"Roblox {niche}"
            search = await budget.call(self.connectors, "youtube_search", query) if budget else await self.connectors.youtube_search(query)
            video_ids = [
                item.get("id", {}).get("videoId") for item in search.payload.get("items", [])
                if item.get("id", {}).get("videoId")
            ]
            if not video_ids:
                return
            if budget:
                seen = set(budget.state.get("video_ids", []))
                video_ids = [vid for vid in dict.fromkeys(video_ids) if vid not in seen][:min(10, budget.state["limits"]["videos"] - len(seen))]
                if not video_ids:
                    return
                budget.reserve("videos", len(video_ids))
                videos = await budget.call(self.connectors, "youtube_videos", video_ids)
                budget.save(video_ids=sorted(seen | set(video_ids)))
            else:
                videos = await self.connectors.youtube_videos(video_ids)
        except ConnectorError:
            return
        pool = [context.as_match_candidate() for context in candidates]
        candidate_row_ids = {context.candidate_id: context.candidate_id for context in candidates}
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
                snippet = item.get("snippet", {}) or {}
                video_id = str(item.get("id", ""))
                if not video_id:
                    continue
                subject = MatchSubjectView(
                    subject_id=f"yt:{video_id}",
                    subject_type="youtube_video",
                    external_id=video_id,
                    raw_title=str(snippet.get("title", "")),
                    raw_description=str(snippet.get("description", "")),
                    raw_url=f"https://www.youtube.com/watch?v={video_id}",
                    creator_name=str(snippet.get("channelTitle", "")),
                    creator_external_id=str(snippet.get("channelId", "")),
                    niche=niche,
                    source_artifact_id=artifact.id,
                    source_artifact_sha256=artifact.sha256,
                    extraction_method="youtube_videos_api",
                    source_tier="primary",
                    pointer_prefix=f"/items/{index}",
                )
                # Retrieval can run the local embedding model, which is CPU
                # bound and may download weights on first use. Running it
                # inline would stall the event loop — and with it the whole
                # local service — for the duration of every video.
                decision = await self.associations.associate_async(
                    db, subject, pool,
                    niche=niche, candidate_row_ids=candidate_row_ids,
                )
                apply_association(db, decision.record)
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

    async def audit(self, candidate_id: str, proposal_id: str | None = None, *, budget=None, gaps=None, operation=None, on_event=None) -> dict[str, Any]:
        audit_activity.start(candidate_id, session_factory=self.session_factory)
        def emit(_candidate_id, stage, detail="", **extra):
            audit_activity.emit(_candidate_id, stage, detail, **extra)
            if on_event:
                on_event(stage, detail, **extra)
        with self.session_factory() as db:
            readiness = audit_readiness(db, candidate_id, proposal_id)
            candidate = db.get(Candidate, candidate_id)
            run = db.get(ResearchRun, candidate.run_id)
            hunter = db.get(Proposal, proposal_id) if proposal_id else db.scalar(select(Proposal).where(
                Proposal.candidate_id == candidate_id, Proposal.agent == "meta_hunter").order_by(Proposal.created_at.desc()))
            if operation == "analyze_game":
                hunter = None
                for gate in readiness.gates:
                    if gate.label == "Selected Hunter proposal":
                        gate.state, gate.passed, gate.detail = "not_applicable", False, "Analyze game: no Hunter design selected"
            packet = evidence_packet(db, candidate_id)
            selected_id = hunter.id if hunter and hunter.candidate_id == candidate_id else None
            hunter_payload = hunter.payload if selected_id else None
            niche = run.niche if run else "Roblox"
            latest_decision = db.scalar(select(DecisionRecord).where(DecisionRecord.candidate_id == candidate_id).order_by(DecisionRecord.created_at.desc()))
            decision = latest_decision.kind if latest_decision else "collection_only"
        result = {
            "candidate_id": candidate_id, "proposal_id": selected_id, "proposal": None,
            "gates": [gate.model_dump() for gate in readiness.gates],
            "evidence_state": "blocked", "decision": decision,
            "risks": [], "note": "Speculative design audit, not a prediction of game success.",
        }
        emit(
            candidate_id, "gates",
            f"{sum(1 for g in readiness.gates if g.passed)} of {len(readiness.gates)} gates pass",
        )
        if not readiness.ready:
            result["risks"] = [g.label + ": " + g.detail for g in readiness.gates if g.state in {"fail", "missing"}]
            for gate in readiness.gates:
                if gate.state in {"fail", "missing"}:
                    emit(candidate_id, "gate_blocked", f"{gate.label}: {gate.detail}")
        else:
            try:
                emit(
                    candidate_id, "evidence",
                    f"{len(packet)} verified fact(s) packed"
                    + (" with a Hunter proposal to critique" if hunter_payload else "; no Hunter proposal, analysing the evidence directly"),
                )
                kwargs = {
                    "agent": "Venture Scout", "niche": niche,
                    "sourced_name": "Selected sourced experience",
                    "fact_ids": [p["id"] for p in packet], "evidence": packet,
                    "hunter_proposal": hunter_payload, "gaps": gaps or [], "require_citations": True,
                    "on_event": lambda stage, detail, **extra: emit(candidate_id, stage, detail, **extra),
                }
                if budget:
                    kwargs["before_attempt"] = budget.model_attempt
                    generated = await asyncio.wait_for(self.llm.generate(**kwargs), timeout=budget.remaining)
                else:
                    # A manual audit is not inside a run budget, so it gets the
                    # full deliberation allowance: every pass may take the whole
                    # per-request timeout on a local model.
                    settings = get_settings()
                    generated = await asyncio.wait_for(
                        self.llm.generate(**kwargs),
                        timeout=settings.ollama_timeout_seconds * settings.scout_deliberation_passes,
                    )
                result["proposal"] = generated.payload.model_dump()
                result["risks"] = generated.payload.risks
                result["evidence_state"] = "source_backed_design_speculative"
                emit(candidate_id, "proposal_accepted", f"Design accepted from {generated.model}")
            except (LLMUnavailable, TimeoutError) as exc:
                # Saying "within the budget" for a schema rejection sent every
                # investigation after the clock instead of the actual cause.
                # The reason the model's answer was refused is the useful part.
                result["risks"] = [
                    "The audit ran out of time before the model answered."
                    if isinstance(exc, TimeoutError)
                    else f"Every attempt was refused before it could be trusted: {redact(str(exc))}"
                ]
                if budget:
                    budget.error("scout", exc)
        with self.session_factory() as db:
            cited, withdrawn = verify_citations(
                db, candidate_id, (result["proposal"] or {}).get("supporting_fact_ids", []),
            )
            result["cited_fact_ids"], result["withdrawn_fact_ids"] = cited, withdrawn
            emit(
                candidate_id, "citations",
                f"{len(cited)} citation(s) re-verified, {len(withdrawn)} withdrawn",
            )
            row = AuditRecord(candidate_id=candidate_id, proposal_id=selected_id, payload=result)
            db.add(row)
            db.commit()
            audit_activity.finish(
                candidate_id,
                "stored" if result["proposal"] else "blocked",
                f"Audit {row.id} written to the ledger",
                audit_id=row.id,
            )
            return {**result, "audit_id": row.id}
