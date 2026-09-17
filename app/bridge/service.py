"""The local bridge: the only thing that may speak to the Studio plugin.

A web page cannot reach into a desktop application's memory, so something on
this machine has to stand between them. That thing is a small HTTP service the
Studio plugin polls, exactly as Rojo's plugin polls Rojo.

It is deliberately dull. It holds a queue of validated operation batches, hands
them to the plugin when it asks, takes the results back, and remembers whether
the plugin is still there. It does not generate code, decide anything, or run
commands -- there is no endpoint that takes a command, because a localhost
service with one is a remote code execution hole for every page in the browser.

Authentication is a pairing token: a secret generated when the bridge starts and
shown to the person, entered once in the plugin. Both the backend and the plugin
present it on every request. This is not defence against a determined attacker
with local access -- it is defence against every OTHER program on the machine,
including a web page that guessed the port, which is the realistic threat.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import deque
from dataclasses import dataclass, field

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from .protocol import (
    PROTOCOL_VERSION,
    BatchResult,
    OperationBatch,
    ProtocolError,
    RuntimeError_,
    StudioState,
    validate_batch,
)

# How long without a heartbeat before the plugin is considered gone. Studio can
# stall for a few seconds under load, so this is generous: reporting a
# disconnection that has not happened sends the user to fix a working setup.
HEARTBEAT_TIMEOUT = 15.0
# How long the plugin's poll waits for work before returning empty. Long enough
# that an idle plugin is not a busy loop, short enough that Studio's HTTP
# timeout never fires first.
POLL_SECONDS = 20.0
MAX_EVENTS = 500


class Pairing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str
    bridge_version: str
    protocol_version: int


@dataclass
class BridgeState:
    """Everything the bridge knows. One build at a time, on purpose: two builds
    interleaving operations into one DataModel is not a recoverable state."""

    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    bridge_version: str = "0.1.0"
    clock: object = time.monotonic
    queue: deque = field(default_factory=deque)
    results: dict[str, BatchResult] = field(default_factory=dict)
    runtime_errors: list[RuntimeError_] = field(default_factory=list)
    events: deque = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))
    studio: StudioState | None = None
    last_heartbeat: float | None = None
    waiting: asyncio.Event = field(default_factory=asyncio.Event)

    def now(self) -> float:
        return self.clock()  # type: ignore[operator]

    def plugin_connected(self) -> bool:
        if self.last_heartbeat is None:
            return False
        return (self.now() - self.last_heartbeat) < HEARTBEAT_TIMEOUT

    def note(self, kind: str, detail: str) -> None:
        self.events.append({"at": self.now(), "kind": kind, "detail": detail[:2000]})


def create_bridge(state: BridgeState | None = None) -> FastAPI:
    """The bridge app. Built as a factory so tests get their own state rather
    than sharing a module-level singleton."""
    bridge = FastAPI(title="Venture Engineer Bridge", version="0.1.0")
    bridge.state.bridge = state or BridgeState()

    def current() -> BridgeState:
        return bridge.state.bridge

    def authorised(x_bridge_token: str = Header(default=""),
                   shared: BridgeState = Depends(current)) -> BridgeState:
        """Constant-time comparison: a token checked with `==` leaks its prefix
        to anything that can time the response."""
        if not x_bridge_token or not secrets.compare_digest(x_bridge_token, shared.token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bridge token missing or wrong")
        return shared

    @bridge.get("/bridge/hello")
    def hello(shared: BridgeState = Depends(current)) -> dict:
        """Unauthenticated on purpose, and says nothing secret: it exists so the
        backend can tell "bridge not running" from "bridge running, wrong token",
        which are different problems with different instructions."""
        return {"bridge": "venture-engineer", "bridge_version": shared.bridge_version,
                "protocol_version": PROTOCOL_VERSION}

    @bridge.get("/bridge/status")
    def bridge_status(shared: BridgeState = Depends(authorised)) -> dict:
        return {
            "bridge_version": shared.bridge_version,
            "protocol_version": PROTOCOL_VERSION,
            "plugin_connected": shared.plugin_connected(),
            "last_heartbeat": shared.last_heartbeat,
            "studio": shared.studio.model_dump() if shared.studio else None,
            "queued_batches": len(shared.queue),
            "runtime_errors": len(shared.runtime_errors),
        }

    @bridge.post("/bridge/batches", status_code=status.HTTP_202_ACCEPTED)
    def submit(batch: OperationBatch, shared: BridgeState = Depends(authorised)) -> dict:
        """The backend queues work. Validated here even though the backend
        validated it: this endpoint is reachable by anything holding the token,
        and the plugin trusts what the bridge hands it."""
        try:
            validate_batch(batch)
        except (ProtocolError, ValueError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
        shared.queue.append(batch)
        shared.note("batch_queued", f"{batch.batch_id}: {len(batch.operations)} operation(s)")
        shared.waiting.set()
        return {"queued": batch.batch_id, "operations": len(batch.operations),
                "position": len(shared.queue)}

    @bridge.post("/bridge/heartbeat")
    async def heartbeat(state_in: StudioState,
                        shared: BridgeState = Depends(authorised)) -> dict:
        """The plugin says it is alive and what it is looking at."""
        shared.studio = state_in
        shared.last_heartbeat = shared.now()
        return {"ok": True, "queued_batches": len(shared.queue)}

    @bridge.get("/bridge/work")
    async def work(shared: BridgeState = Depends(authorised)) -> dict:
        """The plugin asks for work and waits rather than polling in a loop.

        Returns an empty answer rather than blocking forever, because Studio's
        HTTP client has its own timeout and a request it kills looks like a
        disconnection to everyone.
        """
        shared.last_heartbeat = shared.now()
        if not shared.queue:
            shared.waiting.clear()
            try:
                await asyncio.wait_for(shared.waiting.wait(), timeout=POLL_SECONDS)
            except (TimeoutError, asyncio.TimeoutError):
                return {"batch": None}
        if not shared.queue:
            return {"batch": None}
        batch = shared.queue.popleft()
        shared.note("batch_sent", batch.batch_id)
        return {"batch": batch.model_dump(by_alias=True)}

    @bridge.post("/bridge/results")
    def results(result: BatchResult, shared: BridgeState = Depends(authorised)) -> dict:
        shared.results[result.batch_id] = result
        shared.last_heartbeat = shared.now()
        shared.note("batch_result",
                    f"{result.batch_id}: {len(result.applied)} applied, {len(result.failed)} failed")
        return {"ok": True}

    @bridge.get("/bridge/results/{batch_id}")
    def read_result(batch_id: str, shared: BridgeState = Depends(authorised)) -> dict:
        found = shared.results.get(batch_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no result for that batch yet")
        return found.model_dump()

    @bridge.post("/bridge/runtime-errors")
    def runtime_error(error: RuntimeError_, shared: BridgeState = Depends(authorised)) -> dict:
        """What makes the repair loop possible: Studio's own errors, coming back."""
        shared.runtime_errors.append(error)
        shared.last_heartbeat = shared.now()
        shared.note("runtime_error", f"{error.script_path}:{error.line}: {error.message[:200]}")
        return {"ok": True, "count": len(shared.runtime_errors)}

    @bridge.get("/bridge/runtime-errors")
    def read_runtime_errors(build_id: str = "", shared: BridgeState = Depends(authorised)) -> dict:
        errors = [error for error in shared.runtime_errors
                  if not build_id or error.build_id == build_id]
        return {"errors": [error.model_dump() for error in errors]}

    @bridge.get("/bridge/events")
    def read_events(shared: BridgeState = Depends(authorised)) -> dict:
        """Real events only. Every entry was appended by something that
        happened; nothing here is generated to fill a log window."""
        return {"events": list(shared.events)}

    return bridge


class BridgeUnavailable(RuntimeError):
    pass
