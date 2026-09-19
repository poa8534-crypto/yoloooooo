"""Tests for automatic reconciliation of missing blueprint systems."""

from __future__ import annotations

import pytest

from app.blueprint.api import router
from app.blueprint.compile import compile_spec
from app.blueprint.reconcile import (
    find_missing_systems,
    infer_layer,
    reconcile_missing_systems,
    synthesize_system,
)
from app.blueprint.schemas import (
    Blueprint,
    BlueprintConfig,
    Complexity,
    FeatureSuggestion,
    GameSystem,
    SystemLayer,
)
from app.engineer.doctrine import GameplayPath, PathNode, PlayerJourney
from app.blueprint.store import BlueprintStore

CRITERION = "Spending more resources than the player holds is refused and changes nothing."


@pytest.fixture
def store(session_factory):
    return BlueprintStore(session_factory)


def sample_blueprint(**overrides) -> Blueprint:
    fields = {
        "id": "bp-test",
        "project_id": "proj-1",
        "audit_id": "audit-1",
        "title": "Test Game",
        "user_intent": "A mining adventure",
        "config": BlueprintConfig(),
        "player_journey": PlayerJourney(
            first_action="Mine the rock",
            core_loop=["Mine ore", "Sell at station", "Upgrade pickaxe"],
            entry_state="Spawns at camp",
        ),
        "gameplay_path": GameplayPath(
            nodes=[
                PathNode(id="node_1", label="Spawn into starter claim", systems=["SpawnService", "PlotManagementService"]),
                PathNode(id="node_2", label="Hatch starter egg", systems=["EggHatchService", "BrainrotEntityService"]),
                PathNode(id="node_3", label="Mine ore", systems=["MiningService"]),
            ]
        ),
        "systems": [
            GameSystem(
                id="sys_spawn",
                name="SpawnService",
                layer=SystemLayer.SERVER,
                purpose="Spawns the player character.",
                acceptance_criteria=[CRITERION],
            ),
            GameSystem(
                id="sys_egg",
                name="EggHatchService",
                layer=SystemLayer.SERVER,
                purpose="Hatches pet eggs.",
                acceptance_criteria=[CRITERION],
                depends_on=["InventoryService"],  # Dependency not yet in systems
            ),
            GameSystem(
                id="sys_mine",
                name="MiningService",
                layer=SystemLayer.SERVER,
                purpose="Mines rock deposits.",
                acceptance_criteria=[CRITERION],
                builds_world=True,
            ),
        ],
    }
    fields.update(overrides)
    return Blueprint(**fields)


def test_find_missing_systems_finds_path_nodes_and_dependencies():
    bp = sample_blueprint()
    missing = find_missing_systems(bp)

    names = {m.name for m in missing}
    assert "PlotManagementService" in names
    assert "BrainrotEntityService" in names
    assert "InventoryService" in names


def test_infer_layer_classifies_correctly():
    assert infer_layer("InteractionController") is SystemLayer.CLIENT
    assert infer_layer("StatusHUD") is SystemLayer.CLIENT
    assert infer_layer("InventoryView") is SystemLayer.CLIENT
    assert infer_layer("MathUtil") is SystemLayer.SHARED
    assert infer_layer("GameConfig") is SystemLayer.SHARED
    assert infer_layer("PlotManagementService") is SystemLayer.SERVER
    assert infer_layer("BrainrotEntityService") is SystemLayer.SERVER


def test_reconcile_missing_systems_makes_blueprint_compilable():
    bp = sample_blueprint()
    # Before reconciliation, compile_spec must fail because systems are missing
    with pytest.raises(Exception, match="(is not in the plan|are not in the build)"):
        compile_spec(bp)

    updated, added = reconcile_missing_systems(bp)
    assert len(added) == 3
    added_names = {s.name for s in added}
    assert added_names == {"PlotManagementService", "BrainrotEntityService", "InventoryService"}

    # After reconciliation, compile_spec must succeed cleanly
    spec = compile_spec(updated)
    spec_systems = {s.name for s in spec.systems}
    assert {"PlotManagementService", "BrainrotEntityService", "InventoryService"} <= spec_systems
    assert len(spec.build_order) == len(spec.systems)


