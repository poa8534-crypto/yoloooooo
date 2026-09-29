"""An animated preview of the Void rod (the motion the game will play).

    blender -b --python blender/rods/new/void_anim.py
    (then python joins the frames into renders/void_anim.gif -- done at the end here too if Pillow is found)

Orbit rings spin about their own axis, the floating shards bob and turn, the
sparks drift towards the tip and twinkle, the glow breathes. Frames go to
renders/void_anim/####.png.
"""

import math
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
FRAMES = HERE / "renders" / "void_anim"
FRAMES.mkdir(parents=True, exist_ok=True)
N = 36

bpy.ops.wm.open_mainfile(filepath=str(HERE / "void_rod.blend"))
scene = bpy.context.scene
rod = bpy.data.objects["Rod"]
cam = scene.camera


def pose(tip_dir):
    t = Vector(tip_dir).normalized()
    down = Vector((0, 0, -1))
    reel = (down - t * down.dot(t)).normalized()
    x = -reel
    y = t.cross(x).normalized()
    return Matrix((x, y, t)).transposed().to_4x4()


rod.matrix_world = pose((0.85, 0.15, 0.5))
bpy.context.view_layer.update()
focus = rod.matrix_world @ Vector((0, 0, 3.6))
d = Vector((0.1, 1.0, 0.25)).normalized()
cam.data.lens = 32
cam.location = focus - d * (3.0 / math.tan(cam.data.angle / 2))
cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
scene.render.resolution_x, scene.render.resolution_y = 960, 540

kids = [o for o in rod.children if "vfx" in o]
base = {o.name: o.matrix_local.copy() for o in kids}
glow = bpy.data.materials["Glow"].node_tree.nodes["Principled BSDF"]
glow_base = glow.inputs["Emission Strength"].default_value

for f in range(N):
    t = f / N
    for i, o in enumerate(kids):
        m0 = base[o.name]
        kind = o["vfx"]
        if kind == "orbit":
            o.matrix_local = m0 @ Matrix.Rotation(2 * math.pi * t * (1 if i % 2 else -1), 4, "Z")
        elif kind == "float":
            phase = i * 0.7
            lift = Matrix.Translation((0, 0, 0.05 * math.sin(2 * math.pi * t + phase)))
            c = m0.to_translation()
            spin = Matrix.Translation(c) @ Matrix.Rotation(0.12 * math.sin(2 * math.pi * t + phase), 4, "Z") \
                @ Matrix.Translation(-c)
            o.matrix_local = lift @ spin @ m0
        elif kind == "particle":
            c = m0.to_translation()
            z = (c.z + 1.2 * t) % 10.6 - 0.3
            s = 0.6 + 0.4 * abs(math.sin(2 * math.pi * t * 2 + i))
            o.matrix_local = Matrix.Translation((c.x, c.y, z)) @ Matrix.Scale(s, 4)
    glow.inputs["Emission Strength"].default_value = glow_base * (0.8 + 0.35 * (0.5 + 0.5 * math.sin(2 * math.pi * t)))
    scene.render.filepath = str(FRAMES / f"{f:04d}.png")
    bpy.ops.render.render(write_still=True)
print("FRAMES", N)
