"""Write the game's entry points into the repository, in build order.

Rojo ships what the repository holds. Every generated system is a
ModuleScript, and a ModuleScript runs only when something requires it, so a
repository of systems and no entry point syncs into a place where nothing
happens -- no data loads, no island appears, no remote is ever created.

Until now the only thing that started the game was the bootstrap our bridge
writes into Studio at sync time. That works, and it means the same commit
behaves differently depending on which path put it in Studio: an audit that
read the repository concluded the game could not run, while Studio's own log
from a bridge sync said thirty-six modules started. Both were true.

So the entry points live here, generated from the same function the bridge
uses, and the bridge stands down when it sees them (app/bridge/from_project.py,
entry_scripts). One source of truth, whichever way the place is filled.

    python scripts/write_bootstrap.py            # the game the blueprint names
    python scripts/write_bootstrap.py --check    # fail if they are out of date

Run it after a system lands. `--check` is the form for a hook or a gate: it
writes nothing and exits non-zero when the files on disk are not what this
would write, which is how a forgotten regeneration is caught rather than
discovered in a playtest.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.blueprint.compile import compile_spec  # noqa: E402
from app.blueprint.store import BlueprintStore  # noqa: E402
from app.bridge.from_project import bootstrap_source, in_build_order  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.engineer.runs import resolve_game_repo  # noqa: E402

# Where each side's entry point goes, and what Rojo makes of it. `Main.server`
# rather than `init.server`: init turns the CONTAINING FOLDER into the script,
# which the bridge's operation protocol cannot express, so the two paths would
# disagree about the shape of the tree again.
ENTRY_POINTS = {
    "src/server": "src/server/Main.server.luau",
    "src/client": "src/client/Main.client.luau",
}
GENERATED_NAMES = {"Services"}


def read_exactly(path: Path) -> str | None:
    """The file's bytes as text, with no line-ending translation.

    Python's default translation turns the CRLF git hands back on Windows into
    LF on the way in and LF into CRLF on the way out, so a file this script had
    just written could read back as "out of date" and StyLua would reformat it
    every time. Both ends are explicit here.
    """
    if not path.is_file():
        return None
    return path.read_bytes().decode("utf-8")


def write_exactly(path: Path, source: str) -> None:
    path.write_bytes(source.encode("utf-8"))


def modules_in(repo: Path, root: str) -> list[str]:
    """The module names Rojo will put in that folder, entry points aside."""
    directory = repo / root
    if not directory.is_dir():
        return []
    names = []
    for file in sorted(directory.glob("*.luau")):
        name = file.name
        if name.endswith(".server.luau") or name.endswith(".client.luau"):
            continue
        stem = name[:-5]
        if stem in GENERATED_NAMES:
            continue
        names.append(stem)
    return names


def wanted(repo: Path, order: list[str] | None) -> dict[Path, str]:
    """What each entry point should contain, given what the repository holds."""
    written: dict[Path, str] = {}
    for root, target in ENTRY_POINTS.items():
        names = modules_in(repo, root)
        if not names:
            continue
        written[repo / target] = bootstrap_source(in_build_order(names, order), inside=True)
    return written


def build_order() -> list[str] | None:
    """The approved order, when a blueprint compiles; otherwise alphabetical.

    A system that another requires at start-up must be started first, and the
    specification is where that order is decided. Without one the files are
    still written -- a game with no blueprint should not be left unstartable.
    """
    try:
        store = BlueprintStore(SessionLocal)
        for blueprint in store.list(limit=20):
            if blueprint.systems:
                return compile_spec(blueprint).build_order
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        print(f"(no build order from the blueprint: {type(exc).__name__}: {exc})")
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=None, help="the game repository (default: the blueprint's)")
    parser.add_argument("--check", action="store_true", help="write nothing; fail if they are out of date")
    args = parser.parse_args(argv[1:])

    settings = Settings()
    repo = args.repo
    if repo is None:
        store = BlueprintStore(SessionLocal)
        blueprint = next((b for b in store.list(limit=20) if b.systems), None)
        if blueprint is None:
            print("no blueprint with systems; pass --repo")
            return 2
        repo = resolve_game_repo(settings, blueprint)
    print(f"project {repo}")

    files = wanted(repo, build_order())
    if not files:
        print("no modules to start")
        return 0

    stale = []
    for path, source in files.items():
        current = read_exactly(path)
        relative = path.relative_to(repo).as_posix()
        if current == source:
            print(f"  {relative}: up to date ({source.count(chr(10))} lines)")
            continue
        stale.append(relative)
        if args.check:
            print(f"  {relative}: OUT OF DATE")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        write_exactly(path, source)
        print(f"  {relative}: written")

    if args.check and stale:
        print(f"\n{len(stale)} entry point(s) out of date; run scripts/write_bootstrap.py")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
