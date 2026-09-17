"""Run the Luau guard over a game repo's `src` tree and report violations.

A separate entry point rather than a `__main__` on `luau_guard`, so the module
stays a library the Engineer Agent imports in-process during its retry loop
while the gate shells out to this.

Exit code is the contract: 0 clean, 1 violations found, 2 could not run. The
gate reads the code, not the text.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.engineer.luau_guard import check_project

# Roblox tags service classes in its API dump; `globalTypes.None.d.luau` does
# not mark them at all, so it cannot be the source. `Players extends Instance`
# and `Folder extends Instance` are identical there.
SERVICES_CACHE = Path(__file__).resolve().parent.parent / "data" / "roblox-services.json"


def known_services() -> frozenset[str]:
    """Service names, or an empty set when the cache is absent.

    An empty set means `check_services_module` cannot refuse an invented
    service name. That is a real reduction in checking, so it is reported
    rather than passed over quietly.
    """
    if not SERVICES_CACHE.exists():
        return frozenset()
    try:
        return frozenset(json.loads(SERVICES_CACHE.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        return frozenset()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", type=Path, help="the game repo's src directory")
    parser.add_argument("--services", type=Path, action="append", default=None,
                        help="path to a generated Services.luau; repeatable "
                             "(default: <src>/shared/Services.luau and <src>/client/Services.luau)")
    args = parser.parse_args()

    src: Path = args.src
    if not src.is_dir():
        print(f"guard: not a directory: {src}", file=sys.stderr)
        return 2
    # The client gets its own generated copy: it cannot reach the shared one at
    # run time, because its scripts are copied into Player.PlayerScripts and the
    # walk up resolves to a different tree (app/engineer/luau_guard.py).
    services = tuple(args.services) if args.services else (
        src / "shared" / "Services.luau", src / "client" / "Services.luau")

    names = known_services()
    if not names:
        print("guard: no service-name cache; an invented service name in the "
              "Services module cannot be refused. Refresh it with "
              "scripts/refresh_roblox_services.py", file=sys.stderr)

    try:
        violations = check_project(src, services, names)
    except OSError as failure:
        print(f"guard: could not read the tree: {failure}", file=sys.stderr)
        return 2

    for violation in violations:
        print(violation)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
