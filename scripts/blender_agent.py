"""Build the island in Blender by asking for it.

    python scripts/blender_agent.py "raise a 260 stud island with a beach ring"
    python scripts/blender_agent.py --show
    python scripts/blender_agent.py --retry 2 "add the four plot pads"
    python scripts/blender_agent.py --export data/blender/export

Each run opens the same .blend, asks a model for a script, lets Blender run
it, then measures the scene and saves the file. What is printed is what
Blender holds afterwards -- the sizes, positions and triangle counts are read
back out of the file, not taken from the model's word.

With --retry, a script whose result breaks a house rule (a mesh left
untriangulated, a smooth-shaded face, an unapplied scale) is handed back with
what was wrong, which is the same loop the game's Engineer uses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.blender import bridge  # noqa: E402
from app.config import Settings  # noqa: E402

DEFAULT_BLEND = Path("data/blender/island_haven.blend")


def show(report: dict) -> None:
    objects = report.get("objects") or {}
    if not objects:
        print("  the file is empty")
        return
    for name, got in sorted(objects.items()):
        size = " x ".join(str(value) for value in got["size"])
        centre = ", ".join(str(value) for value in got["centre"])
        print(f"  {name:22} {size:>26} studs   at ({centre})   {got['triangles']} tris")
    print(f"  {'':22} {report.get('total_triangles', 0)} triangles in the file")


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instruction", nargs="*", help="what to build or change")
    parser.add_argument("--blend", type=Path, default=DEFAULT_BLEND)
    parser.add_argument("--model", default="", help="only a provider/model containing this")
    parser.add_argument("--retry", type=int, default=1, help="attempts, when the scene breaks a rule")
    parser.add_argument("--show", action="store_true", help="print what the file holds and stop")
    parser.add_argument("--export", type=Path, default=None, metavar="DIR",
                        help="write each mesh out for Roblox, one file per object, and stop")
    parser.add_argument("--format", default="obj", choices=("obj", "fbx"))
    parser.add_argument("--only", default="", help="export only these objects, comma separated")
    parser.add_argument("--expect", type=Path, default=None, metavar="BRIEF",
                        help="a json brief of measurements each object must meet")
    parser.add_argument("--check", default="",
                        help="brief entries to hold this run to, comma separated; "
                             "the default checks whichever of them are already in the file")
    args = parser.parse_args(argv[1:])

    try:
        print(f"Blender: {bridge.blender_executable()}")
    except bridge.NoBlender as exc:
        print(exc)
        return 2

    blend = args.blend.resolve()
    print(f"file:    {blend}")
    if args.export is not None:
        only = tuple(name.strip() for name in args.only.split(",") if name.strip())
        written = bridge.export(blend, args.export.resolve(), only=only, fmt=args.format)
        if written["error"]:
            print(written["error"][:400])
        for name, got in sorted(written["files"].items()):
            print(f"  {name:22} {got['triangles']:>6} tris   {got['bytes']:>9} bytes   {got['path']}")
        for name, why in sorted(written["refused"].items()):
            print(f"  {name:22} NOT WRITTEN: {why}")
        if not written["files"]:
            print("  nothing was exported")
        # Refusals matter as much as the error: a run that exported three of
        # four objects and exited cleanly has still not done the job.
        return 0 if written["files"] and not written["refused"] and not written["error"] else 1

    if args.show or not args.instruction:
        show(bridge.read_scene(blend))
        return 0

    wanted: dict = {}
    if args.expect is not None:
        brief = json.loads(args.expect.read_text(encoding="utf-8"))
        brief = {name: rules for name, rules in brief.items() if not name.startswith("_")}
        named = [name.strip() for name in args.check.split(",") if name.strip()]
        if named:
            missing = [name for name in named if name not in brief]
            if missing:
                print(f"{args.expect} has no entry for: {', '.join(missing)}")
                return 2
            wanted = {name: brief[name] for name in named}
        else:
            # Whatever the file already holds. A pass that builds the dock is
            # not failed for the tower it was never asked to build.
            here = bridge.read_scene(blend).get("objects", {})
            wanted = {name: rules for name, rules in brief.items() if name in here}

    settings = Settings()
    instruction = " ".join(args.instruction)
    feedback = ""
    for attempt in range(1, max(1, args.retry) + 1):
        result = await bridge.ask(instruction, blend, settings, only=args.model, feedback=feedback)
        if result.error and not result.ran:
            print(f"attempt {attempt}: {result.error[:400]}")
            if not result.code:
                # No code came back at all: the gateway timed out, the tier is
                # exhausted, something upstream. Worth another go rather than
                # ending the run, because the previous prompt was fine -- but
                # not worth new feedback, because there is nothing to fix.
                continue
            feedback = f"Blender refused the script: {result.error[:400]}"
            continue

        print(f"attempt {attempt}: {result.provider}/{result.model} in {result.seconds:.0f}s")
        show(result.report)
        # Two different questions, so two different lists: what Roblox will
        # not accept, and what this island was asked to be. A shell 370 studs
        # across passed the first and failed the second.
        complaints = result.complaints() + bridge.shortfalls(result.report, wanted)
        if not complaints:
            print("the scene keeps the house rules" if not wanted
                  else "the scene keeps the house rules and matches the brief")
            return 0
        print("what the scene breaks:")
        for complaint in complaints:
            print(f"  - {complaint}")
        feedback = "\n".join(complaints)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
