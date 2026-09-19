"""The stage between an idea and a build.

The failure that matters here is silent and permanent: a feature the user
rejected reappearing in the specification, or a readiness percentage that means
nothing. Both look fine in the UI. Most of these tests are about what must not
happen.
"""

from __future__ import annotations

import json

import pytest

from app.blueprint.architect import (
    MAX_SUGGESTIONS,
    ArchitectRefused,
    BlueprintArchitect,
    build_systems_prompt,
    parse_suggestions,
    parse_systems,
    user_feature,
)
from app.blueprint.compile import NotReady, compile_spec, human_preview
from app.blueprint.readiness import assess, scope_of
from app.blueprint.schemas import (
    Blueprint,
    BlueprintConfig,
    BuildStatus,
    Complexity,
    FeatureSuggestion,
    GameSystem,
    Persistence,
    Scope,
    SystemLayer,
)
from app.blueprint.transitions import IllegalTransition, can, check, controls_for, is_running

CRITERION = "Spending more resources than the player holds is refused and changes nothing."


def suggestion(identifier: str = "mutation", **overrides) -> FeatureSuggestion:
    fields = {"id": identifier, "title": "Mutation Research Tree",
              "description": "Researching a cure alters which zombies spawn.",
              "reason": "Makes progression change the world rather than only the numbers."}
    return FeatureSuggestion(**{**fields, **overrides})


def system(name: str = "ResourceService", **overrides) -> GameSystem:
    fields = {"id": name.lower(), "name": name, "layer": SystemLayer.SERVER,
              "purpose": "Track what each survivor is carrying.",
              "acceptance_criteria": [CRITERION]}
    return GameSystem(**{**fields, **overrides})


def blueprint(**overrides) -> Blueprint:
    fields = {"id": "bp1", "project_id": "proj1", "audit_id": "audit1",
              "title": "Zombie Quarantine Lab", "user_intent": "Survivors run a research bunker.",
              "systems": [system()], "suggestions": []}
    return Blueprint(**{**fields, **overrides})


# ---- the contract: a rejected feature stays rejected ------------------------

def test_a_rejected_feature_is_recorded_as_excluded_not_forgotten():
    """The Engineer is told what was refused, by name. An absence it could fill
    in helpfully is how an unapproved feature gets built."""
    plan = blueprint(suggestions=[suggestion("a", selected=True, title="Power Management"),
                                  suggestion("b", selected=False, title="Player Infection")])

    spec = compile_spec(plan)

    assert spec.excluded_features == ["Player Infection"]
    assert spec.included_features == ["Power Management"]


def test_a_system_that_exists_only_for_a_rejected_feature_is_dropped():
    plan = blueprint(
        suggestions=[suggestion("infection", selected=False)],
        systems=[system("ResourceService"), system("InfectionService", from_feature="infection")])

    assert [s.name for s in plan.active_systems()] == ["ResourceService"]
    assert [s.name for s in compile_spec(plan).systems] == ["ResourceService"]


def test_a_system_for_a_selected_feature_survives():
    plan = blueprint(suggestions=[suggestion("power", selected=True)],
                     systems=[system("PowerService", from_feature="power")])
    assert [s.name for s in plan.active_systems()] == ["PowerService"]


def test_the_architect_is_shown_the_rejections_explicitly():
    plan = blueprint(suggestions=[suggestion("b", selected=False, title="Player Infection")])
    prompt = build_systems_prompt({}, plan)

    assert "rejected_features" in prompt and "Player Infection" in prompt
    assert "must not appear as systems" in prompt


def test_a_system_serving_a_rejected_feature_is_refused_when_parsed():
    """Belt and braces: the prompt says not to, and the parser refuses it."""
    plan = blueprint(suggestions=[suggestion("infection", selected=False)])
    answer = json.dumps({"systems": [{"id": "x", "name": "InfectionService", "layer": "server",
                                      "purpose": "Infect players over time.",
                                      "acceptance_criteria": [CRITERION],
                                      "from_feature": "infection"}]})

    with pytest.raises(ArchitectRefused, match="rejected"):
        parse_systems(answer, plan)


# ---- suggestions -----------------------------------------------------------

def valid_suggestions(count: int = 4) -> str:
    return json.dumps({"summary": "A co-op research survival game.", "suggestions": [
        {"id": f"feature_{i}", "title": f"Feature {i}",
         "description": "something concrete about this game",
         "reason": "why it helps this game specifically"} for i in range(count)]})


