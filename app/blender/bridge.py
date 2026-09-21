"""Ask a model for Blender Python, run it in Blender, and measure what appeared.

The loop is small and has no clever parts:

    a prompt  ->  a model  ->  a python script  ->  Blender runs it  ->  a
    report of what is now in the scene  ->  the .blend is saved

What makes it usable rather than a demo is the last two steps. The scene is
measured after every run -- names, world-space sizes, positions, triangle
counts -- so what comes back is what Blender holds, never what the model said
it did. And the file persists, so the next prompt starts from the island as it
is rather than from an empty world, and the model is told what is already
there.

A .blend is not something Roblox can open, so the last step out is `export`:
each mesh becomes its own file, because a MeshPart is one mesh, with the axes
turned from Blender's Z-up to Roblox's Y-up on the way. What it reports is
read off the disk afterwards, so a file Blender said it wrote and did not is a
failure rather than a success.

Nothing here is specific to one provider: the model is whichever compatible
one is configured and answering, tried in the order the settings name, so an
exhausted free tier moves to the next rather than ending the session.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..engineer import subprocesses
from ..engineer.gemini import GeminiIncomplete, GeminiRefused, GeminiUnavailable
from ..engineer.openai_compat import OpenAICompatClient
from ..engineer.runs import COMPAT_PROVIDERS, compat_model_names

# Where Blender is. An installed path is looked for rather than assumed, and
# BLENDER_EXE overrides it for a machine that keeps it elsewhere.
BLENDER_SEARCH = (
    Path(r"C:\Program Files\Blender Foundation"),
    Path(r"C:\Program Files (x86)\Blender Foundation"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Blender Foundation",
)

SYSTEM = """You write Blender Python for a Roblox game's art pipeline.

Answer with ONE fenced python code block and nothing else: no explanation
before it, no notes after it. The block is a complete script that will be run
inside Blender with `blender --background the_file.blend --python yours.py`.

You are writing for Blender {version}. Use only API that exists in that
version: an attribute renamed or removed since stops the script dead, and the
error comes straight back to you. Two that have stopped scripts here:
- mathutils.noise.noise2 does not exist. Use
  mathutils.noise.noise(Vector((x, y, 0.0))), or plain math.sin/cos sums.
- bmesh sequences need bm.verts.ensure_lookup_table() (and the same for
  edges and faces) after adding elements and before indexing bm.verts[i].

The rules below come from Roblox, not from Blender, and they are not
negotiable:

- 1 Blender unit is 1 Roblox stud. Model at that scale.
- Blender X is Roblox X. Blender Z is height, and becomes Roblox Y. Blender Y
  becomes Roblox MINUS Z, so Blender +Y is Roblox north and Blender -Y is
  Roblox south. That sign is not a preference: Blender is Z-up and Roblox is
  Y-up, and this is the only mapping that keeps X and height pointing the way
  they were built without mirroring the mesh and turning every face
  inside-out. The world origin is the centre of the island and sea level is
  z = 0.
- Low poly: flat shading, no subdivision surface, no smooth shading, no
  bevels under half a stud.
- Triangulate every mesh you make before the script ends.
- Apply scale and rotation, so nothing carries an unapplied scale. Where an
  object's origin sits does not matter; where its geometry lands does.
- A single Roblox MeshPart may not exceed 10,000 triangles, so keep every
  object under 8,000.
- Names are used by the game's own code. Use exactly the names you are given.

The file may already hold work. Do NOT clear the scene unless you are asked
to. Change or add only what the instruction asks for, and leave everything
else alone.

