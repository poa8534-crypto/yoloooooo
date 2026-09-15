from __future__ import annotations

import asyncio
import importlib.util
import json
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Text, func, select
from sqlalchemy.orm import Session

from .association import AssociationService, active_thresholds, is_association_usable
from .association.materialize import apply_association
from .association.service import human_confirmation
from .calibration import calibration_status, load_artifact
from .config import ROOT, get_settings
from .db import SessionLocal, get_db, init_db
from .evidence import fact_freshness, render_fact
from .matching import DEFAULT_EMBEDDING_MODEL
from .models import (
    AssociationRecord,
    AuditRecord,
    Candidate,
    ConfidenceRecord,
    DecisionOverride,
    DecisionRecord,
    Fact,
    MatchCandidate,
    MatchSubject,
    Observation,
    Proposal,
    ResearchCheckpoint,
    ResearchReport,
    ResearchRun,
    RunStatus,
    ScoreRecord,
    SourceArtifact,
    SystemState,
)
from .research_budget import progress
from .research_evidence import (
    audit_readiness,
    evidence_packet,
    history,
    verified_fact_packet,
    verify_citations,
)
from .scheduler import catch_up_if_needed, start_scheduler
from .schemas import (
    AuditReadiness,
    AuditView,
    CalibrationStatus,
    CandidateView,
    FactView,
    MatchingArtifactView,
    MatchingCandidateView,
    MatchingReviewCreate,
    MatchingReviewResult,
    MatchingReviewView,
    MatchingStatus,
    OverrideCreate,
    ProposalPayload,
    ResearchRunCreate,
    RunView,
)
from .security import RedactedResponses, install_log_redaction, sanitize_url
from .workflows import ResearchOrchestrator

TASKS: set[asyncio.Task] = set()

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def assert_local_only(host: str) -> None:
    """Refuse to serve this API on a non-loopback interface.

    There is no authentication on any endpoint: the ledger, the review
    queue and the override controls are all open to whoever can reach the
    port. That is acceptable bound to loopback and not acceptable anywhere
    else, so binding wider fails loudly instead of quietly exposing it.
    """
    if host not in LOOPBACK_HOSTS:
        raise RuntimeError(
            f"refusing to bind {host}: this service has no authentication and "
            "must stay on loopback. Put it behind an authenticating proxy if "
            "you need remote access."
        )


SERVICE_STARTED_AT = datetime.now(UTC)


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_local_only(get_settings().host)
    init_db()
    with SessionLocal() as db:
        orphaned = list(db.scalars(select(ResearchRun).where(
            ResearchRun.status.in_([RunStatus.QUEUED.value, RunStatus.RUNNING.value])
        )))
        for run in orphaned:
            run.status = "interrupted"
            run.message = "Interrupted by a previous service shutdown; no evidence was fabricated."
            run.completed_at = datetime.now(UTC)
        db.commit()
    app.state.orchestrator = ResearchOrchestrator(SessionLocal)
    # Shadow mode stays on until a benchmark validates fuzzy matching.
    app.state.association_service = app.state.orchestrator.associations
    app.state.scheduler = start_scheduler()
    await catch_up_if_needed()
    yield
    app.state.scheduler.shutdown(wait=False)
    for task in TASKS:
        task.cancel()
    await asyncio.gather(*TASKS, return_exceptions=True)
    await app.state.orchestrator.close()


app = FastAPI(title="Roblox Venture Agents", version="0.1.0", lifespan=lifespan)
install_log_redaction()
app.add_middleware(RedactedResponses)


