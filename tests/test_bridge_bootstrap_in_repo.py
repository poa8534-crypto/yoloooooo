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
from app.bridge.protocol import OperationKind


def created(batch) -> list[str]:
    """The paths a batch CREATES. A delete carries a path too, and counting
    both made a batch that removes a stale bootstrap look like one that
    writes a second copy of it."""
    return [op.path for op in batch.operations
            if op.operation in (OperationKind.CREATE_SCRIPT, OperationKind.CREATE_INSTANCE)]


def deleted(batch) -> list[str]:
    return [op.path for op in batch.operations if op.operation is OperationKind.DELETE_INSTANCE]


def test_an_entry_script_in_the_repo_is_recognised_per_side():
    files = {"src/server/Main.server.luau": "x", "src/server/A.luau": "y",
             "src/client/B.luau": "z"}
    assert entry_scripts(files) == {"src/server"}


def test_the_bridge_stands_down_where_the_project_brings_its_own():
    files = {"src/server/Main.server.luau": "--!strict\nreturn nil\n",
             "src/server/A.luau": "--!strict\nreturn {}\n",
             "src/client/C.luau": "--!strict\nreturn {}\n"}
    paths = created(operations_for(files))
    assert not any("VentureBootstrap" in str(path) for path in paths)
    # The client brought none, so it still gets one: a HUD that never starts
    # is indistinguishable from a build that failed.
    assert any("VentureClientBootstrap" in str(path) for path in paths)


def test_without_an_entry_script_the_bridge_still_provides_one():
    files = {"src/server/A.luau": "--!strict\nreturn {}\n"}
    paths = created(operations_for(files))
    assert any("VentureBootstrap" in str(path) for path in paths)


def test_the_in_repo_form_reaches_its_folder_as_its_own_parent():
    """It sits INSIDE the folder it starts, and must not reach for `game`:
    the guard refuses that global in a generated file, and this does not
    need it."""
    source = bootstrap_source(["A", "B"], inside=True)
    assert "local folder = script.Parent :: Instance" in source
    assert "game:GetService" not in source
    assert source.index('"A"') < source.index('"B"')


def test_a_behaviour_spec_is_never_offered_to_studio():
    """Specs are landed with the work and run under Lune here; Roblox has no
    place for them. Offering one failed a whole build after three systems had
    already landed: "tests/AnalyticsService.spec.luau: outside src/client,
    src/server, src/shared"."""
    from app.blueprint.builds import buildable

    files = {"src/server/A.luau": "a", "tests/A.spec.luau": "spec",
             "src/shared/B.luau": "b"}
    assert buildable(files) == {"src/server/A.luau": "a", "src/shared/B.luau": "b"}


def test_the_foundation_starts_before_the_systems_that_use_it():
    """Build order answers "what can be written next"; start order answers
    "what must already be running". Taking the first as an answer to the
    second put RemoteRegistry twenty-ninth, so twenty-eight services had each
    made their own remotes before the system whose job is to make them first
    ever ran."""
    from types import SimpleNamespace

    import scripts.write_bootstrap as bootstrap

    def system(name, priority, flow):
        return SimpleNamespace(name=name, priority_class=priority, player_flow_index=flow)

    built = ["IslandService", "PlayerDataService", "NetworkManifest", "RemoteRegistry", "ShopView"]
    systems = [system("IslandService", "P1", 10), system("PlayerDataService", "P1", 11),
               system("NetworkManifest", "P0", 2), system("RemoteRegistry", "P0", 1),
               system("ShopView", "P4", 41)]

    order = bootstrap.start_order(built, systems)
    assert order[0] == "RemoteRegistry", f"the first system started is {order[0]}"
    assert order[1] == "NetworkManifest", "the foundation should start in the plan's flow order"
    assert order.index("RemoteRegistry") < order.index("IslandService"), (
        "RemoteRegistry starts after a service that creates its own remotes")
    assert sorted(order) == sorted(built), "hoisting changed which systems start"
    assert len(order) == len(set(order)), "a system appears twice in the start order"


def test_a_stale_bootstrap_is_removed_when_the_project_brings_its_own():
    """Not creating it any more is not enough: the one an earlier sync left
    behind is still in the place, starting everything a second time.

    Measured on island-haven: "[VentureStart] 27 started" and
    "[VentureBootstrap] 25 started" in the same run, with eleven warnings
    about systems that had since been retired. Every service ran twice,
    each copy with its own state, against one DataStore.
    """
    files = {"src/server/Main.server.luau": "--!strict\nreturn nil\n",
             "src/server/A.luau": "--!strict\nreturn {}\n",
             "src/client/Main.client.luau": "--!strict\nreturn nil\n",
             "src/client/B.luau": "--!strict\nreturn {}\n"}
    gone = deleted(operations_for(files))
    assert any("VentureBootstrap" in path for path in gone), (
        "the stale server bootstrap is left in the place, starting every system twice")
    assert any("VentureClientBootstrap" in path for path in gone), (
        "the stale client bootstrap is left in the place")


def test_nothing_is_deleted_when_the_bridge_provides_the_bootstrap():
    """A project without its own entry point still needs the generated one,
    and deleting what this same batch creates would leave a dead place."""
    files = {"src/server/A.luau": "--!strict\nreturn {}\n"}
    gone = deleted(operations_for(files))
    assert not gone, f"a batch that provides the bootstrap also deleted {gone}"
