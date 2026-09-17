"""The hard-coded integration build.

Before any generated code goes near Studio, one batch with known contents must
apply and visibly run. If this does not work, nothing measured downstream of it
means anything -- a failure would be somewhere in the pipeline and we would be
guessing which end.

It creates exactly what section 7 asks for, and one thing more: the server
script moves the red block, so success is something you SEE rather than a
green tick claiming instances exist.

Every value here is fixed. There is no model in this path.
"""

from __future__ import annotations

import secrets

from .protocol import (
    CreateInstance,
    CreateScript,
    OpenScript,
    OperationBatch,
    SetProperty,
    StartPlaytest,
    validate_batch,
)

CONFIG_SOURCE = """--!strict

-- Written by the Venture Engineer integration build. If you are reading this
-- inside Roblox Studio, the bridge, the plugin and the operation protocol all
-- work, and the source arrived intact.

return {
	buildName = "GeneratedTest",
	bobHeight = 6,
	bobSeconds = 2,
}
"""

RUNNER_SOURCE = """--!strict

-- Written by the Venture Engineer integration build.
--
-- It moves RedBlock up and down. Movement rather than a print, because a print
-- proves a script ran and movement proves the place is RUNNING the build.

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local folder = ReplicatedStorage:WaitForChild("GeneratedTest")
local config = require(folder:WaitForChild("Config"))

local block = workspace:WaitForChild("GeneratedTest"):WaitForChild("RedBlock")
local origin = block.Position
local elapsed = 0

print(`[{config.buildName}] runner started; the build is executing`)

RunService.Heartbeat:Connect(function(delta: number)
	elapsed += delta
	local phase = math.sin(elapsed * (math.pi * 2) / config.bobSeconds)
	block.Position = origin + Vector3.new(0, phase * config.bobHeight, 0)
end)
"""


def vector3(x: float, y: float, z: float) -> dict:
    """A Roblox value written so the plugin can rebuild it.

    Tagged rather than a bare list: `[0, 5, 0]` could be a Vector3, a Color3 or
    a size, and the plugin would have to guess.
    """
    return {"type": "Vector3", "value": [x, y, z]}


def color3(r: int, g: int, b: int) -> dict:
    return {"type": "Color3", "value": [r, g, b]}


def smoke_batch(build_id: str | None = None, *, play: bool = True) -> OperationBatch:
    """The batch. `play` false stops before starting a test session, for when
    you want to look at the Explorer before anything moves."""
    build = build_id or f"smoke-{secrets.token_hex(4)}"
    operations: list = []

    def add(operation) -> None:
        operations.append(operation)

    step = iter(range(1000))

    def op(kind, **fields):
        index = next(step)
        return kind(operation_id=f"{build}-{index:03d}", sequence=index, **fields)

    # --- the world ---------------------------------------------------------
    add(op(CreateInstance, path="Workspace/GeneratedTest", className="Folder"))
    add(op(CreateInstance, path="Workspace/GeneratedTest/Floor", className="Part",
           properties={"Anchored": True, "Size": vector3(80, 1, 80),
                       "Position": vector3(0, 0, 0), "Color": color3(70, 75, 80),
                       "Material": "Concrete"}))
    add(op(CreateInstance, path="Workspace/GeneratedTest/RedBlock", className="Part",
           properties={"Anchored": True, "Size": vector3(4, 4, 4),
                       "Position": vector3(0, 6, 0), "Color": color3(200, 60, 60),
                       "Material": "Neon"}))
    # Somewhere to stand, so Play mode puts the character on the floor rather
    # than wherever the place last had a spawn.
    add(op(CreateInstance, path="Workspace/GeneratedTest/Spawn", className="SpawnLocation",
           properties={"Anchored": True, "Size": vector3(6, 1, 6),
                       "Position": vector3(0, 1, 12), "CanCollide": True}))

    # --- shared config -----------------------------------------------------
    add(op(CreateInstance, path="ReplicatedStorage/GeneratedTest", className="Folder"))
    add(op(CreateScript, path="ReplicatedStorage/GeneratedTest/Config",
           scriptType="ModuleScript", source=CONFIG_SOURCE))

    # --- the server script that makes it move ------------------------------
    add(op(CreateScript, path="ServerScriptService/GeneratedTestRunner",
           scriptType="Script", source=RUNNER_SOURCE))
    add(op(OpenScript, path="ServerScriptService/GeneratedTestRunner"))

    if play:
        add(op(StartPlaytest, mode="play"))

    batch = OperationBatch(build_id=build, batch_id=f"{build}-batch", operations=operations)
    return validate_batch(batch)


def expected_checklist() -> list[str]:
    """What a person should see. Written here so the claim and the batch cannot
    drift apart."""
    return [
        "Workspace/GeneratedTest/Floor exists and is a large grey Part",
        "Workspace/GeneratedTest/RedBlock exists and is a red Neon Part",
        "Workspace/GeneratedTest/Spawn exists",
        "ReplicatedStorage/GeneratedTest/Config is a ModuleScript whose source mentions bobHeight",
        "ServerScriptService/GeneratedTestRunner is a Script whose source mentions Heartbeat",
        "GeneratedTestRunner opened in the script editor",
        "Studio entered Play mode",
        "the Output window shows: [GeneratedTest] runner started; the build is executing",
        "RedBlock moves up and down while the test runs",
    ]


__all__ = ["CONFIG_SOURCE", "RUNNER_SOURCE", "expected_checklist", "smoke_batch"]
