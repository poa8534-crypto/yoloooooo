"""One prompt in, a game in Studio out. Nothing steered by hand.

    audited idea -> blueprint -> architect proposes features
                 -> every suggestion accepted as proposed
                 -> architect proposes systems
                 -> specification -> Engineer writes each one
                 -> six-check gate -> landed if the project still builds
                 -> operations -> bridge -> Studio

The decisions a person would make are made by taking the architect's own
proposal unchanged. That is the point of the run: to see what the chain does
when nobody rescues it, and to record every place it needs rescuing.

Driven in-process rather than over HTTP because the dashboard needs a
credential this script has no business holding. Every function called here is
the one the endpoint calls.

    python scripts/autobuild.py --audit <audit-id> --prompt "one paragraph"
    python scripts/autobuild.py --audit <audit-id>      # reuse the saved plan

The repository it writes into is GAME_PROJECT_DIR, so building a different
game is a configuration change rather than an edit here. Given an audit whose
blueprint already carries a plan, the architect is not asked again: a second
opinion costs a second bill and builds a different game than the one that was
reviewed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.blueprint.architect import BlueprintArchitect  # noqa: E402
from app.blueprint.builds import BuildFailed, run_build  # noqa: E402
from app.blueprint.compile import compile_spec  # noqa: E402
from app.blueprint.readiness import assess  # noqa: E402
from app.blueprint.store import BlueprintStore  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.engineer.runs import (  # noqa: E402
    build_clients, build_model_call, load_design, resolve_game_repo, usage_recorder,
)

problems: list[str] = []


def line(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def problem(text: str) -> None:
    problems.append(text)
    line(f"  !! {text}")


class _AlreadyPlanned(Exception):
    """The blueprint already carries a plan, so the architect is not asked."""


def apply_revisions(blueprint, revisions: dict, marker: str = ""):
    """Append each revision's purpose and criteria to its system in the blueprint.

    Idempotent: a revision already present (by its exact text) is not added
    twice, so rerunning a revise build does not grow the specification.

    With a `marker`, a revision SUPERSEDES the last one carrying the same
    marker: purpose paragraphs and criteria containing it are removed first,
    and the new ones are written with it. Without that, moving the world a
    second time appended new coordinates beside the old ones, and the
    Engineer was handed both.
    """
    systems = []
    touched = []
    for system in blueprint.systems:
        change = revisions.get(system.name)
        if not change:
            systems.append(system)
            continue
        purpose = system.purpose
        criteria = list(system.acceptance_criteria)
        new_purpose = change.get("purpose", "")
        new_criteria = list(change.get("criteria", []))
        if marker:
            tag = f" [{marker}]"
            purpose = "\n\n".join(p for p in purpose.split("\n\n") if marker not in p)
            criteria = [c for c in criteria if marker not in c]
            new_purpose = f"{new_purpose}{tag}" if new_purpose else ""
            new_criteria = [f"{c}{tag}" for c in new_criteria]
        if new_purpose and new_purpose not in purpose:
            purpose = f"{purpose}\n\n{new_purpose}"
        for criterion in new_criteria:
            if criterion not in criteria:
                criteria.append(criterion)
        # The schema caps both; a revision that does not fit is refused here
        # rather than silently cut.
        if len(purpose) > 2000:
            raise ValueError(f"{system.name}: revised purpose is {len(purpose)} characters, over 2000")
        if len(criteria) > 20:
            raise ValueError(f"{system.name}: {len(criteria)} acceptance criteria, over 20")
        systems.append(system.model_copy(update={"purpose": purpose, "acceptance_criteria": criteria}))
        touched.append(system.name)
    missing = sorted(set(revisions) - set(touched))
    if missing:
        raise ValueError(f"the blueprint has no system named: {', '.join(missing)}")
    return blueprint.model_copy(update={"systems": systems}), touched


async def main(audit: str, prompt: str, revise_file: str = "") -> int:
    settings = get_settings()
    store = BlueprintStore(SessionLocal)
    design = load_design(SessionLocal, audit)
    title = str((design.get("proposal") or {}).get("concept_title") or "Idea")
    line(f"idea: {title}")
    line(f"prompt: {prompt[:110]}...")

    blueprint = store.for_audit(audit) or store.create(audit_id=audit, title=title)
    blueprint = store.save(blueprint.model_copy(update={"user_intent": prompt or blueprint.user_intent}))
    line(f"blueprint {blueprint.id} revision {blueprint.revision}")
    # The repository the build will resolve, not GAME_PROJECT_DIR as written:
    # the two differ whenever the setting still names the previous game.
    line(f"game repository: {resolve_game_repo(settings, blueprint)}")

    planned = bool(blueprint.systems and blueprint.player_journey and blueprint.gameplay_path)
    if planned:
        # The architect already answered for this blueprint. Asking again would
        # pay for a second opinion and then build a different game than the one
        # that was reviewed.
        line(f"plan already on the blueprint: {len(blueprint.systems)} systems, "
             "skipping the architect")

    clients = build_clients(settings, on_usage=usage_recorder(SessionLocal))
    architect = BlueprintArchitect(
        build_model_call(settings, clients),
        on_event=lambda stage, detail: line(f"  architect {stage}: {detail[:110]}"))

    try:
        if planned:
            raise _AlreadyPlanned
        line("asking the architect for features")
        suggestions, summary = await architect.suggest(
            design, prompt, blueprint.config, time.monotonic() + 900)
        for suggestion in suggestions:
            line(f"  - {suggestion.title} "
                 f"[{suggestion.implementation_complexity.value}/"
                 f"{suggestion.mvp_priority.value}]")

        # Every suggestion accepted as proposed: nobody is steering this run.
        decided = [s.model_copy(update={"selected": True}) for s in suggestions]
        blueprint = store.save(blueprint.model_copy(
            update={"suggestions": decided, "summary": summary or blueprint.summary}))
        line(f"accepted all {len(decided)} features")

        line("asking the architect what the PLAYER experiences")
        journey, gameplay = await architect.journey(
            design, blueprint, time.monotonic() + 900)
        line(f"  entry: {journey.entry_state[:90]}")
        line(f"  first action: {journey.first_action[:90]}")
        line(f"  first reward: {journey.first_reward[:60]} -> {journey.reward_destination[:60]}")
        line("  core loop: " + " -> ".join(journey.core_loop))
        for step in gameplay.nodes:
            line(f"    {step.label[:52]:54} {', '.join(step.systems)[:50]}")
        blueprint = store.save(blueprint.model_copy(
            update={"player_journey": journey, "gameplay_path": gameplay}))

        line("asking the architect for systems")
        systems, notes = await architect.systems(
            design, blueprint, time.monotonic() + 900)
        for note in notes:
            line(f"  note: {note[:110]}")
        for system in systems:
            marks = []
            if system.required_for_vertical_slice:
                marks.append("slice")
            if system.core_loop_blocker:
                marks.append("blocks loop")
            if system.builds_world:
                marks.append("builds world")
            line(f"  - {system.name:28} {system.layer.value:7} {system.priority_class} "
                 f"{'/'.join(marks) or '-'}")
        blueprint = store.save(blueprint.model_copy(update={"systems": systems}))
    except _AlreadyPlanned:
        pass
    except Exception as exc:  # noqa: BLE001 - the run exists to report
        problem(f"the architect failed: {type(exc).__name__}: {str(exc)[:300]}")
        traceback.print_exc()
        return 1
    finally:
        for _name, client in clients:
            await client.close()

    revise: set[str] = set()
    if revise_file:
        import json as _json
        loaded = _json.loads(Path(revise_file).read_text(encoding="utf-8"))
        revisions = loaded["systems"]
        try:
            revised, touched = apply_revisions(blueprint, revisions, loaded.get("marker", ""))
        except ValueError as exc:
            problem(f"the revisions do not fit the blueprint: {exc}")
            return 1
        blueprint = store.save(revised)
        revise = set(touched)
        line(f"revising {len(touched)} system(s) from {revise_file}: {', '.join(touched)}")

    readiness = assess(blueprint)
    line(f"readiness {readiness.percent}% ready={readiness.ready}")
    if not readiness.ready:
        for gate in readiness.gates:
            if not gate.passed:
                problem(f"not ready: {gate.label} -- {gate.detail}")
        return 1

    spec = compile_spec(blueprint)
    line(f"specification {spec.spec_id} revision {spec.revision} hash {spec.content_hash}")
    line("build order: " + " -> ".join(spec.build_order))

    token = (ROOT / "data" / "bridge_token.txt").read_text(encoding="utf-8").strip()
    line("starting the build (the Engineer writes each system, then Studio)")
    try:
        record = await run_build(blueprint.id, settings=settings, factory=SessionLocal,
                                 token=token, play=False, revise=revise)
    except BuildFailed as exc:
        problem(f"the build failed: {str(exc)[:300]}")
        return 1
    except Exception as exc:  # noqa: BLE001
        problem(f"the build raised: {type(exc).__name__}: {str(exc)[:300]}")
        traceback.print_exc()
        return 1

    print()
    line(f"build {record['id']}: {record['status']}")
    for event in record["events"]:
        line(f"  {event['stage']:18} {event['detail'][:110]}")

    print()
    line("systems")
    for name, outcome in (record.get("systems") or {}).items():
        landed = (outcome.get("landed") or {})
        mark = "landed" if landed.get("committed") else "NOT LANDED"
        line(f"  {name:32} {outcome['status']:8} {mark} "
             f"{(outcome.get('reason') or landed.get('detail') or '')[:80]}")
        if outcome["status"] != "built":
            problem(f"{name} was {outcome['status']}: {str(outcome.get('reason'))[:160]}")
        elif not landed.get("committed"):
            problem(f"{name} built but did not land: {str(landed.get('detail'))[:160]}")

    result = record.get("result") or {}
    print()
    if result:
        line(f"Studio: {result.get('applied')} applied, {result.get('skipped')} unchanged, "
             f"{result.get('failed_count')} failed")
        for failure in result.get("failures") or []:
            problem(f"Studio refused {failure.get('operation_id')}: "
                    f"{str(failure.get('detail'))[:160]}")
    else:
        problem("Studio did not report a result")

    print()
    line(f"PROBLEMS: {len(problems)}")
    for item in problems:
        line(f"  - {item}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a game from an audited idea.")
    parser.add_argument("--audit", required=True, help="the audited idea to build")
    parser.add_argument("--prompt", default="",
                        help="what the game is, in a paragraph; omit to reuse the saved one")
    parser.add_argument("--revise", default="",
                        help="a revisions json: those systems get new criteria and are rebuilt "
                             "even though the project already has them")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.audit, args.prompt, args.revise)))
