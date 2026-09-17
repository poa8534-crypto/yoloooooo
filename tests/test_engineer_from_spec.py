"""GameBuildSpecification -> EngineeringTasks.

The link that was missing: until this, the Engineer was handed tasks a person
wrote by hand. The risk in automating it is that a constraint gets lost on the
way -- the specification says NO DATASTORE, the task does not, and the Engineer
helpfully adds saving to a prototype that was meant to be session-only.
"""

from __future__ import annotations

import pytest

from app.blueprint.compile import compile_spec
from app.blueprint.schemas import (
    Blueprint,
    BlueprintConfig,
    BuildTarget,
    FeatureSuggestion,
    GameSystem,
    Persistence,
    SystemLayer,
)
from app.engineer.from_spec import SpecUnusable, plan_summary, tasks_from

CRITERION = "Spending more resources than the player holds is refused and changes nothing."


def system(name: str, **overrides) -> GameSystem:
    fields = {"id": name.lower(), "name": name, "layer": SystemLayer.SERVER,
              "purpose": "Track what each survivor is carrying.",
              "acceptance_criteria": [CRITERION]}
    return GameSystem(**{**fields, **overrides})


def spec_for(*systems, config: BlueprintConfig | None = None, suggestions=None):
    plan = Blueprint(id="bp", project_id="proj", audit_id="audit-77",
                     title="Zombie Quarantine Lab", user_intent="A research bunker.",
                     systems=list(systems) or [system("ResourceService")],
                     suggestions=suggestions or [],
                     config=config or BlueprintConfig())
    return compile_spec(plan)


def test_a_system_becomes_a_task_bound_to_the_original_idea():
    [task] = tasks_from(spec_for(system("ResourceService")))

    assert task.system == "ResourceService"
    assert task.audit_id == "audit-77", "the task still traces back to the Scout idea"
    assert task.acceptance_criteria == [CRITERION]


def test_tasks_come_out_in_build_order():
    spec = spec_for(system("WaveService", depends_on=["ResourceService"]),
                    system("ResourceService"))

    names = [task.system for task in tasks_from(spec)]

    assert names.index("ResourceService") < names.index("WaveService")


def test_a_dependency_is_named_in_the_notes_as_something_to_require():
    """The difference between a codebase and a pile of modules."""
    spec = spec_for(system("ResourceService"),
                    system("WaveService", depends_on=["ResourceService"]))

    wave = next(task for task in tasks_from(spec) if task.system == "WaveService")

    assert any("ResourceService" in note and "reimplement" in note for note in wave.notes)


def test_every_task_carries_the_no_datastore_constraint():
    """A constraint the Engineer is not shown is a constraint it will break.
    This is the one that silently changes what gets built."""
    spec = spec_for(system("Alpha"), system("Beta"),
                    config=BlueprintConfig(persistence=Persistence.SESSION_ONLY))

    for task in tasks_from(spec):
        assert any("NO DATASTORE" in note for note in task.notes), task.system


def test_saved_progression_carries_the_opposite_instruction():
    spec = spec_for(system("Alpha"),
                    config=BlueprintConfig(persistence=Persistence.SAVED_PROGRESSION))

    notes = " ".join(tasks_from(spec)[0].notes)

    assert "DataStoreService" in notes and "NO DATASTORE" not in notes


def test_every_task_carries_what_was_refused():
    """The Engineer never sees the blueprint. A feature it was not told was
    refused is one it may add helpfully."""
    spec = spec_for(system("Alpha"), suggestions=[
        FeatureSuggestion(id="infection", title="Player Infection",
                          description="Players become infected over time.",
                          reason="Adds tension to being hit.", selected=False)])

    notes = " ".join(tasks_from(spec)[0].notes)

    assert "REFUSED" in notes and "Player Infection" in notes


def test_the_layer_decides_where_the_task_says_to_write():
    spec = spec_for(system("Alpha", layer=SystemLayer.SERVER),
                    system("Beta", layer=SystemLayer.CLIENT),
                    system("Gamma", layer=SystemLayer.SHARED))

    by_name = {task.system: " ".join(task.notes) for task in tasks_from(spec)}

    assert "src/server/Alpha.luau" in by_name["Alpha"]
    assert "src/client/Beta.luau" in by_name["Beta"]
    assert "own siblings" in by_name["Beta"], "the client require rule travels with the task"
    assert "src/shared/Gamma.luau" in by_name["Gamma"]


def test_a_system_the_project_already_has_is_not_rebuilt():
    """What makes a second build of a revised spec a patch rather than a
    regeneration."""
    spec = spec_for(system("ResourceService"), system("WaveService"))

    names = [task.system for task in tasks_from(spec, already_built={"ResourceService"})]

    assert names == ["WaveService"]


def test_a_specification_with_nothing_left_to_build_says_so():
    spec = spec_for(system("ResourceService"))

    with pytest.raises(SpecUnusable, match="already exists"):
        tasks_from(spec, already_built={"ResourceService"})


def test_a_specification_whose_order_misses_a_system_is_refused():
    """Half-building is worse than refusing: the missing system is the one
    nothing would notice until something depends on it."""
    spec = spec_for(system("Alpha"), system("Beta"))
    broken = spec.model_copy(update={"build_order": ["Alpha"]})

    with pytest.raises(SpecUnusable, match="not in its build order"):
        tasks_from(broken)


def test_the_plan_says_what_it_will_and_will_not_build():
    spec = spec_for(system("ResourceService"), system("WaveService"))

    summary = plan_summary(spec, already_built={"ResourceService"})

    assert summary["building"] == ["WaveService"]
    assert summary["skipping_because_they_exist"] == ["ResourceService"]
    assert summary["content_hash"] == spec.content_hash


def test_building_into_an_existing_place_warns_every_task():
    spec = spec_for(system("Alpha"),
                    config=BlueprintConfig(build_target=BuildTarget.CURRENT_PLACE))

    assert any("inspect what is there first" in note for note in tasks_from(spec)[0].notes)


def test_a_task_never_carries_more_notes_than_the_schema_allows():
    """EngineeringTask caps notes at 20; a spec with many constraints must not
    produce a task that fails validation on the way in."""
    spec = spec_for(*[system(f"System{i}", depends_on=[]) for i in range(3)],
                    suggestions=[FeatureSuggestion(
                        id=f"f{i}", title=f"Refused feature {i}",
                        description="something that was considered",
                        reason="a reason it was considered", selected=False)
                        for i in range(20)])

    for task in tasks_from(spec):
        assert len(task.notes) <= 20