def _candidate_view(db: Session, candidate: Candidate) -> CandidateView:
    packet = evidence_packet(db, candidate.id)
    fact_views = [FactView(id=f["id"], text=f["text"], source_ids=f["source_ids"],
                          freshness=f["freshness"], verification_state="source_backed") for f in packet]
    name = next((str(f["slots"][0]["value"]) for f in packet if f["template_id"] == "roblox_name"), "Sourced Roblox experience")
    proposal_row = db.scalar(
        select(Proposal).where(Proposal.candidate_id == candidate.id, Proposal.agent == "meta_hunter").order_by(Proposal.created_at.desc())
    )
    try:
        proposal = ProposalPayload.model_validate(proposal_row.payload) if proposal_row else None
    except ValueError:
        proposal = None  # Legacy prose must satisfy the current firewall to display.
    decision = db.scalar(
        select(DecisionRecord).where(DecisionRecord.candidate_id == candidate.id).order_by(DecisionRecord.created_at.desc())
    )
    score = db.get(ScoreRecord, decision.score_id) if decision and decision.score_id else None
    confidence = db.get(ConfidenceRecord, decision.confidence_id) if decision and decision.confidence_id else None
    active = bool((load_artifact() or {}).get("active"))
    cited, withdrawn = verify_citations(
        db, candidate.id, proposal.supporting_fact_ids if proposal else []
    )
    return CandidateView(
        id=candidate.id,
        external_id=candidate.external_id,
        display_name=name,
        facts=fact_views,
        proposal=proposal,
        proposal_id=proposal_row.id if proposal_row and proposal else None,
        decision=decision.kind if decision and (active or decision.kind != "recommend") else "collection_only",
        decision_id=decision.id if decision else None,
        score=score.value if active and score else None,
        confidence=confidence.value if active and confidence else None,
        cited_fact_ids=cited,
        withdrawn_fact_ids=withdrawn,
    )


def _run_view(db: Session, run: ResearchRun) -> RunView:
    candidates = [
        _candidate_view(db, candidate)
        for candidate in db.scalars(select(Candidate).where(Candidate.run_id == run.id).order_by(Candidate.created_at))
    ]
    return RunView(
        id=run.id,
        niche=run.niche,
        status=run.status,
        message=run.message,
        created_at=run.created_at,
        completed_at=run.completed_at,
        candidates=candidates,
        passing_results=[candidate for candidate in candidates if candidate.decision == "recommend"],
        progress=progress(db, run),
    )


@app.post("/api/research-runs", response_model=RunView, status_code=202)
async def create_research_run(body: ResearchRunCreate, db: Session = Depends(get_db)):
    run = ResearchRun(niche=" ".join(body.niche.split()))
    db.add(run)
    db.flush()
    db.add(ResearchCheckpoint(run_id=run.id, state={"mode": body.mode}))
    db.commit()
    task = asyncio.create_task(app.state.orchestrator.research(run.id))
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)
    return _run_view(db, run)


@app.get("/api/research-runs", response_model=list[RunView])
def list_research_runs(limit: int = 25, db: Session = Depends(get_db)):
    safe_limit = max(1, min(limit, 100))
    runs = db.scalars(
        select(ResearchRun).order_by(ResearchRun.created_at.desc()).limit(safe_limit)
    )
    return [_run_view(db, run) for run in runs]


@app.get("/api/research-runs/{run_id}", response_model=RunView)
def get_research_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(404, "research run not found")
    return _run_view(db, run)


@app.get("/api/research-runs/{run_id}/events")
async def research_events(run_id: str):
    async def stream():
        previous = None
        while True:
            with SessionLocal() as db:
                run = db.get(ResearchRun, run_id)
                if run is None:
                    yield "event: error\ndata: {\"detail\":\"research run not found\"}\n\n"
                    return
                payload = {"status": run.status, "message": run.message, **progress(db, run)}
            encoded = json.dumps(payload)
            if encoded != previous:
                yield f"event: progress\ndata: {encoded}\n\n"
                previous = encoded
            if payload["status"] in {"complete", "failed", "partial", "interrupted"}:
                return
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/candidates/{candidate_id}/audit", response_model=AuditView)
async def audit_candidate(candidate_id: str, proposal_id: str | None = None, db: Session = Depends(get_db)):
    if db.get(Candidate, candidate_id) is None:
        raise HTTPException(404, "candidate not found")
    try:
        result = await app.state.orchestrator.audit(candidate_id, proposal_id)
        return AuditView.model_validate(result)
    except KeyError:
        raise HTTPException(404, "candidate not found")


