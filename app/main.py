from __future__ import annotations

import asyncio
import importlib.util
import json
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import hmac

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Text, func, select
from sqlalchemy.orm import Session

from .association import AssociationService, active_thresholds, is_association_usable
from .association.materialize import apply_association
from .association.service import human_confirmation
from . import access, audit_activity, dependency_health
from .binding import wait_for_address
from .build_identity import build_identity
from .audit_jobs import AuditJobs, JobConflict, ACTIVE as ACTIVE_AUDIT_STATES
from .calibration import calibration_status, load_artifact
from .config import ROOT, get_settings
from .db import SessionLocal, get_db, init_db
from .evidence import fact_freshness, render_fact
from .matching import DEFAULT_EMBEDDING_MODEL
from . import pillars as pillars_module
from . import scout_queue
from . import opportunity as opportunity_module
from . import calibration_outcomes
from .research_budget import utc as budget_utc
from .models import (
    AssociationRecord,
    AuditActivityEvent,
    AuditRecord,
    Candidate,
    ConfidenceRecord,
    MarketSample,
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
    ScoutAuditRun,
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
    AgentRunView,
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
    ScoutQueueRun,
)
from .security import RedactedResponses, install_log_redaction, sanitize_url
from .workflows import ResearchOrchestrator

TASKS: set[asyncio.Task] = set()
# The same tasks, keyed by the run each one is doing. `TASKS` can only be
# cancelled wholesale, which is what shutdown wants and what an operator
# stopping one run does not.
RUN_TASKS: dict[str, asyncio.Task] = {}


def start_run(run_id: str) -> None:
    """Launch a research task, remembering which run it belongs to.

    Three places start these -- startup picking up what a restart interrupted,
    a new run, and a resume -- and a Stop that knew about only one of them
    would report success while the run carried on.
    """
    task = asyncio.create_task(app.state.orchestrator.research(run_id))
    TASKS.add(task)
    RUN_TASKS[run_id] = task

    def forget(finished: asyncio.Task) -> None:
        TASKS.discard(finished)
        # Only if it is still this run's task: a resume registers a newer one,
        # and the old task finishing must not unregister it.
        if RUN_TASKS.get(run_id) is finished:
            del RUN_TASKS[run_id]

    task.add_done_callback(forget)


LOOPBACK_HOSTS = access.LOOPBACK_HOSTS


def assert_local_only(host: str) -> None:
    """Refuse a wider bind unless a token is configured.

    Kept under its original name because callers and tests use it. The rule
    itself moved to `app.access`: loopback needs nothing, anything wider needs
    `DASHBOARD_TOKEN`.
    """
    access.assert_bindable(host, get_settings().dashboard_token)


SERVICE_STARTED_AT = datetime.now(UTC)


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_local_only(get_settings().host)
    init_db()
    with SessionLocal() as db:
        orphaned = list(db.scalars(select(ResearchRun).where(
            ResearchRun.status.in_([RunStatus.QUEUED.value, RunStatus.RUNNING.value])
        )))
        # A deep run keeps a checkpoint, so an interruption is recoverable and
        # leaving it stopped throws away everything it already paid for. This
        # has cost two real runs: both died on their last step -- 29 games
        # inspected, concepts about to be written -- because the service was
        # restarted underneath them, and both then sat waiting for someone to
        # notice and press a button.
        #
        # A run with no checkpoint, or a quick run, cannot resume and is still
        # recorded as interrupted rather than pretended over.
        resumable = []
        for run in orphaned:
            checkpoint = db.get(ResearchCheckpoint, run.id)
            if checkpoint and checkpoint.state.get("mode") == "deep":
                run.status = RunStatus.QUEUED.value
                run.message = "Resuming after a service restart; the checkpoint was kept."
                run.completed_at = None
                resumable.append(run.id)
                continue
            run.status = "interrupted"
            run.message = "Interrupted by a previous service shutdown; no evidence was fabricated."
            run.completed_at = datetime.now(UTC)

        orphaned_audits = list(db.scalars(select(ScoutAuditRun).where(
            ScoutAuditRun.status == "running"
        )))
        for scout_run in orphaned_audits:
            scout_run.status = "interrupted"
            scout_run.message = "Interrupted by a previous service shutdown; ledger integrity preserved."
            scout_run.completed_at = datetime.now(UTC)
            max_seq = db.scalar(
                select(func.max(AuditActivityEvent.sequence))
                .where(AuditActivityEvent.run_id == scout_run.id)
            ) or 0
            interrupted_event = AuditActivityEvent(
                candidate_id=scout_run.candidate_id,
                run_id=scout_run.id,
                sequence=max_seq + 1,
                stage="interrupted",
                detail="Service was restarted while audit was in flight. Evidence and ledger integrity preserved.",
                extra_json={"interrupted": True},
                created_at=datetime.now(UTC),
            )
            db.add(interrupted_event)
        db.commit()
    app.state.orchestrator = ResearchOrchestrator(SessionLocal)
    app.state.audit_jobs = AuditJobs(SessionLocal, app.state.orchestrator)
    app.state.audit_jobs.reconcile()
    # Shadow mode stays on until a benchmark validates fuzzy matching.
    app.state.association_service = app.state.orchestrator.associations
    app.state.scheduler = start_scheduler()
    await catch_up_if_needed()
    # Started after the orchestrator exists, and not awaited: a run takes
    # minutes and startup must not block on it.
    for run_id in resumable:
        start_run(run_id)
    yield
    app.state.scheduler.shutdown(wait=False)
    await app.state.audit_jobs.close()
    for task in TASKS:
        task.cancel()
    await asyncio.gather(*TASKS, return_exceptions=True)
    await app.state.orchestrator.close()


