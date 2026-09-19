"""A new game's repository, from the template the pipeline owns.

    python scripts/new_game.py neuromine
    python scripts/new_game.py neuromine --root C:/RobloxGames --inputs-from C:/RobloxGames/ascent

One repository is one game (app/blueprint/builds.py refuses to build a second
game into the first one's). Every game so far was set up by copying the last
one's first commit by hand -- tidebound's says so: "same toolchain as the
first". The toolchain now lives in templates/game, beside the pipeline that
depends on it, so a better verify.ps1 reaches the next game rather than
staying in the repository where it was written.

What this makes: templates/game copied byte for byte, the Rojo project named
after the game (Rojo names the DataModel from it, and a place wearing another
game's name is how the wrong project was noticed twice), the two generated
inputs the checks need copied from an existing game, and one commit. It then
runs the game's own verify.ps1, because an untouched project that fails its
checks makes every system of the first build fail preflight for a reason that
has nothing to do with the system.

It does not touch .env. Which game the Engineer builds is GAME_PROJECT_DIR,
the owner's file; the last lines printed say what to set.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEMPLATE = ROOT / "templates" / "game"
SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
# Generated upstream output the checks read, gitignored in every game.
GENERATED_INPUTS = ("globalTypes.None.d.luau", "roblox.yml")


class Refused(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> str:
    from app.engineer.workspace import git

    return git(list(args), repo)


def make_source_roots(repo: Path) -> None:
    """The folders Rojo maps, which git will not carry while they are empty.

    Measured on the first repository this script made: the template has
    nothing in src/server yet, so the folder did not exist, and Rojo refused
    the project -- "$path that could not be turned into a Roblox Instance ...
    src/server" -- which fails the sourcemap and every check after it. The
    Engineer's worktrees make the same folders for the same reason
    (app/engineer/workspace.py Worktree.create).
    """
    from app.engineer.workspace import WRITABLE_ROOTS

    for source_root in WRITABLE_ROOTS:
        (repo / source_root).mkdir(parents=True, exist_ok=True)


def create(name: str, root: Path, inputs_from: Path | None = None,
           template: Path = TEMPLATE) -> tuple[Path, list[str]]:
    """Make `root/name` from the template and commit it. Returns the new
    repository and the generated inputs it could not find."""
    from app.engineer.workspace import GIT_IDENTITY

    if not SLUG.match(name):
        raise Refused(f"{name!r}: use lower case letters, digits and dashes, starting with a "
                      "letter -- it becomes a folder, a branch prefix and the place's name")
    target = root / name
    if target.exists() and any(target.iterdir()):
        raise Refused(f"{target} already exists and is not empty; nothing was touched")
    if not (template / "default.project.json").is_file():
        raise Refused(f"no template at {template}")

    for source in sorted(template.rglob("*")):
        if source.is_file():
            destination = target / source.relative_to(template)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())

    project = target / "default.project.json"
    data = json.loads(project.read_text(encoding="utf-8"))
    data["name"] = name
    project.write_bytes((json.dumps(data, indent=2) + "\n").encode("utf-8"))

    make_source_roots(target)

    missing = []
    for input_name in GENERATED_INPUTS:
        found = inputs_from / input_name if inputs_from else None
        if found is not None and found.is_file():
            shutil.copy2(found, target / input_name)
        else:
            missing.append(input_name)

    _git(target, "init", "-q", "-b", "master")
    _git(target, "add", "-A")
    _git(target, *GIT_IDENTITY, "commit", "-q", "-m",
         f"chore: empty {name} project, toolchain only\n\n"
         "Made by scripts/new_game.py from the pipeline's templates/game.")
    return target, missing


def verify(repo: Path) -> tuple[bool, str]:
    """The game's own checks, run on the untouched project."""
    script = repo / "scripts" / "verify.ps1"
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=repo, capture_output=True, timeout=600, check=False)
    output = (completed.stdout + completed.stderr).decode("utf-8", "replace")
    return completed.returncode == 0, output


def main() -> int:
    parser = argparse.ArgumentParser(description="Make a new game's repository.")
    parser.add_argument("name", help="lower-case folder name, e.g. neuromine")
    parser.add_argument("--root", type=Path, default=None,
                        help="where games live; defaults to the folder holding GAME_PROJECT_DIR")
    parser.add_argument("--inputs-from", type=Path, default=None,
                        help="an existing game to copy the generated type files from; "
                             "defaults to GAME_PROJECT_DIR")
    parser.add_argument("--no-verify", action="store_true", help="skip running verify.ps1")
    args = parser.parse_args()

    from app.config import get_settings

    current = get_settings().game_project_dir
    root = args.root or (Path(current).parent if current else None)
    if root is None:
        print("No --root given and GAME_PROJECT_DIR is not set, so there is nowhere to put it.")
        return 2
    inputs_from = args.inputs_from or (Path(current) if current else None)
    try:
        repo, missing = create(args.name, root, inputs_from)
    except Refused as exc:
        print(f"Refused: {exc}")
        return 1

    print(f"Created {repo} with one commit.")
    if missing:
        print("Not found, so the checks will refuse until they are added: " + ", ".join(missing))
        print("verify.ps1 prints where each one comes from.")
    if not args.no_verify:
        passed, output = verify(repo)
        print("verify.ps1 on the untouched project: " + ("PASSED" if passed else "FAILED"))
        if not passed:
            print(output[-3000:])
            return 1
    print()
    print("To build into it, set this line in .env yourself, then restart the dashboard")
    print("(a running Python process keeps the .env it started with) and Rojo:")
    print(f"    GAME_PROJECT_DIR={repo.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
