"""Which model writes Blender Python we can actually use.

The plan is a bridge: a prompt goes to a model, the model answers with Blender
Python, and Blender runs it. Before wiring that up it is worth knowing which
model to wire, and the only honest way to know is to give each of them the
same job and look at what appears in the scene.

So each model is asked for one script, the script is run in a headless Blender,
and the scene is then measured: are the objects there, are they named as asked,
are they the right size, are they in the right place, are the faces triangles.
Nothing here reads the model's prose or takes its word for anything.

    python scripts/blender_bench.py                 # every configured model
    python scripts/blender_bench.py --models glm    # only those matching

The task is the first real piece of the island, at the coordinates the game's
own code uses, so a model that passes has demonstrated the exact thing it would
be asked to do next.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.engineer import subprocesses  # noqa: E402
from app.engineer.gemini import GeminiIncomplete, GeminiRefused, GeminiUnavailable  # noqa: E402
from app.engineer.openai_compat import OpenAICompatClient  # noqa: E402
from app.engineer.runs import COMPAT_PROVIDERS, compat_model_names  # noqa: E402

BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")

SYSTEM = """You write Blender Python for a Roblox game's art pipeline.

Answer with ONE fenced python code block and nothing else: no explanation
before it, no notes after it. The block must be a complete script that runs
inside Blender with `blender --background --python script.py`.

Rules that come from Roblox, not from Blender:
- 1 Blender unit is 1 Roblox stud. Model at that scale.
- Blender X is Roblox X, Blender Y is Roblox Z (south is +Y), Blender Z is
  height. World origin is the centre of the island.
- Low poly: flat shading, no subdivision surface, no smooth shading.
- Every mesh must be triangulated before the script ends.
- Names are used by the game's code and must match exactly.

Start the script by clearing the scene, so it can be run twice and give the
same result both times."""

TASK = """Build these three objects, exactly:

1. `Test_Cube` -- a cube 4 x 4 x 4 units, centred at (0, 0, 2), so it sits on
   the ground rather than half under it.

2. `Plot_NW` -- a player's building pad: a flat slab 32 units square and 1 unit
   thick, its TOP surface at z = 6.5, centred at x = -90, y = -100.

3. `Tower_Body` -- the island's landmark: an eight-sided tower 40 units tall,
   standing on the ground at z = 6 so its top is at z = 46, centred at
   x = 0, y = 0. Its bounding box must measure 14 x 14 units: that is 14
   corner to corner, so the circumscribed radius is 7.

