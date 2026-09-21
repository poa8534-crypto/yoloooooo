"""Open the .blend in a real Blender window that reloads itself as it changes.

    python scripts/blender_watch.py
    python scripts/blender_watch.py --blend data/blender/island_haven.blend

The bridge builds in a headless Blender, so there is nothing to watch in the
run itself. What this does instead is open the file in the ordinary Blender
window and leave a timer running inside it: every couple of seconds it looks
at the file's modification time, and when the background run saves, the window
reloads the file. So each step appears as it lands, in the real viewport,
rather than being described to you.

Two honest limits. The reload is a revert, so anything you have selected or
were part way through editing in that window is discarded when a new version
lands -- treat the window as a view, not a workspace. And it refreshes when
the file is SAVED, which is once per step, not stroke by stroke: you see each
step arrive, not the model thinking.

The window is deliberately let go of, so it stays open after this exits.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.blender import bridge  # noqa: E402

DEFAULT_BLEND = Path("data/blender/island_haven.blend")

WATCHER = '''
import os

import bpy

_WATCHED = {path!r}
_state = {{"stamp": None}}


def _stamp():
    try:
        got = os.stat(_WATCHED)
    except OSError:
        return None
    return (got.st_mtime, got.st_size)


def _tick():
    now = _stamp()
    if now is not None and _state["stamp"] is not None and now != _state["stamp"]:
        _state["stamp"] = now
        try:
            # The save may still be in flight; the next tick catches it.
            bpy.ops.wm.revert_mainfile()
        except RuntimeError:
            pass
    elif now is not None:
        _state["stamp"] = now
    return {every}


# Persistent, because a revert loads a new file and an ordinary timer would
# not survive it -- the window would refresh once and then go quiet.
bpy.app.timers.register(_tick, first_interval={every}, persistent=True)
print("BRIDGE_WATCHING", _WATCHED)
'''


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blend", type=Path, default=DEFAULT_BLEND)
    parser.add_argument("--every", type=float, default=2.0, help="seconds between checks")
    args = parser.parse_args(argv[1:])

    try:
        executable = bridge.blender_executable()
    except bridge.NoBlender as exc:
        print(exc)
        return 2

    blend = args.blend.resolve()
    if not blend.is_file():
        print(f"{blend} does not exist yet; build something into it first")
        return 1

    watcher = blend.parent / f"{blend.stem}_watch.py"
    watcher.write_text(WATCHER.format(path=str(blend), every=args.every), encoding="utf-8")

    # Let go of it on purpose: this process exits and the window stays.
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(  # noqa: S603 - the command is built here
        [str(executable), str(blend), "--python", str(watcher)],
        creationflags=creationflags, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"watching {blend}")
    print(f"reloads every {args.every:g}s when the file changes on disk")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
