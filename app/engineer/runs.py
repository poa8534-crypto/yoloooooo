"""Engineer runs: the design they build from, their record, and their assembly.

A run is tracked in `system_state` under `engineer-run:<id>`, the same way
Scout jobs are (app/audit_jobs.py). Its events describe the work -- which
attempt, which checks failed, which branch -- and never carry the model's
code: accepted code, and the last refused attempt, live on the run's branch in
the game repository; the handoff packet carries the refused files for Claude.

Gemini usage per key per day is kept under `gemini-usage:<date>`. It exists
so you can see where the keys went. It does not ration them.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from ..config import Settings
from ..models import AuditRecord, SystemState
from ..security import redact
from .capture import AttemptRecorder
from .catalog import load_services
from .gate import Gate, run_command
from .gemini import GeminiClient
from .loop import EngineerLoop, EngineerResult, gemini_models
from .ollama import OllamaClient, ollama_models
from .schemas import EngineeringTask
from .workspace import Worktree

PREFIX = "engineer-run:"
USAGE_PREFIX = "gemini-usage:"


class NoDesign(ValueError):
    """The engineer builds Venture Scout designs and nothing else."""


class NotConfigured(RuntimeError):
    pass


def load_design(factory, audit_id: str) -> dict:
    with factory() as db:
        audit = db.get(AuditRecord, audit_id)
        if audit is None:
            raise NoDesign(f"No Venture Scout audit {audit_id!r}. The engineer only builds audited designs.")
        payload = dict(audit.payload or {})
    if not payload.get("proposal"):
        raise NoDesign(f"Audit {audit_id} produced no design (it was blocked), so there is nothing to build.")
    return payload


def run_name(system: str) -> str:
    slug = re.sub(r"(?<!^)(?=[A-Z])", "-", system).lower()
    return f"{slug[:40]}-{secrets.token_hex(4)}"


class EngineerRuns:
    def __init__(self, factory):
        self.factory = factory

    def create(self, run_id: str, task: EngineeringTask) -> dict:
        record = {"id": run_id, "task": task.model_dump(), "status": "running",
                  "created_at": datetime.now(UTC).isoformat(), "completed_at": None,
                  "events": [], "result": None}
        with self.factory() as db:
            db.add(SystemState(key=PREFIX + run_id, value_json=record))
            db.commit()
        return record

    def get(self, run_id: str) -> dict:
        with self.factory() as db:
            row = db.get(SystemState, PREFIX + run_id)
            if row is None:
                raise KeyError(run_id)
            return dict(row.value_json)

    def _update(self, run_id: str, **changes) -> None:
        with self.factory() as db:
            row = db.get(SystemState, PREFIX + run_id)
            row.value_json = {**row.value_json, **changes}
            row.updated_at = datetime.now(UTC)
            db.commit()

    def event(self, run_id: str, stage: str, detail: str = "", **extra) -> dict:
        events = self.get(run_id)["events"]
        entry = {"sequence": len(events) + 1, "stage": stage, "detail": redact(detail),
                 "at": datetime.now(UTC).isoformat(), **{k: v for k, v in extra.items() if k != "checks"}}
        if "checks" in extra:
            entry["checks"] = [{**check, "output": redact(check["output"])} for check in extra["checks"]]
        self._update(run_id, events=[*events, entry])
        return entry

    def finish(self, run_id: str, result: EngineerResult) -> None:
        self._update(run_id, status=result.status, completed_at=datetime.now(UTC).isoformat(),
                     result={"status": result.status, "reason": redact(result.reason),
                             "attempts": result.attempts, "branch": result.branch, "commit": result.commit,
                             "handoff": result.handoff, "planned": result.planned, "files": result.files})

    def fail(self, run_id: str, exc: BaseException) -> None:
        self._update(run_id, status="failed", completed_at=datetime.now(UTC).isoformat(),
                     result={"status": "failed", "reason": redact(f"{type(exc).__name__}: {exc}"), "planned": False})


def usage_recorder(factory):
    def record(key_label: str, model: str, outcome: str, usage: dict[str, int]) -> None:
        key = USAGE_PREFIX + datetime.now(UTC).date().isoformat()
        with factory() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            row = db.get(SystemState, key)
            value = dict(row.value_json) if row else {}
            entry = dict(value.get(key_label, {}))
            entry["calls"] = entry.get("calls", 0) + 1
            entry[outcome] = entry.get(outcome, 0) + 1
            for name, amount in usage.items():
                entry[name] = entry.get(name, 0) + amount
            entry.setdefault("models", {})
            entry["models"] = {**entry["models"], model: entry["models"].get(model, 0) + 1}
            value[key_label] = entry
            if row is None:
                db.add(SystemState(key=key, value_json=value))
            else:
                row.value_json = value
                row.updated_at = datetime.now(UTC)
            db.commit()
    return record


def game_repo(settings: Settings) -> Path:
    if settings.game_project_dir is None:
        raise NotConfigured("GAME_PROJECT_DIR is not set; point it at the game repository (e.g. C:/RobloxGames/game)")
    repo = Path(settings.game_project_dir)
    if not (repo / ".git").exists():
        raise NotConfigured(f"GAME_PROJECT_DIR {repo} is not a git repository")
    return repo


def engineer_keys(settings: Settings) -> list[str]:
    """Key 1, and key 2 only when asked: key 2 is Hermes's, and both share one quota."""
    keys = [settings.gemini_api_key_1]
    if settings.engineer_use_both_gemini_keys:
        keys.append(settings.gemini_api_key_2)
    return [key for key in keys if key]


