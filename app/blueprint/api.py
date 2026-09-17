"""The Blueprint stage over HTTP.

What the frontend can actually call. Every endpoint here is backed by something
that works: the architect runs through the Engineer's provider chain, readiness
and scope are counted, and the specification is compiled deterministically.

What is deliberately ABSENT: any endpoint that starts a Roblox build. Compiling
a specification into Studio operations does not exist yet, so there is no route
that pretends to. `/studio` reports what the bridge really says, including
"not running", so the UI can show the truth rather than a spinner.
"""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Body, HTTPException, status
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


@build_router.post("", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def start_build(blueprint_id: str = Body(embed=True)) -> dict:
    """Not implemented, and it says so rather than pretending.

    Everything up to here is real: the specification compiles, and
    `tasks_from` turns it into Engineer tasks. What does not exist is the step
    that turns generated Luau into Studio operations -- the Engineer writes
    files into a git worktree, and nothing converts that into a batch for the
    plugin. Until it does, this endpoint returns 501 so the UI can disable the
    button honestly instead of showing a spinner that goes nowhere.
    """
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "Building into Studio is not wired up yet. The blueprint compiles to a specification "
        "and the specification compiles to Engineer tasks, but nothing yet turns generated Luau "
        "into Studio operations. The deterministic build (app/bridge/smoke.py) does reach Studio; "
        "a generated one does not.")
