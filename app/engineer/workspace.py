"""Where generated code is written: a git worktree, never the game checkout.

Other writers share the game repository -- you, Studio through Rojo, and
Hermes. So a run never touches the main checkout. It gets its own worktree on
its own branch, `engineer/<run-id>`, cut from the base branch. Nothing is ever
committed to the base branch from here; merging is the gate's and a person's
decision (docs/GATING.md). The worktree is removed when the run ends.

Files are written as bytes: UTF-8 without a BOM and with LF endings. A BOM has
already broken a `--!strict` directive in this project, and CRLF breaks both
StyLua's configured line endings and the Services module's byte identity.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .luau_guard import render_services_module

SERVICES_PATH = "src/shared/Services.luau"
# The client cannot require the shared module: at run time its scripts are
# copied into Player.PlayerScripts, so the walk up to ReplicatedStorage resolves
# to a different tree than the one luau-lsp checked (see luau_guard.CLIENT_ROOT).
# It gets its own generated copy, reached sideways as `script.Parent.Services`,
# which is stable in both trees.
CLIENT_SERVICES_PATH = "src/client/Services.luau"
PROJECT_FILE = "default.project.json"
# src/client is writable, with one rule the guard enforces: a client require may
# not walk above its own folder. Inside the folder Roblox copies into
# Player.PlayerScripts the relative depth is preserved, so `script.Parent.Thing`
# is the same instance in the sourcemap and in a live game. One step further up
# is not, and the checks cannot see the difference.
WRITABLE_ROOTS = ("src/server", "src/shared", "src/client")
READABLE_ROOTS = ("src/server", "src/client", "src/shared")
# The one file outside the source roots a model may write: the behaviour spec
# for the system it is building. Until this existed the source roots were the
# whole allowance, so every attempt to write a test was refused as an unsafe
# path -- and twelve systems reached master having passed five checks that only
# read the code and one that ran nothing. The harness and the runner stay out
# of reach: they decide what a passing test means, and a model that can rewrite
# them can pass by lowering the bar.
SPEC_PATH = re.compile(r"^tests/[A-Za-z0-9_]+\.spec\.luau$")
PROTECTED_TEST_FILES = ("tests/harness.luau", "tests/runner.luau")


def spec_path(system: str) -> str:
    """Where the behaviour spec for a system belongs; the runner globs this."""
    return f"tests/{system}.spec.luau"
_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                     *(f"lpt{i}" for i in range(1, 10))}
_SERVICE_NAME = re.compile(r'^\t[A-Za-z_]\w* = game:GetService\("([A-Za-z_]\w*)"\)', re.MULTILINE)
_DATAMODEL_NAME = re.compile(r'^\tDataModel = game :: DataModel,$', re.MULTILINE)
# Inputs the checks need that git does not carry. They are generated and
# gitignored, so `git worktree add` produces a tree without them, and luau-lsp
# answers a missing definitions file with
#
#     [ERROR] Failed to read definitions file ... Extended types will not be
#             provided
#
# and then exits 0. Measured in a fresh worktree: `Services.Players:MadeUp()`
# drew no diagnostic at all, which is the one mistake the Services module
# exists to catch. `verify.ps1` now refuses when the file is absent, so without
# this copy every run would stop at preflight.
#
# `reset()` between attempts runs `git clean -fd`, which leaves ignored files
# alone, so copying once at creation is enough.
CARRIED_INTO_WORKTREE = ("globalTypes.None.d.luau", "roblox.yml")
GIT_IDENTITY = ("-c", "user.name=Roblox Engineer Agent", "-c", "user.email=engineer@roblox-venture-agents.invalid")


class UnsafePath(ValueError):
    pass


class GitError(RuntimeError):
    pass


def validate_path(path: str) -> str:
    """A model-proposed path, accepted only inside the writable source roots."""
    if not isinstance(path, str) or not path or "\\" in path or ":" in path or path.startswith("/"):
        raise UnsafePath(f"{path!r}: use a relative forward-slash path such as src/server/CombatService.luau")
    parts = PurePosixPath(path).parts
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise UnsafePath(f"{path!r}: empty, '.' and '..' path segments are not allowed")
    if path.lower() in PROTECTED_TEST_FILES:
        raise UnsafePath(f"{path!r}: the test harness and runner are not yours to change; "
                         f"write only your system's own spec")
    if not any(path.startswith(root + "/") for root in WRITABLE_ROOTS) and not SPEC_PATH.match(path):
        raise UnsafePath(f"{path!r}: files may only be written under {', '.join(WRITABLE_ROOTS)}, "
                         f"plus your system's behaviour spec at tests/<System>.spec.luau")
    if not path.endswith(".luau"):
        raise UnsafePath(f"{path!r}: generated files must be .luau")
    if path.lower() in (SERVICES_PATH.lower(), CLIENT_SERVICES_PATH.lower()):
        raise UnsafePath(f"{path!r}: the Services module is generated; request services in `services` instead")
    for part in parts:
        if not _SEGMENT.match(part) or part.endswith(".") or part.split(".")[0].lower() in _WINDOWS_RESERVED:
            raise UnsafePath(f"{path!r}: segment {part!r} is not a safe file name")
    return path


def normalize_source(text: str) -> bytes:
    text = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return (text if text.endswith("\n") else text + "\n").encode("utf-8")


def read_normalized(file: Path) -> str:
    """A file's text with the line endings writes already use.

    Git for Windows defaults `core.autocrlf` to true at system level, so a
    worktree checkout hands back CRLF for content committed with LF. Read raw,
    that showed the model CRLF sources while `.gitattributes` forces LF and
    stylua.toml declares Unix endings -- a contradiction it could not see and
    did not create. `normalize_source` already did this for writes; every read
    goes through here so the round trip cannot go asymmetric again.
    """
    text = file.read_bytes().decode("utf-8", "replace")
    return text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def services_in(source: str) -> set[str]:
    """Names the module exposes, including `DataModel`.

    `DataModel` is a cast of `game` rather than a `GetService` call, so it
    needs its own pattern. Missing it would tell the model the module lacks
    something it already has.
    """
    names = set(_SERVICE_NAME.findall(source))
    if _DATAMODEL_NAME.search(source):
        names.add("DataModel")
    return names


# Another git process holding a lock in the shared repository. Several systems
# are written at once now, and their worktrees share one .git: one run adding a
# worktree, another deleting its branch, a third committing -- and any commit
# may start a background `gc --auto` that packs refs. Git's locks make each of
# those safe, by refusing the second one outright rather than waiting. A refused
# `worktree remove` after a system was ACCEPTED would lose the system to a
# collision that had nothing to do with it, so a lock refusal is tried again,
# briefly. Nothing else is: every other failure is an answer.
LOCKED = re.compile(r"\.lock'?:? File exists|cannot lock ref|unable to create '[^']*\.lock'"
                    r"|another git process seems to be running", re.IGNORECASE)
LOCK_RETRY_DELAYS = (0.25, 0.5, 1.0, 2.0)


def run_git(args: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    """`git`, tried again while the only complaint is a lock another holds."""
    for delay in (*LOCK_RETRY_DELAYS, None):
        completed = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                                   timeout=timeout, check=False)
        output = (completed.stdout + completed.stderr).decode("utf-8", "replace")
        if completed.returncode == 0 or delay is None or not LOCKED.search(output):
            return completed
        time.sleep(delay)
    raise AssertionError("unreachable")


def git(args: list[str], cwd: Path, timeout: float = 120.0) -> str:
    try:
        completed = run_git(args, cwd, timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {' '.join(args[:2])}: {exc}") from None
    output = (completed.stdout + completed.stderr).decode("utf-8", "replace").strip()
    if completed.returncode != 0:
        raise GitError(f"git {' '.join(args[:2])} failed: {output[-800:]}")
    return completed.stdout.decode("utf-8", "replace").strip()


@dataclass
class Worktree:
    repo: Path
    path: Path
    branch: str
    base_commit: str

    @classmethod
    def create(cls, repo: Path, base_branch: str, root: Path, name: str) -> Worktree:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,80}", name):
            raise ValueError(f"worktree name {name!r} is not a safe slug")
        base_commit = git(["rev-parse", "--verify", f"refs/heads/{base_branch}^{{commit}}"], repo)
        root.mkdir(parents=True, exist_ok=True)
        path = root / name
        branch = f"engineer/{name}"
        git(["worktree", "add", "-b", branch, str(path), base_commit], repo)
        for carried in CARRIED_INTO_WORKTREE:
            source = repo / carried
            if source.is_file():
                shutil.copy2(source, path / carried)
        # Git does not track an empty directory, so a project with no server
        # code yet arrives in the worktree without `src/server` at all -- and
        # Rojo cannot resolve a `$path` that is not there:
        #
        #   Rojo project referred to a file using $path that could not be
        #   turned into a Roblox Instance by Rojo.  File $path: src/server
        #
        # The sourcemap fails, luau-lsp cannot run without one, and every
        # system of the build is refused with "the untouched project already
        # fails its checks". Which is true, and says nothing about the reason.
        # It is exactly the state every NEW game starts in, so the first build
        # of a project could never succeed.
        for source_root in WRITABLE_ROOTS:
            (path / source_root).mkdir(parents=True, exist_ok=True)
        return cls(repo=repo, path=path, branch=branch, base_commit=base_commit)

    def reset(self) -> None:
        """Back to the base commit, so each attempt starts from the same tree."""
        git(["reset", "--hard", self.base_commit], self.path)
        git(["clean", "-fdq"], self.path)

    def read_existing_sources(self) -> dict[str, str]:
        """What the model is shown of the tree it is extending."""
        files: dict[str, str] = {}
        for root in READABLE_ROOTS:
            directory = self.path / root
            if directory.is_dir():
                for file in sorted(directory.rglob("*.luau")):
                    files[file.relative_to(self.path).as_posix()] = read_normalized(file)
        return files

    def project_file(self) -> str:
        file = self.path / PROJECT_FILE
        return read_normalized(file) if file.is_file() else ""

    def existing_services(self) -> set[str]:
        module = self.path / SERVICES_PATH
        return services_in(read_normalized(module)) if module.is_file() else set()

    def write(self, files: dict[str, str], services: set[str]) -> list[str]:
        written: list[str] = []
        for relative, content in files.items():
            target = self.path / validate_path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(normalize_source(content))
            written.append(relative)
        rendered = render_services_module(services).encode("utf-8")
        generated = [SERVICES_PATH]
        # The client gets its own copy whenever there is client code to read it.
        # Byte-identical to the shared one, and checked the same way, so there is
        # one generator and not two standards.
        if any(path.startswith("src/client/") for path in written):
            generated.append(CLIENT_SERVICES_PATH)
        for relative in generated:
            module = self.path / relative
            module.parent.mkdir(parents=True, exist_ok=True)
            module.write_bytes(rendered)
        return [*written, *generated]

    def commit(self, paths: list[str], message: str, *, allow_empty: bool = False) -> str:
        git(["add", "--", *paths], self.path)
        git([*GIT_IDENTITY, "commit", "-q", *(["--allow-empty"] if allow_empty else []), "-m", message], self.path)
        return git(["rev-parse", "HEAD"], self.path)

    def remove(self, *, delete_branch: bool) -> None:
        """Drop the worktree; the branch survives only if it holds accepted work."""
        try:
            git(["worktree", "remove", "--force", str(self.path)], self.repo)
        except GitError:
            shutil.rmtree(self.path, ignore_errors=True)
            git(["worktree", "prune"], self.repo)
        if delete_branch:
            git(["branch", "-D", self.branch], self.repo)


_SERVICES_USE = re.compile(r"\bServices\.([A-Z][A-Za-z0-9]*)")


def services_used(sources: Iterable[str]) -> set[str]:
    """Every `Services.X` a body of Luau reaches for.

    Read from the source rather than taken from what a run reported, because
    the source is the thing that has to type-check.
    """
    names: set[str] = set()
    for source in sources:
        names.update(_SERVICES_USE.findall(source))
    return names


def services_for_project(repo: Path, incoming: dict[str, str],
                         known: set[str]) -> dict[str, str]:
    """The Services modules the project needs once `incoming` is in it.

    This is what made landing impossible. Each run regenerates Services in its
    own worktree with exactly the names that run needed, and the project's copy
    is whatever the last landed system happened to require. So a system that
    used CollectionService passed its own gate and then failed in the project
    with "Key 'CollectionService' not found" -- four of six systems were held
    back by that alone, none of them for anything wrong with the system.

    The module is regenerated from the union of what the project's own sources
    use and what the incoming ones use, so it grows to fit and never shrinks
    away from a system already landed.
    """
    sources = []
    for root in WRITABLE_ROOTS:
        directory = repo / root
        if not directory.is_dir():
            continue
        for file in sorted(directory.rglob("*.luau")):
            relative = file.relative_to(repo).as_posix()
            if relative in (SERVICES_PATH, CLIENT_SERVICES_PATH):
                continue
            sources.append(incoming.get(relative) or read_normalized(file))
    sources.extend(incoming.values())

    wanted = (services_used(sources) & known) | {"DataModel"}
    rendered = render_services_module(wanted)

    modules = {SERVICES_PATH: rendered}
    # The client gets its own copy whenever there is client code to read it --
    # byte-identical, one generator, one standard.
    has_client = (repo / "src" / "client").is_dir() or any(
        path.startswith("src/client/") for path in incoming)
    if has_client:
        modules[CLIENT_SERVICES_PATH] = rendered
    return modules