app = FastAPI(title="Roblox Venture Agents", version="0.1.0", lifespan=lifespan)
install_log_redaction()
app.add_middleware(RedactedResponses)

# The Blueprint stage: idea -> refined build -> specification. A router rather
# than more routes in this file, which is long enough.
from .blueprint.api import build_router, router as blueprint_router, studio_router  # noqa: E402

app.include_router(blueprint_router)
app.include_router(studio_router)
app.include_router(build_router)


@app.middleware("http")
async def require_token(request, call_next):
    """Every request, including the static frontend.

    Gating only `/api` would serve the application shell to anyone and leave
    the credential as the only thing between them and a running agent.
    """
    try:
        access.check(request, get_settings().dashboard_token)
    except HTTPException as exc:
        from fastapi.responses import JSONResponse, RedirectResponse
        # A browser typing the address gets the sign-in page; an API caller
        # gets the status code, because a redirect would look like success.
        if access.wants_html(request) and not request.url.path.startswith("/api/"):
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return await call_next(request)


@app.get("/api/health/live")
def liveness():
    """Unauthenticated on purpose: a platform health probe holds no token."""
    return {"status": "ok"}


LOGIN_PAGE = """<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Sign in</title>
<style>body{font:15px/1.5 system-ui,sans-serif;background:#f8f9fa;color:#202124;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:24px}
form{background:#fff;border:1px solid #dadce0;border-radius:8px;padding:28px;max-width:360px;width:100%}
h1{font-size:18px;margin:0 0 6px}p{color:#5f6368;font-size:13px;margin:0 0 18px}
input{width:100%;box-sizing:border-box;padding:10px;border:1px solid #dadce0;border-radius:4px;font:inherit}
button{margin-top:12px;width:100%;padding:10px;border:0;border-radius:4px;background:#202124;color:#fff;font:inherit;cursor:pointer}
.err{color:#c5221f;font-size:13px;margin-top:10px;min-height:18px}</style>
<form onsubmit="go(event)"><h1>Roblox Venture Agents</h1>
<p>This dashboard is reachable from your tailnet. Enter the access token.</p>
<input id=t type=password autocomplete=current-password placeholder="Access token" autofocus
 onkeydown="if(event.key==='Enter'){go(event)}">
<button type=submit>Sign in</button><div class=err id=e></div></form>
<script>async function go(ev){ev.preventDefault();document.getElementById('e').textContent='';
try{const r=await fetch('/api/session',{method:'POST',headers:{'content-type':'application/json'},
body:JSON.stringify({token:document.getElementById('t').value})});
if(r.ok){location.href='/';}
else if(r.status===401){document.getElementById('e').textContent='That token was not accepted.';}
else{document.getElementById('e').textContent='Sign-in failed ('+r.status+'). The service may have just restarted.';}}
catch(err){document.getElementById('e').textContent='Could not reach the service.';}}</script>"""


@app.get("/login")
def login_page():
    from fastapi.responses import HTMLResponse
    return HTMLResponse(LOGIN_PAGE)


@app.post("/api/session")
async def create_session(request: Request):
    """Exchange the token for a cookie the browser can carry.

    Deliberately slow and identical on failure: this is the one endpoint that
    has to be open, so a wrong guess must cost the guesser something and tell
    them nothing.
    """
    from fastapi.responses import JSONResponse

    configured = get_settings().dashboard_token
    if not configured:
        raise HTTPException(404, "no token is configured; this service is loopback-only")
    body = await request.json()
    offered = str(body.get("token", "")) if isinstance(body, dict) else ""
    if not access.same_secret(offered, configured):
        await asyncio.sleep(1.0)
        raise HTTPException(401, "authentication required")
    response = JSONResponse({"status": "ok"})
    response.set_cookie("dashboard_token", configured, httponly=True, samesite="lax",
                        max_age=60 * 60 * 24 * 30, path="/")
    return response


def _audited_candidate_ids(db: Session) -> set[str]:
    """Candidates that already have a stored audit, resolved once per session.

    The Idea Panel had no way to tell which of a hundred-odd candidates had
    been audited, so the only way to find a finished brief was to open them one
    at a time. Audit records are append-only, so the row count is enough to
    know the cached answer is still current.
    """
    total = db.scalar(select(func.count(AuditRecord.id))) or 0
    cached = db.info.get("audited_candidates")
    if cached is not None and cached[0] == total:
        return cached[1]
    audited = set(db.scalars(select(AuditRecord.candidate_id).distinct()))
    db.info["audited_candidates"] = (total, audited)
    return audited


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
        has_audit=candidate.id in _audited_candidate_ids(db),
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
    start_run(run.id)
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
            if payload["status"] in {"complete", "failed", "partial", "interrupted", "cancelled"}:
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