def test_suggestions_arrive_undecided_however_the_model_answered():
    """A model that pre-ticks its own boxes is choosing for the person."""
    answer = json.dumps({"suggestions": [
        {"id": f"f{i}", "title": f"Feature {i}", "description": "d" * 12, "reason": "r" * 12,
         "selected": True} for i in range(4)]})

    suggestions, _summary = parse_suggestions(answer)

    assert all(item.selected is None for item in suggestions)


def test_too_few_suggestions_is_refused_with_the_number_wanted():
    with pytest.raises(ArchitectRefused, match="between 4 and 6"):
        parse_suggestions(valid_suggestions(2))


def test_more_than_six_suggestions_are_trimmed():
    suggestions, _ = parse_suggestions(valid_suggestions(11))
    assert len(suggestions) == MAX_SUGGESTIONS


def test_duplicate_ids_are_collapsed_not_counted_twice():
    answer = json.dumps({"suggestions": [
        {"id": "same", "title": "Alpha", "description": "d" * 12, "reason": "r" * 12},
        {"id": "same", "title": "Beta", "description": "d" * 12, "reason": "r" * 12},
        {"id": "c", "title": "Gamma", "description": "d" * 12, "reason": "r" * 12},
    ]})
    with pytest.raises(ArchitectRefused, match="usable suggestions"):
        parse_suggestions(answer)


def test_a_feature_the_person_wrote_is_already_selected():
    """Asking someone to tick a box on their own idea is a step that exists
    only to be forgotten."""
    feature = user_feature("Sample Extraction", "Harvest samples from downed zombies.")
    assert feature.selected is True and feature.origin == "user"


def test_a_fenced_answer_is_unwrapped():
    suggestions, _ = parse_suggestions("```json\n" + valid_suggestions() + "\n```")
    assert len(suggestions) == 4


# ---- systems and their criteria --------------------------------------------

def test_a_criterion_too_short_to_fail_is_refused():
    answer = json.dumps({"systems": [{"id": "x", "name": "WaveService", "layer": "server",
                                      "purpose": "Run the waves.",
                                      "acceptance_criteria": ["Works correctly.", "Is fun."]}]})
    with pytest.raises(ArchitectRefused, match="too short to be checkable"):
        parse_systems(answer, blueprint())


def test_a_system_with_no_criteria_at_all_is_refused():
    answer = json.dumps({"systems": [{"id": "x", "name": "WaveService", "layer": "server",
                                      "purpose": "Run the waves.", "acceptance_criteria": []}]})
    with pytest.raises(ArchitectRefused, match="no acceptance criteria"):
        parse_systems(answer, blueprint())


def test_a_dependency_on_nothing_that_will_exist_is_refused():
    answer = json.dumps({"systems": [{"id": "x", "name": "WaveService", "layer": "server",
                                      "purpose": "Run the waves.",
                                      "acceptance_criteria": [CRITERION],
                                      "depends_on": ["NoSuchService"]}]})
    with pytest.raises(ArchitectRefused, match="neither planned nor already built"):
        parse_systems(answer, blueprint())


def test_a_dependency_on_something_already_in_the_project_is_fine():
    answer = json.dumps({"systems": [{"id": "x", "name": "WaveService", "layer": "server",
                                      "purpose": "Run the waves.",
                                      "acceptance_criteria": [CRITERION],
                                      "depends_on": ["ResourceService"]}]})
    systems, _assets = parse_systems(answer, blueprint(), already_built={"ResourceService"})
    assert [s.name for s in systems] == ["WaveService"]


def test_a_system_the_project_already_has_is_skipped_not_refused():
    answer = json.dumps({"systems": [
        {"id": "a", "name": "ResourceService", "layer": "server", "purpose": "purpose here",
         "acceptance_criteria": [CRITERION]},
        {"id": "b", "name": "WaveService", "layer": "server", "purpose": "purpose here",
         "acceptance_criteria": [CRITERION]}]})
    systems, _ = parse_systems(answer, blueprint(), already_built={"ResourceService"})
    assert [s.name for s in systems] == ["WaveService"]


# ---- readiness is counted, never claimed -----------------------------------

