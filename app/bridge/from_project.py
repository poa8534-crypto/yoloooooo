"""Generated Luau files -> Studio operations.

The missing link between the two halves that already work. The Engineer writes
`.luau` files into a git worktree; the plugin applies typed operations to a
DataModel. Nothing turned one into the other, so generated code could be
verified by six checks and still never reach Studio.

The mapping is the Rojo project's, because that is already the agreed answer to
"where does this file live in the tree", and two answers would drift:

    src/shared/X.luau   ->  ReplicatedStorage/Shared/X                 ModuleScript
    src/server/X.luau   ->  ServerScriptService/Server/X               ModuleScript
    src/client/X.luau   ->  StarterPlayer/StarterPlayerScripts/Client/X  ModuleScript

A ModuleScript does nothing on its own. Every system the Engineer writes is a
module, so a place containing them and nothing else runs no code at all -- which
would look exactly like a failed build. So two bootstraps are generated: a
Script that requires each server module and a LocalScript that does the same
for the client modules, each in the specification's build order, calling
`Start()` where a module has one. They are generated here, deterministically,
and never by a model.
"""

from __future__ import annotations

import secrets

from .protocol import (
    DeleteInstance,
    CreateInstance,
    BuildWorld,
    CreateScript,
    OpenScript,
    OperationBatch,
    StartPlaytest,
    validate_batch,
)

ROOTS: dict[str, str] = {
    "src/shared": "ReplicatedStorage/Shared",
    "src/server": "ServerScriptService/Server",
    "src/client": "StarterPlayer/StarterPlayerScripts/Client",
}
# Rojo's own naming: a file called init.server.luau becomes the Script for the
# folder it sits in, rather than a child module.
ENTRY_SUFFIX: dict[str, str] = {
    ".server.luau": "Script",
    ".client.luau": "LocalScript",
}
BOOTSTRAP_NAME = "VentureBootstrap"
# Beside the Client folder rather than inside it. Rojo owns what is inside a
# folder it maps and removes anything it did not put there, and Roblox copies
# StarterPlayerScripts into each player, so the LocalScript and the folder it
# starts arrive in PlayerScripts together.
CLIENT_BOOTSTRAP_NAME = "VentureClientBootstrap"
CLIENT_BOOTSTRAP_PATH = f"StarterPlayer/StarterPlayerScripts/{CLIENT_BOOTSTRAP_NAME}"


class Unmappable(ValueError):
    pass


def studio_path(source_path: str) -> tuple[str, str]:
    """(DataModel path, script class) for a repository path."""
    normalised = source_path.replace("\\", "/").lstrip("./")
    for root, destination in ROOTS.items():
        if not normalised.startswith(root + "/"):
            continue
        remainder = normalised[len(root) + 1:]
        for suffix, script_class in ENTRY_SUFFIX.items():
            if remainder.endswith(suffix):
                name = remainder[: -len(suffix)]
                # Rojo turns `init.server.luau` into the CONTAINING FOLDER
                # itself, as a Script with children. The operation protocol has
                # no way to say that -- a path is either a script or a folder --
                # and creating the folder for its siblings first would make the
                # script collide with it. So it becomes a child called `Main`.
                # A deliberate divergence from Rojo, in the one place the two
                # models of a tree disagree.
                leaf = f"{destination}/Main" if name == "init" else f"{destination}/{name}"
                return leaf, script_class
        if not remainder.endswith(".luau"):
            raise Unmappable(f"{source_path}: only .luau files are built into Studio")
        return f"{destination}/{remainder[:-5]}", "ModuleScript"
    raise Unmappable(f"{source_path}: outside {', '.join(sorted(ROOTS))}")


def entry_scripts(files: dict[str, str]) -> set[str]:
    """The source roots whose files already include something that RUNS.

    A project that carries its own entry point must not be given a second one:
    two scripts starting the same modules start every system twice, and the
    second `Start()` lands on a service that is already running.
    """
    roots: set[str] = set()
    for source_path in files:
        try:
            _target, script_class = studio_path(source_path)
        except Unmappable:
            continue
        if script_class == "ModuleScript":
            continue
        for root in ROOTS:
            if source_path.replace("\\", "/").lstrip("./").startswith(root + "/"):
                roots.add(root)
    return roots


