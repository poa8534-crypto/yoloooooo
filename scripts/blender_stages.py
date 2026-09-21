"""Build a whole island in passes, one part per model answer.

    python scripts/blender_stages.py blender/haven_bay/stages.json
    python scripts/blender_stages.py blender/haven_bay/stages.json --from "arrival dock"
    python scripts/blender_stages.py blender/haven_bay/stages.json --only "town square" --hand

A brief for a whole island does not fit in one answer: the model's output
budget runs out long before the railway does, and a script cut off halfway is
a script that does not run. So a stages file splits the brief into passes, and
each pass is asked for against the file the passes before it saved. Every
pass sees the full brief, the shared layout and what the file already holds,
and is told to build only its own part.

Each pass is checked the way a single prompt is -- the house rules Roblox
imposes, and the names and measurements the stage expects -- and a pass that
fails is retried from a copy of the file taken before it, so a rejected attempt
never leaves its objects behind for the next attempt to trip over.

Everything specific to one island lives in the stages file, so a second island
is a second file and not a change here.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.blender import bridge  # noqa: E402
from app.config import Settings  # noqa: E402


KIT = Path(__file__).resolve().parent.parent / "app" / "blender" / "lowpoly_kit.py"
HAND_ONLY = False


def prelude(plan: dict) -> str:
    """The kit on the path and the island loaded, before any pass's own code."""
    if not plan.get("island"):
        return ""
    return "\n".join([
        "import json as _json",
        "import sys as _sys",
        f"_sys.path.insert(0, {str(KIT.parent)!r})",
        "import lowpoly_kit as kit",
        # Models write `import kit` however often they are told not to, so
        # make that import work rather than fail a pass over a name.
        "_sys.modules['kit'] = kit",
        f"kit.load_island(_json.loads({json.dumps(plan['island'])!r}))",
        "kit.ensure_collections()",
    ])


