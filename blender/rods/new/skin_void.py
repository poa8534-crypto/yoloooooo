"""The Void skin, on the base rod (run build_base_rod.py first).

    blender -b --python blender/rods/new/skin_void.py

Opens base_rod.blend and turns it into the Void rod:

  * materials: the shell becomes black-violet crystal with a magenta rim
    (a fresnel glow -- in Roblox a pink Highlight does the same job), the
    accents and every glow part burn hot magenta, the line glows pink;
  * floating crystal shards (they are separate parts so the game can bob and
    turn them): a crown of blades around the receiver, a spiral climbing the
    blank, two trailing off the stock, and a crystal spear for a tip;
  * void orbs set into both sides of the receiver, each ringed by glow;
  * orbit rings round the blank and the reel (the game spins them);
  * drifting particles (for the render; in the game these are ParticleEmitters).

Every added part carries `section` (where it sits on the rod) and `vfx`
("float", "orbit", "particle" or "static") for the Roblox build.
Renders into blender/rods/new/renders/void_*.png and saves void_rod.blend.
"""

import math
import random
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
RENDERS = HERE / "renders"
bpy.ops.wm.open_mainfile(filepath=str(HERE / "base_rod.blend"))
scene = bpy.context.scene
rod = bpy.data.objects["Rod"]
rng = random.Random(7)

SECTIONS = [("Handle", -1.0), ("Segment1", 3.15), ("Segment2", 4.85), ("Segment3", 6.55), ("Tip", 8.25)]
MAGENTA = (1.0, 0.18, 0.85)
VIOLET = (0.45, 0.12, 1.0)


def section_of(z):
    s = "Handle"
    for name, z0 in SECTIONS:
        if z >= z0:
            s = name
    return s


# ------------------------------------------------------------------ materials


def bsdf(mat):
    return mat.node_tree.nodes.get("Principled BSDF")


def set_mat(name, color, metallic, rough, emission=None, strength=0.0):
    m = bpy.data.materials[name]
    b = bsdf(m)
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    if emission:
        b.inputs["Emission Color"].default_value = (*emission, 1)
    b.inputs["Emission Strength"].default_value = strength
    return m


def rim_glow(mat, color, power):
    """Emission that rises at grazing angles: a glowing rim on dark crystal."""
    nt = mat.node_tree
    b = bsdf(mat)
    lw = nt.nodes.new("ShaderNodeLayerWeight")
    lw.inputs["Blend"].default_value = 0.5
    sharp = nt.nodes.new("ShaderNodeMath")
    sharp.operation = "POWER"
    sharp.inputs[1].default_value = 3.0
    nt.links.new(lw.outputs["Facing"], sharp.inputs[0])
    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    mul.inputs[1].default_value = power
    nt.links.new(sharp.outputs[0], mul.inputs[0])
    nt.links.new(mul.outputs[0], b.inputs["Emission Strength"])
    b.inputs["Emission Color"].default_value = (*color, 1)


shell = set_mat("Shell", (0.018, 0.008, 0.05), 0.0, 0.42)
rim_glow(shell, MAGENTA, 1.3)
set_mat("Dark", (0.004, 0.002, 0.012), 0.0, 0.5)
set_mat("Accent", MAGENTA, 0.0, 0.3, (1.0, 0.1, 0.75), 4.5)
set_mat("Metal", (0.25, 0.18, 0.4), 1.0, 0.12)
set_mat("Glow", (1.0, 0.2, 0.85), 0.0, 0.3, (1.0, 0.12, 0.8), 7.0)
set_mat("Line", (1.0, 0.5, 1.0), 0.0, 0.3, (1.0, 0.3, 0.95), 2.5)


