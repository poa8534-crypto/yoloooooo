"""The seam that decides whether the steering panel is real.

Everything else about steering can be right -- the record, the statuses, the
refusals, the panel -- and the feature can still be theatre, because the one
thing that matters happens inside the build loop: the directive has to be in
the prompt the Engineer is actually given, for a system asked for AFTER the
directive was written.

So these tests run the loop with a stand-in Engineer, add a directive partway
through exactly as the API does, and read back the notes the next system was
asked with. Nothing is mocked about the carrying itself.

The build always fails at the end here, because there is no bridge and no
Studio in a test. That is the right place to stop: by then the Engineer's half
has already run, and the half being tested is the Engineer's.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.blueprint import builds as module
from app.blueprint.builds import BuildFailed, BuildRecord, run_build
from app.blueprint.schemas import GameSystem, SystemLayer
from app.blueprint.steering import new_directive, with_directive
from app.blueprint.store import BlueprintStore

CRITERION = "Spending more than the player holds is refused and changes nothing."
# What run_build reads from settings itself; the rest it reaches through
# functions these tests replace.
ONE_AT_A_TIME = SimpleNamespace(engineer_parallel_systems=1, engineer_provider_concurrency="")


def system(name: str, depends: list[str] | None = None) -> GameSystem:
    return GameSystem(id=name.lower(), name=name, layer=SystemLayer.SERVER,
                      purpose=f"Look after what {name} is for.",
                      acceptance_criteria=[CRITERION], depends_on=depends or [])


class Refused:
    """What run_task returns on a refusal: enough for the loop to carry on to
    the next system, which is the part being tested."""

    status = "refused"
    reason = "the stand-in Engineer accepts nothing"
    branch = None
    commit = None
    attempts = 1


@pytest.fixture
def build(session_factory, tmp_path, monkeypatch):
    """A blueprint with two systems, and a build loop with no model in it."""
    store = BlueprintStore(session_factory)
    plan = store.create(audit_id="audit-1", title="Zombie Quarantine Lab")
    plan = store.save(plan.model_copy(update={
        "user_intent": "Survivors run a research bunker under siege.",
        "systems": [system("ResearchService"), system("WaveService", ["ResearchService"])],
    }))

    repo = tmp_path / "game"
    (repo / "src" / "server").mkdir(parents=True)
    monkeypatch.setattr(module, "game_repo", lambda _settings: repo)

    asked: list[list[str]] = []

    async def stand_in(task, **_kwargs):
        asked.append(list(task.notes))
        # The real Engineer awaits a model for minutes. This one has to await
        # something, or the loop never yields and a directive written "during"
        # the build could only ever arrive after it.
        await asyncio.sleep(0.02)
        return Refused()

    monkeypatch.setattr(module, "run_task", stand_in)
    return plan, asked, repo


async def _build_and_steer(plan, session_factory, build_id: str, asked: list,
                           directive: dict | None) -> None:
    """Run the build, adding a directive the moment the first system is asked.

    That timing is the whole point: it is exactly what happens when a person
    types into the panel while watching the build, and the only way the
    directive can reach the second system is if the loop re-reads the record
    rather than trusting the plan it made before the build began.
    """
    record = BuildRecord(session_factory, build_id)

    async def steer_once_running():
        if directive is None:
            return
        while not asked:
            await asyncio.sleep(0.01)
        record.mutate(lambda current: {"directives": with_directive(current, directive)})

    steering = asyncio.create_task(steer_once_running())
    try:
        # One at a time: these systems depend on each other in a line, and the
        # test is about what the second one is asked with.
        await run_build(plan.id, settings=ONE_AT_A_TIME, factory=session_factory,
                        token="t", play=False, build_id=build_id)
    except BuildFailed:
        pass
    await steering


def test_a_directive_written_midway_reaches_the_next_system(build, session_factory):
    """The whole feature in one test."""
    plan, asked, _repo = build
    directive = new_directive("Cap every wave at eight infected.", "",
                              reachable=["WaveService"])

    asyncio.run(_build_and_steer(plan, session_factory, "build-steered", asked, directive))

    assert len(asked) == 2, "both systems should have been asked for"
    first, second = asked
    assert not any("STEERING" in note for note in first)
    assert any("Cap every wave at eight infected." in note for note in second)


def test_the_directive_arrives_as_an_instruction_to_follow(build, session_factory):
    """Not as a stray sentence among the specification's notes: the Engineer
    has to be able to tell a mid-build instruction from the goal it was
    already given."""
    plan, asked, _repo = build
    directive = new_directive("Cap every wave at eight infected.", "",
                              reachable=["WaveService"])

    asyncio.run(_build_and_steer(plan, session_factory, "build-worded", asked, directive))

    steering_notes = [note for note in asked[1] if note.startswith("STEERING")]
    assert steering_notes == [
        "STEERING (added during this build, follow it): Cap every wave at eight infected."]


def test_a_directive_is_marked_carried_with_the_system_it_reached(build, session_factory):
    """Recorded at the moment the prompt is built, so "carried" is a fact about
    what the Engineer was told rather than a hopeful label."""
    plan, asked, _repo = build
    directive = new_directive("Read every limit from one config table.", "",
                              reachable=["WaveService"])

    asyncio.run(_build_and_steer(plan, session_factory, "build-carried", asked, directive))

    stored = BuildRecord(session_factory, "build-carried").read()["directives"][0]
    assert stored["status"] == "carried"
    assert stored["carried_into"] == ["WaveService"]


def test_a_directive_for_one_system_does_not_reach_another(build, session_factory):
    plan, asked, _repo = build
    directive = new_directive("Cap every wave at eight.", "ResearchService",
                              reachable=["ResearchService", "WaveService"])

    asyncio.run(_build_and_steer(plan, session_factory, "build-narrow", asked, directive))

    # It was added after ResearchService was already asked for, so it reaches
    # nothing at all -- which is what the API refuses up front, and what the
    # loop must not quietly do anyway.
    assert not any("STEERING" in note for note in asked[1])
    stored = BuildRecord(session_factory, "build-narrow").read()["directives"][0]
    assert stored["status"] == "pending"


def test_a_build_with_no_directives_carries_none(build, session_factory):
    """The notes a system is asked with are the specification's, unchanged,
    when nobody has steered anything."""
    plan, asked, _repo = build

    asyncio.run(_build_and_steer(plan, session_factory, "build-plain", asked, None))

    assert asked and not any("STEERING" in note for group in asked for note in group)


def test_a_system_the_project_already_has_is_recorded_as_skipped(build, session_factory):
    """The graph used to infer which system was in flight from build order, so
    a system the Engineer skips showed as being written for the whole run. The
    build now records which ones it is not going to write."""
    plan, asked, repo = build
    Path(repo / "src" / "server" / "ResearchService.luau").write_text(
        "--!strict\nreturn {}\n", encoding="utf-8")

    asyncio.run(_build_and_steer(plan, session_factory, "build-skipping", asked, None))

    record = BuildRecord(session_factory, "build-skipping").read()
    assert record["skipped"] == ["ResearchService"]
    assert [note for note in asked[0]], "WaveService should still have been asked for"
    assert len(asked) == 1
