from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from .calibration import calibration_status
from .config import ROOT, get_settings
from .db import SessionLocal, get_db, init_db
from .evidence import candidate_facts, fact_freshness, render_fact
from .models import (
    Candidate,
    ConfidenceRecord,
    DecisionOverride,
    DecisionRecord,
    Fact,
    Observation,
    Proposal,
    ResearchRun,
    RunStatus,
    ScoreRecord,
    SourceArtifact,
    SystemState,
)
from .scheduler import catch_up_if_needed, start_scheduler
from .schemas import (
    AuditView,
    CalibrationStatus,
    CandidateView,
    FactView,
    OverrideCreate,
    ProposalPayload,
    ResearchRunCreate,
    RunView,
)
from .workflows import ResearchOrchestrator

TASKS: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        orphaned = list(db.scalars(select(ResearchRun).where(
            ResearchRun.status.in_([RunStatus.QUEUED.value, RunStatus.RUNNING.value])
        )))
        for run in orphaned:
            run.status = RunStatus.FAILED.value
            run.message = "Interrupted by a previous service shutdown; no evidence was fabricated."
            run.completed_at = datetime.now(UTC)
        db.commit()
    app.state.orchestrator = ResearchOrchestrator(SessionLocal)
    app.state.scheduler = start_scheduler()
    await catch_up_if_needed()
    yield
    app.state.scheduler.shutdown(wait=False)
    await app.state.orchestrator.close()


app = FastAPI(title="Roblox Venture Agents", version="0.1.0", lifespan=lifespan)


def _candidate_view(db: Session, candidate: Candidate) -> CandidateView:
    facts = candidate_facts(db, candidate.id)
    fact_views = [
        FactView(
            id=fact.id,
            text=render_fact(db, fact),
            source_ids=fact.source_ids,
            freshness=fact_freshness(db, fact),
            verification_state=fact.verification_state,
        ) for fact in facts
    ]
    name = "Sourced Roblox experience"
    if candidate.display_name_observation_id:
        observation = db.get(Observation, candidate.display_name_observation_id)
        if observation is not None:
            name = str(observation.value_json)
    proposal_row = db.scalar(
        select(Proposal).where(Proposal.candidate_id == candidate.id).order_by(Proposal.created_at.desc())
    )
    proposal = ProposalPayload.model_validate(proposal_row.payload) if proposal_row else None
    decision = db.scalar(
        select(DecisionRecord).where(DecisionRecord.candidate_id == candidate.id).order_by(DecisionRecord.created_at.desc())
    )
    score = db.get(ScoreRecord, decision.score_id) if decision and decision.score_id else None
    confidence = db.get(ConfidenceRecord, decision.confidence_id) if decision and decision.confidence_id else None
    return CandidateView(
        id=candidate.id,
        external_id=candidate.external_id,
        display_name=name,
        facts=fact_views,
        proposal=proposal,
        decision=decision.kind if decision else "collection_only",
        decision_id=decision.id if decision else None,
        score=score.value if score else None,
        confidence=confidence.value if confidence else None,
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
    )


@app.post("/api/research-runs", response_model=RunView, status_code=202)
async def create_research_run(body: ResearchRunCreate, db: Session = Depends(get_db)):
    run = ResearchRun(niche=" ".join(body.niche.split()))
    db.add(run)
    db.commit()
    task = asyncio.create_task(app.state.orchestrator.research(run.id))
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)
    return _run_view(db, run)


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
                payload = {"status": run.status, "message": run.message}
            encoded = json.dumps(payload)
            if encoded != previous:
                yield f"event: progress\ndata: {encoded}\n\n"
                previous = encoded
            if payload["status"] in {RunStatus.COMPLETE.value, RunStatus.FAILED.value}:
                return
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/candidates/{candidate_id}/audit", response_model=AuditView)
async def audit_candidate(candidate_id: str, db: Session = Depends(get_db)):
    if db.get(Candidate, candidate_id) is None:
        raise HTTPException(404, "candidate not found")
    try:
        result = await app.state.orchestrator.audit(candidate_id)
        return AuditView.model_validate(result)
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
            "url": artifact.url,
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
    return {
        "status": "ok",
        "database": "connected",
        "ollama": ollama,
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
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