@app.get("/api/research-runs/{run_id}/report")
def get_report(run_id: str, db: Session = Depends(get_db)):
    row = db.scalar(select(ResearchReport).where(ResearchReport.run_id == run_id).order_by(ResearchReport.created_at.desc()))
    if row is None:
        raise HTTPException(404, "Report not finalized; see run progress")
    payload = json.loads(json.dumps(row.payload))
    for dossier in payload["comparison"]:
        original = dossier["facts"]
        dossier["facts"] = [verified for saved in original
                            if (fact := db.get(Fact, saved["id"])) is not None
                            and (verified := verified_fact_packet(db, fact)) is not None]
        dossier["omitted_facts"] = len(original) - len(dossier["facts"])
        dossier["history"] = history(db, dossier["candidate_id"])
    remaining = {f["id"] for dossier in payload["comparison"] for f in dossier["facts"]}
    for question in payload["questions"]:
        original_ids = question["fact_ids"]
        question["fact_ids"] = [fid for fid in original_ids if fid in remaining]
        if original_ids and not question["fact_ids"]:
            question["state"] = "limited"
            question["limitation"] = "Previously cited evidence is no longer admissible."
        elif question.get("id") == "creators" and question["fact_ids"]:
            question["limitation"] = "Associated metadata is not watched video or audience sentiment."
    payload["limitations"] = [question["limitation"] for question in payload["questions"]]
    return {"report_id": row.id, **payload}


@app.get("/api/audits/{audit_id}", response_model=AuditView)
def get_audit(audit_id: str, db: Session = Depends(get_db)):
    row = db.get(AuditRecord, audit_id)
    if row is None:
        raise HTTPException(404, "audit not found")
    return {**row.payload, "audit_id": row.id}


@app.get("/api/candidates/{candidate_id}/audit", response_model=AuditView)
def get_latest_audit(candidate_id: str, db: Session = Depends(get_db)):
    """The most recent stored audit, so a reload does not lose the brief.

    An audit takes minutes and is written to the ledger, but nothing served it
    back: reopening the page showed an empty brief as though the audit had
    never run. Citations are re-resolved here rather than replayed from the
    stored payload, because what was admissible then may not be now.
    """
    if db.get(Candidate, candidate_id) is None:
        raise HTTPException(404, "candidate not found")
    row = db.scalar(
        select(AuditRecord)
        .where(AuditRecord.candidate_id == candidate_id)
        .order_by(AuditRecord.created_at.desc())
    )
    if row is None:
        raise HTTPException(404, "no audit recorded for this candidate")
    payload = dict(row.payload)
    cited, withdrawn = verify_citations(
        db, candidate_id, (payload.get("proposal") or {}).get("supporting_fact_ids", []),
    )
    payload["cited_fact_ids"], payload["withdrawn_fact_ids"] = cited, withdrawn
    return {**payload, "audit_id": row.id}


@app.get("/api/candidates/{candidate_id}/history")
def get_history(candidate_id: str, db: Session = Depends(get_db)):
    if db.get(Candidate, candidate_id) is None:
        raise HTTPException(404, "candidate not found")
    return history(db, candidate_id)


@app.post("/api/research-runs/{run_id}/resume", status_code=202)
async def resume_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(ResearchRun, run_id)
    checkpoint = db.get(ResearchCheckpoint, run_id)
    if run is None:
        raise HTTPException(404, "research run not found")
    if run.status != "interrupted" or not checkpoint or checkpoint.state.get("mode") != "deep":
        raise HTTPException(409, "Only interrupted deep runs can resume")
    run.status = "queued"
    run.completed_at = None
    db.commit()
    task = asyncio.create_task(app.state.orchestrator.research(run_id))
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)
    return _run_view(db, run)


@app.get("/api/candidates/{candidate_id}/audit-readiness", response_model=AuditReadiness)
def candidate_audit_readiness(candidate_id: str, db: Session = Depends(get_db)):
    try:
        return audit_readiness(db, candidate_id)
    except KeyError:
        raise HTTPException(404, "candidate not found")