def bootstrap_source(modules: list[str], *, client: bool = False, inside: bool = False) -> str:
    """A script that starts the generated systems: the server's, or the client's.

    Without it a build is a folder of ModuleScripts that nothing requires, which
    at run time is indistinguishable from a build that failed. The client needs
    its own: a Script never runs on a client, so a HUD or a camera written as a
    client module was copied into every player and never started.

    `Start` is called when a module has one and ignored otherwise, so a module
    that is only a library is not forced to pretend it is a service. Each
    require is wrapped: one module that throws must not stop the rest, and the
    error has to reach the Output window rather than vanishing.
    """
    tag = CLIENT_BOOTSTRAP_NAME if client else BOOTSTRAP_NAME
    if inside:
        # Written into the repository, so Rojo puts it INSIDE the folder it
        # starts: the modules are its siblings and the folder is its parent.
        # It must not reach for `game`, which the guard refuses in a generated
        # file and which this does not need.
        tag = "VentureStart"
        header = [
            "-- Generated by scripts/write_bootstrap.py. It requires each module beside",
            "-- it, in build order, and starts the ones that have a Start function.",
            "-- Without it Rojo ships a folder of ModuleScripts that nothing runs.",
            "-- Regenerate it rather than editing it; the build order comes from the",
            "-- approved specification.",
            "",
            "local folder = script.Parent :: Instance",
        ]
    elif client:
        header = [
            "-- Generated by Venture Engineer. It requires each client module in build",
            "-- order and starts the ones that have a Start function. Roblox copies it",
            "-- into every player beside the Client folder, and without it the client",
            "-- modules arrive there and never run.",
            "",
            'local folder = (script.Parent :: Instance):WaitForChild("Client")',
        ]
    else:
        header = [
            "-- Generated by Venture Engineer. It requires each server module in build",
            "-- order and starts the ones that have a Start function. Without this, a",
            "-- place full of ModuleScripts runs no code at all.",
            "",
            'local ServerScriptService = game:GetService("ServerScriptService")',
            "",
            'local folder = ServerScriptService:WaitForChild("Server")',
        ]
    lines = ["--!strict", "", *header, "local started = 0", "", "local ORDER: { string } = {"]
    lines.extend(f'\t"{name}",' for name in modules)
    lines.extend([
        "}",
        "",
        "for _, name in ORDER do",
        "\tlocal module = folder:FindFirstChild(name)",
        "\tif module == nil then",
        f"\t\twarn(`[{tag}] {{name}} is missing`)",
        "\t\tcontinue",
        "\tend",
        "\tlocal ok, loaded = pcall(require, module)",
        "\tif not ok then",
        f"\t\twarn(`[{tag}] {{name}} failed to load: {{loaded}}`)",
        "\t\tcontinue",
        "\tend",
        '\tif type(loaded) == "table" and type((loaded :: any).Start) == "function" then',
        "\t\tlocal running = pcall(function()",
        "\t\t\t(loaded :: any):Start()",
        "\t\tend)",
        "\t\tif running then",
        "\t\t\tstarted += 1",
        "\t\telse",
        f"\t\t\twarn(`[{tag}] {{name}}:Start() errored`)",
        "\t\tend",
        "\tend",
        "end",
        "",
        f"print(`[{tag}] {{#ORDER}} module(s) loaded, {{started}} started`)",
        "",
    ])
    return "\n".join(lines)


def in_build_order(names: list[str], order: list[str] | None) -> list[str]:
    """Module names in the specification's build order.

    A system's `Start` can rely on what an earlier system's `Start` put in
    place -- the world has to be built before anything registers the nodes in
    it -- and the build order is the one ordering that already says what comes
    first. A module the order does not name (a generated Services module, a
    helper) follows, alphabetically, so the result is still stable.
    """
    position = {name: index for index, name in enumerate(order or [])}
    return sorted(names, key=lambda name: (position.get(name, len(position)), name))


