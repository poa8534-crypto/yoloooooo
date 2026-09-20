"""Ask the Engineer for the behaviour spec of every system that has none.

Twelve systems reached island-haven's master with no spec, because `tests/`
was outside the writable roots and every attempt to write one was refused as
an unsafe path. They passed five checks that read the code and one that ran
nothing. This asks for the missing half, one system at a time, through the
same provider chain and the same gate as any other work.

Nothing here is specific to those twelve: the list is whatever the project
holds without a spec beside it, so this stays useful for any system that
arrives unproven.

    python scripts/backfill_specs.py --list
    python scripts/backfill_specs.py                     # every missing spec
    python scripts/backfill_specs.py TradeService ...    # only those named

Each accepted spec is landed on the project's base branch by the same `land`
the build uses: written, the WHOLE project checked, and committed only if it
passed. A spec that fails is left on its engineer/ branch and reported -- and
a spec that fails because the SYSTEM is wrong is the point of the exercise,
so read the failure before deciding which of the two to change.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.blueprint.builds import files_on_branch  # noqa: E402
from app.blueprint.compile import compile_spec  # noqa: E402
from app.blueprint.store import BlueprintStore  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.engineer.from_spec import tasks_from  # noqa: E402
from app.engineer.land import land  # noqa: E402
from app.engineer.runs import build_gate, resolve_game_repo, run_task  # noqa: E402
from app.engineer.schemas import EngineeringTask  # noqa: E402
from app.engineer.workspace import spec_path  # noqa: E402


def in_project(repo: Path, system: str) -> str | None:
    """Where the system's source is, or None when the project has not got it."""
    for root in ("src/server", "src/shared", "src/client"):
        candidate = repo / root / f"{system}.luau"
        if candidate.is_file():
            return f"{root}/{system}.luau"
    return None


def unproven(repo: Path, tasks: list[EngineeringTask]) -> list[EngineeringTask]:
    """Systems the project holds that no spec loads, in build order."""
    missing = []
    for task in tasks:
        if in_project(repo, task.system) and not (repo / spec_path(task.system)).is_file():
            missing.append(task)
    return missing


def chosen() -> tuple[object, object]:
    """The newest blueprint that compiles, and its specification.

    Resolved exactly as handbuild.py resolves it, so the plan and the
    repository below always belong to the same game.
    """
    store = BlueprintStore(SessionLocal)
    problems: list[str] = []
    for blueprint in store.list(limit=20):
        if not blueprint.systems:
            continue
        try:
            return blueprint, compile_spec(blueprint)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            problems.append(f"{blueprint.title}: {type(exc).__name__}: {str(exc)[:160]}")
    raise SystemExit("no blueprint compiles into a specification:\n  " + "\n  ".join(problems[:5]))


async def backfill(task: EngineeringTask, settings: Settings, repo: Path) -> str:
    """One system's spec: asked for, checked, and landed or reported."""
    print(f"\n=== {task.system}")
    spec_task = task.model_copy(update={"deliverable": "spec"})
    try:
        result = await run_task(spec_task, settings=settings, factory=SessionLocal,
                                on_event=lambda entry: print(f"    {entry.get('stage')}: "
                                                             f"{str(entry.get('detail'))[:160]}"),
                                repo=repo)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        print(f"    FAILED: {str(exc)[:300]}")
        return "error"

    if result.status != "complete" or not result.branch:
        print(f"    refused after {result.attempts} attempt(s): {result.reason[:300]}")
        return "refused"

    wanted = spec_path(task.system)
    on_branch = files_on_branch(repo, result.branch)
    if wanted not in on_branch:
        print(f"    accepted but {wanted} is not on {result.branch}; nothing landed")
        return "refused"

    gate = build_gate(settings, repo)

    def project_check(root: Path) -> tuple[bool, str]:
        report = gate.run(root, task.system)
        if report.passed:
            return True, ""
        return False, "; ".join(f"{check.name}: {check.output.strip().splitlines()[0]}"
                                for check in report.failed if check.output.strip())

    landed = land(repo, {wanted: on_branch[wanted]}, verify=project_check,
                  branch=settings.game_base_branch,
                  message=(f"test({task.system}): the behaviour spec it landed without\n\n"
                           f"Written by the Engineer as a spec-only task, accepted on "
                           f"{result.branch} after {result.attempts} attempt(s)."))
    print(f"    {'landed ' + landed.commit[:12] if landed.committed else 'not landed: ' + landed.detail[:200]}")
    return "landed" if landed.committed else "refused"