def new_mat(name, color, metallic, rough, emission=None, strength=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = bsdf(m)
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    if emission:
        b.inputs["Emission Color"].default_value = (*emission, 1)
        b.inputs["Emission Strength"].default_value = strength
    return m


CRYSTAL = new_mat("VoidCrystal", (0.03, 0.02, 0.32), 0.4, 0.05)
rim_glow(CRYSTAL, VIOLET, 4.0)
CRYSTAL_HOT = new_mat("VoidCrystalHot", (0.5, 0.1, 0.9), 0.0, 0.1, (0.6, 0.15, 1.0), 5.0)
ORB = new_mat("VoidOrb", (0.0, 0.0, 0.0), 0.0, 0.05)
rim_glow(ORB, MAGENTA, 8.0)
SPARK = new_mat("VoidSpark", (1, 0.4, 0.95), 0.0, 0.3, (1.0, 0.2, 0.85), 9.0)
SPARK_V = new_mat("VoidSparkViolet", (0.6, 0.4, 1), 0.0, 0.3, (0.5, 0.25, 1.0), 9.0)
GLOW = bpy.data.materials["Glow"]
# Deep blacks: less sky reflection on the dark surfaces.
for name in ("Shell", "Dark"):
    b = bsdf(bpy.data.materials[name])
    if "Specular IOR Level" in b.inputs:
        b.inputs["Specular IOR Level"].default_value = 0.18
# A tighter bloom: glow around the glowing parts, not a haze over the rod.
comp = getattr(scene, "compositing_node_group", None) or getattr(scene, "node_tree", None)
if comp:
    for n in comp.nodes:
        if n.bl_idname == "CompositorNodeGlare":
            for key, value in (("Type", "Fog Glow"), ("Strength", 1.1), ("Size", 0.5), ("Threshold", 1.5)):
                if n.inputs.get(key) is not None:
                    n.inputs[key].default_value = value

# ------------------------------------------------------------------ building blocks

added = []


def adopt(obj, mat, vfx, smooth=25):
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    obj.parent = rod
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(smooth))
    except Exception:
        pass
    centre = obj.matrix_world @ (sum((Vector(c) for c in obj.bound_box), Vector()) / 8)
    obj["section"] = section_of(centre.z)
    obj["vfx"] = vfx
    added.append(obj)
    return obj


def shard(name, base, direction, length, width, mat, vfx="float", sides=5, lean=0.0):
    """A crystal shard: a jagged bipyramid from `base` pointing along `direction`."""
    bm = bmesh.new()
    d = Vector(direction).normalized()
    u = d.orthogonal().normalized()
    w = d.cross(u)
    mid_at = length * rng.uniform(0.22, 0.38)
    ring_pts = []
    for i in range(sides):
        a = 2 * math.pi * i / sides + rng.uniform(-0.25, 0.25)
        r = width * rng.uniform(0.7, 1.15)
        ring_pts.append(bm.verts.new(Vector(base) + d * (mid_at + rng.uniform(-0.03, 0.03) * length)
                                     + (u * math.cos(a) + w * math.sin(a)) * r))
    tip = bm.verts.new(Vector(base) + d * length + u * lean * length)
    root = bm.verts.new(Vector(base) - d * length * 0.08)
    for i in range(sides):
        a, b = ring_pts[i], ring_pts[(i + 1) % sides]
        bm.faces.new((a, b, tip))
        bm.faces.new((b, a, root))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    o = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(o)
    return adopt(o, mat, vfx, smooth=5)


def torus(name, centre, major, minor, normal, mat, vfx, dashes=3, gap=0.45):
    """A dashed ring (arcs with gaps), so its spin can be seen. `vfx` says
    which way the game spins it: "orbit_axial" about the rod's direction,
    "orbit_side" about the line from the rod's axis out to the ring."""
    n = Vector(normal).normalized()
    u = n.orthogonal().normalized()
    w = n.cross(u)
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = minor
    cu.bevel_resolution = 2
    cu.use_fill_caps = True
    span = 2 * math.pi / dashes
    for k in range(dashes):
        a0 = k * span
        a1 = a0 + span * (1 - gap)
        sp = cu.splines.new("POLY")
        pts = [Vector(centre) + (u * math.cos(a) + w * math.sin(a)) * major
               for a in (a0 + (a1 - a0) * i / 16 for i in range(17))]
        sp.points.add(len(pts) - 1)
        for pt, co in zip(sp.points, pts):
            pt.co = (*co, 1)
    cobj = bpy.data.objects.new(name + "_c", cu)
    scene.collection.objects.link(cobj)
    deps = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(cobj.evaluated_get(deps))
    bpy.data.objects.remove(cobj, do_unlink=True)
    o = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(o)
    return adopt(o, mat, vfx, smooth=80)


