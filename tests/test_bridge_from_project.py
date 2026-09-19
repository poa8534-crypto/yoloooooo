"""Generated files -> Studio operations.

The link that was missing: six checks could pass and the code still never
reached Studio. The failures worth testing are the quiet ones -- a file landing
in the wrong service, a script colliding with the folder holding its siblings,
or a place full of ModuleScripts that nothing requires, which at run time looks
exactly like a build that failed.
"""

from __future__ import annotations

import pytest

from app.bridge.from_project import (
    BOOTSTRAP_NAME,
    CLIENT_BOOTSTRAP_PATH,
    Unmappable,
    bootstrap_source,
    operations_for,
    read_project,
    studio_path,
    world_operations,
)
from app.bridge.protocol import OperationKind, validate_batch

MODULE = "--!strict\nreturn {}\n"


# ---- where a file lands ----------------------------------------------------

@pytest.mark.parametrize("source, target, script_class", [
    ("src/shared/DamageMath.luau", "ReplicatedStorage/Shared/DamageMath", "ModuleScript"),
    ("src/server/WaveService.luau", "ServerScriptService/Server/WaveService", "ModuleScript"),
    ("src/client/HudController.luau",
     "StarterPlayer/StarterPlayerScripts/Client/HudController", "ModuleScript"),
])
def test_each_source_root_lands_in_its_service(source, target, script_class):
    assert studio_path(source) == (target, script_class)


def test_an_init_script_becomes_a_child_rather_than_the_folder():
    """Rojo makes `init.server.luau` the containing folder itself, as a Script
    with children. The protocol cannot say that -- a path is a script or a
    folder -- and the folder gets created for its siblings first, so the script
    would collide with it. It becomes `Main` instead."""
    assert studio_path("src/server/init.server.luau") == ("ServerScriptService/Server/Main", "Script")
    assert studio_path("src/client/init.client.luau") == (
        "StarterPlayer/StarterPlayerScripts/Client/Main", "LocalScript")


def test_a_file_outside_the_source_roots_is_refused():
    with pytest.raises(Unmappable, match="outside"):
        studio_path("docs/README.luau")


def test_a_file_that_is_not_luau_is_refused():
    with pytest.raises(Unmappable, match="only .luau"):
        studio_path("src/shared/notes.txt")


# ---- the batch -------------------------------------------------------------

def test_every_file_becomes_a_script_operation():
    batch = operations_for({"src/shared/A.luau": MODULE, "src/server/B.luau": MODULE},
                           build_id="b1", play=False)

    paths = [op.path for op in batch.operations if op.operation is OperationKind.CREATE_SCRIPT]
    assert "ReplicatedStorage/Shared/A" in paths
    assert "ServerScriptService/Server/B" in paths


def test_the_batch_validates_against_the_protocol():
    """Belt and braces: the compiler could emit a path the plugin would refuse,
    and the place to find that out is here."""
    batch = operations_for({"src/server/B.luau": MODULE}, build_id="b1")
    assert validate_batch(batch) is batch


def test_no_folder_operations_are_emitted():
    """The plugin creates missing folders on the way to a path, so an operation
    per folder would be a second way to do one thing."""
    batch = operations_for({"src/shared/Deep/Nested.luau": MODULE}, build_id="b1", play=False)

    assert all(op.operation is not OperationKind.CREATE_INSTANCE for op in batch.operations)


def test_the_batch_ends_by_starting_play_mode():
    batch = operations_for({"src/server/B.luau": MODULE}, build_id="b1")
    assert batch.operations[-1].operation is OperationKind.START_PLAYTEST


def test_a_build_can_be_sent_without_running_it():
    batch = operations_for({"src/server/B.luau": MODULE}, build_id="b1", play=False)
    assert all(op.operation is not OperationKind.START_PLAYTEST for op in batch.operations)


def test_a_project_with_nothing_buildable_is_refused():
    with pytest.raises(Unmappable, match="no .luau files"):
        operations_for({}, build_id="b1")


# ---- the bootstrap ---------------------------------------------------------

def test_server_modules_get_a_script_that_actually_starts_them():
    """A ModuleScript does nothing on its own. A place full of them runs no code
    at all, which at run time is indistinguishable from a failed build."""
    batch = operations_for({"src/server/WaveService.luau": MODULE}, build_id="b1", play=False)

    bootstrap = [op for op in batch.operations
                 if getattr(op, "path", "").endswith(BOOTSTRAP_NAME)]
    assert bootstrap, "nothing would require the generated modules"
    created = next(op for op in bootstrap if op.operation is OperationKind.CREATE_SCRIPT)
    assert created.script_type == "Script"
    assert "WaveService" in created.source


