"""Steering a build that is already running.

A directive is an instruction the person adds while the Engineer works. It is
not a note on a dashboard: it is carried into the prompt for every system the
Engineer has not started yet, read fresh from the build record at the moment
each system is asked for, so a directive typed thirty seconds ago reaches the
next system.

What it cannot do is the part worth being strict about. A system that is
already written was written without it, and a system already in flight was
asked for before it existed. Neither can be steered retroactively, so a
directive aimed at one is refused with the list of systems it could reach
instead. The alternative -- accepting it and showing it in a panel where it
changes nothing -- is the kind of interface that looks like control and is not.

Every directive therefore carries its own record of what happened to it:

    pending   recorded, not yet carried into any prompt
    carried   it went into the prompt for the systems named in carried_into
    stale     the build ended before it could be carried

`carried` says which systems, because "applied" with nothing behind it is a
claim rather than a fact.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from datetime import UTC, datetime

# The Engineer's task schema caps notes at 20, and the specification already
# spends about six of them on constraints. Six active directives leaves room
# for the ones that describe the system itself.
MAX_ACTIVE = 6
MAX_TEXT = 600
MIN_TEXT = 8


class DirectiveRefused(ValueError):
    """Raised with a reason a person can act on."""


def _clean(text: str) -> str:
    """Printable text on one or more lines, and nothing else.

    Control characters are removed rather than escaped: this string is going
    into a prompt, where a stray carriage return or a zero-width joiner is at
    best noise and at worst a way to make one line look like two.
    """
    normalised = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    kept = "".join(character for character in normalised
                   if character == "\n" or unicodedata.category(character)[0] != "C")
    return re.sub(r"\n{3,}", "\n\n", kept).strip()


def directives_of(record: dict) -> list[dict]:
    return list(record.get("directives") or [])


def active(record: dict) -> list[dict]:
    return [entry for entry in directives_of(record) if entry["status"] != "stale"]


def new_directive(text: str, system: str, *, reachable: list[str]) -> dict:
    """Validate and build one directive, or refuse with a usable reason.

    `reachable` is the systems the Engineer has not started. It is computed by
    the caller from the build record, never from what the person believes.
    """
    cleaned = _clean(text)
    if len(cleaned) < MIN_TEXT:
        raise DirectiveRefused(
            f"a directive needs at least {MIN_TEXT} characters of instruction")
    if len(cleaned) > MAX_TEXT:
        raise DirectiveRefused(f"a directive is at most {MAX_TEXT} characters")
    if not reachable:
        raise DirectiveRefused(
            "every system has been started, so there is nothing left for a directive to "
            "reach. Revise the blueprint and build again to change what was written.")
    if system and system not in reachable:
        raise DirectiveRefused(
            f"{system} has already been started, so a directive cannot reach it. "
            f"It could reach: {', '.join(reachable)}")
    return {
        "id": f"steer-{secrets.token_hex(4)}",
        "text": cleaned,
        "system": system,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "pending",
        "carried_into": [],
        # Recorded at the time it was written, so the panel can say what the
        # answer was WHEN the person wrote it even after the build moves on.
        "reachable_when_written": list(reachable),
    }


def with_directive(record: dict, directive: dict) -> list[dict]:
    existing = directives_of(record)
    if len([entry for entry in existing if entry["status"] == "pending"]) >= MAX_ACTIVE:
        raise DirectiveRefused(
            f"{MAX_ACTIVE} directives are already waiting to be carried. The Engineer's "
            "task notes have a fixed size, so remove one before adding another.")
    return [*existing, directive]


def without_directive(record: dict, directive_id: str) -> list[dict]:
    """Remove one, if it has not been used.

    A carried directive stays: it went into a prompt, and deleting the record
    of that would make the build history disagree with what actually happened.
    """
    existing = directives_of(record)
    found = next((entry for entry in existing if entry["id"] == directive_id), None)
    if found is None:
        raise DirectiveRefused("no such directive on this build")
    if found["status"] == "carried":
        raise DirectiveRefused(
            "that directive has already gone into a prompt, so it cannot be withdrawn. "
            f"It was carried into: {', '.join(found['carried_into'])}")
    return [entry for entry in existing if entry["id"] != directive_id]


def notes_for(record: dict, system: str) -> list[str]:
    """The note lines to add to one system's task, in the order written.

    A directive naming a system reaches only that system; one naming none
    reaches every system still to be written.
    """
    lines = []
    for entry in directives_of(record):
        if entry["status"] == "stale":
            continue
        if entry["system"] and entry["system"] != system:
            continue
        lines.append(f"STEERING (added during this build, follow it): {entry['text']}")
    return lines


def carried(record: dict, system: str) -> list[dict]:
    """The same directives, marked as having gone into that system's prompt."""
    updated = []
    for entry in directives_of(record):
        if entry["status"] == "stale":
            updated.append(entry)
            continue
        if entry["system"] and entry["system"] != system:
            updated.append(entry)
            continue
        updated.append({**entry, "status": "carried",
                        "carried_into": [*entry["carried_into"], system]})
    return updated


def as_shown(record: dict, *, running: bool) -> list[dict]:
    """The directives with the one status that can be derived rather than stored.

    A directive that was never carried and whose build has stopped reached
    nothing, whatever the record says. Deriving it means a build that failed
    halfway -- which never gets to write a tidy ending -- cannot leave a
    directive sitting in the panel looking like it is about to be used. It is
    the same reason readiness is recomputed on every read: a stored status can
    disagree with the thing it describes, and the disagreement is invisible.
    """
    return [entry if running or entry["status"] != "pending"
            else {**entry, "status": "stale"}
            for entry in directives_of(record)]