def operations_for(files: dict[str, str], *, build_id: str | None = None,
                   play: bool = True, bootstrap: bool = True,
                   world_builder: str | None = None,
                   order: list[str] | None = None) -> OperationBatch:
    """One batch that puts a generated project into Studio and runs it.

    Folders are not emitted: the plugin creates missing folders on the way to a
    path, so an explicit operation per folder would be a second way to do one
    thing. Scripts come in a stable order so a failure is reproducible.

    `order` is the specification's build order. The bootstraps start modules in
    it; without one they fall back to alphabetical order.
    """
    build = build_id or f"build-{secrets.token_hex(4)}"
    counter = iter(range(10_000))

    def op(kind, **fields):
        index = next(counter)
        return kind(operation_id=f"{build}-{index:03d}", sequence=index, **fields)

    operations: list = []
    server_modules: list[str] = []
    client_modules: list[str] = []
    for source_path in sorted(files):
        target, script_class = studio_path(source_path)
        operations.append(op(CreateScript, path=target, scriptType=script_class,
                             source=files[source_path]))
        if script_class != "ModuleScript":
            continue
        if source_path.startswith("src/server/"):
            server_modules.append(target.rsplit("/", 1)[-1])
        elif source_path.startswith("src/client/"):
            client_modules.append(target.rsplit("/", 1)[-1])

    # A project that brings its own entry point keeps it; see entry_scripts.
    carried = entry_scripts(files)
    #[[
    #   A bootstrap this bridge wrote in an earlier sync is still in the place,
    #   and it starts everything a second time.
    #
    #   Measured: a sync of island-haven reported "[VentureStart] 27 started"
    #   and "[VentureBootstrap] 25 started" in the same run, plus eleven
    #   warnings about systems that had since been retired. Every service was
    #   running twice, each with its own state, against one DataStore.
    #
    #   Not creating it any more is not enough; the old one has to go.
    #]]
    for root, stale in (("src/server", f"ServerScriptService/{BOOTSTRAP_NAME}"),
                        ("src/client", CLIENT_BOOTSTRAP_PATH)):
        if root in carried:
            operations.append(op(DeleteInstance, path=stale))

    if bootstrap and server_modules and "src/server" not in carried:
        operations.append(op(CreateScript, path=f"ServerScriptService/{BOOTSTRAP_NAME}",
                             scriptType="Script",
                             source=bootstrap_source(in_build_order(server_modules, order))))
        operations.append(op(OpenScript, path=f"ServerScriptService/{BOOTSTRAP_NAME}"))

    if bootstrap and client_modules and "src/client" not in carried:
        operations.append(op(CreateScript, path=CLIENT_BOOTSTRAP_PATH, scriptType="LocalScript",
                             source=bootstrap_source(in_build_order(client_modules, order),
                                                     client=True)))

    if not operations:
        raise Unmappable("there are no .luau files to build")

    # Before any playtest: the place should be worth looking at in edit mode,
    # not only once somebody presses Play.
    if world_builder:
        location, _class = studio_path(world_builder)
        operations.append(op(BuildWorld, path=location))

    if play:
        operations.append(op(StartPlaytest, mode="play"))

    return validate_batch(OperationBatch(build_id=build, batch_id=f"{build}-batch",
                                         operations=operations))


def world_operations(build_id: str, *, floor: bool = True) -> list:
    """Somewhere to stand.

    A generated prototype is server logic; without a floor and a spawn, Play
    mode drops the character into empty space and the build looks broken when
    it is not. Emitted only when the place has nothing of its own -- the caller
    decides, because overwriting someone's world is not recoverable.
    """
    counter = iter(range(9000, 9100))

    def op(kind, **fields):
        index = next(counter)
        return kind(operation_id=f"{build_id}-{index}", sequence=index, **fields)

    if not floor:
        return []
    return [
        op(CreateInstance, path="Workspace/VentureWorld", className="Folder"),
        op(CreateInstance, path="Workspace/VentureWorld/Floor", className="Part",
           properties={"Anchored": True, "Size": {"type": "Vector3", "value": [120, 1, 120]},
                       "Position": {"type": "Vector3", "value": [0, 0, 0]},
                       "Color": {"type": "Color3", "value": [70, 75, 80]}, "Material": "Concrete"}),
        op(CreateInstance, path="Workspace/VentureWorld/Spawn", className="SpawnLocation",
           properties={"Anchored": True, "Size": {"type": "Vector3", "value": [8, 1, 8]},
                       "Position": {"type": "Vector3", "value": [0, 1, 0]}}),
    ]


def read_project(root, roots: tuple[str, ...] = tuple(ROOTS)) -> dict[str, str]:
    """Every buildable file under a checkout, keyed by repository path.

    Read as bytes and decoded: a BOM or CRLF reaching a Studio script is the
    same class of bug the Engineer's own workspace guards against.
    """
    from pathlib import Path

    base = Path(root)
    found: dict[str, str] = {}
    for relative in roots:
        directory = base / relative
        if not directory.is_dir():
            continue
        for file in sorted(directory.rglob("*.luau")):
            text = file.read_bytes().decode("utf-8", "replace")
            text = text.removeprefix("﻿").replace("\r\n", "\n")
            found[file.relative_to(base).as_posix()] = text
    return found