Every object must be flat shaded and triangulated, with scale and rotation
applied so no object carries an unapplied scale. Where each object's ORIGIN
sits does not matter; what matters is where its geometry ends up in the
world."""

# Measured, not asked for: what the scene must hold afterwards.
WANTED = {
    "Test_Cube": {"size": (4.0, 4.0, 4.0), "centre": (0.0, 0.0, 2.0)},
    "Plot_NW": {"size": (32.0, 32.0, 1.0), "centre": (-90.0, -100.0, 6.0)},
    "Tower_Body": {"size": (14.0, 14.0, 40.0), "centre": (0.0, 0.0, 26.0)},
}
# Studs. A pad half a stud out is fine; five studs out is a different island.
TOLERANCE = 1.0

EPILOGUE = '''

# ---- added by scripts/blender_bench.py: measures what the script produced ----
import json as _json

import bpy as _bpy
from mathutils import Vector as _Vector

_report = {"objects": {}}
for _obj in _bpy.data.objects:
    if _obj.type != "MESH":
        continue
    _mesh = _obj.data
    _tris = sum(1 for _p in _mesh.polygons if len(_p.vertices) == 3)
    # Where the GEOMETRY is, in world space -- not where the object's origin
    # is. A script that places a slab correctly and then applies its
    # transforms leaves the origin at world zero with the mesh exactly where
    # it was asked for, and reading the origin called that a failure.
    _corners = [_obj.matrix_world @ _Vector(_corner) for _corner in _obj.bound_box]
    _lo = [min(_corner[_i] for _corner in _corners) for _i in range(3)]
    _hi = [max(_corner[_i] for _corner in _corners) for _i in range(3)]
    _report["objects"][_obj.name] = {
        "size": [round(_hi[_i] - _lo[_i], 3) for _i in range(3)],
        "centre": [round((_hi[_i] + _lo[_i]) / 2.0, 3) for _i in range(3)],
        "polygons": len(_mesh.polygons),
        "triangles": _tris,
        "all_triangles": _tris == len(_mesh.polygons) and len(_mesh.polygons) > 0,
        "flat_shaded": not any(_p.use_smooth for _p in _mesh.polygons),
        "scale": [round(_v, 3) for _v in _obj.scale],
    }
print("BENCH_REPORT_BEGIN")
print(_json.dumps(_report))
print("BENCH_REPORT_END")
'''


@dataclass
class Result:
    provider: str
    model: str
    asked_seconds: float = 0.0
    output_tokens: int = 0
    error: str = ""
    ran: bool = False
    blender_error: str = ""
    report: dict = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        """Objects that are present, named, sized, placed and triangulated."""
        if not self.ran:
            return 0
        found = 0
        for name in WANTED:
            if name in self.report.get("objects", {}) and not any(name in f for f in self.findings):
                found += 1
        return found


def code_from(answer: str) -> str:
    """The script out of the answer.

    Some models wrap it in a fence, some add prose around it, and one of them
    thinks out loud in a <think> block first. What is wanted is the longest
    python block; failing that, the whole answer, which is often just code.
    """
    without_thinking = re.sub(r"<think>.*?</think>", "", answer, flags=re.S)
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", without_thinking, flags=re.S)
    if blocks:
        return max(blocks, key=len).strip()
    return without_thinking.strip()


def measure(report: dict) -> list[str]:
    """What is wrong with the scene, in the words of what was asked for."""
    findings: list[str] = []
    objects = report.get("objects", {})
    for name, wanted in WANTED.items():
        got = objects.get(name)
        if got is None:
            near = [other for other in objects if name.lower().replace("_", "") in
                    other.lower().replace("_", "")]
            findings.append(f"{name}: missing" + (f" (found {near[0]!r} instead)" if near else ""))
            continue
        for axis, (want, have) in enumerate(zip(wanted["size"], got["size"])):
            if abs(want - have) > TOLERANCE:
                findings.append(f"{name}: {'xyz'[axis]} is {have}, asked for {want}")
        for axis, (want, have) in enumerate(zip(wanted["centre"], got["centre"])):
            if abs(want - have) > TOLERANCE:
                findings.append(f"{name}: sits at {'xyz'[axis]}={have}, asked for {want}")
        if not got["all_triangles"]:
            findings.append(f"{name}: {got['polygons'] - got['triangles']} faces are not triangles")
        if not got["flat_shaded"]:
            findings.append(f"{name}: smooth shaded, not flat")
        if any(abs(value - 1.0) > 0.001 for value in got["scale"]):
            findings.append(f"{name}: scale {got['scale']} was never applied")
    return findings


def run_in_blender(script: str, where: Path) -> tuple[bool, str, dict]:
    """(ran cleanly, what went wrong, what the scene held)."""
    where.write_text(script + EPILOGUE, encoding="utf-8")
    try:
        done = subprocesses.run([str(BLENDER), "--background", "--factory-startup",
                                 "--python-exit-code", "1", "--python", str(where)],
                                timeout=180)
    except subprocess.TimeoutExpired:
        return False, "Blender did not finish within 180s", {}

    out = done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace")
    match = re.search(r"BENCH_REPORT_BEGIN\s*(\{.*?\})\s*BENCH_REPORT_END", out, flags=re.S)
    report = {}
    if match:
        try:
            report = json.loads(match.group(1))
        except ValueError:
            report = {}
    if done.returncode != 0:
        trouble = [line for line in out.splitlines()
                   if "Error" in line or "Traceback" in line or "error:" in line]
        return False, (trouble[-1] if trouble else f"Blender exited {done.returncode}")[:300], report
    return True, "", report


async def ask(provider: str, model: str, settings: Settings, scratch: Path) -> Result:
    key_field, url_field, _models = COMPAT_PROVIDERS[provider]
    key = str(getattr(settings, key_field) or "").strip()
    result = Result(provider=provider, model=model)
    if not key:
        result.error = f"no {provider} key is set"
        return result

    client = OpenAICompatClient(provider=provider, base_url=str(getattr(settings, url_field)),
                               api_key=key, timeout=settings.engineer_compat_timeout_seconds,
                               max_output_tokens=settings.engineer_compat_max_output_tokens)
    began = time.monotonic()
    try:
        reply = await client.generate(model=model, system=SYSTEM, prompt=TASK, json_output=False)
    except (GeminiUnavailable, GeminiRefused, GeminiIncomplete) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        await client.close()
        result.asked_seconds = time.monotonic() - began

    result.output_tokens = reply.usage.get("output_tokens", 0)
    script = code_from(reply.text)
    if not script:
        result.error = "the answer carried no code"
        return result

    safe = re.sub(r"[^A-Za-z0-9]+", "_", f"{provider}_{model}")
    (scratch / f"{safe}.py").write_text(script, encoding="utf-8")
    result.ran, result.blender_error, result.report = run_in_blender(script, scratch / f"{safe}_run.py")
    result.findings = measure(result.report) if result.ran else []
    return result


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default="", help="only models whose name contains this")
    parser.add_argument("--scratch", type=Path,
                        default=Path(r"C:\Users\tcgxu\AppData\Local\Temp\claude\blender-bench"))
    args = parser.parse_args(argv[1:])

    if not BLENDER.is_file():
        print(f"Blender is not at {BLENDER}")
        return 2
    args.scratch.mkdir(parents=True, exist_ok=True)
    settings = Settings()

    wanted: list[tuple[str, str]] = []
    for provider in COMPAT_PROVIDERS:
        for model in compat_model_names(provider, settings):
            if not args.models or args.models.lower() in model.lower():
                wanted.append((provider, model))
    if not wanted:
        print("no configured model matched")
        return 1

    print(f"Blender {BLENDER.parent.name}, {len(wanted)} model(s), scripts kept in {args.scratch}\n")
    results: list[Result] = []
    for provider, model in wanted:
        print(f"--- {provider}/{model}")
        result = await ask(provider, model, settings, args.scratch)
        results.append(result)
        if result.error:
            print(f"    no answer: {result.error[:200]}")
            continue
        print(f"    answered in {result.asked_seconds:.0f}s, {result.output_tokens} output tokens")
        if not result.ran:
            print(f"    Blender refused the script: {result.blender_error}")
            continue
        print(f"    {result.score} of {len(WANTED)} objects correct")
        for finding in result.findings:
            print(f"      - {finding}")

    print("\n=== what the scene says")
    for result in sorted(results, key=lambda r: (-r.score, r.asked_seconds)):
        state = (f"{result.score}/{len(WANTED)}" if result.ran else
                 ("no answer" if result.error else "script failed"))
        print(f"  {result.provider}/{result.model:38} {state:>10}  {result.asked_seconds:5.0f}s")
    best = max(results, key=lambda r: (r.score, -r.asked_seconds), default=None)
    if best is not None and best.score > 0:
        print(f"\nbest: {best.provider}/{best.model}")
    else:
        print("\nno model built the scene; read the kept scripts before choosing one")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
