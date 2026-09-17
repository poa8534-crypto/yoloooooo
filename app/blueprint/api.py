"""The Blueprint stage over HTTP.

What the frontend can actually call. Every endpoint here is backed by something
that works: the architect runs through the Engineer's provider chain, readiness
and scope are counted, and the specification is compiled deterministically.

`/api/builds` runs the whole chain: compile the specification, ask the Engineer
to write the systems that are missing, take what its six-check gate accepted,
turn that into typed operations and send it to Studio. It is accepted rather
than awaited -- the Engineer takes minutes per system -- and followed on
`/api/builds/{id}/events`, where every line is a real transition.

`/api/studio` reports what the bridge really says, including "not running", so
the UI can show the truth rather than a spinner.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings
from ..engineer.from_spec import SpecUnusable, plan_summary, tasks_from
from ..engineer.runs import NoDesign, build_clients, build_model_call, load_design, usage_recorder
from .architect import ArchitectRefused, BlueprintArchitect, user_feature
from .compile import NotReady, compile_spec, human_preview
from .readiness import assess, scope_of
from .schemas import Blueprint, BlueprintConfig, BuildStatus, FeatureSuggestion, GameSystem
from .store import BlueprintNotFound, BlueprintStore
from .transitions import IllegalTransition, controls_for

router = APIRouter(prefix="/api/blueprints", tags=["blueprint"])

BRIDGE_URL = "http://127.0.0.1:34873"
ARCHITECT_SECONDS = 900.0


def _store():
    from ..db import SessionLocal

    return BlueprintStore(SessionLocal), SessionLocal


def _load(blueprint_id: str) -> Blueprint:
    store, _factory = _store()
    try:
        return store.get(blueprint_id)
    except BlueprintNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such blueprint") from None


def _view(blueprint: Blueprint) -> dict:
    """One shape for the whole stage, so the UI never has to ask twice.

    Readiness and scope are recomputed on every read rather than stored: a
    stored number can disagree with the blueprint it describes, and the
    disagreement is invisible.
    """
    readiness = assess(blueprint)
    return {
        "blueprint": blueprint.model_dump(mode="json"),
        "readiness": readiness.as_dict(),
        "scope": scope_of(blueprint).as_dict(),
        "controls": list(controls_for(blueprint.status)),
        "counts": {
            "selected_features": len(blueprint.selected_features()),
            "rejected_features": len(blueprint.rejected_features()),
            "undecided_features": len(blueprint.undecided_features()),
            "systems": len(blueprint.active_systems()),
        },
    }


class ProceedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audit_id: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=200)


@router.post("/proceed", status_code=status.HTTP_201_CREATED)
def proceed(request: ProceedRequest) -> dict:
    """Start (or return) the blueprint for an idea.

    Proceeding twice on the same idea returns the work in progress rather than
    quietly starting a second one beside it.
    """
    store, factory = _store()
    try:
        design = load_design(factory, request.audit_id)
    except NoDesign as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None

    existing = store.for_audit(request.audit_id)
    if existing is not None:
        return {**_view(existing), "resumed": True}

    proposal = design.get("proposal") or {}
    title = request.title or str(proposal.get("concept_title") or "Untitled idea")[:200]
    return {**_view(store.create(audit_id=request.audit_id, title=title)), "resumed": False}


@router.get("/{blueprint_id}")
def read(blueprint_id: str) -> dict:
    return _view(_load(blueprint_id))


class IntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: str = Field(default="", max_length=8000)
    config: BlueprintConfig | None = None


@router.post("/{blueprint_id}/intent")
def set_intent(blueprint_id: str, request: IntentRequest) -> dict:
    """What the person wants, in their own words. Saved before any model runs,
    so a failed suggestion call never loses what they typed."""
    store, _factory = _store()
    blueprint = _load(blueprint_id)
    updated = blueprint.model_copy(update={
        "user_intent": request.intent,
        "config": request.config or blueprint.config,
    })
    target = (BuildStatus.BLUEPRINTING
              if blueprint.status in (BuildStatus.DRAFT, BuildStatus.READY_TO_BUILD)
              else blueprint.status)
    try:
        return _view(store.save(updated, status=target))
    except IllegalTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


async def _architect(factory) -> tuple[BlueprintArchitect, list]:
    settings = get_settings()
    clients = build_clients(settings, on_usage=usage_recorder(factory))
    return BlueprintArchitect(build_model_call(settings, clients)), clients


@router.post("/{blueprint_id}/suggestions")
async def suggest(blueprint_id: str, replace: bool = False) -> dict:
    """Ask the architect for features.

    `replace=false` ADDS to what is there, so asking for more does not throw
    away decisions already made. Anything the person has already accepted or
    rejected is excluded from the new batch by id, and their own features are
    never touched.
    """
    store, factory = _store()
    blueprint = _load(blueprint_id)
    design = load_design(factory, blueprint.audit_id)
    architect, clients = await _architect(factory)
    try:
        fresh, summary = await architect.suggest(design, blueprint.user_intent, blueprint.config,
                                                 time.monotonic() + ARCHITECT_SECONDS)
    except ArchitectRefused as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    finally:
        for _name, client in clients:
            await client.close()

    if replace:
        # Decided ones survive a regenerate: throwing away a choice the person
        # made is worse than showing them one suggestion twice.
        kept = [s for s in blueprint.suggestions if s.selected is not None or s.origin == "user"]
    else:
        kept = list(blueprint.suggestions)
    known = {s.id.lower() for s in kept}
    merged = [*kept, *[s for s in fresh if s.id.lower() not in known]]

    updated = blueprint.model_copy(update={
        "suggestions": merged[:24],
        "summary": summary or blueprint.summary,
    })
    return _view(store.save(updated, status=BuildStatus.BLUEPRINTING))


class SelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # id -> selected. null means undecided again.
    selections: dict[str, bool | None] = Field(default_factory=dict)


@router.post("/{blueprint_id}/selections")
def select(blueprint_id: str, request: SelectionRequest) -> dict:
    store, _factory = _store()
    blueprint = _load(blueprint_id)
    known = {s.id for s in blueprint.suggestions}
    unknown = sorted(set(request.selections) - known)
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"no such feature(s): {', '.join(unknown)}")
    suggestions = [s.model_copy(update={"selected": request.selections[s.id]})
                   if s.id in request.selections else s
                   for s in blueprint.suggestions]
    updated = blueprint.model_copy(update={"suggestions": suggestions})
    return _view(store.save(updated, status=BuildStatus.BLUEPRINTING))


class FeatureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=2000)


@router.post("/{blueprint_id}/features")
def add_feature(blueprint_id: str, request: FeatureRequest) -> dict:
    """A feature the person wrote. Already selected: asking someone to tick a
    box on their own idea is a step that exists only to be forgotten."""
    store, _factory = _store()
    blueprint = _load(blueprint_id)
    feature = user_feature(request.title, request.description)
    updated = blueprint.model_copy(update={"suggestions": [*blueprint.suggestions, feature][:24]})
    return _view(store.save(updated, status=BuildStatus.BLUEPRINTING))


class EditFeatureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=3, max_length=120)
    description: str | None = Field(default=None, min_length=10, max_length=2000)


@router.patch("/{blueprint_id}/features/{feature_id}")
def edit_feature(blueprint_id: str, feature_id: str, request: EditFeatureRequest) -> dict:
    store, _factory = _store()
    blueprint = _load(blueprint_id)
    found = next((s for s in blueprint.suggestions if s.id == feature_id), None)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such feature")
    changes = {key: value for key, value in request.model_dump().items() if value is not None}
    edited: FeatureSuggestion = found.model_copy(update=changes)
    suggestions = [edited if s.id == feature_id else s for s in blueprint.suggestions]
    return _view(store.save(blueprint.model_copy(update={"suggestions": suggestions}),
                            status=BuildStatus.BLUEPRINTING))


@router.post("/{blueprint_id}/config")
def set_config(blueprint_id: str, config: BlueprintConfig) -> dict:
    store, _factory = _store()
    blueprint = _load(blueprint_id)
    return _view(store.save(blueprint.model_copy(update={"config": config}),
                            status=BuildStatus.BLUEPRINTING))


@router.post("/{blueprint_id}/systems")
async def systems(blueprint_id: str) -> dict:
    """Ask the architect which systems the chosen features need.

    A separate call from suggestions on purpose: this one is given the
    selections and the REJECTIONS, so a refused feature cannot come back as a
    system.
    """
    store, factory = _store()
    blueprint = _load(blueprint_id)
    if blueprint.undecided_features():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "decide on every suggested feature first: the systems depend on what was chosen")
    design = load_design(factory, blueprint.audit_id)
    architect, clients = await _architect(factory)
    try:
        planned, assets = await architect.systems(design, blueprint,
                                                  time.monotonic() + ARCHITECT_SECONDS)
    except ArchitectRefused as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    finally:
        for _name, client in clients:
            await client.close()

    updated = blueprint.model_copy(update={
        "systems": planned[:40],
        "asset_requirements": assets[:40],
    })
    saved = store.save(updated, status=BuildStatus.BLUEPRINTING)
    ready = assess(saved).ready
    if ready:
        saved = store.save(saved, status=BuildStatus.READY_TO_BUILD)
    return _view(saved)


class SystemToggleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: dict[str, bool] = Field(default_factory=dict)


@router.post("/{blueprint_id}/systems/toggle")
def toggle_systems(blueprint_id: str, request: SystemToggleRequest) -> dict:
    """Turn systems off, and say what that breaks.

    A system another system depends on is not silently removed: the answer
    names the dependents, so a contradictory specification cannot be built by
    accident.
    """
    store, _factory = _store()
    blueprint = _load(blueprint_id)
    index = {system.name: system for system in blueprint.systems}
    unknown = sorted(set(request.enabled) - set(index))
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"no such system(s): {', '.join(unknown)}")

    disabled = {name for name, on in request.enabled.items() if not on}
    blocked: list[dict] = []
    for system in blueprint.systems:
        if system.name in disabled:
            continue
        needed = [name for name in system.depends_on if name in disabled]
        if needed:
            blocked.append({"system": system.name, "depends_on": needed})
    if blocked:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "message": "other systems depend on the ones you turned off",
            "blocked": blocked,
        })

    systems: list[GameSystem] = [system for system in blueprint.systems
                                 if request.enabled.get(system.name, True)]
    return _view(store.save(blueprint.model_copy(update={"systems": systems}),
                            status=BuildStatus.BLUEPRINTING))


@router.get("/{blueprint_id}/specification")
def specification(blueprint_id: str) -> dict:
    """What the Engineer would be handed, and what it would do with it.

    Compiled fresh on every read. A stored preview can disagree with the
    blueprint it claims to describe, and nothing would notice.
    """
    blueprint = _load(blueprint_id)
    try:
        spec = compile_spec(blueprint)
    except NotReady as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    try:
        tasks = tasks_from(spec)
    except SpecUnusable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return {
        "specification": spec.model_dump(mode="json"),
        "preview": human_preview(spec),
        "plan": plan_summary(spec),
        "tasks": [{"system": task.system, "criteria": len(task.acceptance_criteria)}
                  for task in tasks],
    }


engineer_router = APIRouter(prefix="/api/engineer", tags=["engineer"])


def _toolchain() -> dict:
    from ..engineer.runs import game_repo
    from ..engineer.toolchain import inspect

    settings = get_settings()
    repo = game_repo(settings)
    return inspect(repo=repo, definitions=repo / settings.luau_definitions_file).as_dict()


@engineer_router.get("/toolchain")
def toolchain() -> dict:
    """Whether the machine that would run a build can actually run one.

    The build is driven from a browser that may be on another device, so the
    person pressing the button cannot see whether `agy` is installed on the
    machine that does the work. This answers that from the machine itself,
    resolving every tool exactly the way the gate resolves it.

    It is read live rather than cached: a tool appears on PATH the moment it is
    installed, and a cached "not ready" would outlive the fix.
    """
    return _toolchain()


studio_router = APIRouter(prefix="/api/studio", tags=["studio"])


@studio_router.get("")
def studio(token: str = "") -> dict:
    """What Roblox Studio is really doing, or why we cannot tell.

    Three states the UI has to tell apart, because each has a different fix:
    the bridge is not running, the bridge is running but the token is wrong,
    and the bridge is running but Studio has not connected.
    """
    try:
        hello = httpx.get(f"{BRIDGE_URL}/bridge/hello", timeout=2.0)
    except httpx.HTTPError:
        return {"bridge": "offline", "plugin_connected": False,
                "detail": "The local bridge is not running. Start it with: python -m app.bridge.run"}
    if hello.status_code != 200:
        return {"bridge": "unknown", "plugin_connected": False,
                "detail": f"something is answering on {BRIDGE_URL} but it is not the bridge"}
    if not token:
        return {"bridge": "online", "plugin_connected": False,
                "detail": "The bridge is running. Paste its pairing token to see Studio's state.",
                "needs_token": True}
    began = time.perf_counter()
    try:
        answer = httpx.get(f"{BRIDGE_URL}/bridge/status", timeout=3.0,
                           headers={"X-Bridge-Token": token})
    except httpx.HTTPError as exc:
        return {"bridge": "online", "plugin_connected": False, "detail": str(exc)[:200]}
    # Measured, not claimed: the round trip to the bridge this request just made.
    latency_ms = round((time.perf_counter() - began) * 1000, 1)
    if answer.status_code == 401:
        return {"bridge": "online", "plugin_connected": False,
                "detail": "The bridge refused that token. It prints the right one when it starts.",
                "needs_token": True}
    state = answer.json()
    return {"bridge": "online", "latency_ms": latency_ms, **state}


build_router = APIRouter(prefix="/api/builds", tags=["build"])


class BuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blueprint_id: str = Field(min_length=1, max_length=64)
    token: str = Field(min_length=1, max_length=200, description="the bridge pairing token")
    play: bool = True


@build_router.post("", status_code=status.HTTP_202_ACCEPTED)
async def start_build(request: BuildRequest) -> dict:
    """Generate the specification's systems and put them into Studio.

    Accepted rather than awaited: the Engineer takes minutes per system, and a
    request held open for that long is a request that times out somewhere in
    between. The build id comes back at once and the work is followed on
    `/api/builds/{id}/events`.
    """
    from ..db import SessionLocal
    from .builds import BuildFailed, new_id, run_build

    # First, before the blueprint is even looked at. "This machine cannot build
    # anything" outranks "this blueprint is not ready", and it is the one the
    # person cannot see: they are driving from a browser on another device, and
    # a missing formatter would otherwise surface eight minutes in, after the
    # run has spent an attempt on every system.
    ready = _toolchain()
    if not ready["ready"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "the build machine is missing: " + ", ".join(ready["missing"])
            + ". Open the Engineering Agent to see what each one needs.")

    blueprint = _load(request.blueprint_id)
    try:
        compile_spec(blueprint)
    except NotReady as exc:
        # Checked here so an unready blueprint fails the REQUEST rather than
        # failing silently in a background task nobody is watching yet.
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None

    settings = get_settings()
    build_id = new_id()

    async def work() -> None:
        try:
            await run_build(request.blueprint_id, settings=settings, factory=SessionLocal,
                            token=request.token, play=request.play, build_id=build_id)
        except BuildFailed:
            pass  # already recorded on the build, with its reason
        except Exception:  # noqa: BLE001
            from .builds import BuildRecord
            from .schemas import BuildStatus as S

            record = BuildRecord(SessionLocal, build_id)
            try:
                record.event("failed", "the build stopped unexpectedly", status=S.FAILED.value)
            except KeyError:
                pass

    asyncio.create_task(work())
    return {"build_id": build_id, "blueprint_id": request.blueprint_id,
            "follow": f"/api/builds/{build_id}/events"}


@build_router.get("")
def builds(blueprint_id: str = "", limit: int = 20) -> dict:
    """Build history, newest first. Reproducibility: every build carries the
    specification revision and content hash it was made from."""
    from ..db import SessionLocal
    from .builds import list_builds

    return {"builds": list_builds(SessionLocal, blueprint_id, max(1, min(limit, 100)))}


@build_router.get("/{build_id}")
def build(build_id: str) -> dict:
    from ..db import SessionLocal
    from .builds import read_build

    try:
        return read_build(SessionLocal, build_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such build") from None


def _subject(event: dict) -> str:
    """The system name an event is about: every one is written "Name: what".""" 
    return str(event.get("detail") or "").split(":", 1)[0].strip()


def _progress(record: dict, spec) -> tuple[dict, bool, str | None, set[str]]:
    """Where the build actually is: outcomes, whether it runs, what is in
    flight, and what it is not going to write at all.

    One function because the graph and the steering panel must never disagree
    about which system is being written -- one of them saying a directive can
    still reach a system the other is drawing as in flight would be worse than
    either being wrong alone.
    """
    from .transitions import is_running

    outcomes: dict[str, dict] = record.get("systems") or {}
    running = is_running(BuildStatus(record["status"]))

    # Which system is being written comes from the event that recorded the
    # Engineer being asked -- not from build order. The Engineer skips a system
    # the project already has, so walking build_order named a system that was
    # never asked for and drew it as in flight for the whole run.
    asked = [_subject(event) for event in record["events"]
             if event.get("stage") == "generating"
             and "asking the Engineer" in str(event.get("detail") or "")]
    current = (next((name for name in reversed(asked) if name not in outcomes), None)
               if running else None)

    # Recorded since this was written; derived for builds made before it, where
    # a system nobody asked for while later ones were asked can only have been
    # skipped for already existing.
    stored = record.get("skipped")
    if stored is None:
        seen = set(asked)
        last = max((spec.build_order.index(name) for name in seen
                    if name in spec.build_order), default=-1)
        skipped = {name for index, name in enumerate(spec.build_order)
                   if name not in seen and name not in outcomes and index < last}
    else:
        skipped = set(stored)
    return outcomes, running, current, skipped


def _build_and_spec(build_id: str):
    """The build record and the specification it was made from, or a 404/409."""
    from ..db import SessionLocal
    from .builds import read_build

    try:
        record = read_build(SessionLocal, build_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such build") from None
    try:
        return record, compile_spec(_load(record["blueprint_id"]))
    except NotReady as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


def _studio_location(source_path: str) -> tuple[str, str]:
    """Where a generated file lands in the DataModel, or ("", "") if nowhere.

    The same mapping the sync uses, so the explorer shows the tree the build
    would really produce rather than a second opinion about it.
    """
    from ..bridge.from_project import Unmappable, studio_path

    try:
        return studio_path(source_path)
    except Unmappable:
        return "", ""


def _system_durations(events: list[dict]) -> dict[str, float]:
    """How long each system actually took, from the recorded timestamps.

    Measured, not estimated: the clock starts at the event that says the
    Engineer was asked and stops at the event that records what came back.
    A system with no end event is left out entirely rather than counted as
    however long it has been running, which would make an average drift
    upward the longer you watch it.
    """
    from datetime import datetime

    started: dict[str, str] = {}
    spans: dict[str, float] = {}
    for event in events:
        detail = str(event.get("detail") or "")
        name = detail.split(":", 1)[0].strip()
        if not name:
            continue
        if event.get("stage") == "generating" and "asking the Engineer" in detail:
            started[name] = str(event.get("at") or "")
        elif event.get("stage") in {"system_built", "system_refused", "system_failed"}:
            begin, end = started.get(name), str(event.get("at") or "")
            if not begin or not end:
                continue
            try:
                spans[name] = (datetime.fromisoformat(end)
                               - datetime.fromisoformat(begin)).total_seconds()
            except ValueError:
                continue
    return spans


def _sync_state(events: list[dict]) -> dict:
    """What Studio was sent and what it reported back.

    Empty until the build actually queues a batch. The UI must be able to say
    "nothing has been sent" rather than show a tree implying it has.
    """
    sent: dict = {"batch_id": "", "operations": 0, "sent_at": "",
                  "applied": None, "skipped": None, "failed": None, "reported_at": ""}
    for event in events:
        if event.get("stage") == "syncing" and event.get("batch_id"):
            sent["batch_id"] = event["batch_id"]
            sent["operations"] = event.get("operations") or 0
            sent["sent_at"] = event.get("at") or ""
        if event.get("stage") == "studio_result":
            result = event.get("result") or {}
            sent["applied"] = result.get("applied")
            sent["skipped"] = result.get("skipped")
            sent["failed"] = result.get("failed_count")
            sent["reported_at"] = event.get("at") or ""
    return sent


def _explorer(nodes: list[dict]) -> list[dict]:
    """The DataModel tree these systems map to, as nested rows.

    Built from the same path mapping the sync uses. Every leaf carries the
    state of the system it came from, so a folder of waiting modules cannot be
    mistaken for a folder of applied ones.
    """
    root: dict[str, dict] = {}
    for node in nodes:
        location = node.get("studio_path") or ""
        if not location:
            continue
        here = root
        segments = location.split("/")
        for depth, segment in enumerate(segments):
            leaf = depth == len(segments) - 1
            entry = here.setdefault(segment, {
                "name": segment, "children": {},
                "class": node["studio_class"] if leaf else "Folder",
                "system": node["id"] if leaf else "",
                "state": node["state"] if leaf else "",
            })
            here = entry["children"]

    def flatten(level: dict) -> list[dict]:
        rows = []
        for entry in sorted(level.values(), key=lambda item: (not item["children"], item["name"])):
            rows.append({**entry, "children": flatten(entry["children"])})
        return rows

    return flatten(root)


@build_router.get("/{build_id}/graph")
def build_graph(build_id: str) -> dict:
    """The architecture as a graph, with each node's REAL state.

    Nodes are the specification's systems and edges are their declared
    dependencies -- both already exist, so nothing here invents structure.

    A node's state is derived, never guessed:

        built     the Engineer wrote it and the six-check gate accepted it
        refused   it wrote it and the gate refused it
        error     the run itself failed
        building  the last system the Engineer was asked for, still unanswered
        existing  the project already had it, so this build does not rewrite it
        waiting   everything else

    "building" comes from the event that recorded the Engineer being asked, not
    from position in the build order. That distinction was not academic: the
    Engineer skips a system the project already has, so reading build order
    named a system nobody had asked for and drew it as in flight for the whole
    run. If the build is not running, nothing is building -- a UI that pulses a
    node while nothing is happening is the simulated progress this endpoint
    exists to avoid.
    """
    from ..db import SessionLocal
    from .builds import read_build

    try:
        record = read_build(SessionLocal, build_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such build") from None

    blueprint = _load(record["blueprint_id"])
    try:
        spec = compile_spec(blueprint)
    except NotReady as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None

    outcomes, running, current, skipped = _progress(record, spec)

    by_name = {system.name: system for system in spec.systems}
    nodes = []
    for name in spec.build_order:
        system = by_name.get(name)
        if system is None:
            continue
        outcome = outcomes.get(name)
        location, script_class = _studio_location(system.path)
        if outcome is None:
            state = ("building" if name == current
                     else "existing" if name in skipped else "waiting")
        elif outcome["status"] == "built":
            state = "built"
        elif outcome["status"] == "refused":
            state = "refused"
        else:
            state = "error"
        nodes.append({
            "id": system.name, "name": system.name, "layer": system.layer.value,
            "path": system.path, "purpose": system.purpose,
            "acceptance_criteria": system.acceptance_criteria,
            "depends_on": system.depends_on, "state": state,
            "detail": (outcome or {}).get("reason") or (outcome or {}).get("detail") or "",
            "branch": (outcome or {}).get("branch"),
            "commit": (outcome or {}).get("commit") or "",
            "attempts": (outcome or {}).get("attempts") or 0,
            "studio_path": location, "studio_class": script_class,
            "order": spec.build_order.index(name),
        })

    edges = [{"from": dependency, "to": node["id"]}
             for node in nodes for dependency in node["depends_on"]
             if dependency in by_name]

    counts: dict[str, int] = {}
    for node in nodes:
        counts[node["state"]] = counts.get(node["state"], 0) + 1

    # Facts about how long this has taken, and nothing beyond them. There is no
    # countdown here on purpose: a "4m 28s remaining" is a guess dressed as a
    # measurement, and the systems already written differ in size from the ones
    # that have not been started.
    durations = _system_durations(record["events"])
    attempted = sum(node["attempts"] for node in nodes)
    pace = {
        "started_at": record["created_at"],
        "completed_at": record.get("completed_at") or "",
        "systems_measured": len(durations),
        "average_system_seconds": (round(sum(durations.values()) / len(durations), 1)
                                   if durations else None),
        "slowest_system_seconds": round(max(durations.values()), 1) if durations else None,
        "per_system_seconds": {name: round(value, 1) for name, value in durations.items()},
        "attempts_spent": attempted,
        "systems_accepted": counts.get("built", 0),
    }

    return {
        "build_id": build_id,
        "status": record["status"],
        "title": record["title"],
        "spec_revision": record["spec_revision"],
        "content_hash": record["content_hash"],
        "goal": {
            "title": blueprint.title,
            "intent": blueprint.user_intent,
            "included_features": spec.included_features,
            "excluded_features": spec.excluded_features,
            "constraints": spec.technical_constraints,
            "acceptance_criteria_total": len(spec.acceptance_criteria),
        },
        "nodes": nodes,
        "edges": edges,
        "current": current,
        "counts": {**counts, "total": len(nodes)},
        "pace": pace,
        "sync": _sync_state(record["events"]),
        "steering": _steering_view(record, spec),
        "explorer": _explorer(nodes),
        "events": record["events"][-200:],
    }


def _reachable(record: dict, spec) -> list[str]:
    """The systems a directive added right now could still reach.

    Not the one in flight: its prompt was built before the directive existed,
    so listing it would promise something the build cannot deliver. Not one the
    project already has either -- this build is not going to write it.
    """
    outcomes, running, current, skipped = _progress(record, spec)
    if not running:
        return []
    return [name for name in spec.build_order
            if name not in outcomes and name not in skipped and name != current]


def _steering_view(record: dict, spec) -> dict:
    from .steering import MAX_ACTIVE, as_shown

    _outcomes, running, _current, _skipped = _progress(record, spec)
    return {
        "directives": as_shown(record, running=running),
        "reachable": _reachable(record, spec),
        "accepting": running,
        "max_active": MAX_ACTIVE,
    }


class DirectiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    # "" means every system still to be written.
    system: str = Field(default="", max_length=64)


@build_router.get("/{build_id}/directives")
def read_directives(build_id: str) -> dict:
    record, spec = _build_and_spec(build_id)
    return _steering_view(record, spec)


@build_router.post("/{build_id}/directives", status_code=status.HTTP_201_CREATED)
def add_directive(build_id: str, request: DirectiveRequest) -> dict:
    """Add an instruction the Engineer will be given for the systems it has
    not started.

    Refused rather than stored when it cannot reach anything: a panel listing
    directives that changed nothing is worse than no panel.
    """
    from ..db import SessionLocal
    from .builds import BuildRecord
    from .steering import DirectiveRefused, new_directive, with_directive

    record, spec = _build_and_spec(build_id)
    try:
        directive = new_directive(request.text, request.system,
                                  reachable=_reachable(record, spec))
        with_directive(record, directive)  # the ceiling, checked before writing
    except DirectiveRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None

    written = BuildRecord(SessionLocal, build_id).mutate(
        lambda current: {"directives": with_directive(current, directive)})
    return {"directive": directive, **_steering_view(written, spec)}


@build_router.delete("/{build_id}/directives/{directive_id}")
def remove_directive(build_id: str, directive_id: str) -> dict:
    """Withdraw one that has not been used yet."""
    from ..db import SessionLocal
    from .builds import BuildRecord
    from .steering import DirectiveRefused, without_directive

    record, spec = _build_and_spec(build_id)
    try:
        without_directive(record, directive_id)
    except DirectiveRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None

    written = BuildRecord(SessionLocal, build_id).mutate(
        lambda current: {"directives": without_directive(current, directive_id)})
    return _steering_view(written, spec)


@build_router.get("/{build_id}/systems/{system}/source")
def system_source(build_id: str, system: str) -> dict:
    """The Luau the gate accepted for one system, read from its commit.

    Not the worktree: the worktree is removed when the run ends, and the commit
    is the thing the six checks were actually run against. A system that was
    refused has no accepted source, and says so rather than showing the last
    rejected attempt as though it had passed.
    """
    from ..db import SessionLocal
    from ..engineer.runs import game_repo
    from .builds import files_on_branch, read_build

    try:
        record = read_build(SessionLocal, build_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such build") from None

    outcome = (record.get("systems") or {}).get(system)
    if outcome is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"{system} has no recorded outcome in this build")
    if outcome.get("status") != "built":
        return {"system": system, "state": outcome.get("status"), "source": "",
                "path": "", "commit": "", "branch": outcome.get("branch") or "",
                "detail": outcome.get("reason") or outcome.get("detail") or "",
                "lines": 0}

    reference = outcome.get("commit") or outcome.get("branch") or ""
    repo = game_repo(get_settings())
    files = files_on_branch(repo, reference) if reference else {}
    for path, source in files.items():
        if path.rsplit("/", 1)[-1].removesuffix(".luau") == system:
            return {"system": system, "state": "built", "source": source, "path": path,
                    "commit": outcome.get("commit") or "", "branch": outcome.get("branch") or "",
                    "detail": "", "lines": len(source.splitlines())}
    raise HTTPException(status.HTTP_404_NOT_FOUND,
                        f"{system} was accepted on {reference} but no file for it is there")


class OpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=200, description="the bridge pairing token")


@build_router.post("/{build_id}/systems/{system}/open")
def open_in_studio(build_id: str, system: str, request: OpenRequest) -> dict:
    """Ask Studio to open this system's script in its editor.

    A real operation over the real bridge, so it fails honestly when Studio is
    not connected instead of pretending to have opened something.
    """
    from ..bridge.protocol import OpenScript, OperationBatch
    from ..db import SessionLocal
    from .builds import read_build

    try:
        record = read_build(SessionLocal, build_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such build") from None

    blueprint = _load(record["blueprint_id"])
    try:
        spec = compile_spec(blueprint)
    except NotReady as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None

    wanted = next((entry for entry in spec.systems if entry.name == system), None)
    if wanted is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{system} is not in this specification")
    location, _ = _studio_location(wanted.path)
    if not location:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{wanted.path} does not map into the DataModel")

    batch = OperationBatch(
        build_id=build_id, batch_id=f"open-{system}-{int(time.time())}"[:64],
        operations=[OpenScript(operation_id="open-1", sequence=0, path=location)],
    )
    try:
        queued = httpx.post(f"{BRIDGE_URL}/bridge/batches", timeout=10.0,
                            headers={"X-Bridge-Token": request.token},
                            json=batch.model_dump(by_alias=True, mode="json"))
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "The local bridge is not running.") from None
    if queued.status_code == 401:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "The bridge refused that token.")
    if queued.status_code != 202:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"the bridge refused the batch: {queued.text[:200]}")
    return {"opened": location, "batch_id": batch.batch_id}


@build_router.get("/{build_id}/events")
async def build_events(build_id: str) -> StreamingResponse:
    """The build as it happens, over the SSE the rest of the app already uses.

    Only real transitions are sent. Nothing here emits a line to make the UI
    look alive, and when the build reaches a terminal state the stream ends
    rather than idling forever.
    """
    from ..db import SessionLocal
    from .builds import read_build
    from .transitions import is_terminal

    async def stream():
        sent = 0
        for _ in range(1200):  # about ten minutes at the poll below
            try:
                record = read_build(SessionLocal, build_id)
            except KeyError:
                yield 'event: error\ndata: {"detail": "no such build"}\n\n'
                return
            events = record["events"]
            while sent < len(events):
                payload = json.dumps({**events[sent], "status": record["status"]})
                yield f"data: {payload}\n\n"
                sent += 1
            if is_terminal(BuildStatus(record["status"])):
                done = json.dumps({"stage": "done", "status": record["status"]})
                yield f"data: {done}\n\n"
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@build_router.get("/batches/{batch_id}")
def build_result(batch_id: str, token: str = "") -> dict:
    # Two segments, so it cannot be shadowed by /api/builds/{build_id}.
    """What Studio did with it. Straight from the bridge, unedited."""
    if not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the bridge pairing token is required")
    try:
        answer = httpx.get(f"{BRIDGE_URL}/bridge/results/{batch_id}", timeout=10.0,
                           headers={"X-Bridge-Token": token})
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "the local bridge is not running") from None
    if answer.status_code == 404:
        return {"status": "pending", "detail": "the plugin has not reported yet"}
    if answer.status_code != 200:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, answer.text[:300])
    result = answer.json()
    failed = [entry for entry in result["results"] if entry["status"] == "failed"]
    return {"status": "failed" if failed else "applied", **result,
            "applied": len([e for e in result["results"] if e["status"] == "applied"]),
            "skipped": len([e for e in result["results"] if e["status"] == "skipped"]),
            "failed_count": len(failed)}
