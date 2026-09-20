"""The entry point can live in the repository, and then only one exists.

A repository of ModuleScripts starts nothing: Rojo ships them and no script
requires them. Until the entry points were generated into the repository the
only thing that started the game was the bootstrap the bridge writes at sync
time, so the same commit behaved differently depending on which path filled
Studio -- an audit read the repository and concluded the game could not run,
while Studio's log from a bridge sync said 36 modules started.

Both bootstraps existing at once would be worse than neither: every system
would start twice, the second Start() landing on a service already running.
"""

from __future__ import annotations

from app.bridge.from_project import bootstrap_source, entry_scripts, operations_for


def test_an_entry_script_in_the_repo_is_recognised_per_side():
    files = {"src/server/Main.server.luau": "x", "src/server/A.luau": "y",
             "src/client/B.luau": "z"}
    assert entry_scripts(files) == {"src/server"}


def test_the_bridge_stands_down_where_the_project_brings_its_own():
    files = {"src/server/Main.server.luau": "--!strict\nreturn nil\n",
             "src/server/A.luau": "--!strict\nreturn {}\n",
             "src/client/C.luau": "--!strict\nreturn {}\n"}
    paths = [getattr(op, "path", "") for op in operations_for(files).operations]
    assert not any("VentureBootstrap" in str(path) for path in paths)
    # The client brought none, so it still gets one: a HUD that never starts
    # is indistinguishable from a build that failed.
    assert any("VentureClientBootstrap" in str(path) for path in paths)


def test_without_an_entry_script_the_bridge_still_provides_one():
    files = {"src/server/A.luau": "--!strict\nreturn {}\n"}
    paths = [getattr(op, "path", "") for op in operations_for(files).operations]
    assert any("VentureBootstrap" in str(path) for path in paths)


def test_the_in_repo_form_reaches_its_folder_as_its_own_parent():
    """It sits INSIDE the folder it starts, and must not reach for `game`:
    the guard refuses that global in a generated file, and this does not
    need it."""
    source = bootstrap_source(["A", "B"], inside=True)
    assert "local folder = script.Parent :: Instance" in source
    assert "game:GetService" not in source
    assert source.index('"A"') < source.index('"B"')
