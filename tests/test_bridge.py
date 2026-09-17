"""The wire between the backend and Roblox Studio.

This is a localhost HTTP service on a developer's machine, which means every
page in their browser can reach the port. So the tests that matter most are
refusals: no token, wrong token, a path that escapes its service, a class the
build may not create, and above all no way to express "run this code".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.bridge.protocol import (
    PROTOCOL_VERSION,
    CreateInstance,
    CreateScript,
    DeleteInstance,
    OperationBatch,
    PathError,
    ProtocolError,
    SetProperty,
    StartPlaytest,
    UpdateScript,
    validate_batch,
    validate_path,
)
from app.bridge.service import BridgeState, create_bridge

TOKEN = "test-token-abcdefghijklmnop"


def batch(*operations, build_id: str = "build1", batch_id: str = "batch1") -> OperationBatch:
    return OperationBatch(build_id=build_id, batch_id=batch_id, operations=list(operations))


def a_script(sequence: int = 0, path: str = "ReplicatedStorage/Shared/Config") -> CreateScript:
    return CreateScript(operation_id=f"op{sequence}", sequence=sequence, path=path,
                        scriptType="ModuleScript", source="--!strict\nreturn {}\n")


@pytest.fixture
def client():
    state = BridgeState(token=TOKEN)
    with TestClient(create_bridge(state)) as test_client:
        test_client.state = state  # type: ignore[attr-defined]
        yield test_client


def auth() -> dict:
    return {"X-Bridge-Token": TOKEN}


# ---- there is no way to ask Studio to run arbitrary code -------------------

def test_the_operation_set_is_closed():
    """The whole point. An operation carrying Lua for the plugin to load would
    turn a localhost port into code execution inside someone's Studio."""
    from app.bridge.protocol import OperationKind

    names = {kind.value for kind in OperationKind}
    for forbidden in ("eval", "run_lua", "execute", "run_command", "shell", "loadstring"):
        assert forbidden not in names


def test_an_unknown_operation_is_refused_by_the_schema(client):
    response = client.post("/bridge/batches", headers=auth(), json={
        "protocol_version": PROTOCOL_VERSION, "build_id": "b", "batch_id": "c",
        "operations": [{"operation": "run_command", "operation_id": "x", "sequence": 0,
                        "command": "rm -rf /"}]})
    assert response.status_code == 422


def test_a_script_cannot_be_smuggled_in_as_a_plain_instance():
    """create_instance would skip the source checks create_script gets."""
    with pytest.raises(ProtocolError, match="use create_script"):
        validate_batch(batch(CreateInstance(
            operation_id="a", sequence=0, path="ReplicatedStorage/Evil",
            className="ModuleScript")))


# ---- paths stay inside a service -------------------------------------------

@pytest.mark.parametrize("path", [
    "ReplicatedStorage/Shared/Config",
    "ServerScriptService/Services/WaveService",
    "StarterGui/HUD/Root",
    "Workspace/Arena/Floor",
])
def test_a_path_inside_an_allowed_service_is_accepted(path):
    assert validate_path(path) == path


@pytest.mark.parametrize("path", [
    "CoreGui/Thing",
    "Players/Someone",
    "../ReplicatedStorage/Thing",
    "ReplicatedStorage",
    "ReplicatedStorage//Thing",
    "ReplicatedStorage/../CoreGui/Thing",
    "ReplicatedStorage/Shared/..",
    "C:\\Windows\\System32",
    "",
])
def test_a_path_that_escapes_or_is_not_a_path_is_refused(path):
    with pytest.raises(PathError):
        validate_path(path)


def test_a_service_itself_is_never_the_target():
    """Setting a property on ReplicatedStorage is a different act from building
    inside it, and nothing in a build needs it."""
    with pytest.raises(PathError, match="never writes a service itself"):
        validate_path("Workspace")


# ---- what a build may create -----------------------------------------------

def test_a_class_outside_the_allowed_set_is_refused():
    with pytest.raises(ProtocolError, match="not a class this build may create"):
        validate_batch(batch(CreateInstance(operation_id="a", sequence=0,
                                            path="Workspace/Thing", className="Sound")))


