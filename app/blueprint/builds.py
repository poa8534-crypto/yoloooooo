"""One build: specification in, running Roblox place out.

The pieces all existed and nothing drove them. This is the driver:

    compile the spec        -> refuses unless the blueprint is ready
    tasks_from(spec)        -> one EngineeringTask per system, in build order
    run_task(...)           -> the Engineer's own loop and six-check gate
    files from the branch   -> what the gate accepted, not what a model claimed
    operations_for(...)     -> typed operations
    bridge                  -> the plugin -> Studio

Two decisions worth stating, because both cost something.

A system that fails the gate does NOT stop the build. It is recorded, the rest
carry on, and the build ends PARTIAL rather than FAILED -- four working systems
and one honest refusal is a better outcome than nothing, and the refusal is
reported rather than buried.

Files are read from the accepted commit rather than from the worktree, which is
deleted when a run ends. What reaches Studio is therefore exactly what passed
six checks, and there is no path by which unverified code gets there.

Every event is a real transition. Nothing here writes a log line for the look
of it.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from ..bridge.from_project import ROOTS, Unmappable, operations_for
from ..db import lock_home
from ..engineer.from_spec import SpecUnusable, tasks_from
from ..engineer.catalog import load_services
from ..engineer.land import land
from ..engineer.runs import build_gate, game_repo, provider_limits, resolve_game_repo, run_task
from ..engineer.schedule import in_dependency_order
from ..engineer.workspace import SPEC_PATH, run_git, services_for_project, spec_path
from ..models import SystemState
from ..ownership import Held, is_held, whoami
from .compile import NotReady, compile_spec
from .schemas import Blueprint, BuildStatus, GameBuildSpecification
from .store import BlueprintStore
from .steering import carried, notes_for
from .transitions import IllegalTransition, check, is_running

PREFIX = "build:"


def systems_in_project(repo: Path | None) -> set[str]:
    """The system names the game repository holds, read from the checkout.

    Measured from disk rather than from build records, so a system is "in the
    project" however it got there: this build, an earlier one, a hand-build or
    a salvage. The Engineering page draws from this, which is why a project
    with thirty-three systems on master stopped reading as "0 of 33 built".
    """
    if repo is None or not repo.exists():
        return set()
    found: set[str] = set()
    for root in ROOTS:
        directory = repo / root
        if directory.is_dir():
            found.update(path.stem for path in directory.glob("*.luau") if path.stem != "Services")
    return found


def new_id() -> str:
    return f"build-{secrets.token_hex(6)}"


def studio_batch(spec: GameBuildSpecification, project: dict[str, str], *, build_id: str,
                 play: bool):
    """The batch that puts a project into Studio, started the way its
    specification says.

    One function for a build and for syncing a project built by hand, so the
    two cannot send Studio different things.
    """
    # The one system the specification marked as building the place, so
    # Studio shows it in edit mode rather than only under Play.
    world = next((system.path for system in spec.systems if system.builds_world), None)
    return operations_for(project, build_id=build_id, play=play, world_builder=world,
                          order=spec.build_order)


def files_on_branch(repo: Path, branch: str) -> dict[str, str]:
    """The .luau files as they are on a branch the gate accepted.

    Read from the commit rather than from a worktree: the worktree is removed
    when the run ends, and reading it would race that removal. More to the
    point, the commit is the thing six checks were run against.
    """
    listing = subprocess.run(["git", "ls-tree", "-r", "--name-only", branch],
                             cwd=repo, capture_output=True, check=False)
    if listing.returncode != 0:
        return {}
    found: dict[str, str] = {}
    for path in listing.stdout.decode("utf-8", "replace").splitlines():
        if not path.endswith(".luau"):
            continue
        # The source roots, plus the behaviour specs. A spec lives outside src
        # because it never ships to Roblox, and keeping only src meant a spec
        # that had passed every check was invisible to the code that lands it:
        # "accepted but tests/ItemCatalog.spec.luau is not on ...; nothing
        # landed", on a branch that was holding the file all along.
        if not any(path.startswith(root + "/") for root in ROOTS) and not SPEC_PATH.match(path):
            continue
        blob = subprocess.run(["git", "show", f"{branch}:{path}"], cwd=repo,
                              capture_output=True, check=False)
        if blob.returncode == 0:
            text = blob.stdout.decode("utf-8", "replace")
            found[path] = text.removeprefix("﻿").replace("\r\n", "\n")
    return found


def _first_problem(output: str) -> str:
    """The first line of a check's output that names a problem.

    A gate report carries every passing line too, and the build log needs the
    one sentence a person can act on.
    """
    for line in output.splitlines():
        stripped = line.strip()
        if any(mark in stripped for mark in
               ("TypeError", "SyntaxError", "error[", "warning[", "FAIL")):
            return stripped[:300]
    return output.strip().splitlines()[0][:300] if output.strip() else "no detail"


class BuildRecord:
    """A build as it is stored and streamed. Status moves only through the
    state machine, so an impossible state cannot be written even by mistake."""

    def __init__(self, factory, build_id: str):
        self.factory = factory
        self.id = build_id

    @property
    def key(self) -> str:
        return PREFIX + self.id

    def create(self, blueprint: Blueprint, spec: GameBuildSpecification,
               owner: dict | None = None) -> dict:
        record = {
            "id": self.id, "blueprint_id": blueprint.id, "project_id": blueprint.project_id,
            "spec_id": spec.spec_id, "spec_revision": spec.revision,
            "content_hash": spec.content_hash, "title": blueprint.title,
            "status": BuildStatus.QUEUED.value, "events": [],
            "systems": {}, "batch_id": None, "operations": 0,
            "created_at": datetime.now(UTC).isoformat(), "completed_at": None,
            # The process running it and the lock it holds while it does.
            "owner": owner,
        }
        with self.factory() as db:
            db.add(SystemState(key=self.key, value_json=record))
            db.commit()
        return record

    def read(self) -> dict:
        with self.factory() as db:
            row = db.get(SystemState, self.key)
        if row is None:
            raise KeyError(self.id)
        return dict(row.value_json)

    def update(self, **changes) -> dict:
        with self.factory() as db:
            row = db.get(SystemState, self.key)
            record = {**row.value_json, **changes}
            row.value_json = record
            row.updated_at = datetime.now(UTC)
            db.commit()
        return record

    def mutate(self, change) -> dict:
        """Read, change and write inside one session.

        `update` computes its new value outside the session, which is fine
        while one writer owns a field. Steering broke that: the person adds a
        directive from the API at the same moment the build loop marks another
        as carried, and whichever read first wins with a value computed before
        the other existed. `change` is given the record as it is at write time.
        """
        with self.factory() as db:
            row = db.get(SystemState, self.key)
            if row is None:
                raise KeyError(self.id)
            record = {**row.value_json, **change(dict(row.value_json))}
            row.value_json = record
            row.updated_at = datetime.now(UTC)
            db.commit()
        return record

    def move(self, status: BuildStatus, detail: str = "") -> dict:
        current = BuildStatus(self.read()["status"])
        check(current, status)
        return self.event(status.value, detail or status.value, status=status.value)

    def event(self, stage: str, detail: str, **extra) -> dict:
        record = self.read()
        entry = {"at": datetime.now(UTC).isoformat(), "stage": stage, "detail": detail[:2000]}
        return self.update(events=[*record["events"], entry], **extra)


class BuildFailed(RuntimeError):
    pass


async def run_build(blueprint_id: str, *, settings, factory, token: str,
                    play: bool = True, build_id: str | None = None) -> dict:
    """Generate the specification's systems, then put them into Studio.

    Long-running on purpose -- the Engineer takes minutes per system -- so the
    caller starts it in the background and follows the event stream.

    The build's lock is held for exactly as long as this runs, and the record
    names it, so whoever reads the record can ask whether the build is still
    running instead of believing what it last said. The lock is taken before
    the record exists, so there is no moment when the record says the build is
    running and nothing holds it.
    """
    build_id = build_id or new_id()
    lock = Held(lock_home(factory) / f"{build_id}.lock")
    if not lock.acquire():
        raise BuildFailed(f"{build_id} is already being run by another process")
    owner = {**whoami(), "lock": str(lock.path)}
    try:
        return await _build(blueprint_id, build_id, settings=settings, factory=factory,
                            token=token, play=play, owner=owner)
    except BaseException as exc:
        _record_stop(factory, build_id, owner, exc)
        raise
    finally:
        lock.release()


def _record_stop(factory, build_id: str, owner: dict, exc: BaseException) -> None:
    """Say why a build stopped early, when the code that stopped it did not.

    BuildFailed has already moved the build to FAILED with its reason, so a
    build that is no longer running is left alone. Anything else that escaped
    used to leave the record claiming the build was in progress: two of the
    five found that way stopped exactly where a transition the state machine
    then refused would have raised, inside a dashboard that went on serving.

    Only a record this run created is touched. One that already existed under
    the same id belongs to whoever made it.
    """
    try:
        record = BuildRecord(factory, build_id)
        stored = record.read()
        if stored.get("owner") != owner:
            return
        if not is_running(BuildStatus(stored["status"])):
            # Already said why -- BuildFailed moves the build to FAILED with
            # its reason -- but not when. A failed build with no end time was
            # drawn with an elapsed time still counting, hours after it
            # stopped.
            if not stored.get("completed_at"):
                record.update(completed_at=datetime.now(UTC).isoformat())
            return
        if isinstance(exc, Exception):
            record.move(BuildStatus.FAILED,
                        f"stopped by an error: {type(exc).__name__}: {exc}")
        else:
            record.move(BuildStatus.INTERRUPTED,
                        f"stopped before it finished ({type(exc).__name__}): the process "
                        "running it was shutting down, or its task was cancelled")
        record.update(completed_at=datetime.now(UTC).isoformat())
    except Exception:  # noqa: BLE001 - never mask the exception that stopped the build
        pass


async def _build(blueprint_id: str, build_id: str, *, settings, factory, token: str,
                 play: bool, owner: dict) -> dict:
    import httpx

    from .api import BRIDGE_URL

    store = BlueprintStore(factory)
    blueprint = store.get(blueprint_id)
    try:
        spec = compile_spec(blueprint)
    except NotReady as exc:
        raise BuildFailed(str(exc)) from None

    record = BuildRecord(factory, build_id)
    record.create(blueprint, spec, owner=owner)
    record.event("queued", f"{len(spec.systems)} system(s) in the specification")

    repo = resolve_game_repo(settings, blueprint, repo_fn=game_repo)
    others = other_games(repo, blueprint.id, factory)
    if others:
        reason = another_games_repo(repo, blueprint.title, others)
        record.move(BuildStatus.FAILED, reason)
        raise BuildFailed(reason)
    existing = systems_in_project(repo)

    record.move(BuildStatus.PLANNING, "working out what still has to be built")
    try:
        tasks = tasks_from(spec, already_built=existing, repo=repo)
    except SpecUnusable as exc:
        # Everything already exists: not a failure, there is simply nothing to
        # generate, and the sync below still puts it into Studio.
        record.event("planning", str(exc))
        tasks = []

    # Which systems this build will NOT write because the project already has
    # them. Without this the graph has to guess, and it guessed wrong: it
    # assumed the Engineer works through spec.build_order, so a skipped system
    # showed as "being written now" for as long as the build ran.
    planned = {task.system for task in tasks}
    skipped = [name for name in spec.build_order if name not in planned]
    record.update(skipped=skipped)
    if skipped:
        record.event("planning",
                     f"already in the project, not rewritten: {', '.join(skipped)}")

    generated: dict[str, str] = {}
    systems: dict[str, dict] = {}

    try:
        known_services = load_services(settings.roblox_services_file)
    except Exception:  # noqa: BLE001 - no catalog means no regeneration, not a broken one
        known_services = set()

    def project_check(root: Path) -> tuple[bool, str]:
        """Does the WHOLE project still build?

        Each system was checked alone, in its own worktree, with its own
        regenerated Services module and none of its siblings. That check cannot
        see a system calling a function another does not export, requiring one
        the gate refused, or using a service the project's Services module does
        not carry -- and all three happened on one real build. So the question
        asked before keeping a file is about the project, not the file.
        """
        report = build_gate(settings, repo).run(root)
        if report.passed:
            return True, ""
        # `failed` is a property, not a method. Calling it raised inside the
        # verifier, which `land` reports as "the project check could not run"
        # -- so every system that genuinely broke the project was skipped for
        # the wrong reason, and the real one was never printed.
        return False, "; ".join(
            f"{check.name}: {_first_problem(check.output)}" for check in report.failed)

    # One landing at a time. Landing writes the candidate files into the
    # project's own checkout and checks the WHOLE project there, so two at once
    # would each be checking the other's half-written files. Measured on ascent
    # at 2.5-2.9 s a system, so queueing for it costs next to nothing.
    landing = asyncio.Lock()
    # Shared by every system in flight, so a provider's limit holds for the
    # build rather than for each system separately.
    slots = {name: asyncio.Semaphore(count) for name, count in provider_limits(settings).items()}
    by_name = {task.system: task for task in tasks}

    async def write(name: str) -> None:
        """One system: asked for, checked, and landed or refused."""
        task = by_name[name]
        # Read now, not when the plan was made: a directive typed a minute ago
        # has to reach the system being asked for a minute later, and the plan
        # was built before it existed.
        steering = notes_for(record.read(), task.system)
        if steering:
            task = task.model_copy(update={"notes": [*task.notes, *steering][:20]})
            record.mutate(lambda current, name=task.system:
                          {"directives": carried(current, name)})
            record.event("steering",
                         f"{task.system}: {len(steering)} directive(s) carried into the prompt")
        record.event("generating", f"{task.system}: asking the Engineer")
        try:
            result = await run_task(task, settings=settings, factory=factory, slots=slots,
                                    repo=repo)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            systems[task.system] = {"status": "error", "detail": str(exc)[:500]}
            record.event("system_failed", f"{task.system}: {str(exc)[:300]}",
                         systems=systems)
            return

        if result.status == "complete" and result.branch:
            files = await asyncio.to_thread(files_on_branch, repo, result.branch)
            # Only the file this task was for. A run's branch also carries the
            # generated Services module and whatever was already on the base
            # branch, and sending those as this system's work would misreport
            # what was built.
            # The system's own file AND its behaviour spec. Matching on the
            # stem alone kept `src/server/X.luau` and dropped
            # `tests/X.spec.luau`, whose stem is "X.spec" -- so a spec would
            # have been written, checked, committed to the branch and then
            # never landed, leaving the project as unproven as before.
            mine: dict[str, str] = {}
            for path, source in files.items():
                if Path(path).stem == task.system or path == spec_path(task.system):
                    mine[path] = source
            generated.update(mine)
            systems[task.system] = {"status": "built", "branch": result.branch,
                                    "commit": result.commit, "attempts": result.attempts}
            record.event("system_built", f"{task.system}: accepted on {result.branch}",
                         systems=systems)

            # Into the project, not just onto a branch. Without this the next
            # build of the same specification sees the system as missing and
            # writes it again: thirty-three engineer/ branches had accumulated
            # that way, holding work the project never got. The system AND the
            # Services module it needs. Each run regenerates Services in its own
            # worktree for exactly what it used, so landing the system alone
            # leaves the project with whatever the last landed system happened
            # to require -- four of six systems were held back by "Key
            # 'CollectionService' not found", none of them for anything wrong
            # with the system. Worked out inside the lock, because it reads the
            # project as the landing before this one left it.
            async with landing:
                changes = dict(mine)
                if known_services:
                    changes.update(services_for_project(repo, mine, known_services))
                landed = await asyncio.to_thread(
                    land, repo, changes, verify=project_check, message=(
                        f"feat({task.system}): accepted by the gate\n\n"
                        f"Generated for build {record.id} from specification "
                        f"{spec.spec_id} revision {spec.revision}, accepted on "
                        f"{result.branch} after {result.attempts} attempt(s)."))
            systems[task.system]["landed"] = landed.as_dict()
            record.event(
                "landed" if landed.committed else "not_landed",
                f"{task.system}: " + (f"committed {landed.commit[:12]} to the project"
                                      if landed.committed else landed.detail),
                systems=systems)
        else:
            # Recorded, and the build carries on. Four working systems and one
            # honest refusal beats nothing.
            systems[task.system] = {"status": "refused", "reason": result.reason[:500],
                                    "branch": result.branch, "attempts": result.attempts}
            record.event("system_refused", f"{task.system}: {result.reason[:300]}",
                         systems=systems)

    if tasks:
        at_once = settings.engineer_parallel_systems
        record.move(BuildStatus.GENERATING, f"{len(tasks)} system(s) to write"
                    + (f", up to {at_once} at a time" if at_once > 1 else ""))
        # A system starts once everything it depends on has finished -- landed
        # or refused -- because its worktree is cut from the project as it
        # stands then, and that is how it sees what its dependencies export.
        await in_dependency_order(
            list(by_name), {system.name: system.depends_on for system in spec.systems},
            at_once, write)

    record.move(BuildStatus.VALIDATING, "reading what the gate accepted")
    from ..bridge.from_project import read_project

    # What is on the base branch already, plus what this build just generated.
    project = {**read_project(repo), **generated}
    if not project:
        record.move(BuildStatus.FAILED, "nothing was generated and the project is empty")
        raise BuildFailed("there is nothing to build: no system was accepted and the "
                          "project has no Luau files")

    record.move(BuildStatus.WAITING_FOR_STUDIO, "checking Studio is connected")
    try:
        status = httpx.get(f"{BRIDGE_URL}/bridge/status", timeout=5.0,
                           headers={"X-Bridge-Token": token})
    except httpx.HTTPError:
        record.move(BuildStatus.FAILED, "the local bridge is not running")
        raise BuildFailed("The local bridge is not running. Start it with: "
                          "python -m app.bridge.run") from None
    if status.status_code == 401:
        record.move(BuildStatus.FAILED, "the bridge refused the token")
        raise BuildFailed("The bridge refused that token.")
    if not status.json().get("plugin_connected"):
        record.move(BuildStatus.FAILED, "the Studio plugin is not connected")
        raise BuildFailed("The Studio plugin is not connected, so nothing would apply the build.")

    record.move(BuildStatus.SYNCING, f"{len(project)} file(s) to send")
    try:
        batch = studio_batch(spec, project, build_id=record.id, play=play)
    except Unmappable as exc:
        record.move(BuildStatus.FAILED, str(exc))
        raise BuildFailed(str(exc)) from None

    queued = httpx.post(f"{BRIDGE_URL}/bridge/batches", timeout=30.0,
                        headers={"X-Bridge-Token": token},
                        json=batch.model_dump(by_alias=True, mode="json"))
    if queued.status_code != 202:
        record.move(BuildStatus.FAILED, f"the bridge refused the batch: {queued.text[:200]}")
        raise BuildFailed(f"the bridge refused the batch: {queued.text[:300]}")

    record.event("syncing", f"{len(batch.operations)} operation(s) queued for Studio",
                 batch_id=batch.batch_id, operations=len(batch.operations))

    record.move(BuildStatus.BUILDING, "Studio is applying the operations")
    applied = await _await_result(record, batch.batch_id, token)

    refused = [name for name, entry in systems.items() if entry["status"] != "built"]
    if applied is None:
        record.move(BuildStatus.PARTIAL, "Studio did not report before the timeout")
    elif applied.get("failed_count"):
        record.move(BuildStatus.PARTIAL,
                    f"{applied['failed_count']} operation(s) failed in Studio")
    elif refused:
        record.move(BuildStatus.PARTIAL,
                    f"built, but {len(refused)} system(s) were refused: {', '.join(refused)}")
    elif play:
        record.move(BuildStatus.PLAYTESTING, "Studio was asked to start a test session")
        record.move(BuildStatus.SUCCEEDED, "the build is in Studio")
    else:
        record.move(BuildStatus.SUCCEEDED, "the build is in Studio")

    return record.update(completed_at=datetime.now(UTC).isoformat())


async def _await_result(record: BuildRecord, batch_id: str, token: str,
                        attempts: int = 40, pause: float = 1.5) -> dict | None:
    """What the plugin reported, or None if it never did.

    Bounded: a plugin that has gone away must not hold a build open forever,
    and "did not report" is a different outcome from "reported a failure".
    """
    import httpx

    from .api import BRIDGE_URL

    for _ in range(attempts):
        await asyncio.sleep(pause)
        try:
            answer = httpx.get(f"{BRIDGE_URL}/bridge/results/{batch_id}", timeout=5.0,
                               headers={"X-Bridge-Token": token})
        except httpx.HTTPError:
            continue
        if answer.status_code != 200:
            continue
        result = answer.json()
        failed = [entry for entry in result["results"] if entry["status"] == "failed"]
        summary = {
            "applied": len([e for e in result["results"] if e["status"] == "applied"]),
            "skipped": len([e for e in result["results"] if e["status"] == "skipped"]),
            "failed_count": len(failed),
            "failures": [{"operation_id": e["operation_id"], "detail": e["detail"]}
                         for e in failed][:20],
        }
        record.event("studio_result",
                     f"{summary['applied']} applied, {summary['skipped']} unchanged, "
                     f"{summary['failed_count']} failed",
                     result=summary)
        return summary
    return None


_LANDED_BY = re.compile(r"Generated for build (build-[0-9a-f]+)")


def other_games(repo: Path, blueprint_id: str, factory) -> dict[str, str]:
    """The blueprints other than this one whose systems `repo` already holds.

    One repository is one game. Which repository a build writes into is one
    line in .env, and left pointing at the last game it sends a new game's
    systems in among the old one's, lands them on its master and pours the
    mixture into Studio. Every landed system's commit names the build that
    made it, and every build names its blueprint, so the repository can say
    whose it is -- which beats remembering to edit a file.

    A build this ledger has no record of cannot be attributed and is not
    counted: this refuses only what it can show.
    """
    completed = run_git(["log", "--format=%B", "--grep=Generated for build"], repo, 30.0)
    if completed.returncode != 0:
        return {}  # no commits yet, so nothing landed yet
    others: dict[str, str] = {}
    for build_id in sorted(set(_LANDED_BY.findall(completed.stdout.decode("utf-8", "replace")))):
        try:
            record = BuildRecord(factory, build_id).read()
        except KeyError:
            continue
        owner = record.get("blueprint_id")
        if owner and owner != blueprint_id:
            others[owner] = record.get("title") or owner
    return others


def another_games_repo(repo: Path, title: str, others: dict[str, str]) -> str:
    return (f"{repo} already holds the systems of {', '.join(sorted(set(others.values())))}. "
            f"One repository is one game: building {title} into it would land its systems "
            "among those and send both to Studio. Make it a repository of its own "
            "(python scripts/new_game.py <name>) and point GAME_PROJECT_DIR at that.")


def settled(factory, record: dict) -> dict:
    """The record -- or, if it says it is running and nothing is running it,
    the record after it has been made to say so.

    A record holds what its process last wrote, and a killed process writes
    nothing more. Five builds went on claiming to be generating, planning or
    building for up to seventeen hours, and the dashboard drew each one as
    work in progress. Whether anything still runs a build is asked of its lock, which
    the operating system releases when the holder dies, however it dies.

    Asked only of a lock on this machine, and only for a record that names
    one. A record written before builds named their lock cannot be asked, and
    is left saying what it says rather than guessed about.
    """
    try:
        running = is_running(BuildStatus(record.get("status")))
    except ValueError:
        return record
    owner = record.get("owner") or {}
    if (not running or not owner.get("lock")
            or owner.get("host") != socket.gethostname() or is_held(owner["lock"])):
        return record

    # Nothing holds it. The owner writes its last status before it lets go, so
    # read again: it may have finished between the first read and the question.
    build = BuildRecord(factory, record["id"])
    current = build.read()
    if not is_running(BuildStatus(current["status"])):
        return current
    events = current.get("events") or []
    last = events[-1] if events else {}
    try:
        build.move(BuildStatus.INTERRUPTED, (
            f"nothing is running this build any more: process {owner.get('pid')} stopped "
            f"without recording why. The last thing it recorded was "
            f"\"{str(last.get('detail', ''))[:200]}\" at {str(last.get('at', ''))[:19]}"))
    except IllegalTransition:
        return build.read()  # another reader settled it first
    # When it stopped is not known, only that it had stopped by now. The last
    # thing it recorded is the latest moment it is known to have been working;
    # now would add every hour it sat unnoticed to how long it took.
    return build.update(completed_at=last.get("at") or current.get("created_at"))


def read_build(factory, build_id: str) -> dict:
    return settled(factory, BuildRecord(factory, build_id).read())


def list_builds(factory, blueprint_id: str = "", limit: int = 20) -> list[dict]:
    with factory() as db:
        rows = [row for row in db.query(SystemState).all() if row.key.startswith(PREFIX)]
        stored = [dict(row.value_json) for row in rows]
    records = [settled(factory, record) for record in stored]
    if blueprint_id:
        records = [record for record in records if record.get("blueprint_id") == blueprint_id]
    records.sort(key=lambda record: record["created_at"], reverse=True)
    return records[:limit]
