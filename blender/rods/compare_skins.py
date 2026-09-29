"""Before/after renders for the rod skins.

    blender -b --python blender/rods/compare_skins.py
    python blender/rods/compare_sheet.py      (joins them into labelled sheets)

BEFORE: the owner's raw skin (output.obj), stood up the old way.
AFTER: the repaired skin (repair_skins.py output), standing Z-up.
Each is rendered alone, from the front (+Y) and the side (+X), at the same
framing, into blender/rods/out/inspect/<folder>_<before|after>_<view>.png.
"""

import math
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "blender" / "rods" / "out" / "inspect"
RAW_TURN = {"Lava rod": Matrix.Identity(4), "Void rod": Matrix.Rotation(math.radians(-90), 4, "Y")}


def scene_setup():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    engines = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    scene.render.resolution_x = 600
    scene.render.resolution_y = 1000
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.55, 0.6, 0.66, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 1.2
    scene.world = world
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3
    sun.rotation_euler = (math.radians(50), 0, math.radians(30))
    scene.collection.objects.link(sun)
    return scene


def stand(obj):
    """Butt at z = 0, centred on the Z axis, 2 units tall."""
    mesh = obj.data
    zs = [v.co.z for v in mesh.vertices]
    xs = [v.co.x for v in mesh.vertices]
    ys = [v.co.y for v in mesh.vertices]
    mesh.transform(Matrix.Translation(Vector((-(min(xs) + max(xs)) / 2, -(min(ys) + max(ys)) / 2, -min(zs)))))
    s = 2 / (max(zs) - min(zs))
    mesh.transform(Matrix.Scale(s, 4))


def shoot(scene, tag):
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 2.3
    scene.collection.objects.link(cam)
    scene.camera = cam
    for view, direction in (("front", Vector((0, -1, 0))), ("side", Vector((1, 0, 0)))):
        cam.location = Vector((0, 0, 1)) + direction * 6
        cam.rotation_euler = (Vector((0, 0, 1)) - cam.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = str(OUT / f"{tag}_{view}.png")
        bpy.ops.render.render(write_still=True)


for folder, turn in RAW_TURN.items():
    tag = folder.replace(" ", "_")
    scene = scene_setup()
    bpy.ops.wm.obj_import(filepath=str(ROOT / folder / "output.obj"))
    raw = [o for o in bpy.data.objects if o.type == "MESH"][0]
    raw.data.transform(raw.matrix_world)
    raw.matrix_world = Matrix.Identity(4)
    raw.data.transform(turn)
    stand(raw)
    shoot(scene, f"{tag}_before")

    scene = scene_setup()
    bpy.ops.wm.obj_import(
        filepath=str(ROOT / "blender" / "rods" / "out" / "repaired" / folder / "output.obj"), forward_axis="Y", up_axis="Z"
    )
    fixed = [o for o in bpy.data.objects if o.type == "MESH"][0]
    fixed.data.transform(fixed.matrix_world)
    fixed.matrix_world = Matrix.Identity(4)
    stand(fixed)
    shoot(scene, f"{tag}_after")
    print("COMPARED", folder)
print("done")