@app.post("/api/candidates/{candidate_id}/audit-jobs", status_code=202)
async def start_audit_job(candidate_id: str, operation: str, proposal_id: str | None = None):
    try:
        return app.state.audit_jobs.start(candidate_id, operation, proposal_id)
    except KeyError:
        raise HTTPException(404, "candidate not found")
    except JobConflict as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _busy_candidates() -> set[str]:
    """Games with a Scout job already in flight, from the job store itself."""
    jobs = getattr(app.state, "audit_jobs", None)
    if jobs is None:
        return set()
    busy = set()
    with SessionLocal() as db:
        for row in db.scalars(select(SystemState).where(SystemState.key.like("audit-job:%"))):
            job = row.value_json
            if job.get("status") in ACTIVE_AUDIT_STATES:
                busy.add(job.get("candidate_id"))
    return busy


@app.get("/api/scout/queue")
def scout_queue_view(db: Session = Depends(get_db)):
    """Every Hunter concept routed to the Scout and still waiting.

    Routing is automatic and running is not, so this reports what *would* run
    and leaves the decision alone. The count is what the run button shows.
    """
    rows = scout_queue.pending(db, active_candidates=_busy_candidates())
    runnable = [row for row in rows if row["available"]]
    return {
        "queued": rows,
        "runnable": len(runnable),
        "blocked": len(rows) - len(runnable),
        "note": "Hunter concepts reach this queue on their own. Nothing runs "
                "until you start it: each audit spends several minutes of the "
                "local model, and the model runs one request at a time.",
    }


@app.post("/api/scout/queue/run", status_code=202)
async def run_scout_queue(selection: ScoutQueueRun, db: Session = Depends(get_db)):
    """Start one Scout audit per selected concept.

    Every job is started now and they serialize behind the model's own gate,
    so this returns immediately with the jobs to follow rather than holding a
    request open for what can be half an hour of work.

    A selection that cannot run is reported per concept. One unstartable
    concept does not cancel the others: the caller asked for the set, and
    silently dropping part of it would leave them waiting for a result that
    was never going to come.
    """
    started, skipped = [], []
    known = {row["proposal_id"]: row for row in scout_queue.pending(db)}
    for proposal_id in dict.fromkeys(selection.proposal_ids):
        entry = known.get(proposal_id)
        if entry is None:
            skipped.append({"proposal_id": proposal_id,
                            "reason": "Not in the queue; it may already be audited"})
            continue
        try:
            started.append(app.state.audit_jobs.start(entry["candidate_id"], "audit_idea", proposal_id))
        except (JobConflict, ValueError, KeyError) as exc:
            skipped.append({"proposal_id": proposal_id, "reason": str(exc) or "Could not start"})
    # Labelled here rather than on the polling endpoint: the page shows one
    # card per job and a card needs the concept's name, but the per-job poll
    # runs every 1.5 seconds and must stay cheap.
    return {"started": scout_queue.label(db, started), "skipped": skipped}


@app.get("/api/agents/status")
def agents_status(db: Session = Depends(get_db)):
    """Which agents are working, answered cheaply enough to poll.

    The sidebar's "Running" badge was read out of `/api/research-runs`, which
    builds a full candidate view -- an evidence packet and a citation
    re-verification -- for every candidate of the last twenty-five runs. At 256
    candidates that is seven seconds of database work, polled on a five-second
    timer, so the badge was always reporting the state of the run as it had
    been several seconds earlier and a finished run kept claiming to be
    running until the next slow answer landed.

    This reads the run rows and nothing else. No candidate is loaded, no
    evidence is packed, and no citation is re-checked.
    """
    active = list(db.scalars(
        select(ResearchRun)
        .where(ResearchRun.status.in_([RunStatus.QUEUED.value, RunStatus.RUNNING.value]))
        .order_by(ResearchRun.created_at.desc())
    ))
    latest = db.scalar(select(ResearchRun).order_by(ResearchRun.created_at.desc()).limit(1))
    audits = [job for job in (app.state.audit_jobs.recent(20)
                              if getattr(app.state, "audit_jobs", None) else [])
              if job.get("status") in ACTIVE_AUDIT_STATES]
    return {
        "hunter": {
            "running": bool(active),
            "runs": [{"id": run.id, "niche": run.niche, "status": run.status,
                      **{key: progress(db, run)[key] for key in ("stage", "elapsed_seconds")}}
                     for run in active],
            "latest": None if latest is None else {
                "id": latest.id, "niche": latest.niche, "status": latest.status,
                "completed_at": latest.completed_at.isoformat() if latest.completed_at else None,
            },
        },
        "scout": {
            "running": bool(audits),
            "jobs": [{"id": job["id"], "status": job["status"],
                      "candidate_id": job.get("candidate_id")} for job in audits],
        },
        # Stamped by the server so the page can say how old its answer is
        # rather than presenting a stale reading as current.
        "observed_at": datetime.now(UTC).isoformat(),
    }


