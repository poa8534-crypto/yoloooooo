"""Durable run budget. Reservations precede I/O; cache successful requests."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

from .connectors import ConnectorResult
from .models import ResearchCheckpoint, ResearchRun, SystemState
from .security import redact

DEFAULT_LIMITS = {"seconds": 1800, "rounds": 4, "tavily_search": 12, "youtube_search": 12,
                  "capture_page": 30, "universes": 30, "videos": 60, "model_attempts": 24}


class BudgetExceeded(RuntimeError):
    """A declared budget refused more work.

    `planned` separates a cap the run was configured to reach -- a limit, a
    local quota allowance, the deadline -- from a refusal that means the
    run's own bookkeeping is in an unexpected state, such as declining to
    replay a request whose outcome was never recorded. Only the second kind
    says anything went wrong.
    """

    def __init__(self, reason, planned=True):
        super().__init__(reason)
        self.planned = planned


def utc(value):
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def progress(db, run):
    checkpoint = db.get(ResearchCheckpoint, run.id)
    state = checkpoint.state if checkpoint else {}
    elapsed = max(0, ((utc(run.completed_at) if run.completed_at else datetime.now(UTC)) - utc(run.created_at)).total_seconds())
    return {"mode": state.get("mode", "quick"), "stage": state.get("stage", run.status),
            "elapsed_seconds": round(elapsed, 2), "remaining_seconds": max(0, round(state.get("limits", DEFAULT_LIMITS)["seconds"] - elapsed, 2)),
            "usage": state.get("usage", {}), "questions": state.get("questions", []),
            "stop_reason": state.get("stop_reason"), "round": state.get("round", 0),
            "errors": state.get("errors", []), "abstentions": state.get("abstentions", []),
            "budget_stops": state.get("budget_stops", []),
            "evidence_additions": state.get("evidence_additions", 0)}


class RunBudget:
    def __init__(self, factory, run_id):
        self.factory, self.run_id = factory, run_id
        with factory() as db:
            run = db.get(ResearchRun, run_id)
            self.created = utc(run.created_at)
            checkpoint = db.get(ResearchCheckpoint, run_id)
            self.state = dict(checkpoint.state) if checkpoint else {}
        self.state.setdefault("limits", dict(DEFAULT_LIMITS))
        for key, default in {"usage": {}, "requests": {}, "errors": [], "abstentions": [], "budget_stops": [], "model_calls": [], "contexts": [], "questions": []}.items():
            self.state.setdefault(key, default)
        self.gate = asyncio.Semaphore(4)

    @property
    def remaining(self):
        return max(0.0, self.state["limits"]["seconds"] - (datetime.now(UTC) - self.created).total_seconds())

    def save(self, **updates):
        self.state.update(updates)
        with self.factory() as db:
            row = db.get(ResearchCheckpoint, self.run_id)
            if row is None:
                row = ResearchCheckpoint(run_id=self.run_id)
                db.add(row)
            row.state = json.loads(json.dumps(self.state))
            row.updated_at = datetime.now(UTC)
            run = db.get(ResearchRun, self.run_id)
            if "stage" in updates:
                run.message = str(updates["stage"])
            db.commit()

    def reserve(self, key, amount=1):
        if self.remaining <= 0:
            raise BudgetExceeded("deadline")
        used = self.state["usage"].get(key, 0)
        if key in self.state["limits"] and used + amount > self.state["limits"][key]:
            raise BudgetExceeded(key + "_limit")
        self.state["usage"][key] = used + amount
        self.save()

    def model_attempt(self, model):
        self.reserve("model_attempts")
        self.state["model_calls"].append({"model": model, "at": datetime.now(UTC).isoformat()})
        self.save()

    def abstain(self, stage, reason):
        """Record a deliberate refusal to produce output.

        Distinct from `error`: abstaining is the system working. Folding the
        two together reported a clean run as degraded.
        """
        self.state.setdefault("abstentions", []).append(
            {"stage": stage, "reason": reason, "at": datetime.now(UTC).isoformat()}
        )
        self.save()

    def error(self, stage, exc):
        """Record a failure, or a planned budget cap as its own kind of event.

        Reaching a configured cap is the budget doing its job. Filing it under
        `errors` marked a run that behaved exactly as specified as degraded,
        so planned refusals go to `budget_stops` instead. They stay visible;
        they just no longer claim something failed.
        """
        entry = {"stage": stage, "error": redact(str(exc)), "kind": type(exc).__name__, "at": datetime.now(UTC).isoformat()}
        if isinstance(exc, BudgetExceeded) and exc.planned:
            self.state.setdefault("budget_stops", []).append(entry)
        else:
            self.state["errors"].append(entry)
        self.save()

    def quota(self, name):
        from .config import get_settings
        settings = get_settings()
        provider = "tavily" if name == "tavily_search" else ("youtube" if name.startswith("youtube") else None)
        if not provider:
            return
        cost = 100 if name == "youtube_search" else 1
        # Local conservative allowance, not a claim to know remote account balance.
        key = f"quota:{provider}:{datetime.now(UTC).date()}"
        limit = settings.tavily_daily_allowance if provider == "tavily" else settings.youtube_daily_allowance
        with self.factory() as db:
            row = db.get(SystemState, key)
            used = row.value_json["reserved"] if row else 0
            if used + cost > limit:
                raise BudgetExceeded(provider + "_local_quota")
            if row is None:
                row = SystemState(key=key)
                db.add(row)
            row.value_json = {"reserved": used + cost, "allowance": limit, "remote_remaining": "unknown"}
            db.commit()
        self.reserve(provider + "_units", cost)

    async def call(self, connectors, name, arg):
        key = hashlib.sha256(json.dumps([name, arg], sort_keys=True).encode()).hexdigest()
        prior = self.state["requests"].get(key)
        if prior:
            if prior["status"] == "complete":
                return ConnectorResult(**prior["result"])
            # Unknown outcome after interruption must not silently replay/spend.
            raise BudgetExceeded("request_previously_" + prior["status"], planned=False)
        async with self.gate:
            self.reserve(name)
            if getattr(connectors, "quota_meter", None):
                if name == "tavily_search":
                    self.reserve("tavily_units")
                elif name.startswith("youtube"):
                    self.reserve("youtube_units", 100 if name == "youtube_search" else 1)
            else:
                self.quota(name)
            self.state["requests"][key] = {"status": "inflight", "method": name, "started_at": datetime.now(UTC).isoformat()}
            self.save()
            try:
                result = await asyncio.wait_for(getattr(connectors, name)(arg), timeout=min(35.0, self.remaining))
                self.state["requests"][key].update(status="complete", result={"url": result.url, "payload": result.payload, "content_type": result.content_type})
                self.save()
                return result
            except BaseException as exc:
                self.state["requests"][key]["status"] = "interrupted" if isinstance(exc, asyncio.CancelledError) else "failed"
                self.save()
                raise