def _palette_names() -> list[str]:
    tree = ast.parse(KIT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "PALETTE":
            return [key.value for key in node.value.keys]
    return []


def kit_guide() -> str:
    """What the kit offers, read out of its own source so it cannot go stale.

    Each public function's signature and the first paragraph of its docstring,
    taken with ast rather than a pattern, so a new kit function is announced
    to the model the moment it is written.
    """
    tree = ast.parse(KIT.read_text(encoding="utf-8"))
    lines = ["A building kit is ALREADY IMPORTED as the global Python module `kit` (write "
             "`kit.pier(...)`, never `bpy.context.scene.kit`; do not import it again) and the island is loaded. "
             "Use it: it builds props correctly, stands them on the ground, and "
             "replaces objects of the same name so a rerun leaves no copies. "
             "Write only the placing. Its functions:"]
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        doc = (ast.get_docstring(node) or "").split("\n\n")[0]
        doc = " ".join(doc.split())
        lines.append(f"  kit.{node.name}({ast.unparse(node.args)})" + (f"  -- {doc}" if doc else ""))
    lines.append("Meshes from the *_mesh() functions are shared: pass them to kit.place or "
                 "kit.scatter many times. kit.ground(x, y) is the ground height; never guess heights.")
    lines.append("For anything the kit lacks: part = kit.Part(); part.box(x, y, z, sx, sy, sz, colour), "
                 "part.cylinder(x, y, z0, radius, height, colour, sides=8, top=None), "
                 "part.blob(x, y, z, radius, colour), part.poly(points, colour, both_sides=False); "
                 "then kit.obj(name, part.finish(name), collection, x, y, z, rz). Coordinates in a Part "
                 "are local to the object. Colours are names: " + ", ".join(sorted(_palette_names())) + ".")
    return "\n".join(lines)


def instruction(plan: dict, brief: str, stage: dict) -> str:
    others = [s["name"] for s in plan["stages"] if s["name"] != stage["name"]]
    return "\n\n".join([
        "THE FULL BRIEF, for context. You are NOT building all of it now.\n\n" + brief,
        "\n".join(plan.get("layout", [])),
        f"THIS PASS: {stage['name']}. Build ONLY this:\n{stage['do']}",
        "Other passes build the rest (" + ", ".join(others) + "); leave that to them "
        "and do not touch objects that are already in the file. Keep the script "
        "compact: loops and helper functions rather than repeated blocks.",
    ])


async def run_stage(plan: dict, brief: str, stage: dict, blend: Path, settings: Settings,
                    *, model: str, retries: int) -> bool:
    backup = blend.with_suffix(".before_stage.blend")
    had_file = blend.is_file()
    if had_file:
        shutil.copyfile(blend, backup)

    if HAND_ONLY and stage.get("fallback"):
        return await run_fallback(plan, stage, blend)

    feedback = ""
    for attempt in range(1, retries + 1):
        if attempt > 1:
            # Start again from the file as it was before this pass, so a
            # rejected attempt's objects do not stay behind as .001 copies.
            if had_file:
                shutil.copyfile(backup, blend)
            elif blend.is_file():
                blend.unlink()

        if stage.get("script"):
            # Written by hand, not asked for: run it as it is.
            code = (Path(stage["script"]).read_text(encoding="utf-8") if stage["script"].endswith(".py")
                    else stage["script"])
            result = bridge.Applied(prompt=stage["name"], provider="hand", model="written",
                                    code=code)
            result.ran, result.error, result.report = bridge.run_script(code, blend, prelude=prelude(plan))
        else:
            result = await bridge.ask(instruction(plan, brief, stage), blend, settings,
                                      only=model, feedback=feedback, prelude=prelude(plan),
                                      guide=kit_guide() if plan.get("island") else "")
        if result.error and not result.ran:
            print(f"  attempt {attempt}: {result.error[:300]}")
            if result.code:
                feedback = f"Blender refused the script: {result.error[:400]}"
            continue

        added = sorted(result.objects)
        complaints = result.complaints() + bridge.shortfalls(result.report, stage.get("expect", {}))
        print(f"  attempt {attempt}: {result.provider}/{result.model} in {result.seconds:.0f}s, "
              f"{len(added)} meshes in the file, {result.report.get('total_triangles', 0)} triangles")
        if not complaints:
            return True
        for complaint in complaints[:12]:
            print(f"    - {complaint}")
        feedback = "\n".join(complaints)

    if had_file:
        # A pass that never came right leaves the file as it found it.
        shutil.copyfile(backup, blend)
    elif blend.is_file():
        blend.unlink()
    return await run_fallback(plan, stage, blend)


async def run_fallback(plan: dict, stage: dict, blend: Path) -> bool:
    """The hand-written version of a pass, for when the model cannot land it.

    Held to the same checks as the model's attempt: a fallback that misses the
    spec's coordinates fails just as loudly.
    """
    script = stage.get("fallback")
    if not script:
        return False
    code = Path(script).read_text(encoding="utf-8")
    ran, error, report = bridge.run_script(code, blend, prelude=prelude(plan))
    if not ran:
        print(f"  fallback: {error[:300]}")
        return False
    result = bridge.Applied(prompt=stage["name"], ran=True, report=report)
    complaints = result.complaints() + bridge.shortfalls(report, stage.get("expect", {}))
    print(f"  fallback (hand-written): {len(result.objects)} meshes in the file, "
          f"{report.get('total_triangles', 0)} triangles")
    for complaint in complaints[:12]:
        print(f"    - {complaint}")
    return not complaints


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stages", type=Path)
    parser.add_argument("--from", dest="start", default="", help="start at this stage")
    parser.add_argument("--only", default="", help="run just these stages, comma separated")
    parser.add_argument("--model", default="", help="only a provider/model containing this")
    parser.add_argument("--retry", type=int, default=3)
    parser.add_argument("--max-output-tokens", type=int, default=16000)
    parser.add_argument("--keep-going", action="store_true",
                        help="carry on past a stage that failed instead of stopping")
    parser.add_argument("--hand", action="store_true",
                        help="run each stage's hand-written fallback instead of asking the model")
    args = parser.parse_args(argv[1:])
    global HAND_ONLY
    HAND_ONLY = args.hand

    plan = json.loads(args.stages.read_text(encoding="utf-8"))
    brief = Path(plan["brief"]).read_text(encoding="utf-8")
    blend = Path(plan["blend"]).resolve()
    settings = Settings(engineer_compat_max_output_tokens=args.max_output_tokens)

    stages = plan["stages"]
    if args.only:
        wanted = {name.strip() for name in args.only.split(",")}
        stages = [s for s in stages if s["name"] in wanted]
    elif args.start:
        names = [s["name"] for s in stages]
        if args.start not in names:
            print(f"no stage named {args.start!r}; stages are: {', '.join(names)}")
            return 2
        stages = stages[names.index(args.start):]

    print(f"Blender: {bridge.blender_executable()}\nfile:    {blend}\n")
    failed: list[str] = []
    for number, stage in enumerate(stages, 1):
        print(f"[{number}/{len(stages)}] {stage['name']}")
        if await run_stage(plan, brief, stage, blend, settings, model=args.model, retries=args.retry):
            print("  kept")
            continue
        failed.append(stage["name"])
        print("  NOT kept; the file is as it was before this pass")
        if not args.keep_going:
            break

    scene = bridge.read_scene(blend)
    print(f"\nthe file holds {len(scene.get('objects', {}))} meshes, "
          f"{scene.get('total_triangles', 0)} triangles")
    if failed:
        print("failed: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