def sphere(name, centre, radius, mat, vfx="static", subdiv=3):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdiv, radius=radius, location=centre)
    o = bpy.context.active_object
    o.name = name
    return adopt(o, mat, vfx, smooth=80)


# ------------------------------------------------------------------ restyle a few base parts

for o in rod.children:
    if o.name in ("TipSpear",):
        o.hide_render = True
        o["skin_hidden"] = True

# ------------------------------------------------------------------ shards

# A crown of blades around the receiver: swept back, like wings.
for i, (ang, z, length) in enumerate([(30, 2.25, 1.1), (65, 2.6, 0.9), (105, 2.85, 0.7), (150, 2.4, 0.55),
                                      (-30, 2.25, 1.1), (-65, 2.6, 0.9), (-105, 2.85, 0.7), (-150, 2.4, 0.55)]):
    a = math.radians(ang)
    out = Vector((math.cos(a), math.sin(a), 0))
    base = out * 0.26 + Vector((0, 0, z))
    direction = out * 0.75 + Vector((0, 0, -0.55))
    shard(f"CrownShard{i}", base, direction, length, 0.1, CRYSTAL, vfx="float")
# Two thin hot blades riding the top rail.
shard("RailBlade0", (0.24, 0, 3.3), (0.25, 0, 1), 0.9, 0.035, CRYSTAL_HOT, vfx="static", sides=4)
shard("RailBlade1", (0.2, 0, 4.2), (0.3, 0, 1), 0.7, 0.03, CRYSTAL_HOT, vfx="static", sides=4)
# A spiral of shards climbing the blank, smaller towards the tip.
for k in range(16):
    t = k / 15
    z = 4.9 + t * 4.6
    a = k * math.radians(137.5)
    radius = 0.34 - 0.2 * t
    centre = Vector((math.cos(a) * radius, math.sin(a) * radius, z))
    direction = Vector((math.cos(a) * 0.35, math.sin(a) * 0.35, 1)) + Vector((-math.sin(a), math.cos(a), 0)) * 0.4
    shard(f"SpiralShard{k}", centre, direction, 0.36 - 0.2 * t, 0.055 - 0.03 * t,
          CRYSTAL if k % 3 else CRYSTAL_HOT, vfx="float")
# Trailing off the stock.
for side in (1, -1):
    shard(f"StockShard{side}", (0, 0.12 * side, 0.0), (0, 0.7 * side, -1), 0.55, 0.06, CRYSTAL, vfx="float")
# The crystal spear tip.
shard("TipCrystal", (0, 0, 9.92), (0, 0, 1), 0.38, 0.045, CRYSTAL_HOT, vfx="static", sides=6)

# ------------------------------------------------------------------ void orbs and rings

for side in (1, -1):
    c = Vector((0.0, 0.215 * side, 2.55))
    sphere(f"VoidOrb{side}", c, 0.11, ORB)
    torus(f"OrbRingIn{side}", c, 0.14, 0.009, (0, side, 0), GLOW, "orbit_side", dashes=4, gap=0.3)
    torus(f"OrbRingOut{side}", c + Vector((0, 0.01 * side, 0)), 0.19, 0.007, (0, side, 0), GLOW, "orbit_side")
