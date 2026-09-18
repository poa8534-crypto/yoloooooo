"""What the backend is allowed to ask Roblox Studio to do.

A closed set of typed operations, and nothing else. The alternative -- sending
Lua for the plugin to load and run -- would make the plugin a remote code
execution service for anything that can reach the bridge, which on a developer's
machine is every page in their browser. So there is no `eval` operation, no
`run_command`, and no way to express one: an operation the plugin does not have
a handler for is refused before it is sent and again when it arrives.

Paths are `Service/Folder/Name`, rooted at a Roblox service. They are validated
here rather than in the plugin because a path that escapes its service is the
one mistake that could damage a place someone cares about, and Luau is a worse
place to be careful in than Python.

Operations carry a sequence number and are applied in order. Where it is
possible they are idempotent -- creating an instance that already exists at that
path with that class is a no-op rather than a duplicate -- so a retried batch
after a dropped connection does not build the project twice.
"""

from __future__ import annotations

import enum
import hashlib
import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 1

# Services a build may touch. Not a style preference: a plugin that can reach
# any service can reach the ones holding a person's unrelated work.
ALLOWED_ROOTS = frozenset({
    "ReplicatedStorage", "ServerScriptService", "ServerStorage", "StarterGui",
    "StarterPlayer", "Workspace", "Lighting", "SoundService", "ReplicatedFirst",
})
# Classes the plugin will create. Everything a prototype needs and nothing that
# executes on its own terms.
ALLOWED_CLASSES = frozenset({
    "Folder", "Model", "Part", "SpawnLocation", "Configuration",
    "ModuleScript", "Script", "LocalScript",
    "RemoteEvent", "RemoteFunction", "BindableEvent", "BindableFunction",
    "ScreenGui", "Frame", "TextLabel", "TextButton", "ImageLabel", "UIListLayout",
    "UICorner", "UIPadding", "IntValue", "NumberValue", "StringValue", "BoolValue",
    "Attachment", "PointLight", "Highlight",
})
SCRIPT_CLASSES = frozenset({"ModuleScript", "Script", "LocalScript"})
REMOTE_CLASSES = frozenset({"RemoteEvent", "RemoteFunction", "BindableEvent", "BindableFunction"})

# Properties a build may set, and nothing else. A whitelist rather than a
# blacklist: Roblox adds properties faster than anyone maintains a list of the
# dangerous ones, and the failure mode of guessing wrong is writing to something
# that changes how a place behaves outside the build.
#
# Deliberately short. Everything here is needed to put a visible, moving
# prototype on screen; the rest can be added when a build actually needs it.
SETTABLE_PROPERTIES = frozenset({
    "Name", "Anchored", "CanCollide", "CanTouch", "Transparency", "Position", "Size",
    "Color", "BrickColor", "Material", "Orientation", "CFrame", "Value", "Text",
    "TextColor3", "BackgroundColor3", "BackgroundTransparency", "Visible", "Enabled",
    "Disabled", "Massless", "CastShadow", "Locked", "ZIndex", "TextSize", "Font",
})

# Value types the plugin knows how to rebuild. Kept beside the property
# whitelist because the two travel together: a property that takes a Vector3 is
# useless without a way to express one.
VALUE_TYPES = frozenset({"Vector3", "Color3", "UDim2", "CFrame"})

_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ .-]{0,62}$")
MAX_SOURCE = 400_000
MAX_OPERATIONS = 500


class PathError(ValueError):
    pass


def validate_path(path: str) -> str:
    """A path inside one allowed service, or a refusal saying why not."""
    if not isinstance(path, str) or not path.strip():
        raise PathError("a path is required, e.g. ReplicatedStorage/Shared/Config")
    if "\\" in path:
        raise PathError(f"{path!r}: use forward slashes")
    parts = [part for part in path.split("/")]
    if any(not part for part in parts):
        raise PathError(f"{path!r}: empty path segment")
    if parts[0] not in ALLOWED_ROOTS:
        raise PathError(f"{path!r}: must start with one of {', '.join(sorted(ALLOWED_ROOTS))}")
    if len(parts) < 2:
        raise PathError(f"{path!r}: a build never writes a service itself, only inside one")
    for part in parts[1:]:
        if part in (".", ".."):
            raise PathError(f"{path!r}: '.' and '..' are not path segments in a DataModel")
        if not _SEGMENT.match(part):
            raise PathError(f"{path!r}: {part!r} is not a usable instance name")
    return path