def test_the_bootstrap_is_opened_so_the_person_sees_code():
    batch = operations_for({"src/server/B.luau": MODULE}, build_id="b1", play=False)
    assert any(op.operation is OperationKind.OPEN_SCRIPT for op in batch.operations)


def test_a_project_with_no_server_modules_gets_no_bootstrap():
    batch = operations_for({"src/shared/A.luau": MODULE}, build_id="b1", play=False)
    assert not any(getattr(op, "path", "").endswith(BOOTSTRAP_NAME) for op in batch.operations)


def test_the_bootstrap_survives_one_module_throwing():
    """One module that errors must not stop the rest, and the error has to
    reach the Output window rather than vanishing."""
    source = bootstrap_source(["Alpha", "Beta"])

    assert "pcall" in source
    assert "warn(" in source
    assert source.count("continue") >= 2


def test_the_bootstrap_only_starts_what_has_a_start():
    """A module that is only a library is not forced to pretend it is a service."""
    source = bootstrap_source(["Alpha"])
    assert '(loaded :: any).Start) == "function"' in source


def test_the_bootstrap_says_what_it_did():
    assert "print(" in bootstrap_source(["Alpha"])


def test_the_bootstrap_is_generated_in_build_order():
    source = bootstrap_source(["First", "Second", "Third"])
    assert source.index('"First"') < source.index('"Second"') < source.index('"Third"')


def _script_at(batch, path: str):
    return next(op for op in batch.operations
                if op.operation is OperationKind.CREATE_SCRIPT and op.path == path)


def test_client_modules_get_a_local_script_that_actually_starts_them():
    """A Script never runs on a client. The HUD, the camera and every other
    client module were copied into each player and never started, because the
    only bootstrap was a server Script."""
    batch = operations_for({"src/client/HudController.luau": MODULE}, build_id="b1", play=False)

    created = _script_at(batch, CLIENT_BOOTSTRAP_PATH)
    assert created.script_type == "LocalScript"
    assert '"HudController"' in created.source
    # Beside the Client folder rather than inside it, where Rojo would remove
    # it; both are copied into the player, so it finds the folder by its side.
    assert CLIENT_BOOTSTRAP_PATH == "StarterPlayer/StarterPlayerScripts/VentureClientBootstrap"
    assert 'script.Parent :: Instance):WaitForChild("Client")' in created.source


def test_a_project_with_no_client_modules_gets_no_client_bootstrap():
    batch = operations_for({"src/server/A.luau": MODULE}, build_id="b1", play=False)
    assert not any(getattr(op, "path", "") == CLIENT_BOOTSTRAP_PATH for op in batch.operations)


def test_a_client_bootstrap_names_itself_in_what_it_reports():
    """Both bootstraps print to the same Output window. A warning that does
    not say which side it came from sends the reader to the wrong half."""
    source = bootstrap_source(["HudController"], client=True)
    assert "[VentureClientBootstrap]" in source
    assert "[VentureBootstrap]" not in source


def test_the_bootstraps_start_modules_in_the_specifications_build_order():
    """The world has to be built before anything registers the nodes in it.
    The bootstrap listed modules in the order of their file names, so a system
    that sorts first could start before the system it relies on."""
    files = {f"src/{side}/{name}.luau": MODULE
             for side in ("server", "client") for name in ("Alpha", "Mid", "Zeta")}

    batch = operations_for(files, build_id="b1", play=False, order=["Zeta", "Alpha"])

    for path in (f"ServerScriptService/{BOOTSTRAP_NAME}", CLIENT_BOOTSTRAP_PATH):
        source = _script_at(batch, path).source
        # What the order names, in its order; what it does not name, after.
        assert source.index('"Zeta"') < source.index('"Alpha"') < source.index('"Mid"'), path


def test_without_a_build_order_the_bootstrap_is_still_stable():
    batch = operations_for({"src/server/Zeta.luau": MODULE, "src/server/Alpha.luau": MODULE},
                           build_id="b1", play=False)
    source = _script_at(batch, f"ServerScriptService/{BOOTSTRAP_NAME}").source
    assert source.index('"Alpha"') < source.index('"Zeta"')


