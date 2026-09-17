"""Steering a running build.

A steering panel is the easiest place in the whole product to lie: a text box,
a button marked Apply, and a list of directives that scrolls. None of that
requires the instruction to reach anything.

So these tests are about the seam between the panel and the prompt. A directive
is refused when it cannot reach a system, it is marked carried only when it
actually went into a prompt, and it names which systems it went into.
"""

from __future__ import annotations

import pytest

from app.blueprint.steering import (
    MAX_ACTIVE,
    DirectiveRefused,
    as_shown,
    carried,
    new_directive,
    notes_for,
    without_directive,
    with_directive,
)

REACHABLE = ["WaveService", "InfectedService"]


def directive(text: str = "Cap every wave at eight infected.", system: str = "",
              reachable: list[str] | None = None) -> dict:
    return new_directive(text, system, reachable=REACHABLE if reachable is None else reachable)


# ---- what may be written ---------------------------------------------------

def test_a_directive_records_what_it_could_reach_when_it_was_written():
    entry = directive()

    assert entry["status"] == "pending"
    assert entry["carried_into"] == []
    assert entry["reachable_when_written"] == REACHABLE


def test_a_directive_is_refused_when_nothing_is_left_to_reach():
    """The important refusal. Accepting it would put an instruction in a list
    beside systems that were all written before it existed."""
    with pytest.raises(DirectiveRefused) as raised:
        directive(reachable=[])

    assert "build again" in str(raised.value)


def test_a_directive_aimed_at_a_system_already_started_is_refused_with_the_alternatives():
    with pytest.raises(DirectiveRefused) as raised:
        directive(system="ResearchService")

    assert "already been started" in str(raised.value)
    assert "WaveService" in str(raised.value)


def test_a_directive_too_short_to_be_an_instruction_is_refused():
    with pytest.raises(DirectiveRefused):
        directive("faster")


def test_control_characters_are_stripped_rather_than_escaped():
    """This string goes into a prompt. A carriage return or a zero-width
    character there is at best noise and at worst a way to make one line of
    instruction look like two."""
    entry = directive("Cap waves\r\nat eight.​ Use a table.\x07")

    assert "\r" not in entry["text"]
    assert "​" not in entry["text"]
    assert "\x07" not in entry["text"]
    assert entry["text"] == "Cap waves\nat eight. Use a table."


def test_only_so_many_may_wait_at_once():
    """The Engineer's task notes are a fixed-size list, and the specification
    already spends some of them. A seventh directive would push one out
    silently, so it is refused loudly instead."""
    record = {"directives": [directive() for _ in range(MAX_ACTIVE)]}

    with pytest.raises(DirectiveRefused) as raised:
        with_directive(record, directive())

    assert "remove one" in str(raised.value)


def test_a_carried_directive_does_not_count_against_the_ceiling():
    record = {"directives": [{**directive(), "status": "carried",
                              "carried_into": ["WaveService"]} for _ in range(MAX_ACTIVE)]}

    assert len(with_directive(record, directive())) == MAX_ACTIVE + 1


# ---- what reaches the prompt ----------------------------------------------

def test_a_directive_with_no_system_reaches_every_system_still_to_be_written():
    record = {"directives": [directive("Cap every wave at eight infected.")]}

    assert len(notes_for(record, "WaveService")) == 1
    assert len(notes_for(record, "InfectedService")) == 1


def test_a_directive_naming_a_system_reaches_only_that_system():
    record = {"directives": [directive("Cap every wave at eight.", system="WaveService")]}

    assert len(notes_for(record, "WaveService")) == 1
    assert notes_for(record, "InfectedService") == []


def test_the_note_says_it_is_a_steering_instruction_to_follow():
    record = {"directives": [directive("Cap every wave at eight infected.")]}

    assert notes_for(record, "WaveService") == [
        "STEERING (added during this build, follow it): Cap every wave at eight infected."]


def test_carrying_names_the_system_it_went_into():
    """"Applied" on its own is a claim. Which system it reached is a fact, and
    it is the one thing that makes the claim checkable afterwards."""
    record = {"directives": [directive()]}

    once = {"directives": carried(record, "WaveService")}
    twice = {"directives": carried(once, "InfectedService")}

    assert twice["directives"][0]["status"] == "carried"
    assert twice["directives"][0]["carried_into"] == ["WaveService", "InfectedService"]


def test_a_directive_for_another_system_is_not_marked_carried():
    record = {"directives": [directive(system="InfectedService")]}

    updated = carried(record, "WaveService")

    assert updated[0]["status"] == "pending"
    assert updated[0]["carried_into"] == []


# ---- what it is shown as ---------------------------------------------------

def test_a_pending_directive_on_a_stopped_build_is_shown_as_stale():
    """Derived rather than stored: a build that fails halfway never gets to
    write a tidy ending, and a directive left looking imminent forever is
    exactly the thing this panel must not do."""
    record = {"directives": [directive()]}

    assert as_shown(record, running=False)[0]["status"] == "stale"
    assert as_shown(record, running=True)[0]["status"] == "pending"


def test_a_carried_directive_stays_carried_after_the_build_stops():
    record = {"directives": carried({"directives": [directive()]}, "WaveService")}

    assert as_shown(record, running=False)[0]["status"] == "carried"


# ---- withdrawing -----------------------------------------------------------

def test_a_pending_directive_can_be_withdrawn():
    entry = directive()
    record = {"directives": [entry]}

    assert without_directive(record, entry["id"]) == []


def test_a_carried_directive_cannot_be_withdrawn():
    """It went into a prompt. Deleting the record of that would make the build
    history disagree with what the Engineer was actually told."""
    record = {"directives": carried({"directives": [directive()]}, "WaveService")}

    with pytest.raises(DirectiveRefused) as raised:
        without_directive(record, record["directives"][0]["id"])

    assert "WaveService" in str(raised.value)


def test_withdrawing_something_that_is_not_there_is_refused():
    with pytest.raises(DirectiveRefused):
        without_directive({"directives": []}, "steer-nope")


# ---- the endpoints ---------------------------------------------------------

@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_steering_a_build_that_does_not_exist_is_a_404(client):
    response = client.post("/api/builds/nope/directives", json={"text": "Cap the waves."})
    assert response.status_code == 404


def test_a_directive_needs_text(client):
    assert client.post("/api/builds/nope/directives", json={}).status_code == 422


def test_reading_directives_for_a_build_that_does_not_exist_is_a_404(client):
    assert client.get("/api/builds/nope/directives").status_code == 404


# ---- where the build reads them -------------------------------------------

def test_the_record_is_changed_inside_one_session(session_factory):
    """Two writers: the person adding a directive from the API and the build
    marking another as carried. `update` computes its value outside the
    session, so whichever read first would win with a value computed before
    the other existed."""
    from app.blueprint.builds import BuildRecord
    from app.blueprint.schemas import Blueprint, BlueprintConfig, GameBuildSpecification

    plan = Blueprint(id="bp", project_id="p", audit_id="a", title="Lab")
    spec = GameBuildSpecification(spec_id="s", project_id="p", blueprint_id="bp",
                                  idea_id="a", title="Lab", config=BlueprintConfig())
    record = BuildRecord(session_factory, "build-steer")
    record.create(plan, spec)

    record.mutate(lambda current: {"directives": [*(current.get("directives") or []),
                                                  directive("First instruction here.")]})
    record.mutate(lambda current: {"directives": [*(current.get("directives") or []),
                                                  directive("Second instruction here.")]})

    assert len(record.read()["directives"]) == 2