def parent_of(path: str) -> str:
    return path.rsplit("/", 1)[0]


class OperationKind(str, enum.Enum):
    CREATE_INSTANCE = "create_instance"
    CREATE_SCRIPT = "create_script"
    UPDATE_SCRIPT = "update_script"
    CREATE_REMOTE = "create_remote"
    SET_PROPERTY = "set_property"
    DELETE_INSTANCE = "delete_instance"
    OPEN_SCRIPT = "open_script"
    START_PLAYTEST = "start_playtest"
    STOP_PLAYTEST = "stop_playtest"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=64)
    sequence: int = Field(ge=0)


class CreateInstance(_Base):
    operation: Literal[OperationKind.CREATE_INSTANCE] = OperationKind.CREATE_INSTANCE
    path: str
    class_name: str = Field(alias="className")
    properties: dict[str, object] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CreateScript(_Base):
    operation: Literal[OperationKind.CREATE_SCRIPT] = OperationKind.CREATE_SCRIPT
    path: str
    script_type: str = Field(alias="scriptType")
    source: str = Field(max_length=MAX_SOURCE)
    disabled: bool = False

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class UpdateScript(_Base):
    operation: Literal[OperationKind.UPDATE_SCRIPT] = OperationKind.UPDATE_SCRIPT
    path: str
    source: str = Field(max_length=MAX_SOURCE)


class CreateRemote(_Base):
    """Its own operation rather than a create_instance, so a remote is always
    created somewhere the client can see it and is never a surprise."""

    operation: Literal[OperationKind.CREATE_REMOTE] = OperationKind.CREATE_REMOTE
    path: str
    remote_type: str = Field(default="RemoteEvent", alias="remoteType")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class OpenScript(_Base):
    """Open a script in Studio's editor, so the person sees code rather than a
    message saying code exists."""

    operation: Literal[OperationKind.OPEN_SCRIPT] = OperationKind.OPEN_SCRIPT
    path: str


class SetProperty(_Base):
    operation: Literal[OperationKind.SET_PROPERTY] = OperationKind.SET_PROPERTY
    path: str
    property: str = Field(min_length=1, max_length=64)
    value: object = None


class DeleteInstance(_Base):
    operation: Literal[OperationKind.DELETE_INSTANCE] = OperationKind.DELETE_INSTANCE
    path: str


class StartPlaytest(_Base):
    operation: Literal[OperationKind.START_PLAYTEST] = OperationKind.START_PLAYTEST
    mode: Literal["run", "play"] = "run"


class StopPlaytest(_Base):
    operation: Literal[OperationKind.STOP_PLAYTEST] = OperationKind.STOP_PLAYTEST


Operation = Annotated[
    Union[CreateInstance, CreateScript, UpdateScript, CreateRemote, SetProperty,
          DeleteInstance, OpenScript, StartPlaytest, StopPlaytest],
    Field(discriminator="operation"),
]