def test_reconcile_endpoint_adds_missing_systems_via_api(store, monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import app.blueprint.api as api_mod

    # Patch store factory in api_mod
    monkeypatch.setattr(api_mod, "_store", lambda: (store, store.factory))

    created = store.create(audit_id="audit-1", title="Test Game")
    bp = sample_blueprint(id=created.id)
    store.save(bp)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Initial view should report missing_systems
    resp = client.get(f"/api/blueprints/{bp.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["missing_systems"]) == 3

    # Call reconcile endpoint
    post_resp = client.post(f"/api/blueprints/{bp.id}/systems/reconcile")
    assert post_resp.status_code == 200
    post_data = post_resp.json()
    assert len(post_data["missing_systems"]) == 0
    system_names = {s["name"] for s in post_data["blueprint"]["systems"]}
    assert "PlotManagementService" in system_names
    assert "BrainrotEntityService" in system_names
    assert "InventoryService" in system_names


def test_a_system_dropped_with_its_feature_counts_as_missing():
    """The check and the compile have to be looking at the same plan.

    A system that came from a rejected feature is still on the blueprint, but
    `active_systems()` drops it, and that is what the specification is built
    from. Reading the whole list here made the check answer "nothing is
    missing" about a system the compile then refused for being missing, with
    no way for the person to see why or fix it.
    """
    rejected = FeatureSuggestion(
        id="feat_shop", title="A trading post",
        description="Somewhere to sell what the player digs up.",
        reason="Gives the reward somewhere to go.",
        selected=False,
    )
    blueprint = sample_blueprint(
        suggestions=[rejected],
        gameplay_path=GameplayPath(nodes=[
            PathNode(id="node_1", label="Mine ore", systems=["MiningService"]),
            PathNode(id="node_2", label="Sell the ore", systems=["ShopService"]),
        ]),
        systems=[
            GameSystem(
                id="sys_mining", name="MiningService", layer=SystemLayer.SERVER,
                purpose="Lets the player mine ore.",
                acceptance_criteria=[CRITERION],
            ),
            GameSystem(
                id="sys_shop", name="ShopService", layer=SystemLayer.SERVER,
                purpose="Sells ore.", acceptance_criteria=[CRITERION],
                from_feature="feat_shop",
            ),
        ],
    )

    assert [system.name for system in blueprint.active_systems()] == ["MiningService"]

    missing = [entry.name for entry in find_missing_systems(blueprint)]

    assert "ShopService" in missing, (
        "the path needs a system the specification will not contain")


def test_a_dependency_on_a_system_dropped_with_its_feature_counts_as_missing():
    rejected = FeatureSuggestion(
        id="feat_shop", title="A trading post",
        description="Somewhere to sell what the player digs up.",
        reason="Gives the reward somewhere to go.",
        selected=False,
    )
    blueprint = sample_blueprint(
        suggestions=[rejected],
        gameplay_path=GameplayPath(nodes=[
            PathNode(id="node_1", label="Mine ore", systems=["MiningService"]),
        ]),
        systems=[
            GameSystem(
                id="sys_mining", name="MiningService", layer=SystemLayer.SERVER,
                purpose="Lets the player mine ore.",
                acceptance_criteria=[CRITERION], depends_on=["ShopService"],
            ),
            GameSystem(
                id="sys_shop", name="ShopService", layer=SystemLayer.SERVER,
                purpose="Sells ore.", acceptance_criteria=[CRITERION],
                from_feature="feat_shop",
            ),
        ],
    )

    missing = [entry.name for entry in find_missing_systems(blueprint)]

    assert "ShopService" in missing


def test_a_system_dropped_with_its_feature_is_not_synthesized_twice():
    """Reconciling twice must not add a second copy under the same name."""
    rejected = FeatureSuggestion(
        id="feat_shop", title="A trading post",
        description="Somewhere to sell what the player digs up.",
        reason="Gives the reward somewhere to go.",
        selected=False,
    )
    blueprint = sample_blueprint(
        suggestions=[rejected],
        gameplay_path=GameplayPath(nodes=[
            PathNode(id="node_1", label="Sell the ore", systems=["ShopService"]),
        ]),
        systems=[
            GameSystem(
                id="sys_shop", name="ShopService", layer=SystemLayer.SERVER,
                purpose="Sells ore.", acceptance_criteria=[CRITERION],
                from_feature="feat_shop",
            ),
        ],
    )

    once, added = reconcile_missing_systems(blueprint)
    twice, added_again = reconcile_missing_systems(once)

    assert [system.name for system in added] == ["ShopService"]
    assert added_again == [], "the second pass had nothing left to add"
    assert [s.name for s in twice.active_systems()].count("ShopService") == 1