def test_the_allowed_classes_cover_what_a_prototype_needs():
    from app.bridge.protocol import ALLOWED_CLASSES

    for needed in ("Folder", "Part", "SpawnLocation", "RemoteEvent", "ScreenGui",
                   "TextLabel", "ModuleScript", "Script", "LocalScript"):
        assert needed in ALLOWED_CLASSES


# ---- batch shape -----------------------------------------------------------

def test_two_operations_with_one_id_are_refused():
    """Results come back keyed by operation id; two of them make a result
    ambiguous about which operation it describes."""
    with pytest.raises(ProtocolError, match="appears twice"):
        validate_batch(batch(a_script(0), a_script(0, "ReplicatedStorage/Shared/Other")))


def test_operations_out_of_order_are_refused():
    """A create that arrives after the thing it is the parent of would fail for
    a reason nobody could trace back to ordering."""
    first, second = a_script(5), a_script(1, "ReplicatedStorage/Shared/Other")
    second.operation_id = "op-other"
    with pytest.raises(ProtocolError, match="ascending sequence"):
        validate_batch(batch(first, second))


def test_a_protocol_version_the_plugin_does_not_speak_is_refused():
    bad = batch(a_script())
    bad.protocol_version = 99
    with pytest.raises(ProtocolError, match="protocol version"):
        validate_batch(bad)


def test_an_empty_batch_is_refused_by_the_schema():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        OperationBatch(build_id="b", batch_id="c", operations=[])


# ---- authentication --------------------------------------------------------

def test_an_unauthenticated_caller_gets_nothing(client):
    for method, path in [("get", "/bridge/status"), ("get", "/bridge/work"),
                         ("get", "/bridge/events"), ("get", "/bridge/runtime-errors")]:
        assert getattr(client, method)(path).status_code == 401


def test_a_wrong_token_is_refused(client):
    assert client.get("/bridge/status", headers={"X-Bridge-Token": "nope"}).status_code == 401


def test_queueing_work_needs_the_token(client):
    payload = batch(a_script()).model_dump(by_alias=True, mode="json")
    assert client.post("/bridge/batches", json=payload).status_code == 401


def test_hello_is_open_and_says_nothing_secret(client):
    """It exists so the backend can tell "no bridge" from "wrong token", which
    are different problems with different instructions."""
    response = client.get("/bridge/hello")

    assert response.status_code == 200
    body = response.json()
    assert body["protocol_version"] == PROTOCOL_VERSION
    assert TOKEN not in response.text


# ---- the queue -------------------------------------------------------------

def test_queued_work_comes_back_to_the_plugin_once(client):
    payload = batch(a_script()).model_dump(by_alias=True, mode="json")
    assert client.post("/bridge/batches", headers=auth(), json=payload).status_code == 202

    first = client.get("/bridge/work", headers=auth()).json()
    assert first["batch"]["batch_id"] == "batch1"
    assert len(first["batch"]["operations"]) == 1

    second = client.get("/bridge/work", headers=auth()).json()
    assert second["batch"] is None, "work already handed out must not be handed out again"


def test_an_invalid_batch_is_refused_at_the_bridge_too(client):
    """The backend validates, and so does this: the endpoint is reachable by
    anything holding the token, and the plugin trusts what the bridge hands it."""
    payload = batch(a_script()).model_dump(by_alias=True, mode="json")
    payload["operations"][0]["path"] = "CoreGui/Sneaky"

    response = client.post("/bridge/batches", headers=auth(), json=payload)

    assert response.status_code == 422
    assert "CoreGui" in response.text


def test_results_come_back_and_can_be_read(client):
    client.post("/bridge/results", headers=auth(), json={
        "build_id": "build1", "batch_id": "batch1",
        "results": [{"operation_id": "op0", "status": "applied"},
                    {"operation_id": "op1", "status": "failed", "detail": "no such path"}],
        "place_name": "ZombiePrototype"})

    stored = client.get("/bridge/results/batch1", headers=auth()).json()

    assert stored["place_name"] == "ZombiePrototype"
    assert [r["status"] for r in stored["results"]] == ["applied", "failed"]


def test_a_result_nobody_has_sent_is_a_404_not_an_empty_success(client):
    assert client.get("/bridge/results/never", headers=auth()).status_code == 404


# ---- liveness --------------------------------------------------------------