torus("OrbitReel", (-0.58, 0, 2.52), 0.3, 0.009, (0, 0, 1), GLOW, "orbit_axial")
torus("OrbitBlankA", (0, 0, 5.6), 0.36, 0.011, (0, 0, 1), GLOW, "orbit_axial", dashes=2, gap=0.35)
torus("OrbitBlankB", (0, 0, 7.6), 0.26, 0.009, (0, 0, 1), GLOW, "orbit_axial")
torus("TipHalo", (0, 0, 10.05), 0.12, 0.007, (0, 0, 1), GLOW, "orbit_axial", dashes=4, gap=0.3)

# ------------------------------------------------------------------ particles (render stand-ins)

for k in range(95):
    z = rng.uniform(-0.3, 10.3) if k % 3 else rng.uniform(1.6, 3.4)
    a = rng.uniform(0, 2 * math.pi)
    radius = rng.uniform(0.25, 0.85 - 0.04 * z)
    c = (math.cos(a) * radius, math.sin(a) * radius, z)
    sphere(f"Spark{k}", c, rng.uniform(0.006, 0.017), SPARK if k % 3 else SPARK_V, vfx="particle", subdiv=1)

# ------------------------------------------------------------------ render

bpy.context.view_layer.update()
cam = scene.camera
sun = bpy.data.objects["Sun"]
fill = bpy.data.objects["Fill"]
rim = bpy.data.objects["Rim"]
sun.data.energy = 2.6
fill.data.energy = 120
rim.data.energy = 900
rim.data.color = (0.8, 0.5, 1.0)
bpy.data.worlds["Sky"].node_tree.nodes["Background"].inputs["Strength"].default_value = 0.75


def pose(tip_dir):
    t = Vector(tip_dir).normalized()
    down = Vector((0, 0, -1))
    reel = (down - t * down.dot(t)).normalized()
    x = -reel
    y = t.cross(x).normalized()
    return Matrix((x, y, t)).transposed().to_4x4()


def shoot(path, tip_dir, look, res, lens, focus_z, radius, key_from):
    rod.matrix_world = pose(tip_dir)
    bpy.context.view_layer.update()
    focus = rod.matrix_world @ Vector((0, 0, focus_z))
    scene.render.resolution_x, scene.render.resolution_y = res
    d = Vector(look).normalized()
    cam.data.type = "PERSP"
    cam.data.lens = lens
    dist = radius / math.tan(cam.data.angle / 2)
    cam.location = focus - d * dist
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    sun.rotation_euler = (-Vector(key_from)).to_track_quat("-Z", "Y").to_euler()
    fill.location = cam.location + Vector((0, 0, 2))
    fill.rotation_euler = (focus - fill.location).to_track_quat("-Z", "Y").to_euler()
    rim.location = focus + Vector((-d.x, -d.y, 0.3)).normalized() * 9 + Vector((0, 0, 4))
    rim.rotation_euler = (focus - rim.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


shoot(RENDERS / "void_hero.png", (0.85, 0.15, 0.5), (0.1, 1.0, 0.25), (1600, 900), 32, 3.0, 2.6, (0.4, 0.6, 1.0))
shoot(RENDERS / "void_full.png", (0.95, 0.1, 0.3), (0.15, 1.0, 0.05), (2000, 900), 40, 4.7, 5.6, (0.3, 0.5, 1.0))
shoot(RENDERS / "void_handle.png", (0.6, 0.3, 0.75), (0.35, 1.0, 0.1), (1400, 1000), 45, 2.3, 1.5, (0.5, 0.4, 1.0))
shoot(RENDERS / "void_side.png", (1, 0, 0), (0, 1, 0), (2400, 800), 60, 5.0, 5.6, (0.2, 0.6, 1.0))
rod.matrix_world = Matrix.Identity(4)
bpy.ops.wm.save_as_mainfile(filepath=str(HERE / "void_rod.blend"))
counts = {}
for o in added:
    counts[o["vfx"]] = counts.get(o["vfx"], 0) + 1
print("VOID", counts)