@app.get("/api/scout/results")
def scout_results(limit: int = 60, db: Session = Depends(get_db)):
    return {"cards": scout_queue.audited(db, limit=limit)}


@app.get("/api/audit-jobs/{job_id}")
def get_audit_job(job_id: str):
    try:
        return app.state.audit_jobs.get(job_id)
    except KeyError:
        raise HTTPException(404, "audit job not found")


@app.get("/api/audit-jobs")
def list_audit_jobs(limit: int = 20, db: Session = Depends(get_db)):
    """Recent Scout jobs, so the page can offer Resume or Restart.

    Labelled with the concept each job is auditing, so a reload still shows
    readable cards instead of truncated identifiers.
    """
    jobs = scout_queue.label(db, app.state.audit_jobs.recent(limit))
    return {"jobs": jobs,
            "resumable": [job["id"] for job in jobs if job.get("status") == "interrupted"],
            "restartable": [job["id"] for job in jobs
                            if job.get("status") in {"failed", "timed_out", "cancelled",
                                                     "interrupted", "blocked"}]}


@app.post("/api/audit-jobs/{job_id}/restart", status_code=202)
def restart_audit_job(job_id: str):
    try:
        return app.state.audit_jobs.restart(job_id)
    except KeyError:
        raise HTTPException(404, "job not found")
    except JobConflict as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/audit-jobs/{job_id}/cancel")
async def cancel_audit_job(job_id: str):
    get_audit_job(job_id)
    return await app.state.audit_jobs.cancel(job_id)


@app.post("/api/audit-jobs/{job_id}/resume", status_code=202)
async def resume_audit_job(job_id: str):
    get_audit_job(job_id)
    try:
        return app.state.audit_jobs.resume(job_id)
    except JobConflict as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/audit-jobs/{job_id}/events")
async def audit_job_events(job_id: str, after: int = 0):
    get_audit_job(job_id)
    async def stream():
        sequence = max(0, after)
        while True:
            job = app.state.audit_jobs.get(job_id)
            for event in job["events"]:
                if event["sequence"] > sequence:
                    sequence = event["sequence"]
                    yield f"id: {sequence}\ndata: {json.dumps(event)}\n\n"
            if job["status"] not in ACTIVE_AUDIT_STATES:
                yield f"event: done\ndata: {json.dumps({'status': job['status']})}\n\n"
                break
            yield ": keepalive\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


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


def _candidate_labels(db: Session, candidate_ids: set[str]) -> dict[str, tuple[str, str, str]]:
    """Name, run id and niche for each candidate, in two queries.

    Deliberately not `_candidate_view`: that rebuilds the whole evidence packet
    per candidate, which is far too much work to label a list of history rows.
    """
    if not candidate_ids:
        return {}
    names: dict[str, str] = {}
    for row in db.scalars(
        select(Observation)
        .where(Observation.candidate_id.in_(candidate_ids), Observation.metric == "roblox_name")
        .order_by(Observation.observed_at)
    ):
        names[row.candidate_id] = str(row.value_json)
    labels: dict[str, tuple[str, str, str]] = {}
    for candidate, run in db.execute(
        select(Candidate, ResearchRun)
        .join(ResearchRun, ResearchRun.id == Candidate.run_id, isouter=True)
        .where(Candidate.id.in_(candidate_ids))
    ):
        labels[candidate.id] = (
            names.get(candidate.id, "Sourced Roblox experience"),
            run.id if run else "",
            run.niche if run else "",
        )
    return labels


@app.get("/api/agent-runs/count")
def count_agent_runs(kind: str = "all", db: Session = Depends(get_db)):
    """How many runs exist behind the page being shown.

    The history page said "47 of 47" because it only ever knew about the rows
    it had been handed, which is not the same claim.
    """
    audits = db.scalar(select(func.count(AuditRecord.id))) or 0
    concepts = db.scalar(
        select(func.count(Proposal.id)).where(Proposal.agent == "meta_hunter")
    ) or 0
    total = {"venture_scout": audits, "meta_hunter": concepts}.get(kind, audits + concepts)
    return {"total": total, "venture_scout": audits, "meta_hunter": concepts}


