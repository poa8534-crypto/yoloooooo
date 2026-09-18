"""What a dependency already in the project actually exports.

Two systems were accepted by six checks and could not be landed:
AirlockService called InfectedService.RedirectAggro, BaseConstructionService
called ResearchService.Has, and neither function was ever written. Each was
checked in its own worktree, where the dependency was not there to disagree.

The names were not wild -- InfectedService has SetPheromoneAggro, and
ResearchService has IsUnlocked and HasEquipment. The Engineer was guessing
plausibly because nothing told it. So these tests are about the note that tells
it, and mostly about what the note must not claim.
"""

from __future__ import annotations

from app.engineer.interfaces import exports_of, interface_note, types_of

SOURCE = """--!strict

local ResearchService = {}

type Internal = { count: number }
export type ResearchNode = { id: string, cost: number }

local function helper(value: number): number
    return value
end

function ResearchService:GetNode(nodeId: string): ResearchNode?
    return nil
end

function ResearchService.new(): ResearchService
    return setmetatable({}, ResearchService)
end

function Other:NotMine(): ()
end

return ResearchService
"""


def test_the_exported_signatures_are_read_from_the_source(tmp_path):
    assert exports_of(SOURCE, "ResearchService") == [
        "ResearchService:GetNode(nodeId: string): ResearchNode?",
        "ResearchService.new(): ResearchService",
    ]


def test_another_system_in_the_same_file_is_not_claimed(tmp_path):
    """A note that says a dependency has a function it does not have is worse
    than no note: it invites exactly the call that cannot compile."""
    assert not any("NotMine" in line for line in exports_of(SOURCE, "ResearchService"))


def test_a_local_function_is_not_an_export():
    assert not any("helper" in line for line in exports_of(SOURCE, "ResearchService"))


def test_exported_types_are_listed_so_a_caller_can_annotate():
    assert types_of(SOURCE) == ["Internal", "ResearchNode"]


def test_the_note_names_the_functions_and_forbids_inventing_others(tmp_path):
    (tmp_path / "src" / "server").mkdir(parents=True)
    (tmp_path / "src" / "server" / "ResearchService.luau").write_text(SOURCE, encoding="utf-8")

    note = interface_note(tmp_path, ["ResearchService"],
                          {"ResearchService": "src/server/ResearchService.luau"})

    assert "GetNode" in note
    assert "do not assume a function exists" in note


def test_a_dependency_not_in_the_project_yet_is_left_out(tmp_path):
    """It is being built in this same run, so it has no signatures to quote.
    An empty list would read as "it exports nothing", which is a lie about a
    system that is about to export plenty."""
    (tmp_path / "src" / "server").mkdir(parents=True)

    note = interface_note(tmp_path, ["NotWrittenYet"],
                          {"NotWrittenYet": "src/server/NotWrittenYet.luau"})

    assert note == ""


def test_a_system_with_no_exports_yet_is_left_out(tmp_path):
    (tmp_path / "src" / "server").mkdir(parents=True)
    (tmp_path / "src" / "server" / "Empty.luau").write_text(
        "--!strict\nlocal Empty = {}\nreturn Empty\n", encoding="utf-8")

    assert interface_note(tmp_path, ["Empty"], {"Empty": "src/server/Empty.luau"}) == ""


def test_the_task_carries_the_interfaces_of_what_it_depends_on(tmp_path):
    from app.blueprint.schemas import (
        BlueprintConfig, GameBuildSpecification, SpecSystem, SystemLayer,
    )
    from app.engineer.from_spec import task_for

    (tmp_path / "src" / "server").mkdir(parents=True)
    (tmp_path / "src" / "server" / "ResearchService.luau").write_text(SOURCE, encoding="utf-8")

    research = SpecSystem(name="ResearchService", layer=SystemLayer.SERVER,
                          purpose="Track research.", path="src/server/ResearchService.luau",
                          acceptance_criteria=["A bad amount is refused."])
    building = SpecSystem(name="BaseConstructionService", layer=SystemLayer.SERVER,
                          purpose="Build things.", path="src/server/BaseConstructionService.luau",
                          acceptance_criteria=["A bad amount is refused."],
                          depends_on=["ResearchService"])
    spec = GameBuildSpecification(
        spec_id="s", project_id="p", blueprint_id="bp", idea_id="a", title="Lab",
        config=BlueprintConfig(), systems=[research, building],
        build_order=["ResearchService", "BaseConstructionService"])

    task = task_for(spec, building, tmp_path)

    assert any("GetNode" in note for note in task.notes)


def test_without_a_repository_the_task_claims_nothing(tmp_path):
    """The old behaviour, kept: a caller with no project to read must not have
    interfaces invented for it."""
    from app.blueprint.schemas import (
        BlueprintConfig, GameBuildSpecification, SpecSystem, SystemLayer,
    )
    from app.engineer.from_spec import task_for

    building = SpecSystem(name="BaseConstructionService", layer=SystemLayer.SERVER,
                          purpose="Build things.", path="src/server/BaseConstructionService.luau",
                          acceptance_criteria=["A bad amount is refused."],
                          depends_on=["ResearchService"])
    spec = GameBuildSpecification(
        spec_id="s", project_id="p", blueprint_id="bp", idea_id="a", title="Lab",
        config=BlueprintConfig(), systems=[building], build_order=["BaseConstructionService"])

    task = task_for(spec, building)

    assert not any("ARE ALREADY IN THE PROJECT" in note for note in task.notes)
