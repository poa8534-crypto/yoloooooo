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

import asyncio
import re
import secrets
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from ..config import Settings
from ..models import AuditRecord, SystemState
from ..security import redact
from .antigravity import AntigravityClient, antigravity_models, chain
from .capture import AttemptRecorder
from .catalog import load_services
from .gate import Gate, run_command
from .gemini import GeminiClient
from .loop import EngineerLoop, EngineerResult, gemini_models
from .planner import PLANNER_SYSTEM, PlanRefused, build_planner_prompt, parse_plan
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
            # Also bucketed by hour, because "am I about to be rate limited"
            # and "what did today cost" are different questions and a daily
            # total cannot answer the first one. Only the current day's hours
            # are kept here; the day key already separates the rest.
            hour = datetime.now(UTC).strftime("%Y-%m-%dT%H")
            hours = dict(entry.get("hours", {}))
            bucket = dict(hours.get(hour, {}))
            bucket["calls"] = bucket.get("calls", 0) + 1
            for name, amount in usage.items():
                bucket[name] = bucket.get(name, 0) + amount
            hours[hour] = bucket
            entry["hours"] = hours
            value[key_label] = entry
            if row is None:
                db.add(SystemState(key=key, value_json=value))
            else:
                row.value_json = value
                row.updated_at = datetime.now(UTC)
            db.commit()
    return record


def game_repo(settings: Settings) -> Path:
    target = getattr(settings, "game_project_dir", None)
    if target is None:
        raise NotConfigured("GAME_PROJECT_DIR is not set; point it at the game repository (e.g. C:/RobloxGames/ascent)")
    repo = Path(target)
    if not (repo / ".git").exists():
        raise NotConfigured(f"GAME_PROJECT_DIR {repo} is not a git repository")
    return repo


def resolve_game_repo(settings: Settings, blueprint: object | None = None, repo_fn=None) -> Path:
    """The git repository for this game.

    If blueprint is given and settings.game_project_dir points to another game's
    folder, this looks for a repository named after the blueprint in the parent
    directory (e.g. C:/RobloxGames/island-haven).
    """
    fn = repo_fn or game_repo
    base = fn(settings)
    if blueprint is None:
        return base

    title = getattr(blueprint, "title", "")
    if not title:
        return base

    clean_first = title.split(":")[0].strip()
    slugs = [
        re.sub(r"[\s_]+", "-", clean_first.lower()),
        re.sub(r"[\s-]+", "_", clean_first.lower()),
        re.sub(r"[\s_-]+", "", clean_first.lower()),
        re.sub(r"[^a-zA-Z0-9\s-]", "", clean_first.lower()).replace(" ", "-"),
    ]
    parent = base.parent
    for slug in slugs:
        if slug:
            candidate = parent / slug
            if (candidate / ".git").exists():
                return candidate
    return base


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


def provider_names(settings: Settings) -> list[str]:
    """The chain, in order. `engineer_provider` still wins when it is set, so
    an .env written before the chain existed keeps meaning what it said."""
    if settings.engineer_provider:
        return [settings.engineer_provider]
    names = [name.strip().lower() for name in settings.engineer_providers.split(",") if name.strip()]
    unknown = [name for name in names if name not in ("antigravity", "gemini", "ollama")]
    if unknown:
        raise NotConfigured(f"unknown engineer provider(s): {', '.join(unknown)}")
    if not names:
        raise NotConfigured("ENGINEER_PROVIDERS is empty; name at least one of antigravity, gemini, ollama")
    return names


def antigravity_model_names(settings: Settings) -> list[str]:
    return [name.strip() for name in settings.engineer_antigravity_models.split(",") if name.strip()]


def build_one_client(name: str, settings: Settings, **kwargs):
    if name == "ollama":
        return OllamaClient(base_url=settings.engineer_ollama_base_url,
                            timeout=settings.engineer_ollama_timeout_seconds,
                            num_ctx=settings.engineer_ollama_context,
                            repeat_penalty=settings.engineer_ollama_repeat_penalty,
                            top_p=settings.engineer_ollama_top_p, **kwargs)
    if name == "antigravity":
        return AntigravityClient(executable=settings.engineer_antigravity_executable,
                                 timeout=settings.engineer_antigravity_timeout_seconds,
                                 effort=settings.engineer_antigravity_effort, **kwargs)
    if not engineer_keys(settings):
        raise NotConfigured("no Gemini key is set")
    return GeminiClient(keys=engineer_keys(settings), base_url=settings.engineer_gemini_base_url,
                        timeout=settings.engineer_gemini_timeout_seconds,
                        max_output_tokens=settings.engineer_gemini_max_output_tokens, **kwargs)


def build_clients(settings: Settings, **kwargs) -> list[tuple[str, object]]:
    """One client per provider in the chain, in order.

    A provider that cannot be built at all -- Gemini with no key -- is dropped
    here rather than failing the run, because that is exactly the case the
    chain exists to survive.
    """
    built: list[tuple[str, object]] = []
    for name in provider_names(settings):
        try:
            built.append((name, build_one_client(name, settings, **kwargs)))
        except NotConfigured:
            continue
    if not built:
        raise NotConfigured("no engineer provider is configured: set a Gemini key, install `agy`, "
                            "or run Ollama")
    return built