@app.get("/api/agent-runs")
def list_agent_runs(limit: int = 100, offset: int = 0, kind: str = "all", paged: bool = False,
                    db: Session = Depends(get_db)):
    """Every Meta Hunter concept and Venture Scout audit, newest first.

    Both kinds are queried in their own descending order and merged, so paging
    happens after the merge: an offset applied per-kind would interleave the
    two lists differently on every page.
    """
    safe_limit = max(1, min(limit, 500))
    safe_offset = max(0, offset)
    # Enough of each kind that the merged window certainly covers this page,
    # whichever kind happens to dominate it.
    window = safe_offset + safe_limit
    audits = concepts = 0
    rows: list[tuple] = []
    if kind in {"all", "venture_scout"}:
        audits = db.scalar(select(func.count(AuditRecord.id))) or 0
        rows += [("venture_scout", row) for row in db.scalars(
            select(AuditRecord).order_by(AuditRecord.created_at.desc()).limit(window)
        )]
    if kind in {"all", "meta_hunter"}:
        concepts = db.scalar(
            select(func.count(Proposal.id)).where(Proposal.agent == "meta_hunter")
        ) or 0
        rows += [("meta_hunter", row) for row in db.scalars(
            select(Proposal).where(Proposal.agent == "meta_hunter")
            .order_by(Proposal.created_at.desc()).limit(window)
        )]
    rows.sort(key=lambda item: item[1].created_at, reverse=True)
    # Counted in the database. Measuring the fetched window instead would
    # report the page size and call it the total.
    total = audits + concepts
    rows = rows[safe_offset:safe_offset + safe_limit]
    labels = _candidate_labels(db, {row.candidate_id for _, row in rows})
    # Several runs usually share a candidate, and building the packet is the
    # expensive part, so resolve each candidate once for the whole page.
    packets = {
        candidate_id: {item["id"]: item for item in evidence_packet(db, candidate_id)}
        for candidate_id in {row.candidate_id for _, row in rows}
    }

    views: list[AgentRunView] = []
    for row_kind, row in rows:
        name, run_id, niche = labels.get(row.candidate_id, ("Sourced Roblox experience", "", ""))
        raw = row.payload if row_kind == "meta_hunter" else (row.payload or {}).get("proposal")
        try:
            design = ProposalPayload.model_validate(raw) if raw else None
        except ValueError:
            # Stored before the current firewall; listed, but not rendered as
            # though it still satisfies it.
            design = None
        stored = row.payload if row_kind == "venture_scout" else {}
        # A stored citation list records what was admissible when the run
        # happened; the brief re-resolves it, and so does this.
        live = packets.get(row.candidate_id, {})
        claimed = list(dict.fromkeys(design.supporting_fact_ids if design else []))
        cited = [fact_id for fact_id in claimed if fact_id in live]
        withdrawn = [fact_id for fact_id in claimed if fact_id not in live]
        views.append(AgentRunView(
            id=row.id,
            kind=row_kind,
            created_at=row.created_at,
            run_id=run_id or None,
            niche=niche,
            candidate_id=row.candidate_id,
            candidate_name=name,
            title=design.concept_title if design else "",
            summary=(design.executive_summary or design.core_loop) if design else "",
            outcome=("design" if design else "blocked") if row_kind == "venture_scout" else (
                "concept" if design else "unreadable"),
            model_name=getattr(row, "model_name", "") or "",
            operation=stored.get("operation") if row_kind == "venture_scout" else None,
            cited_fact_ids=cited,
            cited_facts=[
                FactView(id=fact_id, text=live[fact_id]["text"], source_ids=live[fact_id]["source_ids"],
                         freshness=live[fact_id]["freshness"], verification_state="source_backed")
                for fact_id in cited
            ],
            withdrawn_fact_ids=withdrawn,
            blocking_reasons=list(stored.get("risks", [])) if row_kind == "venture_scout" and not design else [],
            payload=design,
        ))
    if not paged:
        return views
    return {"items": views, "total": total, "offset": safe_offset, "limit": safe_limit,
            "next_offset": safe_offset + len(views) if safe_offset + len(views) < total else None}


@app.get("/api/candidates/{candidate_id}/audit-activity")
async def audit_activity_stream(candidate_id: str):
    """Live feed of what a running audit is doing.

    An audit takes minutes and used to report nothing until it finished, so
    there was no way to tell a working run from a stuck one. The feed
    describes the work; it never carries model output, and it is in-process
    only, so a restart ends it. The stored audit remains the record.
    """
    async def stream():
        sent = 0
        idle = 0
        while True:
            state = audit_activity.snapshot(candidate_id)
            fresh = state["events"][sent:]
            for event in fresh:
                yield f"event: activity\ndata: {json.dumps(event)}\n\n"
            sent = len(state["events"])
            if fresh:
                idle = 0
            if not state["running"] and sent:
                yield "event: done\ndata: {}\n\n"
                return
            idle += 1
            # Nothing has ever started for this candidate; do not hold the
            # connection open indefinitely waiting for something that is not
            # coming.
            if idle > 60 and not sent:
                yield "event: idle\ndata: {}\n\n"
                return
            await audit_activity.wait(candidate_id, timeout=2.0)

    return StreamingResponse(stream(), media_type="text/event-stream")


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
    start_run(run_id)
    return _run_view(db, run)


