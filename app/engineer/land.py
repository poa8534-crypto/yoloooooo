"""Putting an accepted system into the project.

The gap this closes: the Engineer wrote a system, six checks accepted it, the
files went to Studio -- and the repository never gained them. Every accepted
system lived on its own `engineer/*` branch and nowhere else, so the next build
of the same specification saw it as missing and generated it again. Thirty-three
such branches had piled up before anyone counted.

Three things make this narrower than it first looks.

FILES, NOT BRANCHES. A run's branch carries more than the system it was for: the
regenerated Services module, and whatever the worktree happened to start from.
One accepted branch here carried a ninety-one line `InfectedService.luau` from a
system the gate had REFUSED. Merging the branch would have put refused code on
master under the banner of an accepted build.

ACCEPTED ALONE IS NOT ACCEPTED TOGETHER. Each system is checked in its own
worktree, against its own regenerated Services module and without its siblings.
That check cannot see a system calling a function another system does not
export, or requiring one the gate refused, or using a service the project's
Services module does not carry. Landing six systems that had each passed alone
turned the project red with exactly those four errors. So a system is written
into the project, the WHOLE project is checked, and it is kept only if the
project still passes. What is being asserted by a commit here is not "this file
passed on its own" but "the project builds with this file in it".

NEVER OVER SOMEONE'S WORK. The repository is a real checkout that a person may
have open, that Rojo may be serving, and that may be mid-edit. So this refuses
rather than forces: wrong branch, detached head, or a local modification to a
file it was about to write, and it does nothing and says why. A build that
declines to land is a build with an honest note in its log; a build that
overwrites an edit is a lost afternoon.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .workspace import GIT_IDENTITY, run_git

TIMEOUT = 30.0

# Given the repository, answers (does the project still build, what went wrong).
Verify = Callable[[Path], tuple[bool, str]]


@dataclass
class Landed:
    """What happened, in terms the build log can repeat."""

    committed: bool
    commit: str = ""
    paths: tuple[str, ...] = ()
    detail: str = ""

    def as_dict(self) -> dict:
        return {"committed": self.committed, "commit": self.commit,
                "paths": list(self.paths), "detail": self.detail}


def _git(repo: Path, *args: str) -> tuple[int, str]:
    # Tried again while another git process holds a lock: see workspace.LOCKED.
    completed = run_git(list(args), repo, TIMEOUT)
    output = (completed.stdout + b"\n" + completed.stderr).decode("utf-8", "replace").strip()
    return completed.returncode, output


def current_branch(repo: Path) -> str:
    code, output = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return output if code == 0 else ""


def dirty_paths(repo: Path, paths: list[str]) -> list[str]:
    """Which of `paths` have uncommitted changes.

    Untracked is not dirty: a file the Engineer is about to write for the first
    time can perfectly well already exist from a previous run that was never
    committed, and refusing on that would make the gap permanent.
    """
    code, output = _git(repo, "status", "--porcelain", "--", *paths)
    if code != 0:
        return []
    changed = []
    for line in output.splitlines():
        marks, _, name = line.partition(" ")
        if marks.strip() and not marks.startswith("??"):
            changed.append(name.strip().strip('"'))
    return changed


def land(repo: Path, files: dict[str, str], *, message: str,
         branch: str = "master", verify: Verify | None = None) -> Landed:
    """Write accepted files into the project and commit them, or say why not.

    With `verify`, the files are written, the project is checked as a whole,
    and the working tree is put back exactly as it was if the check fails.
    Nothing is committed and then undone: a commit here means the project
    built.
    """
    if not files:
        return Landed(False, detail="nothing to land")
    if not (repo / ".git").exists():
        return Landed(False, detail=f"{repo} is not a git repository")

    on = current_branch(repo)
    if on != branch:
        return Landed(False, detail=(
            f"the project is on {on or 'a detached head'}, not {branch}, so nothing was "
            f"committed. The work is still on its engineer/ branch."))

    paths = sorted(files)
    blocked = dirty_paths(repo, paths)
    if blocked:
        return Landed(False, detail=(
            "uncommitted changes to " + ", ".join(blocked)
            + " -- left alone rather than overwritten. The work is still on its "
              "engineer/ branch."))

    # Whatever was there before, so a failed check leaves no trace. Kept as
    # text because that is what was written; a file that did not exist is None.
    before: dict[str, bytes | None] = {}
    for path in paths:
        target = repo / path
        before[path] = target.read_bytes() if target.is_file() else None

    def restore() -> None:
        # Bytes, not text. Reading a file as text and writing it back
        # normalises its line endings, so a restored file differed from the
        # committed one by every newline and git reported the whole file as
        # modified -- a failed check leaving exactly the trace it must not.
        for path, original in before.items():
            target = repo / path
            if original is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(original)
        # And unstage. Putting the file back while leaving it in the index
        # leaves the project dirty in a way `git status` reports as a staged
        # add of a file that is not there -- which is what a killed build left
        # behind, and what the next build would refuse to land on top of.
        _git(repo, "reset", "-q", "--", *paths)

    for path, source in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8", newline="\n")

    if verify is not None:
        try:
            passed, why = verify(repo)
        except Exception as exc:  # noqa: BLE001 - a broken check must not land code
            restore()
            return Landed(False, detail=f"the project check could not run: {str(exc)[:200]}")
        if not passed:
            restore()
            return Landed(False, paths=tuple(paths), detail=(
                "the project does not build with it: " + why[:600]
                + " -- nothing was committed, and the work is still on its "
                  "engineer/ branch."))

    code, output = _git(repo, "add", "--", *paths)
    if code != 0:
        restore()
        return Landed(False, detail=f"git add failed: {output[:200]}")

    # Staged-but-identical is the normal case for a rebuild of an unchanged
    # system. An empty commit would add a commit that says nothing happened.
    code, _ = _git(repo, "diff", "--cached", "--quiet", "--", *paths)
    if code == 0:
        # Unstage before returning: `git add` on identical content stages
        # nothing to commit but still records the path, and leaving it there
        # makes the next run see a dirty tree and decline to land.
        _git(repo, "reset", "-q", "--", *paths)
        return Landed(False, paths=tuple(paths),
                      detail="already in the project, unchanged")

    # The same identity the Engineer commits its own work under, passed
    # explicitly rather than inherited. A repository created minutes ago has no
    # user.name or user.email, so `git commit` answered
    #
    #     Author identity unknown *** Please tell me who you are.
    #
    # and every system of a fresh project built, passed six checks, and then
    # failed to reach the project -- for a setting that has nothing to do with
    # the code. It also makes the authorship true: an agent wrote these, and a
    # commit attributed to whoever configured the machine says otherwise.
    code, output = _git(repo, *GIT_IDENTITY, "commit", "-m", message, "--", *paths)
    if code != 0:
        restore()
        return Landed(False, detail=f"git commit failed: {output[:200]}")

    _code, commit = _git(repo, "rev-parse", "HEAD")
    return Landed(True, commit=commit[:40], paths=tuple(paths))
