"""Build a system by hand, through the same gate and the same landing.

The Engineer's provider can run out. When Antigravity answers "Individual
quota reached", every remaining system is refused in about twenty seconds and
the build walks the rest of the plan producing nothing. This is the way to
carry on without it: the specification still says what each system is for, the
six checks still decide what is acceptable, and landing still refuses anything
that breaks the project.

Nothing here writes Luau. It hands over the task the Engineer would have been
given, takes back whatever was written, and puts it through the checks every
other system passed. A system built this way is landed by exactly the code
that lands a generated one -- same worktree, same gate, same project verify,
same regenerated Services module -- so the project cannot tell the difference
and neither can the next build.

    python scripts/handbuild.py status
    python scripts/handbuild.py brief --next 3
    python scripts/handbuild.py brief MiningService
    python scripts/handbuild.py open MiningService      # a worktree to write in
    python scripts/handbuild.py check MiningService     # format, then the gate
    python scripts/handbuild.py land MiningService      # onto master, verified
    python scripts/handbuild.py sync                    # into Studio, and play it

The game, the specification and the base branch all come from configuration,
so this works for whichever project GAME_PROJECT_DIR points at.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.blueprint.compile import compile_spec  # noqa: E402
from app.blueprint.schemas import GameBuildSpecification, SpecSystem  # noqa: E402
from app.blueprint.store import BlueprintStore  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.engineer.from_spec import task_for  # noqa: E402
from app.engineer.land import land  # noqa: E402
from app.engineer.runs import build_gate, game_repo  # noqa: E402
from app.engineer.cli import use_utf8  # noqa: E402
from app.engineer.workspace import Worktree, read_normalized, services_for_project  # noqa: E402

WORKTREE_MARK = "handbuilt"


def _settings():
    return get_settings()


def _spec() -> GameBuildSpecification:
    """The specification for the game this repository is pointed at.

    The newest blueprint that compiles and whose systems match the project, so
    this cannot quietly build one game's systems into another's repository.
    """
    store = BlueprintStore(SessionLocal)
    problems: list[str] = []
    for blueprint in store.list(limit=20):
        if not blueprint.systems:
            continue
        try:
            return compile_spec(blueprint)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            problems.append(f"{blueprint.title}: {type(exc).__name__}: {str(exc)[:160]}")
    raise SystemExit("no blueprint compiles into a specification:\n  "
                     + "\n  ".join(problems[:5]))


def _built(repo: Path) -> set[str]:
    """Systems the project already holds, by file name."""
    found: set[str] = set()
    for root in ("src/server", "src/client", "src/shared"):
        directory = repo / root
        if directory.is_dir():
            found.update(file.stem for file in directory.glob("*.luau"))
    return found - {"Services"}


def _slug(name: str) -> str:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name)
    return re.sub(r"[^a-z0-9-]+", "-", spaced.lower()).strip("-")


def _worktree_root(repo: Path) -> Path:
    settings = _settings()
    return settings.engineer_worktree_dir or repo.parent / f"{repo.name}-worktrees"


def _find_worktree(repo: Path, system: SpecSystem) -> Path | None:
    root = _worktree_root(repo)
    prefix = f"{_slug(system.name)}-{WORKTREE_MARK}-"
    if not root.is_dir():
        return None
    for path in sorted(root.iterdir()):
        if path.name.startswith(prefix) and path.is_dir():
            return path
    return None


def _changed_files(worktree: Path) -> list[str]:
    """Paths the worktree has that its base commit did not, or changed.

    Read from git rather than by walking the tree, so a file that was already
    in the project and left alone is not landed again as though it were part
    of this system.
    """
    from app.engineer.workspace import git

    changed: list[str] = []
    for line in git(["status", "--porcelain"], worktree).splitlines():
        entry = line.strip()
        if not entry:
            continue
        path = entry.split(maxsplit=1)[-1].strip().strip('"')
        if path.startswith(("src/", "tests/")) and path.endswith(".luau"):
            changed.append(path)
    return sorted(set(changed))


def _system(spec: GameBuildSpecification, name: str) -> SpecSystem:
    for system in spec.systems:
        if system.name.lower() == name.lower():
            return system
    raise SystemExit(f"{name} is not in this specification. "
                     f"Run `status` to see what is.")


def _remaining(spec: GameBuildSpecification, repo: Path) -> list[SpecSystem]:
    """Still to build, in the order the specification computed."""
    built = {name.lower() for name in _built(repo)}
    index = {system.name: system for system in spec.systems}
    return [index[name] for name in spec.build_order
            if name in index and name.lower() not in built]


def _blocked_by(system: SpecSystem, repo: Path) -> list[str]:
    built = {name.lower() for name in _built(repo)}
    return [need for need in system.depends_on if need.lower() not in built]


# ---- the commands ----------------------------------------------------------

def command_status(_args) -> int:
    repo = game_repo(_settings())
    spec = _spec()
    built = _built(repo)
    remaining = _remaining(spec, repo)

    print(f"project      {repo}")
    print(f"specification {spec.spec_id} revision {spec.revision}: {spec.title}")
    print(f"built        {len(built)} of {len(spec.systems)}")
    print(f"remaining    {len(remaining)}")
    print()
    for system in remaining[:15]:
        waiting = _blocked_by(system, repo)
        state = f"blocked on {', '.join(waiting)}" if waiting else "ready"
        marks = []
        if system.required_for_vertical_slice:
            marks.append("slice")
        if system.core_loop_blocker:
            marks.append("blocks loop")
        print(f"  {system.name:32} {system.layer.value:7} {system.priority_class} "
              f"{'/'.join(marks) or '-':22} {state}")
    if len(remaining) > 15:
        print(f"  ... and {len(remaining) - 15} more")
    return 0


def command_brief(args) -> int:
    repo = game_repo(_settings())
    spec = _spec()

    if args.system:
        wanted = [_system(spec, args.system)]
    else:
        ready = [s for s in _remaining(spec, repo) if not _blocked_by(s, repo)]
        wanted = ready[:args.next]
        if not wanted:
            print("nothing is ready: every remaining system is waiting on another.")
            return 1

    for system in wanted:
        task = task_for(spec, system, repo)
        print("=" * 78)
        print(f"SYSTEM   {system.name}   ({system.layer.value})")
        print(f"FILE     {system.path}")
        print("=" * 78)
        print()
        print("GOAL")
        for line in task.goal.splitlines():
            print(f"  {line}")
        print()
        print("ACCEPTANCE CRITERIA")
        for criterion in task.acceptance_criteria:
            print(f"  - {criterion}")
        print()
        print("NOTES")
        for note in task.notes:
            print(f"  - {note}")
        print()
    return 0


def command_open(args) -> int:
    settings = _settings()
    repo = game_repo(settings)
    spec = _spec()
    system = _system(spec, args.system)

    existing = _find_worktree(repo, system)
    if existing is not None:
        print(f"worktree already open: {existing}")
        print(f"write     {existing / system.path}")
        return 0

    waiting = _blocked_by(system, repo)
    if waiting and not args.force:
        raise SystemExit(
            f"{system.name} depends on {', '.join(waiting)}, which the project does not have "
            "yet. Build those first, or pass --force to write against a missing dependency.")

    name = f"{_slug(system.name)}-{WORKTREE_MARK}-{secrets.token_hex(4)}"
    worktree = Worktree.create(repo, settings.game_base_branch, _worktree_root(repo), name)
    print(f"worktree  {worktree.path}")
    print(f"branch    {worktree.branch}")
    print(f"write     {worktree.path / system.path}")
    print()
    print("siblings already in the project, to require rather than reimplement:")
    for sibling in sorted(_built(repo)):
        print(f"  {sibling}")
    return 0


def command_check(args) -> int:
    settings = _settings()
    repo = game_repo(settings)
    spec = _spec()
    system = _system(spec, args.system)

    worktree = _find_worktree(repo, system)
    if worktree is None:
        raise SystemExit(f"no worktree for {system.name}: run `open {system.name}` first")
    written = worktree / system.path
    if not written.is_file():
        raise SystemExit(f"nothing written yet at {written}")

    gate = build_gate(settings)

    # The Services module the project would need with this file in it, written
    # into the worktree before the checks run, exactly as the generated path
    # does it. Without it a system that uses a service the project's module
    # does not carry passes here and fails when it lands.
    mine = {system.path: read_normalized(written)}
    for relative, content in services_for_project(worktree, mine, gate.known_services).items():
        target = worktree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
        print(f"generated {relative}")

    # Every file that will land, not only the system's own: `land` takes all
    # of them, and a data row added to a shared module for this system was
    # refused by the format check while the system itself was formatted --
    # which is how this was found, on EvolutionService and ColonyConfig.
    targets = sorted({system.path, *(path for path in _changed_files(worktree)
                                     if not path.endswith("Services.luau"))})
    problem = gate.format(worktree, targets)
    print(f"formatted {len(targets)} file(s)" if problem is None else f"not formatted: {problem}")

    report = gate.run(worktree)
    print()
    for check in report.checks:
        print(f"  {'PASS' if check.passed else 'FAIL'}  {check.name}")
        if not check.passed:
            # The lines that say what went wrong, not the first dozen lines of
            # the report. verify.ps1 prints each check in turn and the passing
            # ones come first, so a head of the output is a list of PASSes and
            # the actual failure is below the cut -- which is exactly what it
            # showed the first time this was used.
            lines = check.output.strip().splitlines()
            interesting = [
                line for line in lines
                if re.search(r"error|fail|expected|caused by|\.luau", line, re.IGNORECASE)
            ]
            for line in (interesting or lines)[-25:]:
                print(f"        {line}")
    print()
    print("GATE PASSED" if report.passed else "GATE FAILED")
    return 0 if report.passed else 1


def command_land(args) -> int:
    settings = _settings()
    repo = game_repo(settings)
    spec = _spec()
    system = _system(spec, args.system)

    worktree = _find_worktree(repo, system)
    if worktree is None:
        raise SystemExit(f"no worktree for {system.name}: run `open {system.name}` first")
    written = worktree / system.path
    if not written.is_file():
        raise SystemExit(f"nothing written yet at {written}")

    gate = build_gate(settings)

    def project_check(root: Path) -> tuple[bool, str]:
        """Does the WHOLE project still build with this file in it?"""
        report = gate.run(root)
        if report.passed:
            return True, ""
        return False, "; ".join(
            f"{check.name}: {check.output.strip().splitlines()[0] if check.output.strip() else 'failed'}"
            for check in report.failed)

    # Everything the worktree gained, not only the system's own file. A system
    # can arrive with a behaviour spec beside it, and landing the module alone
    # would leave the check that proves it behind in a branch nobody reads.
    # The generated Services modules are excluded because they are regenerated
    # for the project below, from what the project needs rather than what this
    # worktree happened to use.
    changes = {}
    for relative in _changed_files(worktree):
        if relative.endswith("Services.luau"):
            continue
        changes[relative] = read_normalized(worktree / relative)
    if system.path not in changes:
        changes[system.path] = read_normalized(written)
    changes.update(services_for_project(repo, changes, gate.known_services))

    landed = land(repo, changes, verify=project_check, branch=settings.game_base_branch,
                  message=(f"feat({system.name}): accepted by the gate\n\n"
                           f"Written by hand against specification {spec.spec_id} revision "
                           f"{spec.revision}, after the model provider's quota ran out. "
                           f"Checked and landed by the same gate as every generated system."))

    if not landed.committed:
        print(f"NOT LANDED: {landed.detail}")
        return 1
    print(f"landed {landed.commit[:12]}: {system.name}")
    for path in sorted(changes):
        print(f"  {path}")

    if not args.keep:
        tree = Worktree(repo=repo, path=worktree, branch=f"engineer/{worktree.name}",
                        base_commit="")
        tree.remove(delete_branch=True)
        print(f"removed worktree {worktree.name}")
    return 0


def command_sync(args) -> int:
    """Send the project to the open Studio place and start a playtest.

    A build ends by sending Studio everything it has, but a project finished
    by hand has no build to do that. This sends the same batch a build does
    (`studio_batch`), then reports what Studio's own log says the place's
    scripts printed, warned and threw while the test ran.
    """
    import time
    from collections import Counter

    import httpx

    from app.blueprint.api import BRIDGE_URL
    from app.blueprint.builds import studio_batch
    from app.bridge import studio_log
    from app.bridge.from_project import read_project
    from app.bridge.pairing import read_token

    repo = game_repo(_settings())
    spec = _spec()
    token = read_token(ROOT)
    if not token:
        raise SystemExit("the bridge has no pairing token yet: start it once with "
                         "`python -m app.bridge.run`")
    headers = {"X-Bridge-Token": token}

    def get(path: str, **params) -> httpx.Response:
        return httpx.get(f"{BRIDGE_URL}{path}", headers=headers, params=params, timeout=10.0)

    try:
        status = get("/bridge/status")
    except httpx.HTTPError:
        raise SystemExit(f"no bridge at {BRIDGE_URL}: start it with "
                         "`python -m app.bridge.run`") from None
    if status.status_code == 401:
        raise SystemExit("the bridge refused the stored pairing token")
    state = status.json()
    studio = state.get("studio") or {}
    if not state.get("plugin_connected"):
        raise SystemExit("the Studio plugin is not connected: open the Venture Engineer "
                         "panel in Studio and press Connect")
    if studio.get("mode") == "run":
        raise SystemExit("Studio is already running a test: stop it, then sync")
    place = str(studio.get("place_name", ""))
    # The wrong-project trap, from this side: a sync pours the project into
    # whatever place happens to be open.
    if place.lower() != repo.name.lower() and not args.force:
        raise SystemExit(f"Studio has {place!r} open, not {repo.name!r}. Open the right "
                         "place, or pass --force if it is simply named differently.")

    log = studio_log.newest()
    offset = log.stat().st_size if log else 0

    project = read_project(repo)
    batch = studio_batch(spec, project, build_id=f"sync-{secrets.token_hex(4)}",
                         play=not args.no_play)
    queued = httpx.post(f"{BRIDGE_URL}/bridge/batches", headers=headers, timeout=30.0,
                        json=batch.model_dump(by_alias=True, mode="json"))
    if queued.status_code != 202:
        print(f"the bridge refused the batch: {queued.status_code} {queued.text[:300]}")
        return 1
    print(f"sent {len(project)} file(s) to {place!r} as {len(batch.operations)} "
          f"operation(s), batch {batch.batch_id}")

    reported: dict[str, dict] = {}
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline and not reported:
        answer = get(f"/bridge/results/{batch.batch_id}")
        if answer.status_code == 200:
            reported = {entry["operation_id"]: entry for entry in answer.json()["results"]}
        else:
            time.sleep(1.5)
    if not reported:
        print(f"Studio did not report within {args.timeout:.0f}s")
        return 1
    if not args.no_play:
        # A play mode that refuses at once is reported a second time, over the
        # first report, holding only the correction. Read it once more.
        time.sleep(3)
        again = get(f"/bridge/results/{batch.batch_id}")
        if again.status_code == 200:
            reported.update({entry["operation_id"]: entry for entry in again.json()["results"]})

    counts = Counter(entry["status"] for entry in reported.values())
    print("studio: " + ", ".join(f"{counts[name]} {name}" for name in
                                 ("applied", "skipped", "started", "failed") if counts[name]))
    for operation in batch.operations:
        entry = reported.get(operation.operation_id)
        kind = operation.operation.value
        if entry is None:
            print(f"  NO REPORT  {kind} {getattr(operation, 'path', '')}")
        elif entry["status"] == "failed" or kind in ("build_world", "start_playtest"):
            where = getattr(operation, "path", "") or getattr(operation, "mode", "")
            print(f"  {entry['status'].upper():8} {kind} {where}: {entry['detail'][:400]}")

    heard: list[studio_log.Entry] = []
    if log is not None and not args.no_play:
        print(f"\nwhat the place's scripts said in the first {args.watch:.0f}s, "
              f"from {log.name}:")
        deadline = time.monotonic() + args.watch
        while time.monotonic() < deadline:
            time.sleep(2)
            entries, offset = studio_log.read_since(log, offset)
            for entry in entries:
                print(f"  {entry.level:7} {entry.message[:500]}")
            heard.extend(entries)
    elif log is None:
        print("\nno Studio log was found, so what the test printed cannot be read here")

    levels = Counter(entry.level for entry in heard)
    if heard:
        print(f"\n{levels['error']} error(s), {levels['warning']} warning(s), "
              f"{levels['info']} other line(s)")
    return 1 if counts["failed"] or levels["error"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("status", help="what is built and what is left")

    brief = commands.add_parser("brief", help="the task the Engineer would have been given")
    brief.add_argument("system", nargs="?", help="a system name; omit for the next ready ones")
    brief.add_argument("--next", type=int, default=1, help="how many ready systems to show")

    opened = commands.add_parser("open", help="create a worktree to write the system in")
    opened.add_argument("system")
    opened.add_argument("--force", action="store_true",
                        help="open even though a dependency is missing")

    check = commands.add_parser("check", help="format the file, then run the six checks")
    check.add_argument("system")

    landing = commands.add_parser("land", help="put it into the project, if the project still builds")
    landing.add_argument("system")
    landing.add_argument("--keep", action="store_true", help="leave the worktree in place")

    sync = commands.add_parser("sync", help="send the project to Studio and start a playtest")
    sync.add_argument("--no-play", action="store_true", help="send it, but do not start a test")
    sync.add_argument("--watch", type=float, default=45.0,
                      help="seconds of the test's output to report")
    sync.add_argument("--timeout", type=float, default=90.0,
                      help="seconds to wait for Studio to report the batch")
    sync.add_argument("--force", action="store_true",
                      help="send even though the open place is named after another game")

    # The gate's own output is UTF-8: selene draws boxes, luau-lsp quotes
    # source. Printing that to a cp1252 console raises, and the traceback lands
    # exactly where the error message should have been -- which is how this was
    # found, with the real failure replaced by a UnicodeEncodeError.
    use_utf8(sys.stdout)
    use_utf8(sys.stderr)

    args = parser.parse_args(argv)
    return {
        "status": command_status, "brief": command_brief, "open": command_open,
        "check": command_check, "land": command_land, "sync": command_sync,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