@app.post("/api/research-runs/{run_id}/cancel", response_model=RunView)
async def cancel_research_run(run_id: str, db: Session = Depends(get_db)):
    """Stop a run the operator no longer wants, keeping what it already collected.

    The status is written before the task is cancelled, the way
    `AuditJobs.cancel` does it. `ResearchOrchestrator.research` catches
    `Exception`, and `CancelledError` is not one, so nothing downstream
    overwrites the row on the way out. That ordering is also what lets the deep
    controller tell a decision from a shutdown.

    Stopping is not resuming's opposite number: a cancelled run is finished on
    purpose and is not offered a resume. Restart is the way back to that niche.
    """
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(404, "research run not found")
    if run.status not in (RunStatus.QUEUED.value, RunStatus.RUNNING.value):
        raise HTTPException(409, f"That run is already {run.status}; only a queued or running one can be stopped")
    run.status = RunStatus.CANCELLED.value
    run.message = "Stopped by the operator; everything it had already collected is kept"
    run.completed_at = datetime.now(UTC)
    db.commit()
    task = RUN_TASKS.get(run_id)
    if task is not None:
        task.cancel()
        # Awaited rather than left to unwind on its own: until it does, the run
        # is still spending the budget and writing to the ledger, and a Stop
        # that returns while the work continues is the bug this fixes.
        await asyncio.gather(task, return_exceptions=True)
    db.refresh(run)
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
    """The size of what the source sent, which the UI labels "raw captured
    bytes". Stored payloads are compressed, so the file on disk is roughly a
    sixth of that and reporting it here would understate every capture."""
    if artifact.raw_size:
        return artifact.raw_size
    try:  # captures that predate size recording
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


@app.get("/api/market/pulse")
def market_pulse_view(db: Session = Depends(get_db)):
    """The latest census of Roblox's own front page.

    Reports when it was taken as well as what it said. A census read without
    its age is worse than no census: shelves turn over within a day, and a
    stale sample presented as current is exactly the kind of confident wrong
    number the pillars exist to avoid.
    """
    latest = db.scalar(select(MarketSample.captured_at)
                       .order_by(MarketSample.captured_at.desc()).limit(1))
    if latest is None:
        return {"captured_at": None, "samples": 0, "rows": 0, "shelves": [],
                "genres": [], "rising": [],
                "note": "No census has been sampled yet. The sampler runs on a "
                        "schedule; the first reading appears after it fires."}
    rows = list(db.scalars(select(MarketSample).where(MarketSample.captured_at == latest)))
    captures = db.scalar(select(func.count(func.distinct(MarketSample.captured_at)))) or 0
    genres: dict[str, set[str]] = {}
    for row in rows:
        genres.setdefault(row.genre or "unreported", set()).add(row.universe_id)
    universes = {row.universe_id for row in rows}
    shelves: dict[str, int] = {}
    for row in rows:
        shelves[row.sort_id] = shelves.get(row.sort_id, 0) + 1
    # Up-and-Coming is Roblox's own answer to "what grew in the last day",
    # which is the single hardest signal to reconstruct from anywhere else.
    rising = [row for row in rows if row.sort_id == "up-and-coming"]
    return {
        # SQLite hands back a naive datetime even for a timezone-aware column,
        # and a browser reads a naive ISO string as local time. Sent bare, a
        # census taken seventeen minutes ago would be rendered as hours stale
        # wherever the machine is not on UTC.
        "captured_at": budget_utc(latest).isoformat(),
        "samples": captures,
        "rows": len(rows),
        "universes": len(universes),
        "shelves": [{"sort_id": key, "games": value} for key, value in sorted(shelves.items())],
        "genres": sorted(({"genre": key, "games": len(value),
                           "share": len(value) / len(universes)} for key, value in genres.items()),
                         key=lambda entry: -entry["games"]),
        "rising": [{"universe_id": row.universe_id, "name": row.name, "rank": row.rank,
                    "player_count": row.player_count, "genre": row.genre,
                    "up_votes": row.up_votes, "down_votes": row.down_votes}
                   for row in sorted(rising, key=lambda row: row.rank)],
        "pillars_version": pillars_module.PILLARS_VERSION,
        "note": "A census of what Roblox ranks, not a recommendation. Placement "
                "on a shelf is Roblox's judgement, not this system's.",
    }


@app.get("/api/opportunity/{universe_id}")
def opportunity_for(universe_id: str, db: Session = Depends(get_db)):
    """Deterministic ranking for one game, with every component shown.

    No model is consulted. The score is arithmetic over measured pillars, and
    it is a ranking index rather than a probability until calibration has
    outcomes to measure against.
    """
    ranked = opportunity_module.score(db, universe_id).as_dict()
    ranked["calibration"] = calibration_outcomes.probability_for(db, universe_id)
    return ranked


@app.get("/api/opportunity")
def opportunity_ranking(limit: int = 25, db: Session = Depends(get_db)):
    """Every researched game, best first, unscoreable ones last."""
    universes = [row for row in db.scalars(select(Candidate.external_id).distinct()) if row]
    ranked = opportunity_module.rank(db, universes)[:limit]
    return {"version": opportunity_module.OPPORTUNITY_VERSION,
            "ranked": [item.as_dict() for item in ranked],
            "scored": sum(item.score is not None for item in ranked),
            "note": "A comparable ranking index over measured pillars. Not a "
                    "probability, and not a recommendation."}