The scene may also be COMPLETELY EMPTY, with no default cube, no camera and
no active object -- that is how a new file starts here. So never assume
something is selected or active. `bpy.ops.object.mode_set` and every other
operator that needs an active object will raise "Context missing active
object" against an empty scene; set the active object yourself first, or
build meshes with bpy.data and bmesh, which need no context at all and are
the safer choice in a background run."""

# Added to every script. It measures the scene and saves the file, so the
# answer is what Blender holds and the next prompt starts where this one left
# off.
EPILOGUE = '''

# ---- added by app/blender/bridge.py ----
import json as _json

import bpy as _bpy
from mathutils import Vector as _Vector

_SECTORS = 16


def _shape(_obj, _lo, _hi):
    """The outline and the relief, because a bounding box cannot see either.

    A cylinder 260 studs across has exactly the size, height and triangle
    count an island was asked for. What tells them apart is the coastline
    wandering in and out, the ground rolling rather than lying flat, and the
    underwater part drawing inward. So each is measured: the silhouette is
    sampled as the furthest vertex in each of sixteen compass sectors, and the
    top and bottom bands of the object are looked at separately.
    """
    import math

    _mid = ((_lo[0] + _hi[0]) / 2.0, (_lo[1] + _hi[1]) / 2.0)
    _tall = max(_hi[2] - _lo[2], 1e-6)
    _outline = [0.0] * _SECTORS
    _bottom = [0.0] * _SECTORS
    _top_z = []
    for _vertex in _obj.data.vertices:
        _world = _obj.matrix_world @ _vertex.co
        _dx, _dy = _world[0] - _mid[0], _world[1] - _mid[1]
        _radius = math.hypot(_dx, _dy)
        _sector = int((math.atan2(_dy, _dx) + math.pi) / (2 * math.pi) * _SECTORS) % _SECTORS
        if _radius > _outline[_sector]:
            _outline[_sector] = _radius
        _height = (_world[2] - _lo[2]) / _tall
        if _height <= 0.15 and _radius > _bottom[_sector]:
            _bottom[_sector] = _radius
        if _height >= 0.85:
            _top_z.append(_world[2])

    _reached = [_r for _r in _outline if _r > 0.0]
    _widest = max(_reached) if _reached else 0.0
    _narrowest = min(_reached) if _reached else 0.0
    _mean = (sum(_reached) / len(_reached)) if _reached else 0.0

    # How far the coastline departs from a plain oval. The first measure here
    # was the spread between the widest and narrowest point, and it was no
    # use: a 280 x 233 ellipse scores 0.167 on it with no bay anywhere, so a
    # smooth oval passed as an organic coastline. What a bay or a headland
    # actually is, is a sector that sits off the oval its neighbours describe,
    # so the oval is fitted from the bounding box and the residual measured
    # against it.
    _half_x = max((_hi[0] - _lo[0]) / 2.0, 1e-6)
    _half_y = max((_hi[1] - _lo[1]) / 2.0, 1e-6)
    _off = []
    for _sector, _radius in enumerate(_outline):
        if _radius <= 0.0:
            continue
        _angle = (_sector + 0.5) / _SECTORS * 2 * math.pi - math.pi
        _fit = 1.0 / math.sqrt((math.cos(_angle) / _half_x) ** 2 + (math.sin(_angle) / _half_y) ** 2)
        _off.append(abs(_radius - _fit) / _fit)
    _wobble = (sum(_off) / len(_off)) if _off else 0.0
    _under = [_r for _r in _bottom if _r > 0.0]
    _under_mean = (sum(_under) / len(_under)) if _under else 0.0
    return {
        # How far the coastline wanders: 0.0 is a perfect circle.
        "coast_variation": round((_widest - _narrowest) / _widest, 3) if _widest else 0.0,
        # How far it wanders off a plain oval: this is the one that knows a
        # bay from an ellipse.
        "coast_wobble": round(_wobble, 3),
        # How much the high ground rises and falls across itself.
        "top_roll": round(max(_top_z) - min(_top_z), 2) if _top_z else 0.0,
        # How far the underwater part draws in: 0.0 is a straight-sided disc.
        "skirt_taper": round(1.0 - _under_mean / _mean, 3) if _mean else 0.0,
        "outline": [round(_r, 2) for _r in _outline],
    }


_report = {"objects": {}, "total_triangles": 0}
# A location or rotation set from Python does not reach matrix_world until the
# view layer is evaluated. Without this, a pier placed 258 studs south and
# turned to run north-south measured as sitting at the origin, running east.
_bpy.context.view_layer.update()
for _obj in _bpy.data.objects:
    if _obj.type != "MESH":
        continue
    _mesh = _obj.data
    _tris = sum(1 for _p in _mesh.polygons if len(_p.vertices) == 3)
    _corners = [_obj.matrix_world @ _Vector(_corner) for _corner in _obj.bound_box]
    _lo = [min(_corner[_i] for _corner in _corners) for _i in range(3)]
    _hi = [max(_corner[_i] for _corner in _corners) for _i in range(3)]
    _report["objects"][_obj.name] = {
        "shape": _shape(_obj, _lo, _hi),
        "size": [round(_hi[_i] - _lo[_i], 2) for _i in range(3)],
        "centre": [round((_hi[_i] + _lo[_i]) / 2.0, 2) for _i in range(3)],
        "polygons": len(_mesh.polygons),
        "triangles": _tris,
        "all_triangles": _tris == len(_mesh.polygons) and len(_mesh.polygons) > 0,
        "flat_shaded": not any(_p.use_smooth for _p in _mesh.polygons),
        "unapplied_scale": [round(_v, 3) for _v in _obj.scale] != [1.0, 1.0, 1.0],
    }
    _report["total_triangles"] += _tris

_bpy.ops.wm.save_as_mainfile(filepath=_BRIDGE_BLEND)
print("BRIDGE_REPORT_BEGIN")
print(_json.dumps(_report))
print("BRIDGE_REPORT_END")
'''


class NoBlender(RuntimeError):
    pass


@dataclass
class Applied:
    """One turn of the loop, as it actually went."""

    prompt: str
    provider: str = ""
    model: str = ""
    code: str = ""
    ran: bool = False
    error: str = ""
    report: dict = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def objects(self) -> dict:
        return self.report.get("objects", {})

    def complaints(self) -> list[str]:
        """House rules the scene breaks, whatever the model claimed."""
        problems: list[str] = []
        for name, got in sorted(self.objects.items()):
            if not got["all_triangles"]:
                problems.append(f"{name}: {got['polygons'] - got['triangles']} faces are not triangles")
            if not got["flat_shaded"]:
                problems.append(f"{name}: smooth shaded, not flat")
            if got["unapplied_scale"]:
                problems.append(f"{name}: carries an unapplied scale")
            if got["triangles"] > 8000:
                problems.append(f"{name}: {got['triangles']} triangles, over the 8,000 a MeshPart should keep to")
        return problems


# What a brief can ask of an object, as measured in the file. Each is a
# [low, high] range, and each is derived from the report rather than trusted:
# `top_z` in particular is where the geometry actually ends, which is the
# number a brief means when it says a pad's top surface is at 6.5.
MEASURES: dict[str, str] = {
    "size_x": "is {got} studs wide east-west, wanted {low} to {high}",
    "size_y": "is {got} studs deep north-south, wanted {low} to {high}",
    "size_z": "is {got} studs tall, wanted {low} to {high}",
    "centre_x": "is centred at x = {got}, wanted {low} to {high}",
    "centre_y": "is centred at y = {got}, wanted {low} to {high}",
    "centre_z": "is centred at z = {got}, wanted {low} to {high}",
    "top_z": "has its top surface at z = {got}, wanted {low} to {high}",
    "bottom_z": "has its lowest point at z = {got}, wanted {low} to {high}",
    "coast_variation": ("has an outline that varies by {got} between its widest and narrowest "
                       "point, wanted {low} to {high} (0.0 is a perfect circle, and a circle "
                       "is not a coastline)"),
    "coast_wobble": ("has a coastline that departs from a plain oval by only {got}, wanted "
                     "{low} to {high}; bays and a headland are what make that number rise"),
    "top_roll": "has high ground rising and falling by {got} studs, wanted {low} to {high}",
    "skirt_taper": ("draws in by {got} below the waterline, wanted {low} to {high} "
                    "(0.0 is a straight-sided disc)"),
}


def measured(got: dict) -> dict[str, float]:
    """The numbers a brief talks about, out of the numbers Blender reports."""
    size, centre = got["size"], got["centre"]
    shape = got.get("shape") or {}
    numbers = {
        "size_x": size[0], "size_y": size[1], "size_z": size[2],
        "centre_x": centre[0], "centre_y": centre[1], "centre_z": centre[2],
        "top_z": centre[2] + size[2] / 2.0,
        "bottom_z": centre[2] - size[2] / 2.0,
    }
    for key in ("coast_variation", "coast_wobble", "top_roll", "skirt_taper"):
        if key in shape:
            numbers[key] = shape[key]
    return numbers


def shortfalls(report: dict, wanted: dict) -> list[str]:
    """Where the scene does not match the brief, in the brief's own numbers.

    Kept apart from `Applied.complaints`, which polices what Roblox will not
    accept however the game is designed. This polices what THIS game asked
    for, so the answer lives in a data file next to the island rather than in
    the code, and a second island is a second file.

    It exists because a model built the shell 370 studs across when the brief
    said 260, and everything automatic was happy: the mesh was triangulated,
    flat shaded, under budget and clean. Nothing was checking the size, so the
    run reported success and the island was 42% too big.
    """
    objects = report.get("objects") or {}
    problems: list[str] = []

    # Blender's answer to a name already taken is to add .001, so a model that
    # builds instead of replacing leaves the old one behind and the new one
    # beside it. Six attempts at one island left five of them stacked up, and
    # nothing noticed, because the brief names Island_Terrain and every check
    # obediently looked at Island_Terrain.
    for name in sorted(objects):
        stem = name.rsplit(".", 1)[0]
        if stem in wanted and stem != name:
            problems.append(f"{name}: a leftover copy of {stem}; delete it, "
                            f"there must be exactly one")

    for name in sorted(wanted):
        rules = wanted[name]
        got = objects.get(name)
        if got is None:
            problems.append(f"{name}: missing from the file")
            continue
        numbers = measured(got)
        for key in sorted(rules):
            if key == "max_triangles":
                if got["triangles"] > rules[key]:
                    problems.append(f"{name}: {got['triangles']} triangles, over the {rules[key]} allowed")
                continue
            if key not in MEASURES:
                problems.append(f"{name}: '{key}' is not something that is measured")
                continue
            if key not in numbers:
                # An older report, from before the shape was measured. Saying
                # so beats passing it, which would call an unchecked island
                # checked.
                problems.append(f"{name}: '{key}' was not measured in this file; rebuild it")
                continue
            low, high = rules[key]
            value = round(numbers[key], 2)
            if not (low <= value <= high):
                problems.append(f"{name}: " + MEASURES[key].format(got=value, low=low, high=high))
    return problems


def blender_executable() -> Path:
    override = os.environ.get("BLENDER_EXE", "").strip()
    if override:
        found = Path(override)
        if found.is_file():
            return found
        raise NoBlender(f"BLENDER_EXE points at {found}, which is not a file")
    for root in BLENDER_SEARCH:
        if not root.is_dir():
            continue
        for candidate in sorted(root.glob("*/blender.exe"), reverse=True):
            return candidate
    raise NoBlender("Blender was not found; set BLENDER_EXE to its blender.exe")


def _blender_version() -> str:
    """Which Blender the script will actually run in, from its own folder name.

    A model writing for the wrong version reaches for API that was renamed --
    mathutils.noise.noise2 stopped one script dead on the first retry -- and
    the fastest cure is telling it which Blender this is.
    """
    try:
        return blender_executable().parent.name.replace("Blender ", "") or "4.x"
    except NoBlender:
        return "4.x"


def code_from(answer: str) -> str:
    """The script out of the answer.

    Some models fence it, some add prose, and one thinks out loud in a
    <think> block first. The longest python block wins; with no fence at all
    the whole answer is taken, because some answers are simply code.
    """
    without_thinking = re.sub(r"<think>.*?</think>", "", answer, flags=re.S)
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", without_thinking, flags=re.S)
    if blocks:
        return max(blocks, key=len).strip()
    return without_thinking.strip()


def why_it_failed(output: str, returncode: int) -> str:
    """The line that says what went wrong, not the last line that says so.

    Blender's final word on a broken script is always "Error: script failed,
    file: ...", which names the file and not the fault. Taking the last line
    matching "Error" therefore fed the model its own filename three attempts
    running while the real cause -- "AttributeError: module 'mathutils.noise'
    has no attribute 'noise2'" -- sat a few lines above, unread.

    So the exception is looked for first, with the line of the script it came
    from, and the generic complaint is only a fallback.
    """
    lines = [line.rstrip() for line in output.splitlines()]
    exception = next((line.strip() for line in reversed(lines)
                      if re.match(r"^\s*[A-Za-z_][A-Za-z0-9_.]*(Error|Exception)\b.*:", line)), "")
    where = next((line.strip() for line in reversed(lines)
                  if re.search(r'File ".*", line \d+', line)), "")
    if exception:
        return f"{exception}{('  [' + where + ']') if where else ''}"[:400]
    generic = next((line.strip() for line in reversed(lines)
                    if "Error" in line or "error:" in line), "")
    return (generic or f"Blender exited {returncode}")[:400]


def scene_note(report: dict) -> str:
    """What the file already holds, for the next prompt to build on."""
    objects = report.get("objects") or {}
    if not objects:
        return "The file is empty: nothing has been built yet."
    lines = [f"The file already holds {len(objects)} object(s), "
             f"{report.get('total_triangles', 0)} triangles in total:"]
    for name, got in sorted(objects.items()):
        lines.append(f"  {name}: {got['size'][0]} x {got['size'][1]} x {got['size'][2]} studs, "
                     f"centred at ({got['centre'][0]}, {got['centre'][1]}, {got['centre'][2]}), "
                     f"{got['triangles']} triangles")
    lines.append("Leave these alone unless the instruction below asks for them.")
    return "\n".join(lines)


def read_scene(blend: Path) -> dict:
    """What is in the file now, measured by Blender itself."""
    if not blend.is_file():
        return {}
    ran, _error, report = run_script("pass  # only measuring\n", blend, save=False)
    return report if ran else {}


def _invoke(script_text: str, blend: Path, *, name: str, timeout: float) -> tuple[bool, str, str]:
    """Run one script in Blender and hand back everything it printed.

    Every trip into Blender goes through here, so a build, a measurement and an
    export fail the same way and read the fault out of the output the same way,
    rather than each inventing its own.
    """
    executable = blender_executable()
    blend.parent.mkdir(parents=True, exist_ok=True)
    script = blend.parent / f"{blend.stem}_{name}.py"
    script.write_text(script_text, encoding="utf-8")

    command = [str(executable), "--background"]
    if blend.is_file():
        command.append(str(blend))
    else:
        command.append("--factory-startup")
    command += ["--python-exit-code", "1", "--python", str(script)]

    try:
        done = subprocesses.run(command, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"Blender did not finish within {timeout:.0f}s", ""

    out = done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace")
    if done.returncode != 0:
        return False, why_it_failed(out, done.returncode), out
    return True, "", out


def run_script(code: str, blend: Path, *, save: bool = True, timeout: float = 300.0,
               prelude: str = "") -> tuple[bool, str, dict]:
    """Run one script in Blender against `blend`, and read the scene after.

    An existing file is opened so work accumulates; a missing one starts from
    factory settings and is created by the save at the end.
    """
    epilogue = EPILOGUE if save else EPILOGUE.replace(
        "_bpy.ops.wm.save_as_mainfile(filepath=_BRIDGE_BLEND)", "pass  # measuring only")
    prologue = f"_BRIDGE_BLEND = {str(blend)!r}\n"
    if not blend.is_file():
        # A new file starts EMPTY. Factory settings come with a cube, a camera
        # and a lamp, and the first script politely left the cube alone --
        # correctly, because it had been told not to clear a scene it did not
        # put there.
        prologue += ("import bpy as _startup\n"
                     "_startup.ops.wm.read_factory_settings(use_empty=True)\n")
    # The prelude is the caller's setup -- a kit put on the path, an island
    # loaded -- run before the model's code so that code can lean on it.
    ran, error, out = _invoke(f"{prologue}\n{prelude}\n{code}{epilogue}", blend,
                              name="last_script", timeout=timeout)
    report: dict = {}
    match = re.search(r"BRIDGE_REPORT_BEGIN\s*(\{.*?\})\s*BRIDGE_REPORT_END", out, flags=re.S)
    if match:
        try:
            report = json.loads(match.group(1))
        except ValueError:
            report = {}
    if not ran:
        return False, error, report
    return True, "", report


async def ask(prompt: str, blend: Path, settings: Settings, *,
              only: str = "", feedback: str = "", prelude: str = "", guide: str = "") -> Applied:
    """One turn: ask whoever answers, run what they wrote, measure the scene.

    Providers are tried in the order the settings name them, and a model that
    is unavailable -- an exhausted free tier, a model at capacity -- hands over
    to the next rather than ending the turn.
    """
    import time

    scene = scene_note(read_scene(blend))
    body = f"{scene}\n\nWhat to do now:\n{prompt}"
    if guide:
        body = f"{guide}\n\n{body}"
    if feedback:
        body += ("\n\nYour previous script was run and the scene was measured. "
                 f"Fix these and answer with the whole script again:\n{feedback}")

    attempt = Applied(prompt=prompt)
    passed_over: list[str] = []
    for provider in COMPAT_PROVIDERS:
        key_field, url_field, _models = COMPAT_PROVIDERS[provider]
        key = str(getattr(settings, key_field) or "").strip()
        if not key:
            continue
        for model in compat_model_names(provider, settings):
            if only and only.lower() not in f"{provider}/{model}".lower():
                continue
            client = OpenAICompatClient(
                provider=provider, base_url=str(getattr(settings, url_field)), api_key=key,
                timeout=settings.engineer_compat_timeout_seconds,
                max_output_tokens=settings.engineer_compat_max_output_tokens)
            began = time.monotonic()
            try:
                reply = await client.generate(model=model, system=SYSTEM.format(version=_blender_version()), prompt=body,
                                              json_output=False)
            except (GeminiUnavailable, GeminiIncomplete) as exc:
                passed_over.append(f"{provider}/{model}: {str(exc)[:120]}")
                continue
            except GeminiRefused as exc:
                attempt.error = f"{provider}/{model} refused: {exc}"
                return attempt
            finally:
                await client.close()
                attempt.seconds = time.monotonic() - began

            attempt.provider, attempt.model = provider, model
            attempt.code = code_from(reply.text)
            if not attempt.code:
                attempt.error = "the answer carried no code"
                return attempt
            attempt.ran, attempt.error, attempt.report = run_script(attempt.code, blend, prelude=prelude)
            return attempt

    attempt.error = "no configured model answered: " + ("; ".join(passed_over) or "none is configured")
    return attempt


# One file per object, because a Roblox MeshPart is one mesh. The axes are
# turned on the way out: Blender is Z-up and Roblox is Y-up, so a model that
# built the island correctly in Blender would otherwise arrive in Studio lying
# on its side.
#
# The default turn is forward -Z, up Y, and the sign was measured rather than
# guessed. Exporting the island with forward +Z put its centre at x = -2.21
# where Blender holds it at x = 2.21: east and west had swapped. Keeping both
# X and "+Y is south" is not available, because going from a right-handed
# Z-up space to a right-handed Y-up one costs one sign somewhere, and paying
# it with a mirror would flip every face's winding and light the island from
# the inside. So X and height are kept, and Blender +Y lands on Roblox north.
EXPORT = '''

# ---- added by app/blender/bridge.py ----
import json as _json

import bpy as _bpy

_FORWARD = {"X": "X", "Y": "Y", "Z": "Z",
            "-X": "NEGATIVE_X", "-Y": "NEGATIVE_Y", "-Z": "NEGATIVE_Z"}

_written = {}
_failed = {}
_wanted = set(_BRIDGE_ONLY)
for _obj in list(_bpy.data.objects):
    if _obj.type != "MESH":
        continue
    if _wanted and _obj.name not in _wanted:
        continue
    _mesh = _obj.data
    _tris = sum(1 for _p in _mesh.polygons if len(_p.vertices) == 3)
    for _other in _bpy.context.view_layer.objects:
        _other.select_set(False)
    _obj.select_set(True)
    _bpy.context.view_layer.objects.active = _obj
    _path = _BRIDGE_OUT + "/" + _obj.name + "." + _BRIDGE_FORMAT
    try:
        if _BRIDGE_FORMAT == "obj":
            _bpy.ops.wm.obj_export(
                filepath=_path, export_selected_objects=True, apply_modifiers=True,
                export_triangulated_mesh=True, export_materials=False,
                forward_axis=_FORWARD[_BRIDGE_FORWARD], up_axis=_FORWARD[_BRIDGE_UP])
        else:
            _bpy.ops.export_scene.fbx(
                filepath=_path, use_selection=True, apply_unit_scale=True,
                mesh_smooth_type="FACE", use_triangles=True,
                axis_forward=_BRIDGE_FORWARD, axis_up=_BRIDGE_UP)
    except Exception as _exc:  # noqa: BLE001 - one bad object must not lose the rest
        _failed[_obj.name] = str(_exc)[:200]
        continue
    _written[_obj.name] = {"path": _path, "triangles": _tris, "polygons": len(_mesh.polygons)}

print("BRIDGE_EXPORT_BEGIN")
print(_json.dumps({"files": _written, "refused": _failed}))
print("BRIDGE_EXPORT_END")
'''


EXPORT_SCENE = '''

# ---- added by app/blender/bridge.py ----
import json as _json

import bpy as _bpy

_report = {"objects": {}, "total_triangles": 0}
for _obj in _bpy.data.objects:
    if _obj.type != "MESH":
        continue
    _tris = sum(1 for _p in _obj.data.polygons if len(_p.vertices) == 3)
    _report["objects"][_obj.name] = {
        "triangles": _tris,
        "polygons": len(_obj.data.polygons),
        "all_triangles": _tris == len(_obj.data.polygons) and len(_obj.data.polygons) > 0,
    }
    _report["total_triangles"] += _tris

# Objects in an excluded collection are the reference water and must not
# leave the file. The exporter's own filter is use_visible, so exclusion in
# the view layer is what decides it, not a name we agree to be careful about.
_excluded = []


def _walk(_layer):
    for _child in _layer.children:
        if _child.exclude:
            for _obj in _child.collection.all_objects:
                _excluded.append(_obj.name)
        else:
            _walk(_child)


_walk(_bpy.context.view_layer.layer_collection)
for _name in _excluded:
    _report["objects"].pop(_name, None)
_report["excluded"] = _excluded
_report["total_triangles"] = sum(_got["triangles"] for _got in _report["objects"].values())

_bpy.ops.export_scene.fbx(
    filepath=_BRIDGE_FILE, use_selection=False, use_visible=True,
    object_types={"MESH"}, bake_space_transform=True, use_triangles=True,
    mesh_smooth_type=_BRIDGE_SMOOTHING, use_mesh_modifiers=True, add_leaf_bones=False, bake_anim=False,
    path_mode="COPY", embed_textures=_BRIDGE_EMBED,
    axis_forward=_BRIDGE_FORWARD, axis_up=_BRIDGE_UP)

print("BRIDGE_EXPORT_BEGIN")
print(_json.dumps(_report))
print("BRIDGE_EXPORT_END")
'''


def export_scene(blend: Path, out_file: Path, *, forward: str = "-Z", up: str = "Y",
                 embed: bool = True, timeout: float = 600.0, smoothing: str = "FACE") -> dict:
    """Everything in one FBX, the way Roblox's importer wants it.

    Separate from `export` because the two answer different questions. One
    MeshPart per file is what you want when the game loads meshes one at a
    time; one file holding the lot is what you want when a person is importing
    a scene into Studio and expects the pieces to arrive together and in
    place.

    What comes back is the per-object triangle count measured in the file, and
    the size of the FBX read off the disk, so an export that Blender announced
    and did not write is a failure here too.
    """
    if not blend.is_file():
        return {"objects": {}, "error": f"{blend} does not exist", "bytes": 0}
    out_file.parent.mkdir(parents=True, exist_ok=True)

    prologue = "\n".join([
        f"_BRIDGE_FILE = {str(out_file)!r}",
        f"_BRIDGE_FORWARD = {forward!r}",
        f"_BRIDGE_UP = {up!r}",
        f"_BRIDGE_EMBED = {bool(embed)!r}",
        # "FACE" writes flat facets; "OFF" writes the mesh's own normals, which
        # is what carries smooth shading and sharp edges through to Roblox.
        f"_BRIDGE_SMOOTHING = {smoothing!r}",
    ])
    ran, error, out = _invoke(prologue + EXPORT_SCENE, blend, name="last_fbx", timeout=timeout)
    result: dict = {"objects": {}, "excluded": [], "total_triangles": 0,
                    "error": "" if ran else error, "bytes": 0}
    match = re.search(r"BRIDGE_EXPORT_BEGIN\s*(\{.*?\})\s*BRIDGE_EXPORT_END", out, flags=re.S)
    if match:
        try:
            result.update(json.loads(match.group(1)))
        except ValueError:
            pass
    if out_file.is_file():
        result["bytes"] = out_file.stat().st_size
    elif not result["error"]:
        result["error"] = "Blender exited cleanly but wrote no file"
    return result


def export(blend: Path, out_dir: Path, *, only: tuple[str, ...] = (), fmt: str = "obj",
           forward: str = "-Z", up: str = "Y", timeout: float = 300.0) -> dict:
    """Write each mesh in `blend` out as its own file, and measure what landed.

    What comes back is read off the disk afterwards -- the file is stat-ed for
    its size -- so an export that Blender reported and never wrote is visible
    as a missing file rather than a success.
    """
    if fmt not in ("obj", "fbx"):
        raise ValueError(f"{fmt} is not a format this exports; obj or fbx")
    if not blend.is_file():
        return {"files": {}, "refused": {}, "error": f"{blend} does not exist"}
    out_dir.mkdir(parents=True, exist_ok=True)

    prologue = "\n".join([
        f"_BRIDGE_OUT = {str(out_dir)!r}",
        f"_BRIDGE_FORMAT = {fmt!r}",
        f"_BRIDGE_ONLY = {list(only)!r}",
        f"_BRIDGE_FORWARD = {forward!r}",
        f"_BRIDGE_UP = {up!r}",
    ])
    ok, error, out = _invoke(prologue + EXPORT, blend, name="last_export", timeout=timeout)
    result: dict = {"files": {}, "refused": {}, "error": "" if ok else error}
    match = re.search(r"BRIDGE_EXPORT_BEGIN\s*(\{.*?\})\s*BRIDGE_EXPORT_END", out, flags=re.S)
    if match:
        try:
            result.update(json.loads(match.group(1)))
        except ValueError:
            pass
    # Measured on disk, not taken from the run: a named file that is not there
    # is a failure however cleanly Blender exited.
    for name, got in list(result["files"].items()):
        written = Path(got["path"])
        if not written.is_file():
            result["refused"][name] = "Blender named the file but it is not on disk"
            del result["files"][name]
            continue
        got["bytes"] = written.stat().st_size
    return result


__all__ = ["Applied", "NoBlender", "ask", "blender_executable", "code_from", "export",
           "export_scene", "read_scene", "run_script", "scene_note"]