@app.get("/api/evidence/{fact_id}")
def get_evidence(fact_id: str, db: Session = Depends(get_db)):
    fact = db.get(Fact, fact_id)
    if fact is None:
        raise HTTPException(404, "fact not found")
    observations = []
    artifacts: dict[str, dict] = {}
    for slot, observation_id in fact.slot_observation_ids.items():
        observation = db.get(Observation, observation_id)
        if observation is None:
            raise HTTPException(409, "evidence chain is broken")
        artifact = db.get(SourceArtifact, observation.artifact_id)
        if artifact is None:
            raise HTTPException(409, "evidence chain is broken")
        observations.append({
            "slot": slot,
            "id": observation.id,
            "metric": observation.metric,
            "value": observation.value_json,
            "unit": observation.unit,
            "extraction_method": observation.extraction_method,
            "pointer": observation.pointer,
            "observed_at": observation.observed_at,
            "artifact_id": artifact.id,
        })
        artifacts[artifact.id] = {
            "id": artifact.id,
            "url": sanitize_url(artifact.url),
            "publisher_owner": artifact.publisher_owner,
            "captured_at": artifact.captured_at,
            "sha256": artifact.sha256,
            "content_type": artifact.content_type,
            "source_tier": artifact.source_tier,
            "retrieval_method": artifact.retrieval_method,
        }
    return {
        "fact": {
            "id": fact.id,
            "text": render_fact(db, fact),
            "template_id": fact.template_id,
            "verification_state": fact.verification_state,
            "freshness": fact.freshness,
        },
        "observations": observations,
        "artifacts": list(artifacts.values()),
    }