def engineer_models(settings: Settings) -> list[str]:
    return [name.strip() for name in settings.engineer_gemini_models.split(",") if name.strip()]


def ollama_model_names(settings: Settings) -> list[str]:
    return [name.strip() for name in settings.engineer_ollama_models.split(",") if name.strip()]


def build_client(settings: Settings, **kwargs):
    """The backend `engineer_provider` names. Both answer the same `generate`."""
    if settings.engineer_provider == "ollama":
        return OllamaClient(base_url=settings.engineer_ollama_base_url,
                            timeout=settings.engineer_ollama_timeout_seconds,
                            num_ctx=settings.engineer_ollama_context, **kwargs)
    return GeminiClient(keys=engineer_keys(settings), base_url=settings.engineer_gemini_base_url,
                        timeout=settings.engineer_gemini_timeout_seconds,
                        max_output_tokens=settings.engineer_gemini_max_output_tokens, **kwargs)


def build_model_call(settings: Settings, client):
    """Adapt whichever client was built into the loop's `ModelCall`."""
    if settings.engineer_provider == "ollama":
        return ollama_models(client, ollama_model_names(settings))
    return gemini_models(client, engineer_models(settings))


def build_gate(settings: Settings) -> Gate:
    repo = game_repo(settings)
    return Gate(load_services(settings.roblox_services_file), repo / settings.luau_definitions_file,
                runner=run_command, timeout=settings.engineer_tool_timeout_seconds,
                mode=settings.engineer_gate)


async def run_task(task: EngineeringTask, *, settings: Settings, factory, on_event=None) -> EngineerResult:
    """One engineer run, end to end, recorded as it goes."""
    design = load_design(factory, task.audit_id)
    repo = game_repo(settings)
    gate = build_gate(settings)
    worktrees = settings.engineer_worktree_dir or repo.parent / f"{repo.name}-worktrees"
    run_id = run_name(task.system)
    runs = EngineerRuns(factory)
    runs.create(run_id, task)

    def emit(stage, detail="", **extra):
        entry = runs.event(run_id, stage, detail, **extra)
        if on_event:
            on_event(entry)

    client = build_client(settings, on_usage=usage_recorder(factory))
    recorder = AttemptRecorder(settings.engineer_capture_dir,
                               on_error=lambda message: emit("capture_failed", message))
    loop = EngineerLoop(
        model=build_model_call(settings, client), gate=gate,
        known_services=gate.known_services,
        create_worktree=lambda name: Worktree.create(repo, settings.game_base_branch, worktrees, name),
        handoff_dir=settings.engineer_data_dir / "handoffs",
        max_attempts=settings.engineer_max_attempts, run_seconds=settings.engineer_run_seconds,
        on_event=emit,
        record_attempt=recorder.record if settings.engineer_capture_attempts else None,
    )
    try:
        result = await loop.run(run_id, task, design)
    except BaseException as exc:
        runs.fail(run_id, exc)
        raise
    finally:
        await client.close()
    runs.finish(run_id, result)
    return result