def test_readiness_names_every_missing_requirement():
    plan = blueprint(user_intent="", suggestions=[suggestion("a")])  # undecided feature

    readiness = assess(plan)

    keys = {requirement.key for requirement in readiness.missing}
    assert {"intent", "features_decided"} <= keys
    assert readiness.ready is False
    assert 0 < readiness.percent < 100


def test_a_blueprint_with_everything_decided_is_ready():
    plan = blueprint(suggestions=[suggestion("a", selected=True)])
    assert assess(plan).ready is True
    assert assess(plan).percent == 100


def test_an_undecided_feature_alone_blocks_the_build():
    """Undecided is not the same as rejected, and building either way would be
    choosing for the person."""
    plan = blueprint(suggestions=[suggestion("a", selected=None)])
    assert "features_decided" in {r.key for r in assess(plan).missing}


def test_a_system_without_criteria_blocks_the_build():
    plan = blueprint(systems=[system(acceptance_criteria=[])])
    assert "acceptance" in {r.key for r in assess(plan).missing}


def test_max_players_below_min_is_caught():
    plan = blueprint(config=BlueprintConfig(min_players=4, max_players=1))
    assert "player_count" in {r.key for r in assess(plan).missing}


# ---- scope -----------------------------------------------------------------

def test_a_blueprint_within_its_budget_is_not_flagged():
    plan = blueprint(systems=[system(f"Service{i}") for i in range(4)])
    assert scope_of(plan).verdict in ("lean", "balanced")


def test_too_many_systems_for_the_scope_is_flagged_with_what_to_cut():
    optional = [system(f"Extra{i}", from_feature="f", essential=False,
                       complexity=Complexity.HIGH) for i in range(9)]
    plan = blueprint(config=BlueprintConfig(scope=Scope.QUICK_PROTOTYPE),
                     suggestions=[suggestion("f", selected=True)],
                     systems=[system("CoreService"), *optional])

    verdict = scope_of(plan)

    assert verdict.verdict == "too_large"
    assert verdict.suggested_cut, "a warning with nothing to do about it is noise"
    assert "CoreService" not in verdict.suggested_cut, "the core idea is not the thing to cut"


def test_the_cut_is_a_suggestion_and_changes_nothing():
    plan = blueprint(config=BlueprintConfig(scope=Scope.QUICK_PROTOTYPE),
                     suggestions=[suggestion("f", selected=True)],
                     systems=[system(f"Extra{i}", from_feature="f", essential=False,
                                     complexity=Complexity.HIGH) for i in range(9)])
    before = len(plan.systems)
    scope_of(plan)
    assert len(plan.systems) == before


# ---- compiling the specification -------------------------------------------

def test_a_blueprint_that_is_not_ready_does_not_compile():
    with pytest.raises(NotReady, match="ready"):
        compile_spec(blueprint(user_intent=""))


def test_the_spec_orders_dependencies_before_dependents():
    plan = blueprint(systems=[system("WaveService", depends_on=["ResourceService"]),
                              system("ResourceService")])
    spec = compile_spec(plan)
    assert spec.build_order.index("ResourceService") < spec.build_order.index("WaveService")


def test_a_dependency_cycle_is_refused_rather_than_broken_arbitrarily():
    plan = blueprint(systems=[system("Alpha", depends_on=["Beta"]), system("Beta", depends_on=["Alpha"])])
    with pytest.raises(NotReady, match="circle"):
        compile_spec(plan)


def test_a_dependency_on_a_system_not_in_the_build_is_refused():
    plan = blueprint(systems=[system("WaveService", depends_on=["NoSuchService"])])
    with pytest.raises(NotReady, match="depended on but are not in the build"):
        compile_spec(plan)


def test_two_systems_with_one_name_are_refused():
    plan = blueprint(systems=[system("WaveService"), system("WaveService")])
    with pytest.raises(NotReady, match="share a name"):
        compile_spec(plan)


def test_session_only_persistence_forbids_datastore_in_words():
    """An absence the Engineer could fill in helpfully is not a constraint."""
    plan = blueprint(config=BlueprintConfig(persistence=Persistence.SESSION_ONLY))
    constraints = " ".join(compile_spec(plan).technical_constraints)
    assert "NO DATASTORE" in constraints


def test_saved_progression_asks_for_datastore_instead():
    plan = blueprint(config=BlueprintConfig(persistence=Persistence.SAVED_PROGRESSION))
    constraints = " ".join(compile_spec(plan).technical_constraints)
    assert "DataStoreService" in constraints and "NO DATASTORE" not in constraints