@app.get("/api/calibration/outcomes")
def calibration_outcomes_view(target_ccu: int = calibration_outcomes.DEFAULT_TARGET_CCU,
                              db: Session = Depends(get_db)):
    """The observed rate of reaching a CCU target, per score band."""
    return calibration_outcomes.calibrate(db, target_ccu).as_dict()


@app.get("/api/market/game/{universe_id}")
def market_game_pillars(universe_id: str, db: Session = Depends(get_db)):
    """Measured dimensions for one game. Never a combined score."""
    return pillars_module.pillars_for(db, universe_id)


@app.get("/api/sources")
def list_sources(limit: int = 100, offset: int = 0, q: str = "", paged: bool = False,
                 db: Session = Depends(get_db)):
    """One page of captured artifacts, with the total behind it.

    The list returned a capped slice with no total and no way to reach the rest,
    so a search box filtered whatever happened to be in the first hundred rows
    and quietly reported nothing for everything past them. Filtering happens in
    the database now, and the caller is told how many records the filter
    actually matched.

    `paged=false` keeps the original bare-array shape for existing callers.
    """
    safe_limit = max(1, min(limit, 500))
    safe_offset = max(0, offset)
    query = select(SourceArtifact)
    term = q.strip()
    if term:
        like = f"%{term}%"
        query = query.where(
            SourceArtifact.url.ilike(like)
            | SourceArtifact.publisher_owner.ilike(like)
            | SourceArtifact.retrieval_method.ilike(like)
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(
        db.scalars(
            query.order_by(SourceArtifact.captured_at.desc())
            .offset(safe_offset)
            .limit(safe_limit)
        )
    )
    observation_counts = dict(
        db.execute(
            select(Observation.artifact_id, func.count(Observation.id))
            .group_by(Observation.artifact_id)
        ).all()
    )
    items = [{
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
    if not paged:
        return items
    return {"items": items, "total": total, "offset": safe_offset, "limit": safe_limit,
            "next_offset": safe_offset + len(items) if safe_offset + len(items) < total else None}


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


@app.get("/api/matching/reviews/count")
def count_matching_reviews(include_resolved: bool = False, db: Session = Depends(get_db)):
    """How many associations sit behind the capped list being shown.

    The queue rendered whatever fitted in one request, so a reviewer had no way
    to tell fifty outstanding from five hundred.
    """
    total = db.scalar(select(func.count(AssociationRecord.id))) or 0
    pending = db.scalar(
        select(func.count(AssociationRecord.id))
        .where(AssociationRecord.outcome.in_(("review_required", "blocked_conflict")))
    ) or 0
    return {"total": total if include_resolved else pending,
            "all_associations": total, "pending": pending}


@app.get("/api/matching/reviews", response_model=list[MatchingReviewView])
def list_matching_reviews(
    limit: int = 50, offset: int = 0, include_resolved: bool = False, db: Session = Depends(get_db)
):
    if include_resolved:
        records = list(db.scalars(
            select(AssociationRecord).order_by(AssociationRecord.created_at.desc())
            .offset(max(0, offset)).limit(limit)
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


@app.get("/api/collection/continuity")
def collection_continuity(db: Session = Depends(get_db)):
    """What has actually been captured, day by day.

    The calibration page rendered a "dataset readiness" bar driven by
    complete_clusters / required_clusters. Niche-cluster calibration is not
    implemented, so that number was always zero out of two hundred: a progress
    bar for a pipeline that does not exist. This is the measurement that does
    exist -- snapshots of tracked entities, and whether they are consecutive.
    """
    rows = db.execute(
        select(
            func.date(Observation.observed_at).label("day"),
            func.count(func.distinct(Observation.candidate_id)).label("entities"),
            func.count(Observation.id).label("observations"),
        )
        .where(Observation.metric == "roblox_playing")
        .group_by(func.date(Observation.observed_at))
        .order_by(func.date(Observation.observed_at))
    ).all()
    days = [{"day": str(row.day), "entities": row.entities, "observations": row.observations}
            for row in rows]

    # A gap breaks a streak: days are counted as consecutive calendar dates,
    # never interpolated across a day with no capture.
    longest = streak = 0
    previous: date | None = None
    for entry in days:
        current = date.fromisoformat(entry["day"])
        streak = streak + 1 if previous is not None and (current - previous).days == 1 else 1
        longest = max(longest, streak)
        previous = current

    tracked = db.scalar(select(func.count(func.distinct(Candidate.external_id)))) or 0
    return {
        "days": days,
        "captured_days": len(days),
        "longest_consecutive_days": longest,
        "entities_tracked": tracked,
        "first_capture": days[0]["day"] if days else None,
        "last_capture": days[-1]["day"] if days else None,
        # Stated rather than implied: nothing here is niche-cluster calibration.
        "niche_cluster_calibration": "not_implemented",
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
    # 2 — whether dense retrieval can actually run, rather than a hardcoded
    # "ready". The package check is cheap; the observed flag comes from what
    # the engine last managed to do, which is the part that matters.
    latest_association = db.scalar(
        select(AssociationRecord).order_by(AssociationRecord.created_at.desc()).limit(1)
    )
    seen = dependency_health.observations(db)
    # A local instance costs nothing to ask, so its state is observed rather
    # than inferred from whenever a run last happened to use it.
    # A census older than several sampling intervals means the scheduler is
    # not running, whatever the last sample said when it landed.
    census_at = db.scalar(select(MarketSample.captured_at)
                          .order_by(MarketSample.captured_at.desc()).limit(1))
    market_census = None
    if settings.roblox_charts_enabled:
        if census_at is None:
            market_census = {"state": "never_sampled",
                             "detail": "No market census has been recorded yet."}
        else:
            age = (datetime.now(UTC) - budget_utc(census_at)).total_seconds()
            # Three intervals tolerates one missed sample and a slow one
            # without crying wolf.
            allowed = settings.market_sample_minutes * 60 * 3
            market_census = {
                "state": "last_request_succeeded" if age <= allowed else "stale",
                "detail": f"Last census {int(age // 60)} minutes ago; the sampler "
                          f"runs every {settings.market_sample_minutes} minutes.",
                "at": budget_utc(census_at).isoformat(),
            }
    searxng_reachable = None
    if settings.searxng_enabled:
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                probe = await client.get(settings.searxng_url.rstrip("/") + "/healthz")
                searxng_reachable = probe.status_code < 500
        except httpx.HTTPError:
            searxng_reachable = False
    return {
        "status": "ok",
        "database": "connected",
        # Which code is answering, so a stale service is distinguishable from a
        # freshly restarted one.
        "build": build_identity(),
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
        # Configuration and observation, kept apart. "Configured" never
        # implies working, and a dependency nothing has called is unknown
        # rather than healthy.
        "dependencies": [
            dependency_health.describe("Local model server (Ollama)", configured=True,
                                       observed=None, reachable=ollama["available"]),
            dependency_health.describe("Roblox public API", configured=True,
                                       observed=seen.get("roblox")),
            # Local, so it can be probed for nothing, like the model server.
            dependency_health.describe("Local search (SearxNG)",
                                       configured=settings.searxng_enabled,
                                       observed=seen.get("searxng"),
                                       reachable=searxng_reachable),
            dependency_health.describe("Roblox search", configured=settings.roblox_search_enabled,
                                       observed=seen.get("roblox_search")),
            # The sampler failing is silent by construction: it swallows its
            # own errors so one bad census cannot stop the next, and an
            # interval job that never fires logs nothing at all. Without a row
            # here, a stopped sampler looks exactly like a working one until
            # somebody notices the rates have gone quiet.
            dependency_health.describe("Market census sampler",
                                       configured=settings.roblox_charts_enabled,
                                       observed=market_census),
            dependency_health.describe("Tavily search", configured=bool(settings.tavily_api_key),
                                       observed=seen.get("tavily")),
            dependency_health.describe("YouTube Data API", configured=bool(settings.youtube_api_key),
                                       observed=seen.get("youtube")),
            dependency_health.describe(
                "Embedding model", configured=importlib.util.find_spec("fastembed") is not None,
                observed={"state": "last_request_succeeded" if latest_association is not None
                          and latest_association.embedding_available else "unknown",
                          "at": latest_association.created_at.isoformat() if latest_association else None,
                          "detail": "The most recent association used dense retrieval."
                          if latest_association is not None and latest_association.embedding_available
                          else "Installed, but no association has used dense retrieval yet."}
                if latest_association is not None else None),
        ],
        "scheduler": {
            "timezone": settings.timezone,
            "daily_at": f"{settings.snapshot_hour:02d}:{settings.snapshot_minute:02d}",
            "last_snapshot": state.value_json if state else None,
        },
        "calibration": calibration_status(db).model_dump(),
    }


frontend_dist = ROOT / "frontend" / "dist"
if frontend_dist.exists():
    class FreshIndex(StaticFiles):
        """Never let a browser cache `index.html`.

        The built asset filenames carry a content hash, so they are safe to
        cache forever -- but `index.html` is what names them. Cached, it keeps
        pointing at the previous build, and the dashboard silently stays on old
        code after every deploy. This was live: the page was still serving
        buttons that had been renamed two commits earlier, and the only visible
        symptom was that a fix "had not worked".
        """

        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            if path in {".", "index.html"} or path.endswith("index.html"):
                response.headers["cache-control"] = "no-cache, must-revalidate"
            return response

    app.mount("/", FreshIndex(directory=frontend_dist, html=True), name="dashboard")


def run() -> None:
    settings = get_settings()
    assert_local_only(settings.host)
    # At logon the tailnet interface is usually seconds behind us. Binding
    # before it exists kills the service outright, and the task does not come
    # back on its own. See app/binding.py.
    waited = wait_for_address(
        settings.host,
        on_wait=lambda host: print(
            f"Waiting for {host} to come up before binding.", flush=True
        ),
    )
    if waited:
        print(f"{settings.host} came up after {waited:.1f}s.", flush=True)
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
