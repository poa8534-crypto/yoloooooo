"""The build state machine.

One value, not a handful of booleans. Booleans drift: `building` and `failed`
can both be true, and nothing catches it until the UI renders both. A move that
is not in this table is refused, so an impossible state cannot be reached even
by a caller that means well.
"""

from __future__ import annotations

from .schemas import TERMINAL_STATUSES, BuildStatus as S

ALLOWED: dict[S, frozenset[S]] = {
    S.DRAFT: frozenset({S.BLUEPRINTING, S.CANCELLED}),
    S.BLUEPRINTING: frozenset({S.BLUEPRINTING, S.READY_TO_BUILD, S.DRAFT, S.CANCELLED}),
    # Back to BLUEPRINTING on purpose: editing a ready blueprint un-readies it,
    # which is how a change cannot be built without being assessed again.
    S.READY_TO_BUILD: frozenset({S.QUEUED, S.BLUEPRINTING, S.CANCELLED}),
    S.QUEUED: frozenset({S.PLANNING, S.CANCELLED, S.FAILED}),
    # VALIDATING as well as GENERATING: planning can discover there is nothing
    # to write, because every system in the specification is already in the
    # project. That is the ordinary state of a re-sync, and it was unreachable
    # -- the build refused its own legal path and could not put a finished
    # project into Studio at all.
    S.PLANNING: frozenset({S.GENERATING, S.VALIDATING, S.FAILED, S.CANCELLED}),
    S.GENERATING: frozenset({S.VALIDATING, S.FAILED, S.CANCELLED}),
    S.VALIDATING: frozenset({S.WAITING_FOR_STUDIO, S.GENERATING, S.FAILED, S.CANCELLED}),
    # Back to GENERATING: validation failing is the Engineer's own gate telling
    # it to write the code again, which is the loop that already exists.
    S.WAITING_FOR_STUDIO: frozenset({S.SYNCING, S.FAILED, S.CANCELLED}),
    S.SYNCING: frozenset({S.BUILDING, S.FAILED, S.CANCELLED}),
    # SUCCEEDED as well as PLAYTESTING: a build that was not asked to start
    # a test session is finished when Studio has applied every operation.
    # Without it a sync with play=False could never reach a terminal
    # state -- it did all the work and then died on "a build cannot go
    # from building to succeeded".
    S.BUILDING: frozenset({S.PLAYTESTING, S.SUCCEEDED, S.PARTIAL, S.FAILED, S.CANCELLED}),
    S.PLAYTESTING: frozenset({S.SUCCEEDED, S.REPAIRING, S.PARTIAL, S.FAILED, S.CANCELLED}),
    S.REPAIRING: frozenset({S.GENERATING, S.PLAYTESTING, S.PARTIAL, S.FAILED, S.CANCELLED}),
    S.SUCCEEDED: frozenset({S.BLUEPRINTING, S.QUEUED}),
    S.PARTIAL: frozenset({S.BLUEPRINTING, S.QUEUED, S.REPAIRING}),
    S.FAILED: frozenset({S.BLUEPRINTING, S.QUEUED}),
    S.CANCELLED: frozenset({S.BLUEPRINTING, S.QUEUED}),
}

# Which controls make sense where. The UI asks rather than deciding for itself,
# so a control that would do nothing is never drawn (section 30).
CONTROLS: dict[S, tuple[str, ...]] = {
    S.DRAFT: ("edit_blueprint",),
    S.BLUEPRINTING: ("edit_blueprint",),
    S.READY_TO_BUILD: ("build", "edit_blueprint"),
    S.QUEUED: ("stop",),
    S.PLANNING: ("stop",),
    S.GENERATING: ("stop",),
    S.VALIDATING: ("stop",),
    S.WAITING_FOR_STUDIO: ("stop", "open_studio"),
    S.SYNCING: ("stop",),
    S.BUILDING: ("stop",),
    S.PLAYTESTING: ("stop_playtest", "view_errors"),
    S.REPAIRING: ("stop", "view_errors"),
    S.SUCCEEDED: ("play", "open_studio", "refine", "rebuild", "view_build"),
    S.PARTIAL: ("view_errors", "retry", "rollback", "refine", "open_studio"),
    S.FAILED: ("view_errors", "retry", "rollback", "edit_blueprint"),
    S.CANCELLED: ("retry", "edit_blueprint"),
}


class IllegalTransition(ValueError):
    pass


def can(current: S, following: S) -> bool:
    return following in ALLOWED.get(current, frozenset())


def check(current: S, following: S) -> S:
    """`following`, or a refusal naming what was legal instead."""
    if not can(current, following):
        legal = ", ".join(sorted(status.value for status in ALLOWED.get(current, frozenset())))
        raise IllegalTransition(
            f"a build cannot go from {current.value} to {following.value}; "
            f"from {current.value} it may go to: {legal or 'nowhere'}")
    return following


def controls_for(status: S) -> tuple[str, ...]:
    return CONTROLS.get(status, ())


def is_terminal(status: S) -> bool:
    return status in TERMINAL_STATUSES


def is_running(status: S) -> bool:
    """Whether a build is occupying the Studio bridge.

    The bridge serves one build at a time; a second build started while one is
    mid-sync would interleave operations in the same DataModel.
    """
    return status in {S.QUEUED, S.PLANNING, S.GENERATING, S.VALIDATING,
                      S.WAITING_FOR_STUDIO, S.SYNCING, S.BUILDING,
                      S.PLAYTESTING, S.REPAIRING}