def test_building_into_an_existing_place_says_not_to_replace_it():
    from app.blueprint.schemas import BuildTarget

    plan = blueprint(config=BlueprintConfig(build_target=BuildTarget.CURRENT_PLACE))
    constraints = " ".join(compile_spec(plan).technical_constraints)
    assert "inspect what is there first" in constraints


def test_each_layer_lands_in_its_own_source_root():
    plan = blueprint(systems=[system("Alpha", layer=SystemLayer.SERVER),
                              system("Beta", layer=SystemLayer.CLIENT),
                              system("Gamma", layer=SystemLayer.SHARED)])
    paths = {s.name: s.path for s in compile_spec(plan).systems}
    assert paths == {"Alpha": "src/server/Alpha.luau", "Beta": "src/client/Beta.luau",
                     "Gamma": "src/shared/Gamma.luau"}


def test_every_system_criterion_reaches_the_specification():
    plan = blueprint(systems=[system("Alpha"), system("Beta")])
    spec = compile_spec(plan)
    assert len(spec.acceptance_criteria) == 2
    assert all(CRITERION in criterion for criterion in spec.acceptance_criteria)


def test_the_same_blueprint_compiles_to_the_same_fingerprint():
    """What makes "nothing changed, do not rebuild" decidable rather than a guess."""
    plan = blueprint()
    assert compile_spec(plan).content_hash == compile_spec(plan).content_hash


def test_changing_what_gets_built_changes_the_fingerprint():
    first = compile_spec(blueprint())
    second = compile_spec(blueprint(systems=[system("Alpha"), system("Beta")]))
    assert first.content_hash != second.content_hash


def test_identity_and_time_do_not_change_the_fingerprint():
    """Two specs asking for the same project are the same project."""
    first = compile_spec(blueprint(), spec_id="one")
    second = compile_spec(blueprint(), spec_id="two")
    assert first.content_hash == second.content_hash


# ---- the human preview -----------------------------------------------------

def test_the_preview_counts_come_from_the_specification_itself():
    """A preview that can disagree with the build is worse than no preview."""
    plan = blueprint(systems=[system("Alpha", layer=SystemLayer.SERVER),
                              system("Beta", layer=SystemLayer.CLIENT)])
    preview = human_preview(compile_spec(plan))

    assert preview["systems_total"] == 2
    assert preview["server_systems"] == ["Alpha"] and preview["client_systems"] == ["Beta"]
    assert preview["players"] == "1-4"


def test_the_preview_carries_what_was_excluded():
    plan = blueprint(suggestions=[suggestion("b", selected=False, title="Player Infection")])
    assert human_preview(compile_spec(plan))["excluded_features"] == ["Player Infection"]


# ---- the state machine -----------------------------------------------------

def test_a_build_cannot_skip_from_draft_to_building():
    assert not can(BuildStatus.DRAFT, BuildStatus.BUILDING)
    with pytest.raises(IllegalTransition, match="cannot go from draft to building"):
        check(BuildStatus.DRAFT, BuildStatus.BUILDING)


def test_the_refusal_says_what_was_legal_instead():
    with pytest.raises(IllegalTransition, match="blueprinting"):
        check(BuildStatus.DRAFT, BuildStatus.SUCCEEDED)


def test_editing_a_ready_blueprint_un_readies_it():
    """So a change cannot be built without being assessed again."""
    assert can(BuildStatus.READY_TO_BUILD, BuildStatus.BLUEPRINTING)


def test_validation_failing_sends_the_engineer_back_to_generating():
    assert can(BuildStatus.VALIDATING, BuildStatus.GENERATING)


def test_a_finished_build_can_start_another_one():
    for status in (BuildStatus.SUCCEEDED, BuildStatus.FAILED, BuildStatus.PARTIAL):
        assert can(status, BuildStatus.QUEUED)


def test_every_status_has_a_transition_table_entry():
    """A status nobody wrote a row for is a build that can never move again."""
    from app.blueprint.transitions import ALLOWED

    assert set(ALLOWED) == set(BuildStatus)


def test_controls_are_offered_only_where_they_mean_something():
    assert "build" in controls_for(BuildStatus.READY_TO_BUILD)
    assert "build" not in controls_for(BuildStatus.BUILDING)
    assert "stop" in controls_for(BuildStatus.BUILDING)
    assert "rollback" in controls_for(BuildStatus.FAILED)
    assert "play" in controls_for(BuildStatus.SUCCEEDED)