def land_spec(repo: Path, settings: Settings, system: str, source: str, origin: str) -> bool:
    """One spec into the project, with the whole project checked first."""
    gate = build_gate(settings, repo)

    def project_check(root: Path) -> tuple[bool, str]:
        report = gate.run(root, system)
        if report.passed:
            return True, ""
        return False, "; ".join(f"{check.name}: {check.output.strip().splitlines()[-1]}"
                                for check in report.failed if check.output.strip())

    landed = land(repo, {spec_path(system): source}, verify=project_check,
                  branch=settings.game_base_branch,
                  message=(f"test({system}): the behaviour spec it landed without\n\n"
                           f"Written by the Engineer as a spec-only task, accepted on {origin}."))
    print(f"    {'landed ' + landed.commit[:12] if landed.committed else 'not landed: ' + landed.detail[:300]}")
    return landed.committed


def accepted_branches(repo: Path) -> list[str]:
    """The engineer/ branches this repository holds, newest last."""
    listing = subprocess.run(["git", "branch", "--list", "engineer/*", "--format=%(refname:short)"],
                             cwd=repo, capture_output=True, check=False)
    if listing.returncode != 0:
        return []
    return [line.strip() for line in listing.stdout.decode("utf-8", "replace").splitlines() if line.strip()]


def land_accepted(repo: Path, settings: Settings, wanted: set[str]) -> int:
    """Land every spec that is finished on a branch and missing from the project.

    A spec on a branch passed the six checks there; what it has not passed is
    the project as a whole, which `land` checks before it commits anything.
    """
    landed = 0
    seen: set[str] = set()
    for branch in accepted_branches(repo):
        # A branch the loop kept as evidence holds a spec that FAILED; its
        # commit says so ("refused(System): attempt N failed ..."). Checking
        # it costs a whole-project verify to learn what the commit already
        # states, so it is read rather than re-run.
        subject = subprocess.run(["git", "log", "-1", "--format=%s", branch], cwd=repo,
                                 capture_output=True, check=False)
        if subject.stdout.decode("utf-8", "replace").strip().startswith("refused("):
            continue
        for path, source in files_on_branch(repo, branch).items():
            if not path.startswith("tests/") or not path.endswith(".spec.luau"):
                continue
            system = Path(path).name.removesuffix(".spec.luau")
            if system in seen or (repo / path).is_file():
                continue
            if wanted and system.lower() not in wanted:
                continue
            seen.add(system)
            print(f"\n=== {system} (from {branch})")
            if land_spec(repo, settings, system, source, branch):
                landed += 1
    if not seen:
        print("no accepted spec is waiting on a branch")
    return landed


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("systems", nargs="*", help="only these systems (default: every missing spec)")
    parser.add_argument("--list", action="store_true", help="say what is missing and stop")
    parser.add_argument("--land-accepted", action="store_true",
                        help="land specs already accepted on an engineer/ branch, and stop")
    args = parser.parse_args(argv[1:])

    settings = Settings()
    blueprint, spec = chosen()
    if args.land_accepted:
        repo = resolve_game_repo(settings, blueprint)
        print(f"project {repo}")
        return 0 if land_accepted(repo, settings, {n.lower() for n in args.systems}) else 1
    repo = resolve_game_repo(settings, blueprint)
    # Every system in the plan, not only the ones still to build: a backfill
    # is for systems that ARE built, which the usual filter removes.
    tasks = tasks_from(spec, already_built=set(), repo=repo)
    missing = unproven(repo, tasks)
    if args.systems:
        wanted = {name.lower() for name in args.systems}
        missing = [task for task in missing if task.system.lower() in wanted]

    print(f"project {repo}")
    print(f"specification {spec.spec_id} revision {spec.revision}")
    print(f"{len(missing)} system(s) in the project with no behaviour spec:")
    for task in missing:
        print(f"  {task.system:28} {in_project(repo, task.system)}")
    if args.list or not missing:
        return 0

    counts: dict[str, int] = {}
    for task in missing:
        outcome = await backfill(task, settings, repo)
        counts[outcome] = counts.get(outcome, 0) + 1

    print("\n" + ", ".join(f"{count} {name}" for name, count in sorted(counts.items())))
    return 0 if counts.get("landed") == len(missing) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