def test_a_build_sends_studio_its_specifications_order_and_world():
    """A build and a hand-built project's sync share one function, so what
    Studio is sent cannot depend on which of them sent it."""
    from types import SimpleNamespace

    from app.blueprint.builds import studio_batch

    spec = SimpleNamespace(
        build_order=["WorldService", "AlphaService"],
        systems=[SimpleNamespace(path="src/server/WorldService.luau", builds_world=True),
                 SimpleNamespace(path="src/server/AlphaService.luau", builds_world=False)])
    project = {"src/server/AlphaService.luau": MODULE, "src/server/WorldService.luau": MODULE}

    batch = studio_batch(spec, project, build_id="b1", play=True)

    source = _script_at(batch, f"ServerScriptService/{BOOTSTRAP_NAME}").source
    assert source.index('"WorldService"') < source.index('"AlphaService"')
    world = next(op for op in batch.operations if op.operation is OperationKind.BUILD_WORLD)
    assert world.path == "ServerScriptService/Server/WorldService"
    assert batch.operations[-1].operation is OperationKind.START_PLAYTEST


# ---- somewhere to stand ----------------------------------------------------

def test_a_world_is_offered_but_never_assumed():
    """Overwriting someone's world is not recoverable, so the caller decides."""
    assert world_operations("b1", floor=False) == []
    paths = [op.path for op in world_operations("b1")]
    assert "Workspace/VentureWorld/Floor" in paths
    assert "Workspace/VentureWorld/Spawn" in paths


# ---- reading a checkout ----------------------------------------------------

def test_reading_a_project_keeps_repository_paths(tmp_path):
    (tmp_path / "src" / "server").mkdir(parents=True)
    (tmp_path / "src" / "server" / "WaveService.luau").write_bytes(MODULE.encode("utf-8"))
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ignored.luau").write_bytes(b"-- not a source root\n")

    found = read_project(tmp_path)

    assert list(found) == ["src/server/WaveService.luau"]


def test_a_bom_or_crlf_never_reaches_a_studio_script(tmp_path):
    """The same class of bug the Engineer's own workspace guards against: a BOM
    has already broken a --!strict directive in this project."""
    (tmp_path / "src" / "shared").mkdir(parents=True)
    (tmp_path / "src" / "shared" / "A.luau").write_bytes(
        "﻿--!strict\r\nreturn {}\r\n".encode("utf-8"))

    source = read_project(tmp_path)["src/shared/A.luau"]

    assert source.startswith("--!strict")
    assert "\r" not in source


# ---- the world, in edit mode ------------------------------------------------

def test_the_world_builder_is_run_after_the_files_are_written():
    """A finished build opened in Studio as an empty baseplate: the pier and
    the hut existed as code nobody had called, because everything a generated
    system does happens at run time. The build now runs the one system the
    specification marked as building the place."""
    from app.bridge.from_project import operations_for

    batch = operations_for({"src/server/PierService.luau": "--!strict\nreturn {}\n"},
                           build_id="b1", play=False,
                           world_builder="src/server/PierService.luau")

    kinds = [operation.operation.value for operation in batch.operations]
    assert "build_world" in kinds
    # After the scripts: running a builder before it has been written would
    # call whatever the place held before this build.
    assert kinds.index("build_world") > kinds.index("create_script")
    world = next(o for o in batch.operations if o.operation.value == "build_world")
    assert world.path == "ServerScriptService/Server/PierService"
    assert world.method == "Start"


def test_a_build_with_no_world_builder_does_not_run_anything():
    """Not every specification marks one, and guessing which module to run in
    the person's open place is not a guess worth making."""
    from app.bridge.from_project import operations_for

    batch = operations_for({"src/server/A.luau": "--!strict\nreturn {}\n"},
                           build_id="b1", play=False)

    assert "build_world" not in [o.operation.value for o in batch.operations]


def test_the_world_is_built_before_a_playtest_starts():
    """So the place is worth looking at in edit mode, and the playtest starts
    from the world rather than racing it."""
    from app.bridge.from_project import operations_for

    batch = operations_for({"src/server/PierService.luau": "--!strict\nreturn {}\n"},
                           build_id="b1", play=True,
                           world_builder="src/server/PierService.luau")

    kinds = [operation.operation.value for operation in batch.operations]
    assert kinds.index("build_world") < kinds.index("start_playtest")


def test_the_method_name_cannot_be_arbitrary_text():
    """It is used to index a table in Studio, so it is a name or it is refused."""
    import pytest as _pytest
    from pydantic import ValidationError

    from app.bridge.protocol import BuildWorld

    with _pytest.raises(ValidationError):
        BuildWorld(operation_id="o1", sequence=0, path="ServerScriptService/Server/A",
                   method="Start(); os.exit()")