def _artifact_size(artifact: SourceArtifact) -> int:
    try:
        return Path(artifact.raw_path).stat().st_size
    except OSError:
        return 0


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@app.get("/api/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    now = datetime.now(UTC)
    # Aggregate in SQL. Loading every artifact and stat()-ing each raw file made
    # this endpoint scale with the whole ledger on every dashboard refresh.
    first_capture = _as_utc(db.scalar(select(func.min(SourceArtifact.captured_at))))
    latest_capture = _as_utc(db.scalar(select(func.max(SourceArtifact.captured_at))))
    artifact_count = db.scalar(select(func.count(SourceArtifact.id))) or 0
    measured_bytes = db.scalar(
        select(func.coalesce(func.sum(SourceArtifact.raw_size), 0))
    ) or 0
    unmeasured_artifacts = db.scalar(
        select(func.count(SourceArtifact.id)).where(SourceArtifact.raw_size.is_(None))
    ) or 0
    publisher_count = db.scalar(
        select(func.count(func.distinct(SourceArtifact.publisher_owner)))
    ) or 0
    source_tiers = Counter(dict(db.execute(
        select(SourceArtifact.source_tier, func.count()).group_by(SourceArtifact.source_tier)
    ).all()))
    recent_artifacts = list(db.scalars(
        select(SourceArtifact).order_by(SourceArtifact.captured_at.desc()).limit(6)
    ))
    activity: list[dict] = []
    for item in recent_artifacts:
        activity.append({
            "kind": "source",
            "actor": item.publisher_owner,
            "message": f"{item.retrieval_method.replace('_', ' ')} artifact captured",
            "status": "discovery" if item.is_discovery_only else "verified",
            "at": _as_utc(item.captured_at),
            "target_id": item.id,
        })
    for run in db.scalars(
        select(ResearchRun).order_by(ResearchRun.created_at.desc()).limit(6)
    ):
        activity.append({
            "kind": "research",
            "actor": "Meta Hunter",
            "message": f"{run.niche}: {run.message}",
            "status": run.status,
            "at": _as_utc(run.completed_at or run.created_at),
            "target_id": run.id,
        })
    for record in db.scalars(
        select(AssociationRecord).order_by(AssociationRecord.created_at.desc()).limit(6)
    ):
        activity.append({
            "kind": "match",
            "actor": "Matching Engine",
            "message": record.outcome.replace("_", " "),
            "status": record.outcome,
            "at": _as_utc(record.created_at),
            "target_id": record.id,
        })
    activity.sort(key=lambda item: item["at"], reverse=True)
    return {
        "service_started_at": SERVICE_STARTED_AT,
        "uptime_seconds": max(0, int((now - SERVICE_STARTED_AT).total_seconds())),
        "collection_started_at": first_capture,
        "collection_age_seconds": (
            max(0, int((now - first_capture).total_seconds())) if first_capture else 0
        ),
        "latest_capture_at": latest_capture,
        "counts": {
            "unique_publishers": publisher_count,
            "source_artifacts": artifact_count,
            "artifact_bytes": int(measured_bytes),
            # Artifacts captured before raw_size existed. The ledger is
            # append-only so these are never back-filled; the dashboard says
            # so rather than quietly understating the total.
            "unmeasured_artifacts": int(unmeasured_artifacts),
            "observations": db.scalar(select(func.count(Observation.id))) or 0,
            "verified_facts": db.scalar(select(func.count(Fact.id))) or 0,
            "candidate_clusters": 0,
            "unique_games": db.scalar(select(func.count(func.distinct(Candidate.external_id)))) or 0,
            "candidate_records": db.scalar(select(func.count(Candidate.id))) or 0,
            "proposals": db.scalar(select(func.count(Proposal.id))) or 0,
            "associations": db.scalar(select(func.count(AssociationRecord.id))) or 0,
            "conflicts": db.scalar(
                select(func.count(AssociationRecord.id)).where(
                    AssociationRecord.outcome == "blocked_conflict"
                )
            ) or 0,
            "research_runs": db.scalar(select(func.count(ResearchRun.id))) or 0,
        },
        "source_tiers": dict(source_tiers),
        "activity": activity[:12],
    }


MAX_TIMELINE_DAYS = 400


def _calendar_span(days: list[str]) -> list[str]:
    """Every calendar day from the first to the last, gaps included.

    Plotting only the days that happen to have captures compresses the x
    axis and draws a straight line across a silent fortnight. Emitting the
    empty days makes a flat stretch mean what it looks like.
    """
    if not days:
        return []
    start = date.fromisoformat(days[0])
    end = date.fromisoformat(days[-1])
    total = (end - start).days + 1
    if total > MAX_TIMELINE_DAYS:
        start = end - timedelta(days=MAX_TIMELINE_DAYS - 1)
        total = MAX_TIMELINE_DAYS
    return [(start + timedelta(days=offset)).isoformat() for offset in range(total)]


@app.get("/api/dashboard/timeline")
def dashboard_timeline(db: Session = Depends(get_db)):
    series = {
        "artifacts": Counter(
            value.date().isoformat() for value in db.scalars(select(SourceArtifact.captured_at))
        ),
        "observations": Counter(
            value.date().isoformat() for value in db.scalars(select(Observation.observed_at))
        ),
        "facts": Counter(
            value.date().isoformat() for value in db.scalars(select(Fact.created_at))
        ),
        "associations": Counter(
            value.date().isoformat() for value in db.scalars(select(AssociationRecord.created_at))
        ),
    }
    observed = sorted({day for values in series.values() for day in values})
    days = _calendar_span(observed)
    cumulative = {name: 0 for name in series}
    points = []
    for day in days:
        point: dict[str, int | str] = {"day": day}
        for name, values in series.items():
            cumulative[name] += values[day]
            point[name] = cumulative[name]
        points.append(point)
    return {"points": points}


@app.get("/api/sources")
def list_sources(limit: int = 100, db: Session = Depends(get_db)):
    safe_limit = max(1, min(limit, 500))
    rows = list(
        db.scalars(
            select(SourceArtifact)
            .order_by(SourceArtifact.captured_at.desc())
            .limit(safe_limit)
        )
    )
    observation_counts = dict(
        db.execute(
            select(Observation.artifact_id, func.count(Observation.id))
            .group_by(Observation.artifact_id)
        ).all()
    )
    return [{
        "id": item.id,
        "url": item.url,
        "publisher_owner": item.publisher_owner,
        "retrieval_method": item.retrieval_method,
        "captured_at": item.captured_at,
        "sha256": item.sha256,
        "content_type": item.content_type,
        "source_tier": item.source_tier,
        "is_discovery_only": item.is_discovery_only,
        "raw_size": _artifact_size(item),
        "observation_count": observation_counts.get(item.id, 0),
    } for item in rows]


@app.get("/api/sources/{source_id}")
def get_source(source_id: str, db: Session = Depends(get_db)):
    artifact = db.get(SourceArtifact, source_id)
    if artifact is None:
        raise HTTPException(404, "source artifact not found")
    observations = list(
        db.scalars(
            select(Observation)
            .where(Observation.artifact_id == source_id)
            .order_by(Observation.observed_at)
        )
    )
    observation_ids = {item.id for item in observations}
    candidate_facts_rows = db.scalars(
        select(Fact)
        .where(Fact.source_ids.cast(Text).contains(source_id))
        .order_by(Fact.created_at)
    )
    facts = [
        item for item in candidate_facts_rows
        if observation_ids.intersection((item.slot_observation_ids or {}).values())
    ]
    return {
        "artifact": {
            "id": artifact.id,
            "url": sanitize_url(artifact.url),
            "publisher_owner": artifact.publisher_owner,
            "retrieval_method": artifact.retrieval_method,
            "captured_at": artifact.captured_at,
            "sha256": artifact.sha256,
            "content_type": artifact.content_type,
            "source_tier": artifact.source_tier,
            "is_discovery_only": artifact.is_discovery_only,
            "raw_size": _artifact_size(artifact),
        },
        "observations": [{
            "id": item.id,
            "candidate_id": item.candidate_id,
            "metric": item.metric,
            "value": item.value_json,
            "unit": item.unit,
            "extraction_method": item.extraction_method,
            "pointer": item.pointer,
            "observed_at": item.observed_at,
            "association_id": item.association_id,
        } for item in observations],
        "facts": [{
            "id": item.id,
            "text": render_fact(db, item),
            "template_id": item.template_id,
            "verification_state": item.verification_state,
            "freshness": fact_freshness(db, item),
        } for item in facts],
    }


@app.post("/api/decisions/{decision_id}/override")
def override_decision(decision_id: str, body: OverrideCreate, db: Session = Depends(get_db)):
    decision = db.get(DecisionRecord, decision_id)
    if decision is None:
        raise HTTPException(404, "decision not found")
    override = DecisionOverride(
        decision_id=decision_id,
        requested_kind=body.requested_kind,
        reason=body.reason.strip(),
    )
    db.add(override)
    db.commit()
    return {
        "id": override.id,
        "decision_id": decision_id,
        "engine_verdict": decision.kind,
        "requested_kind": override.requested_kind,
        "reason": override.reason,
        "created_at": override.created_at,
    }


def association_service() -> AssociationService:
    """The running service, or a default one when lifespan has not run."""
    service = getattr(app.state, "association_service", None)
    if service is None:
        service = AssociationService(shadow_mode=True)
        app.state.association_service = service
    return service


CONFLICT_CODES = {
    "multiple_conflicting_explicit_ids",
    "explicit_id_contradiction",
    "every_candidate_contradicted_by_explicit_id",
    "competing_game_names_in_title",
    "title_names_a_different_experience",
    "generic_title_dense_evidence_only",
    "untrusted_injection_text",
    "duplicate_content_counted_once",
    "insufficient_top_two_margin",
    "insufficient_required_feature_coverage",
}


def _matching_candidate(record: AssociationRecord, candidate_id: str | None, db: Session):
    if not candidate_id:
        return None
    row = db.get(MatchCandidate, candidate_id)
    if row is None:
        return None
    entry = next(
        (item for item in (record.candidate_scoreboard or [])
         if item.get("candidate_id") == candidate_id),
        {},
    )
    return MatchingCandidateView(
        candidate_id=candidate_id,
        universe_id=row.universe_id or "",
        display_name=row.raw_name or "",
        score=float(entry.get("score", 0.0)),
        exact_id_evidence=bool(entry.get("exact_id_evidence", False)),
        hard_negative=bool(entry.get("hard_negative", False)),
        retrieval_methods=list(entry.get("retrieval_methods", [])),
    )


def _matching_review_view(db: Session, record: AssociationRecord) -> MatchingReviewView:
    subject = db.get(MatchSubject, record.subject_id)
    review = human_confirmation(db, record.id)
    artifacts = [
        MatchingArtifactView(
            id=artifact.id,
            url=sanitize_url(artifact.url),
            publisher_owner=artifact.publisher_owner,
            sha256=artifact.sha256,
            source_tier=artifact.source_tier,
            retrieval_method=artifact.retrieval_method,
            captured_at=artifact.captured_at,
        )
        for artifact_id in (record.source_artifact_ids or [])
        if (artifact := db.get(SourceArtifact, artifact_id)) is not None
    ]
    alternatives = [
        view for item in (record.candidate_scoreboard or [])
        if (view := _matching_candidate(record, item.get("candidate_id"), db)) is not None
    ]
    return MatchingReviewView(
        association_id=record.id,
        created_at=record.created_at,
        outcome=record.outcome,
        rationale_codes=list(record.rationale_codes or []),
        subject_id=record.subject_id,
        subject_type=subject.subject_type if subject else "",
        subject_external_id=subject.external_id if subject else "",
        subject_title=subject.raw_title if subject else "",
        subject_description=subject.raw_description if subject else "",
        subject_url=subject.raw_url if subject else "",
        subject_creator=subject.creator_name if subject else "",
        niche=subject.niche if subject else "",
        untrusted_codes=list(subject.untrusted_codes or []) if subject else [],
        duplicate_of_subject_id=subject.duplicate_of_subject_id if subject else None,
        candidate=_matching_candidate(record, record.candidate_id, db),
        runner_up=_matching_candidate(record, record.runner_up_candidate_id, db),
        alternatives=alternatives,
        features=dict(record.features or {}),
        feature_availability=dict(record.feature_availability or {}),
        feature_order=list(record.feature_order or []),
        top_score=record.top_score,
        runner_up_score=record.runner_up_score,
        margin=record.margin,
        required_coverage=record.required_coverage,
        conflict_warnings=[
            code for code in (record.rationale_codes or [])
            if code in CONFLICT_CODES or code.startswith("missing_required_field")
        ],
        matcher_version=record.matcher_version,
        feature_schema_version=record.feature_schema_version,
        normalization_version=record.normalization_version,
        threshold_version=record.threshold_version,
        embedding_model=record.embedding_model or "",
        embedding_model_hash=record.embedding_model_hash or "",
        embedding_available=record.embedding_available,
        shadow_mode=record.shadow_mode,
        validated_matcher=record.validated_matcher,
        usable_downstream=is_association_usable(db, record),
        artifacts=artifacts,
        review_verdict=review.verdict if review else None,
        review_reason=review.reason if review else None,
        review_selected_candidate_id=review.selected_candidate_id if review else None,
        reviewed_at=review.created_at if review else None,
    )


@app.get("/api/matching/status", response_model=MatchingStatus)
def matching_status(db: Session = Depends(get_db)):
    service = association_service()
    thresholds = active_thresholds()
    counts = dict(db.execute(
        select(AssociationRecord.outcome, func.count()).group_by(AssociationRecord.outcome)
    ).all())
    return MatchingStatus(
        matcher_version=thresholds.matcher_version,
        feature_schema_version=thresholds.feature_schema_version,
        normalization_version=thresholds.normalization_version,
        threshold_version=thresholds.threshold_version,
        weights_version=thresholds.weights_version,
        shadow_mode=service.shadow_mode,
        fuzzy_auto_enabled=thresholds.fuzzy_auto_enabled,
        validated=thresholds.validated,
        high_threshold=thresholds.high,
        low_threshold=thresholds.low,
        margin_threshold=thresholds.margin_min,
        min_required_coverage=thresholds.min_required_coverage,
        heldout_precision=thresholds.heldout_precision,
        heldout_decisions=thresholds.heldout_decisions,
        dataset_hash=thresholds.dataset_hash,
        embedding_model=thresholds.embedding_model,
        benchmark_reason=thresholds.benchmark_reason,
        artifact_error=thresholds.artifact_error,
        pending_reviews=len(service.pending_reviews(db, limit=1000)),
        total_associations=sum(counts.values()),
        outcome_counts={str(key): int(value) for key, value in counts.items()},
    )


@app.get("/api/matching/reviews", response_model=list[MatchingReviewView])
def list_matching_reviews(
    limit: int = 50, include_resolved: bool = False, db: Session = Depends(get_db)
):
    if include_resolved:
        records = list(db.scalars(
            select(AssociationRecord).order_by(AssociationRecord.created_at.desc()).limit(limit)
        ))
    else:
        records = association_service().pending_reviews(db, limit=limit)
    return [_matching_review_view(db, record) for record in records]


@app.get("/api/matching/reviews/{association_id}", response_model=MatchingReviewView)
def get_matching_review(association_id: str, db: Session = Depends(get_db)):
    record = db.get(AssociationRecord, association_id)
    if record is None:
        raise HTTPException(404, "association record not found")
    return _matching_review_view(db, record)


@app.post("/api/matching/reviews/{association_id}", response_model=MatchingReviewResult)
def submit_matching_review(
    association_id: str, body: MatchingReviewCreate, db: Session = Depends(get_db)
):
    """Record a human decision. The engine's original verdict is never edited."""
    try:
        review, override = association_service().record_review(
            db,
            association_id,
            verdict=body.verdict,
            reason=body.reason,
            selected_candidate_id=body.selected_candidate_id,
            reviewer=body.reviewer,
        )
    except KeyError as exc:
        raise HTTPException(404, f"not found: {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    record = db.get(AssociationRecord, association_id)
    facts = apply_association(db, record)
    db.commit()
    return MatchingReviewResult(
        review_id=review.id,
        association_id=association_id,
        verdict=review.verdict,
        engine_outcome=review.engine_outcome,
        engine_candidate_id=review.engine_candidate_id,
        selected_candidate_id=review.selected_candidate_id,
        reason=review.reason,
        override_id=override.id if override else None,
        facts_created=facts,
        created_at=review.created_at,
    )


@app.get("/api/calibration/status", response_model=CalibrationStatus)
def get_calibration_status(db: Session = Depends(get_db)):
    return calibration_status(db)


@app.get("/api/health")
async def health(db: Session = Depends(get_db)):
    settings = get_settings()
    ollama = {"available": False, "primary_present": False, "fallback_present": False}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{settings.ollama_base_url}/api/tags")
            response.raise_for_status()
            names = {item.get("name", "") for item in response.json().get("models", [])}
            ollama = {
                "available": True,
                "primary_present": any(name.startswith(settings.ollama_primary_model) for name in names),
                "fallback_present": any(name.startswith(settings.ollama_fallback_model) for name in names),
            }
    except httpx.HTTPError:
        pass
    state = db.get(SystemState, "last_snapshot")
    # 2 — whether dense retrieval can actually run, rather than a hardcoded
    # "ready". The package check is cheap; the observed flag comes from what
    # the engine last managed to do, which is the part that matters.
    latest_association = db.scalar(
        select(AssociationRecord).order_by(AssociationRecord.created_at.desc()).limit(1)
    )
    return {
        "status": "ok",
        "database": "connected",
        "quotas": {row.key: row.value_json for row in db.scalars(select(SystemState).where(SystemState.key.like("quota:%:" + str(datetime.now(UTC).date()))))},
        "ollama": {**ollama, "base_url": settings.ollama_base_url},
        "embeddings": {
            "package_installed": importlib.util.find_spec("fastembed") is not None,
            "model_name": DEFAULT_EMBEDDING_MODEL,
            "last_association_used_embeddings": (
                bool(latest_association.embedding_available) if latest_association else None
            ),
        },
        "connectors": {
            "tavily_configured": bool(settings.tavily_api_key),
            "youtube_configured": bool(settings.youtube_api_key),
        },
        "scheduler": {
            "timezone": settings.timezone,
            "daily_at": f"{settings.snapshot_hour:02d}:{settings.snapshot_minute:02d}",
            "last_snapshot": state.value_json if state else None,
        },
        "calibration": calibration_status(db).model_dump(),
    }


frontend_dist = ROOT / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="dashboard")


def run() -> None:
    settings = get_settings()
    assert_local_only(settings.host)
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