def test_a_running_build_is_recognisable_as_one():
    """The bridge serves one build at a time; a second mid-sync would interleave
    operations in the same DataModel."""
    assert is_running(BuildStatus.SYNCING) and is_running(BuildStatus.PLAYTESTING)
    assert not is_running(BuildStatus.SUCCEEDED) and not is_running(BuildStatus.DRAFT)


# ---- the agent, against a scripted model -----------------------------------

class ScriptedModel:
    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def __call__(self, system: str, prompt: str, deadline: float) -> tuple[str, str]:
        self.prompts.append(prompt)
        return self.replies.pop(0), "scripted"


@pytest.mark.anyio
async def test_the_architect_asks_again_with_what_to_fix():
    model = ScriptedModel(valid_suggestions(1), valid_suggestions(4))
    architect = BlueprintArchitect(model)

    suggestions, _summary = await architect.suggest({}, "a research bunker", BlueprintConfig(), 1e9)

    assert len(suggestions) == 4
    assert "previous_attempt" in model.prompts[1], "the retry has to say what was wrong"
    assert "between 4 and 6" in model.prompts[1]


@pytest.mark.anyio
async def test_the_architect_gives_up_after_its_attempts():
    model = ScriptedModel(*[valid_suggestions(1)] * 3)
    with pytest.raises(ArchitectRefused):
        await BlueprintArchitect(model, attempts=3).suggest({}, "x", BlueprintConfig(), 1e9)


@pytest.mark.anyio
async def test_the_architect_never_imports_a_provider():
    """It depends on the call shape the Engineer's chain already produces, so
    Claude, Gemini or a local model is configuration rather than a rewrite."""
    import app.blueprint.architect as module

    source = (module.__file__ or "")
    assert source
    text = open(source, encoding="utf-8").read()
    for vendor in ("anthropic", "openai", "google.generativeai", "httpx"):
        assert vendor not in text, f"the architect reached for {vendor} directly"


def test_the_architect_is_told_every_game_needs_a_visible_world():
    """A build of nine working systems was opened in Studio and showed nothing
    but a test cube from an unrelated smoke test, because no system had been
    asked to make anything. The specification is where that gets fixed: the
    Engineer cannot build a world nobody put in the spec."""
    from app.blueprint.architect import SYSTEMS_SYSTEM

    assert "BUILDS THE VISIBLE WORLD" in SYSTEMS_SYSTEM
    # From primitives, not from assets that do not exist in the project.
    assert "asset_requirements" in SYSTEMS_SYSTEM


def test_the_engineer_is_told_how_to_build_one_without_inventing_assets():
    from app.engineer.prompts import SYSTEM

    assert "BUILDING THE VISIBLE WORLD" in SYSTEM
    # The three that turn a world into a bug report: the forbidden global, a
    # made-up asset id, and a part that falls through the floor on start.
    assert "Services.Workspace" in SYSTEM
    assert "rbxassetid" in SYSTEM
    assert "Anchored" in SYSTEM
    # And it has to be rebuildable, or a second build leaves two worlds.
    assert "one world, not two" in SYSTEM


def test_the_criteria_summary_is_trimmed_rather_than_failing_to_compile():
    """The summary is flattened across every system, so it grows with the plan.

    A hundred systems carrying twenty criteria each is two thousand lines for a
    field that holds a thousand, and the specification would fail to build at
    all. Each system keeps its own criteria either way -- that is what the
    Engineer is handed and what the gate checks -- so the summary is trimmed.
    """
    from app.blueprint.compile import _field_limit
    from app.blueprint.schemas import GameBuildSpecification

    limit = _field_limit(GameBuildSpecification, "acceptance_criteria")
    many = [
        system(f"Service{index:03}", acceptance_criteria=[
            f"Refusing bad input number {n} for service {index} changes nothing."
            for n in range(20)
        ])
        for index in range(60)
    ]

    spec = compile_spec(blueprint(systems=many))

    assert len(many) * 20 > limit, "the fixture has to exceed the cap to prove anything"
    assert len(spec.acceptance_criteria) == limit
    assert len(spec.systems) == len(many), "no system was dropped to make it fit"
    assert all(entry.acceptance_criteria for entry in spec.systems)
