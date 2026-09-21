"""Render a .blend to a PNG so the island can be looked at, not just measured.

    python scripts/blender_render.py data/blender/haven_bay.blend out.png
    python scripts/blender_render.py data/blender/haven_bay.blend out.png --view spawn

Views are data: an overview from the south-east, high up; a top-down map; and
the spawn camera the scene carries. The render uses Workbench with each
material's own colour, which is fast in a headless run and shows the flat
low-poly look as Roblox will, without lighting tricks the game will not have.
Nothing in the .blend is changed: the file is opened, rendered, and closed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.blender import bridge  # noqa: E402

# (camera location, point looked at, lens) -- or "scene" for the file's camera.
VIEWS = {
    "overview": ((330, -470, 300), (0, -10, 0), 30),
    "close": ((210, -260, 150), (0, 10, 8), 30),
    "hub": ((70, -110, 55), (0, 10, 10), 26),
    "lawn": ((30, -190, 40), (0, -50, 6), 28),
    "plots": ((-40, -20, 60), (-120, -40, 6), 26),
    "sanctuary": ((0, 5, 30), (60, 20, 18), 26),
    "sandbar": ((235, -165, 40), (182, -100, -2), 28),
    "pond": ((10, 96, 75), (6, 118, 23), 30),
    "farm": ((-20, 36, 28), (-48, 60, 12), 28),
    "jetty": ((222, -72, 22), (194, -42, 3), 28),
    "south": ((0, -560, 190), (0, -40, 0), 32),
    "top": ((0, 0, 900), (0, 0.01, 0), 30),
    "spawn": "scene",
}

RENDER = '''
import bpy
from mathutils import Vector

scene = bpy.context.scene
if {engine!r} == "eevee":
    # Lit by the scene's own sun and sky, the way the island is meant to look.
    scene.render.engine = "BLENDER_EEVEE"
    try:
        scene.eevee.taa_render_samples = 16
    except AttributeError:
        pass
else:
    scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = {light!r}
if {light!r} == "STUDIO":
    try:
        scene.display.shading.studio_light = {studio!r}
    except TypeError:
        pass
scene.display.shading.color_type = "MATERIAL"
scene.display.shading.show_shadows = {shadows}
scene.display.shading.show_cavity = False
# Standard, not AgX: the palette is picked as hex, and AgX's filmic curve
# greys it down -- #50DCF0 came out as North Sea slate.
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.view_settings.exposure = {exposure}
scene.render.resolution_x = {width}
scene.render.resolution_y = {height}
scene.render.film_transparent = False
if scene.world is not None:
    scene.world.color = (0.45, 0.72, 1.0)

view = {view!r}
if view != "scene":
    (loc, look, lens) = view
    cam_data = bpy.data.cameras.new("_render_cam")
    cam_data.lens = lens
    cam_data.clip_end = 5000
    cam = bpy.data.objects.new("_render_cam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = loc
    cam.rotation_euler = (Vector(look) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam
scene.render.filepath = {out!r}
bpy.ops.render.render(write_still=True)
print("RENDERED", {out!r})
'''


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("blend", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--view", default="overview", choices=sorted(VIEWS))
    parser.add_argument("--engine", default="workbench", choices=("eevee", "workbench"))
    parser.add_argument("--exposure", type=float, default=0.0)
    parser.add_argument("--light", default="STUDIO", choices=("STUDIO", "FLAT", "MATCAP"))
    parser.add_argument("--studio", default="outdoor.sl")
    parser.add_argument("--no-shadows", action="store_true")
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=900)
    args = parser.parse_args(argv[1:])

    out = args.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    script = RENDER.format(width=args.width, height=args.height, view=VIEWS[args.view], out=str(out),
                           engine=args.engine, exposure=args.exposure,
                           light=args.light, studio=args.studio,
                           shadows=not args.no_shadows)
    ran, error, _output = bridge._invoke(script, args.blend.resolve(), name="last_render", timeout=600)
    if not ran:
        print(error)
        return 1
    if not out.is_file():
        print("Blender finished but wrote no image")
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
