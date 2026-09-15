"""Addressable bounded Scout jobs, persisted using the existing state store.

No raw model text is placed in progress events. Final results reference AuditRecord.
Single-process local service; SQLite reservations also protect duplicate HTTP starts.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, text

from .models import Candidate, Proposal, SystemState
from .security import redact

ACTIVE = {"queued", "running"}
PREFIX = "audit-job:"


class JobConflict(ValueError):
    pass


class JobBudget:
    def __init__(self, manager, job_id):
        self.manager, self.job_id = manager, job_id

    @property
    def remaining(self):
        job = self.manager.get(self.job_id)
        return max(0, 1800 - (datetime.now(UTC) - datetime.fromisoformat(job["created_at"])).total_seconds())

    def model_attempt(self, model):
        job = self.manager.get(self.job_id)
        if self.remaining <= 0 or job["model_attempts"] >= 24:
            raise TimeoutError("Scout time or model-attempt budget exhausted")
        self.manager.update(self.job_id, model_attempts=job["model_attempts"] + 1)
        self.manager.event(self.job_id, "model_attempt", model)

    def error(self, stage, exc):
        self.manager.event(self.job_id, stage, type(exc).__name__)


class AuditJobs:
    def __init__(self, factory, orchestrator):
        self.factory, self.orchestrator = factory, orchestrator
        self.tasks = {}

    def get(self, job_id):
        with self.factory() as db:
            row = db.get(SystemState, PREFIX + job_id)
            if row is None:
                raise KeyError(job_id)
            value = json.loads(json.dumps(row.value_json))
        value["remaining_seconds"] = max(0, round(1800 - (datetime.now(UTC) - datetime.fromisoformat(value["created_at"])).total_seconds(), 2))
        return value

    def update(self, job_id, **changes):
        with self.factory() as db:
            row = db.get(SystemState, PREFIX + job_id)
            row.value_json = {**row.value_json, **changes}
            row.updated_at = datetime.now(UTC)
            db.commit()

    def event(self, job_id, stage, detail="", **extra):
        job = self.get(job_id)
        events = job["events"]
        events.append({"sequence": len(events) + 1, "stage": stage, "detail": redact(detail), "at": datetime.now(UTC).isoformat()})
        changes = {"events": events}
        if stage == "draft_started":
            changes["status"] = "running"
        self.update(job_id, **changes)

    def start(self, candidate_id, operation, proposal_id=None):
        if operation not in {"analyze_game", "audit_idea"}:
            raise ValueError("Unknown Scout operation")
        with self.factory() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            if db.get(Candidate, candidate_id) is None:
                raise KeyError(candidate_id)
            if operation == "audit_idea":
                proposal = db.get(Proposal, proposal_id) if proposal_id else None
                if proposal is None or proposal.candidate_id != candidate_id or proposal.agent != "meta_hunter":
                    raise ValueError("Audit idea requires this candidate's exact Hunter proposal ID")
            elif proposal_id:
                raise ValueError("Analyze game does not accept a Hunter proposal")
            for row in db.scalars(select(SystemState).where(SystemState.key.like(PREFIX + "%"))):
                job = row.value_json
                if job["candidate_id"] == candidate_id and job["status"] in ACTIVE:
                    if job["operation"] == operation and job["proposal_id"] == proposal_id:
                        return job
                    raise JobConflict("A different Scout operation is already active for this game")
            job_id = str(uuid4())
            job = {"id": job_id, "candidate_id": candidate_id, "operation": operation,
                   "proposal_id": proposal_id, "status": "queued", "created_at": datetime.now(UTC).isoformat(),
                   "completed_at": None, "model_attempts": 0, "audit_id": None, "events": [], "error": None}
            db.add(SystemState(key=PREFIX + job_id, value_json=job))
            db.commit()
        self.event(job_id, "queued", "Waiting for the serialized local model queue")
        self.tasks[job_id] = asyncio.create_task(self._run(job_id))
        return self.get(job_id)

    async def _run(self, job_id):
        job = self.get(job_id)
        try:
            budget = JobBudget(self, job_id)
            result = await asyncio.wait_for(self.orchestrator.audit(
                job["candidate_id"], job["proposal_id"], budget=budget,
                operation=job["operation"], on_event=lambda stage, detail="", **extra: self.event(job_id, stage, detail)),
                timeout=budget.remaining)
            status = "complete" if result.get("proposal") else "blocked"
            self.update(job_id, status=status, audit_id=result.get("audit_id"), completed_at=datetime.now(UTC).isoformat())
            self.event(job_id, status, "Result persisted to the evidence ledger")
        except asyncio.CancelledError:
            if self.get(job_id)["status"] in ACTIVE:
                self.update(job_id, status="interrupted", completed_at=datetime.now(UTC).isoformat())
                self.event(job_id, "interrupted", "Execution interrupted; consumed budgets retained")
            raise
        except Exception as exc:
            self.update(job_id, status="timed_out" if isinstance(exc, TimeoutError) else "failed",
                        error=redact(type(exc).__name__ + ": " + str(exc)), completed_at=datetime.now(UTC).isoformat())
            self.event(job_id, "stopped", "No completed result; inspect the recorded error")
        finally:
            self.tasks.pop(job_id, None)

    async def cancel(self, job_id):
        if self.get(job_id)["status"] in ACTIVE:
            self.update(job_id, status="cancelled", completed_at=datetime.now(UTC).isoformat())
            self.event(job_id, "cancelled", "Cancelled by the operator; evidence retained")
            task = self.tasks.get(job_id)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        return self.get(job_id)

    def resume(self, job_id):
        job = self.get(job_id)
        if job["status"] != "interrupted" or job["remaining_seconds"] <= 0 or job["model_attempts"] >= 24:
            raise JobConflict("Only interrupted jobs with remaining original budget can resume")
        self.update(job_id, status="queued", completed_at=None)
        self.event(job_id, "resumed", "Resuming with original deadline and attempt count")
        self.tasks[job_id] = asyncio.create_task(self._run(job_id))
        return self.get(job_id)

    def reconcile(self):
        with self.factory() as db:
            ids = [row.value_json["id"] for row in db.scalars(select(SystemState).where(SystemState.key.like(PREFIX + "%"))) if row.value_json["status"] in ACTIVE]
        for job_id in ids:
            self.update(job_id, status="interrupted", completed_at=datetime.now(UTC).isoformat())
            self.event(job_id, "interrupted", "Previous process stopped; no model work replayed automatically")

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
