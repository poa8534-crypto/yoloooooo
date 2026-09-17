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
    try:
        answer = httpx.get(f"{BRIDGE_URL}/bridge/status", timeout=3.0,
                           headers={"X-Bridge-Token": token})
    except httpx.HTTPError as exc:
        return {"bridge": "online", "plugin_connected": False, "detail": str(exc)[:200]}
    if answer.status_code == 401:
        return {"bridge": "online", "plugin_connected": False,
                "detail": "The bridge refused that token. It prints the right one when it starts.",
                "needs_token": True}
    state = answer.json()
    return {"bridge": "online", **state}


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
def builds(blueprint_id: str = "") -> dict:
    """Build history, newest first. Reproducibility: every build carries the
    specification revision and content hash it was made from."""
    from ..db import SessionLocal
    from .builds import list_builds

    return {"builds": list_builds(SessionLocal, blueprint_id)}


@build_router.get("/{build_id}")
def build(build_id: str) -> dict:
    from ..db import SessionLocal
    from .builds import read_build

    try:
        return read_build(SessionLocal, build_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such build") from None


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