def provider_limits(settings: Settings) -> dict[str, int]:
    """How many calls each named provider may have in flight at once.

    From ENGINEER_PROVIDER_CONCURRENCY, `name=count` pairs. Refused rather than
    guessed at when it names a provider that does not exist or a count that is
    not a positive number: a limit that silently did not apply is the one
    nobody would notice until the GPU fell over.
    """
    limits: dict[str, int] = {}
    for pair in settings.engineer_provider_concurrency.split(","):
        if not pair.strip():
            continue
        name, _, count = pair.partition("=")
        name = name.strip().lower()
        if name not in ("antigravity", "gemini", "ollama"):
            raise NotConfigured(f"ENGINEER_PROVIDER_CONCURRENCY names an unknown provider: {name!r}")
        try:
            value = int(count)
        except ValueError:
            value = 0
        if value < 1:
            raise NotConfigured(
                f"ENGINEER_PROVIDER_CONCURRENCY gives {name} {count.strip()!r}; it needs a whole "
                "number of at least 1")
        limits[name] = value
    return limits


def _held(call, slots: asyncio.Semaphore):
    """The same call, waiting for one of its provider's slots first."""
    async def held(system: str, prompt: str, deadline: float) -> tuple[str, str]:
        async with slots:
            return await call(system, prompt, deadline)
    return held


def build_model_call(settings: Settings, clients: list[tuple[str, object]],
                     slots: Mapping[str, asyncio.Semaphore] | None = None):
    """One `ModelCall` for the whole chain.

    `slots` are shared by every system a build is writing at once, one
    semaphore per provider with a limit, so the limit holds across the build
    rather than per system.
    """
    calls = []
    for name, client in clients:
        if name == "ollama":
            call = ollama_models(client, ollama_model_names(settings))
        elif name == "antigravity":
            call = antigravity_models(client, antigravity_model_names(settings))
        else:
            call = gemini_models(client, engineer_models(settings))
        if slots and name in slots:
            call = _held(call, slots[name])
        calls.append(call)
    return chain(calls)


def build_gate(settings: Settings, repo: Path | None = None) -> Gate:
    repo = repo or game_repo(settings)
    return Gate(load_services(settings.roblox_services_file), repo / settings.luau_definitions_file,
                runner=run_command, timeout=settings.engineer_tool_timeout_seconds,
                mode=settings.engineer_gate)


def existing_systems(settings: Settings) -> set[str]:
    """What the game repository already has, by module name.

    The planner is told, so it plans what is missing rather than what is there.
    """
    repo = game_repo(settings)
    names: set[str] = set()
    for root in ("src/server", "src/shared", "src/client"):
        directory = repo / root
        if not directory.is_dir():
            continue
        for file in directory.glob("*.luau"):
            stem = file.stem
            if stem not in ("Services", "Hello") and not stem.startswith("init."):
                names.add(stem)
    return names


async def plan_from_audit(audit_id: str, *, settings: Settings, factory,
                          attempts: int = 3) -> list[EngineeringTask]:
    """An audited design turned into engineering tasks.

    Planning is a separate call from building on purpose: the criteria are what
    the gate measures against, and a model that writes its own criteria while
    writing the code that meets them is grading its own homework.
    """
    design = load_design(factory, audit_id)
    existing = existing_systems(settings)
    clients = build_clients(settings, on_usage=usage_recorder(factory))
    model = build_model_call(settings, clients)
    prompt = build_planner_prompt(design, existing)
    refusal = ""
    try:
        for attempt in range(1, attempts + 1):
            text, _model_name = await model(PLANNER_SYSTEM, prompt + refusal, time.monotonic() + 1800)
            try:
                return parse_plan(text, audit_id, existing)
            except PlanRefused as exc:
                if attempt == attempts:
                    raise
                refusal = (f'\n\n<previous_attempt number="{attempt}">\n{exc}\n'
                           "Return the complete plan again.\n</previous_attempt>")
    finally:
        for _name, client in clients:
            await client.close()
    raise PlanRefused("the planner produced nothing usable")


async def run_task(task: EngineeringTask, *, settings: Settings, factory, on_event=None,
                   slots: Mapping[str, asyncio.Semaphore] | None = None,
                   repo: Path | None = None) -> EngineerResult:
    """One engineer run, end to end, recorded as it goes.

    `slots` are the build's per-provider limits, when several systems are
    being written at once; see build_model_call.

    `repo` is the game the build resolved. Without it the worktree is cut from
    GAME_PROJECT_DIR, which is how a build for one game wrote every system on
    branches of another's repository and then landed nothing.
    """
    design = load_design(factory, task.audit_id)
    repo = repo or game_repo(settings)
    gate = build_gate(settings, repo)
    worktrees = settings.engineer_worktree_dir or repo.parent / f"{repo.name}-worktrees"
    run_id = run_name(task.system)
    runs = EngineerRuns(factory)
    runs.create(run_id, task)

    def emit(stage, detail="", **extra):
        entry = runs.event(run_id, stage, detail, **extra)
        if on_event:
            on_event(entry)

    clients = build_clients(settings, on_usage=usage_recorder(factory))
    recorder = AttemptRecorder(settings.engineer_capture_dir,
                               on_error=lambda message: emit("capture_failed", message))
    loop = EngineerLoop(
        model=build_model_call(settings, clients, slots), gate=gate,
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
        for _name, client in clients:
            await client.close()
    runs.finish(run_id, result)
    return result