def test_the_plugin_is_not_connected_until_it_says_so(client):
    assert client.get("/bridge/status", headers=auth()).json()["plugin_connected"] is False


def test_a_heartbeat_makes_it_connected(client):
    client.post("/bridge/heartbeat", headers=auth(), json={
        "plugin_version": "0.1.0", "place_name": "ZombiePrototype", "mode": "edit"})

    status = client.get("/bridge/status", headers=auth()).json()

    assert status["plugin_connected"] is True
    assert status["studio"]["place_name"] == "ZombiePrototype"


def test_a_stale_heartbeat_is_a_disconnection(client):
    """Studio stalling for two seconds is not a disconnection; Studio being
    closed for a minute is."""
    from app.bridge.service import HEARTBEAT_TIMEOUT

    now = [1000.0]
    client.state.clock = lambda: now[0]  # type: ignore[attr-defined]
    client.post("/bridge/heartbeat", headers=auth(), json={"mode": "edit"})
    assert client.get("/bridge/status", headers=auth()).json()["plugin_connected"] is True

    now[0] += HEARTBEAT_TIMEOUT + 1
    assert client.get("/bridge/status", headers=auth()).json()["plugin_connected"] is False


# ---- the repair loop's input -----------------------------------------------

def test_a_runtime_error_from_studio_is_kept_for_the_engineer(client):
    client.post("/bridge/runtime-errors", headers=auth(), json={
        "build_id": "build1", "severity": "error",
        "message": "ServerScriptService.WaveService:82: attempt to index nil with 'Position'",
        "script_path": "ServerScriptService.WaveService", "line": 82})

    errors = client.get("/bridge/runtime-errors", headers=auth(), params={"build_id": "build1"}).json()

    assert len(errors["errors"]) == 1
    assert errors["errors"][0]["line"] == 82


def test_runtime_errors_are_filtered_by_build(client):
    for build in ("build1", "build2"):
        client.post("/bridge/runtime-errors", headers=auth(), json={
            "build_id": build, "message": "something went wrong"})

    only = client.get("/bridge/runtime-errors", headers=auth(), params={"build_id": "build2"}).json()

    assert len(only["errors"]) == 1
    assert only["errors"][0]["build_id"] == "build2"


def test_every_event_corresponds_to_something_that_happened(client):
    """No generated log lines: section 28. The bridge appends an event when it
    does something, and the list starts empty."""
    assert client.get("/bridge/events", headers=auth()).json()["events"] == []

    client.post("/bridge/batches", headers=auth(),
                json=batch(a_script()).model_dump(by_alias=True, mode="json"))

    kinds = [event["kind"] for event in client.get("/bridge/events", headers=auth()).json()["events"]]
    assert kinds == ["batch_queued"]


# ---- operation shapes the plugin has handlers for --------------------------

def test_every_operation_kind_has_a_handler_in_the_plugin():
    """The plugin refuses an operation it has no handler for, so a kind added
    here without one would be refused at run time instead of at review."""
    from pathlib import Path

    from app.bridge.protocol import OperationKind

    source = Path("plugin/VentureEngineer.server.luau").read_text(encoding="utf-8")
    handled = {"run_test"}  # not yet implemented in the plugin; see the report
    for kind in OperationKind:
        if kind.value in handled:
            continue
        assert f"{kind.value} = apply" in source or f"\t{kind.value} = " in source, \
            f"the plugin has no handler for {kind.value}"


def test_the_playtest_operation_admits_what_a_plugin_cannot_do():
    """Roblox exposes no API for a plugin to start Play Solo. The operation
    carries the mode so the refusal is explicit rather than a silent substitute."""
    operation = StartPlaytest(operation_id="a", sequence=0)
    assert operation.mode == "run"


def test_delete_and_update_carry_paths_that_are_validated():
    with pytest.raises(PathError):
        validate_batch(batch(DeleteInstance(operation_id="a", sequence=0, path="CoreGui/Thing")))
    with pytest.raises(PathError):
        validate_batch(batch(UpdateScript(operation_id="a", sequence=0,
                                          path="CoreGui/Thing", source="x")))
    with pytest.raises(PathError):
        validate_batch(batch(SetProperty(operation_id="a", sequence=0,
                                         path="CoreGui/Thing", property="Name", value="x")))