class OperationBatch(BaseModel):
    """One ordered unit of work for the plugin."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: int = PROTOCOL_VERSION
    build_id: str = Field(min_length=1, max_length=64)
    batch_id: str = Field(min_length=1, max_length=64)
    operations: list[Operation] = Field(min_length=1, max_length=MAX_OPERATIONS)

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


class OperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1, max_length=64)
    # "started" is for an operation whose outcome the plugin cannot report,
    # because performing it is what stops it reporting. Starting play mode is
    # the only one: `ExecutePlayModeAsync` does not return to a plugin that
    # then has to POST a result, so the batch used to be applied in full and
    # never reported at all -- the backend waited sixty seconds and recorded
    # "Studio did not report", about a sync that had entirely worked.
    #
    # It is deliberately not "applied". Play mode may still refuse to start,
    # and a status that claimed otherwise would be the interface asserting
    # something nobody checked.
    status: Literal["applied", "skipped", "failed", "started"]
    detail: str = Field(default="", max_length=4000)
    # Set when the plugin did nothing because the DataModel already matched.
    unchanged: bool = False


class BatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build_id: str = Field(min_length=1, max_length=64)
    batch_id: str = Field(min_length=1, max_length=64)
    results: list[OperationResult] = Field(default_factory=list, max_length=MAX_OPERATIONS)
    place_name: str = Field(default="", max_length=200)
    errors: list[str] = Field(default_factory=list, max_length=200)

    @property
    def failed(self) -> list[OperationResult]:
        return [result for result in self.results if result.status == "failed"]

    @property
    def applied(self) -> list[OperationResult]:
        return [result for result in self.results if result.status == "applied"]


class RuntimeError_(BaseModel):
    """A message Studio produced while running. Named with a trailing underscore
    so it cannot be confused with the builtin."""

    model_config = ConfigDict(extra="forbid")

    build_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=8000)
    script_path: str = Field(default="", max_length=300)
    line: int | None = None
    trace: str = Field(default="", max_length=8000)
    severity: Literal["error", "warning", "info"] = "error"
    at: str = Field(default="", max_length=64)


class StudioState(BaseModel):
    """What the plugin says about itself on every heartbeat."""

    model_config = ConfigDict(extra="forbid")

    plugin_version: str = Field(default="", max_length=32)
    studio_version: str = Field(default="", max_length=32)
    place_name: str = Field(default="", max_length=200)
    place_id: int = 0
    mode: Literal["edit", "run", "play", "unknown"] = "unknown"


class ProtocolError(ValueError):
    pass


def validate_batch(batch: OperationBatch) -> OperationBatch:
    """Everything checkable before the plugin sees it.

    Checked here and again in the plugin. The duplication is deliberate: this
    side can be bypassed by anything that reaches the bridge, and the plugin is
    the side holding the DataModel.
    """
    if batch.protocol_version != PROTOCOL_VERSION:
        raise ProtocolError(f"protocol version {batch.protocol_version} is not {PROTOCOL_VERSION}")

    seen: set[str] = set()
    sequences: list[int] = []
    for operation in batch.operations:
        if operation.operation_id in seen:
            raise ProtocolError(f"operation id {operation.operation_id!r} appears twice")
        seen.add(operation.operation_id)
        sequences.append(operation.sequence)

        path = getattr(operation, "path", None)
        if path is not None:
            validate_path(path)

        if isinstance(operation, CreateInstance):
            if operation.class_name not in ALLOWED_CLASSES:
                raise ProtocolError(
                    f"{operation.class_name!r} is not a class this build may create; "
                    f"allowed: {', '.join(sorted(ALLOWED_CLASSES))}")
            if operation.class_name in SCRIPT_CLASSES:
                raise ProtocolError(
                    f"use create_script for {operation.class_name}, so its source is checked")
        if isinstance(operation, CreateScript) and operation.script_type not in SCRIPT_CLASSES:
            raise ProtocolError(f"{operation.script_type!r} is not a script class")
        if isinstance(operation, CreateRemote) and operation.remote_type not in REMOTE_CLASSES:
            raise ProtocolError(f"{operation.remote_type!r} is not a remote class; "
                                f"allowed: {', '.join(sorted(REMOTE_CLASSES))}")
        if isinstance(operation, SetProperty):
            if operation.property not in SETTABLE_PROPERTIES:
                raise ProtocolError(
                    f"{operation.property!r} is not a property a build may set. The set is a "
                    "whitelist on purpose; add it there when a build needs it.")
            value = operation.value
            tagged = (isinstance(value, dict) and isinstance(value.get("type"), str)
                      and "value" in value)
            if isinstance(value, (dict, list)) and not tagged:
                # `[0, 5, 0]` could be a Vector3, a Color3 or a size, and the
                # plugin would have to guess. A wrong guess writes the wrong
                # thing into a place and looks like it worked.
                raise ProtocolError(
                    f"{operation.property!r}: a non-scalar value must be tagged, as "
                    "{'type': 'Vector3', 'value': [0, 5, 0]}")
            if tagged and value["type"] not in VALUE_TYPES:
                raise ProtocolError(
                    f"{value['type']!r} is not a value type the plugin rebuilds; "
                    f"allowed: {', '.join(sorted(VALUE_TYPES))}")

    if sequences != sorted(sequences):
        raise ProtocolError("operations must be in ascending sequence order")
    return batch


def order_of(batch: OperationBatch) -> list[str]:
    return [operation.operation_id for operation in batch.operations]
