"""The premium rod's base model, built from scratch.

    blender -b --python blender/rods/new/build_base_rod.py

A stylised hard-surface spinning rod in the Sniper Arena look: a rifle-like
body (stock, receiver, handguard, top rail) around a real fishing rod (grip,
compact spinning reel, tapered blank, hexagonal line guides), with chamfered
edges that catch the light, armour plates with gaps, accent stripes and glow
slits. Every part has a material a skin can swap and a section it belongs to:

    Handle   stock, grip, receiver, reel, handguard (everything below the blank)
    Segment1 .. Segment3, Tip   the blank, cut where it bends in the game

Materials: Shell, Dark, Accent, Metal, Glow, Line.
The rod stands on +Z, butt at 0, 10 units long (10 studs in Roblox); the reel
and the guides hang on -X (down, in the hand).

Writes blender/rods/new/base_rod.blend and renders into blender/rods/new/renders/.
"""

import math
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
RENDERS = HERE / "renders"
RENDERS.mkdir(parents=True, exist_ok=True)

LENGTH = 10.0
BLANK_START = 3.15
SECTIONS = [("Handle", -1.0), ("Segment1", BLANK_START), ("Segment2", 4.85), ("Segment3", 6.55), ("Tip", 8.25)]
BEVEL = 0.014

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
rod = bpy.data.objects.new("Rod", None)
scene.collection.objects.link(rod)

# ------------------------------------------------------------------ materials


def material(name, color, metallic=0.0, roughness=0.4, emission=None, strength=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = roughness
    if emission:
        b.inputs["Emission Color"].default_value = (*emission, 1)
        b.inputs["Emission Strength"].default_value = strength
    return m


MAT = {
    "Shell": material("Shell", (0.82, 0.83, 0.82), 0.0, 0.3),
    "Dark": material("Dark", (0.018, 0.019, 0.024), 0.3, 0.4),
    "Accent": material("Accent", (1.0, 0.68, 0.02), 0.1, 0.28),
    "Metal": material("Metal", (0.78, 0.8, 0.83), 1.0, 0.16),
    "Glow": material("Glow", (1.0, 0.85, 0.3), 0.0, 0.3, emission=(1.0, 0.78, 0.2), strength=14.0),
    "Line": material("Line", (0.95, 0.95, 0.95), 0.0, 0.4, emission=(1, 1, 1), strength=0.8),
}

# ------------------------------------------------------------------ building blocks

parts = []


def finish(obj, mat, bevel=BEVEL, smooth_angle=30):
    obj.data.materials.clear()
    obj.data.materials.append(MAT[mat])
    obj.parent = rod
    if bevel:
        mod = obj.modifiers.new("Chamfer", "BEVEL")
        mod.width = bevel
        mod.segments = 2
        mod.limit_method = "ANGLE"
        mod.angle_limit = math.radians(30)
        mod.harden_normals = True
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(smooth_angle))
    except Exception:
        bpy.ops.object.shade_smooth()
    parts.append(obj)
    return obj


def prism(name, r1, r2, z0, z1, mat, sides=8, x=0.0, y=0.0, bevel=BEVEL):
    """A tapered prism along Z (an octagon by default, a flat facing +X)."""
    bpy.ops.mesh.primitive_cone_add(
        vertices=sides, radius1=r1, radius2=r2, depth=z1 - z0, location=(x, y, (z0 + z1) / 2),
        rotation=(0, 0, math.pi / sides),
    )
    o = bpy.context.active_object
    o.name = name
    return finish(o, mat, bevel)


def ring(name, major, minor, z, mat, x=0.0, y=0.0, rot=(0, 0, 0), segs=24, minor_segs=8, bevel=0, smooth=80):
    bpy.ops.mesh.primitive_torus_add(
        major_radius=major, minor_radius=minor, major_segments=segs, minor_segments=minor_segs,
        location=(x, y, z), rotation=rot,
    )
    o = bpy.context.active_object
    o.name = name
    return finish(o, mat, bevel, smooth_angle=smooth)


def tube(name, points, radius, mat, res=4, smooth=70):
    """A bent bar through `points` (the bail, the crank, guide legs, the line)."""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = radius
    cu.bevel_resolution = res
    cu.use_fill_caps = True
    sp = cu.splines.new("POLY")
    sp.points.add(len(points) - 1)
    for p, co in zip(sp.points, points):
        p.co = (*co, 1)
    cobj = bpy.data.objects.new(name + "_c", cu)
    scene.collection.objects.link(cobj)
    deps = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(cobj.evaluated_get(deps))
    bpy.data.objects.remove(cobj, do_unlink=True)
    o = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(o)
    return finish(o, mat, 0, smooth_angle=smooth)


def arc(center, radius, a0, a1, axis_u, axis_v, n=20):
    return [tuple(Vector(center) + (Vector(axis_u) * math.cos(a) + Vector(axis_v) * math.sin(a)) * radius)
            for a in (a0 + (a1 - a0) * i / n for i in range(n + 1))]


def plate(name, outline, thick, mat, angle=0.0, y=0.0, bevel=0.01):
    """A flat piece: `outline` is (x, z) points in the XZ plane, `thick` deep in Y
    (centred on `y`), then turned `angle` about the rod's axis."""
    bm = bmesh.new()
    front = [bm.verts.new((px, y - thick / 2, pz)) for px, pz in outline]
    face = bm.faces.new(front)
    ext = bmesh.ops.extrude_face_region(bm, geom=[face])
    moved = [e for e in ext["geom"] if isinstance(e, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=moved, vec=(0, thick, 0))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    o = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(o)
    o.rotation_euler = (0, 0, angle)
    return finish(o, mat, bevel, smooth_angle=20)


def box(name, size, loc, mat, rot=(0, 0, 0), bevel=0.008):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.name = name
    o.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return finish(o, mat, bevel, smooth_angle=20)


def blank_r(z):
    t = min(1.0, max(0.0, (z - BLANK_START) / (LENGTH - BLANK_START)))
    return 0.1 + (0.028 - 0.1) * t ** 0.8


def shrink(outline, by):
    """An outline pulled `by` towards its middle (for inset panels)."""
    cx = sum(p[0] for p in outline) / len(outline)
    cz = sum(p[1] for p in outline) / len(outline)
    return [(cx + (px - cx) * (1 - by), cz + (pz - cz) * (1 - by)) for px, pz in outline]


# ------------------------------------------------------------------ the stock (butt)

STOCK = [(-0.2, -0.1), (0.2, -0.1), (0.27, 0.02), (0.26, 0.3), (0.21, 0.4), (-0.21, 0.4), (-0.27, 0.28), (-0.26, 0.0)]
plate("Stock", STOCK, 0.36, "Shell", bevel=0.022)
for side in (1, -1):
    plate(f"StockPanel{side}", shrink(STOCK, 0.28), 0.03, "Dark", y=0.18 * side, bevel=0.006)
    box(f"StockGlow{side}", (0.26, 0.012, 0.03), (0.0, 0.197 * side, 0.15), "Glow", bevel=0.004)
box("StockBand", (0.5, 0.39, 0.05), (0.0, 0.0, 0.33), "Accent", bevel=0.01)
prism("StockGem", 0.09, 0.07, -0.16, -0.08, "Glow", sides=6, bevel=0.006)

# ------------------------------------------------------------------ the grip

prism("Grip", 0.185, 0.175, 0.4, 1.78, "Dark", bevel=0.012)
for i, z in enumerate((0.72, 1.1, 1.48)):
    prism(f"GripBand{i}", 0.2, 0.198, z, z + 0.06, "Shell", bevel=0.008)
CHEVRON = [(-0.13, 0.0), (0.0, -0.13), (0.13, 0.0), (0.13, 0.07), (0.0, -0.06), (-0.13, 0.07)]
for side in (1, -1):
    for k, z in enumerate((0.62, 1.0, 1.38)):
        plate(f"GripChevron{side}_{k}", [(px, pz + z) for px, pz in CHEVRON], 0.03, "Accent",
              y=0.183 * side, bevel=0.005)

# ------------------------------------------------------------------ the receiver (reel seat housing)

RECEIVER = [(-0.2, 1.74), (0.2, 1.74), (0.3, 1.86), (0.3, 2.5), (0.25, 2.6), (0.25, 2.98), (0.17, 3.14),
            (-0.17, 3.14), (-0.23, 3.02), (-0.23, 2.55), (-0.3, 2.45), (-0.3, 1.9)]
plate("Receiver", RECEIVER, 0.34, "Shell", bevel=0.024)
for side in (1, -1):
    plate(f"ReceiverPanel{side}", shrink(RECEIVER, 0.22), 0.03, "Dark", y=0.17 * side, bevel=0.006)
    for k, z in enumerate((2.05, 2.25, 2.45)):
        box(f"ReceiverSlit{side}_{k}", (0.3, 0.012, 0.035), (0.02, 0.187 * side, z), "Glow",
            rot=(0, math.radians(-18), 0), bevel=0.004)
    box(f"ReceiverStripe{side}", (0.035, 0.014, 0.52), (0.0, 0.187 * side, 2.82), "Accent", bevel=0.004)
box("ReceiverTop", (0.1, 0.22, 0.62), (0.3, 0.0, 2.18), "Dark", bevel=0.01)
for k in range(5):
    box(f"ReceiverRib{k}", (0.03, 0.24, 0.05), (0.355, 0.0, 1.94 + k * 0.1), "Metal", bevel=0.006)

# ------------------------------------------------------------------ the reel (compact spinning reel, under the receiver)

REEL_X = -0.58
plate("ReelFoot", [(-0.3, 2.1), (-0.3, 2.34), (-0.44, 2.3), (-0.46, 2.14)], 0.12, "Dark", bevel=0.01)
prism("ReelBody", 0.18, 0.2, 1.98, 2.3, "Dark", sides=12, x=REEL_X)
prism("ReelBodyBand", 0.205, 0.205, 2.12, 2.17, "Accent", sides=12, x=REEL_X, bevel=0.005)
prism("ReelRotor", 0.22, 0.2, 2.3, 2.42, "Shell", sides=12, x=REEL_X)
prism("ReelSpool", 0.16, 0.16, 2.42, 2.62, "Metal", sides=16, x=REEL_X)
ring("ReelSpoolGlow", 0.163, 0.01, 2.52, "Glow", x=REEL_X)
prism("ReelSpoolLip", 0.2, 0.19, 2.62, 2.66, "Accent", sides=16, x=REEL_X, bevel=0.005)
prism("ReelCap", 0.09, 0.04, 2.66, 2.75, "Shell", sides=12, x=REEL_X)
tube("ReelBail", arc((REEL_X, 0, 2.5), 0.25, -math.pi / 2, math.pi / 2, (0, 1, 0), (-1, 0, 0.3)), 0.016, "Metal")
tube("ReelCrank", [(REEL_X, 0.2, 2.16), (REEL_X, 0.4, 2.16), (REEL_X - 0.04, 0.46, 1.96)], 0.026, "Metal")
bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=0.055, depth=0.15, location=(REEL_X - 0.04, 0.54, 1.96),
                                    rotation=(math.pi / 2, 0, 0))
knob = bpy.context.active_object
knob.name = "ReelKnob"
finish(knob, "Accent", 0.008)

# ------------------------------------------------------------------ the handguard

prism("Handguard", 0.15, 0.125, 3.14, 4.5, "Shell", bevel=0.012)
prism("HandguardCap", 0.132, 0.11, 4.5, 4.58, "Accent", bevel=0.006)
for side in (1, -1):
    for k in range(4):
        box(f"Vent{side}_{k}", (0.16, 0.02, 0.05), (0.0, 0.13 * side, 3.45 + k * 0.24), "Dark",
            rot=(0, math.radians(-25), 0), bevel=0.005)
    plate(f"Wing{side}", [(-0.06, 4.1), (0.06, 4.1), (0.04, 4.62), (-0.02, 4.75)], 0.3, "Shell",
          angle=math.pi / 2 * side, bevel=0.008)
box("HandguardGlow", (0.015, 0.06, 1.1), (-0.14, 0.0, 3.8), "Glow", bevel=0.004)

# The top rail: a long spine on +X from the receiver to Segment2, the scope silhouette.
RAIL = [(0.1, 3.1), (0.24, 3.12), (0.24, 3.6), (0.2, 4.6), (0.15, 5.4), (0.09, 5.55), (0.09, 3.1)]
plate("Rail", RAIL, 0.1, "Dark", bevel=0.01)
plate("RailEdge", [(0.23, 3.14), (0.26, 3.16), (0.26, 3.58), (0.22, 4.58), (0.17, 5.36), (0.14, 5.36),
                   (0.19, 4.55), (0.23, 3.58)], 0.04, "Glow", bevel=0.004)

# ------------------------------------------------------------------ the blank

cuts = [z for _, z in SECTIONS[1:]] + [LENGTH]
for (name, z0), z1 in zip(SECTIONS[1:], cuts[1:]):
    prism(f"Blank_{name}", blank_r(z0), blank_r(z1), max(z0, 4.5) if name == "Segment1" else z0, z1, "Dark",
          bevel=0.005)
    if name != "Segment1":
        prism(f"Ferrule_{name}", blank_r(z0) + 0.022, blank_r(z0) + 0.016, z0 - 0.05, z0 + 0.05, "Shell",
              bevel=0.005)
        ring(f"FerruleGlow_{name}", blank_r(z0) + 0.021, 0.007, z0, "Glow")

# ------------------------------------------------------------------ hexagonal guides and the tip

guides = [(4.95, 0.15), (5.9, 0.115), (6.8, 0.092), (7.65, 0.075), (8.45, 0.062), (9.15, 0.052), (9.7, 0.045)]
line_points = [(REEL_X, 0, 2.75)]
for i, (z, rr) in enumerate(guides):
    r = blank_r(z)
    cx = -(r + 0.05 + rr)
    ring(f"Guide{i}", rr, rr * 0.22, z, "Shell", x=cx, segs=6, minor_segs=4, smooth=10)
    ring(f"GuideInsert{i}", rr * 0.72, rr * 0.1, z, "Glow", x=cx, segs=6, minor_segs=4, smooth=10)
    plate(f"GuideLeg{i}", [(-r, z - rr * 1.9), (-r, z - rr * 1.5), (cx + rr * 0.9, z + rr * 0.05),
                           (cx + rr * 0.95, z - rr * 0.25)], max(0.02, rr * 0.3), "Dark", bevel=0.004)
    line_points.append((cx, 0, z))
tip_z = LENGTH - 0.05
ring("TipTop", 0.036, 0.011, tip_z, "Shell", x=-blank_r(tip_z) - 0.028, segs=6, minor_segs=4, smooth=10)
prism("TipSpear", blank_r(LENGTH) + 0.018, 0.0, LENGTH - 0.1, LENGTH + 0.14, "Accent", sides=6, bevel=0.003)
line_points.append((-blank_r(tip_z) - 0.028, 0, tip_z))
tube("FishingLine", line_points, 0.005, "Line", res=1)

# Section of every part, for the export later: by the height of its middle.
for o in parts:
    zc = (o.matrix_world @ (sum((Vector(c) for c in o.bound_box), Vector()) / 8)).z
    section = "Handle"
    for name, z0 in SECTIONS:
        if zc >= z0:
            section = name
    o["section"] = section

# ------------------------------------------------------------------ look: sky, light, bloom

engines = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
for attr, value in (("taa_render_samples", 64), ("use_raytracing", True)):
    try:
        setattr(scene.eevee, attr, value)
    except Exception:
        pass
scene.view_settings.view_transform = "Standard"

world = bpy.data.worlds.new("Sky")
world.use_nodes = True
nt = world.node_tree
bg = nt.nodes["Background"]
coord = nt.nodes.new("ShaderNodeTexCoord")
sep = nt.nodes.new("ShaderNodeSeparateXYZ")
ramp = nt.nodes.new("ShaderNodeValToRGB")
ramp.color_ramp.elements[0].color = (0.05, 0.28, 0.85, 1)
ramp.color_ramp.elements[1].color = (0.42, 0.7, 1.0, 1)
nt.links.new(coord.outputs["Window"], sep.inputs[0])
nt.links.new(sep.outputs["Y"], ramp.inputs["Fac"])
nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
scene.world = world

sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
sun.data.energy = 4.0
sun.data.color = (1.0, 0.96, 0.9)
sun.data.angle = math.radians(2)
scene.collection.objects.link(sun)
fill = bpy.data.objects.new("Fill", bpy.data.lights.new("Fill", "AREA"))
fill.data.energy = 400
fill.data.size = 8
fill.data.color = (0.8, 0.88, 1.0)
scene.collection.objects.link(fill)
rim = bpy.data.objects.new("Rim", bpy.data.lights.new("Rim", "AREA"))
rim.data.energy = 1500
rim.data.size = 5
scene.collection.objects.link(rim)


def bloom():
    """A glare/bloom pass, so the glow parts glow (the compositor API differs by version)."""
    try:
        tree = bpy.data.node_groups.new("Comp", "CompositorNodeTree")
        scene.compositing_node_group = tree
        tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        out = tree.nodes.new("NodeGroupOutput")
    except Exception:
        scene.use_nodes = True
        tree = scene.node_tree
        for n in list(tree.nodes):
            tree.nodes.remove(n)
        out = tree.nodes.new("CompositorNodeComposite")
    rl = tree.nodes.new("CompositorNodeRLayers")
    glare = tree.nodes.new("CompositorNodeGlare")
    for key, value in (("glare_type", "BLOOM"), ("quality", "HIGH"), ("threshold", 3.0), ("size", 7)):
        try:
            setattr(glare, key, value)
        except Exception:
            pass
    for name, value in (("Type", "Bloom"), ("Threshold", 3.0), ("Strength", 0.7), ("Size", 0.6)):
        sock = glare.inputs.get(name)
        if sock is not None:
            try:
                sock.default_value = value
            except Exception:
                pass
    tree.links.new(rl.outputs["Image"], glare.inputs["Image"])
    tree.links.new(glare.outputs["Image"], out.inputs[0])
    print("BLOOM ON", [s.name for s in glare.inputs])


try:
    bloom()
except Exception as e:
    print("BLOOM FAILED", e)

cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
scene.collection.objects.link(cam)
scene.camera = cam


def pose(tip_dir):
    """The rod's matrix with its length along `tip_dir` and its reel (-X) hanging down."""
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


# Hero: the Sniper Arena angle -- from below, the rod rising across a blue sky.
shoot(RENDERS / "hero.png", (0.85, 0.15, 0.5), (0.1, 1.0, 0.25), (1600, 900), 32, 3.2, 2.4, (0.4, 0.6, 1.0))
# The whole rod, three-quarter.
shoot(RENDERS / "full.png", (0.95, 0.1, 0.3), (0.15, 1.0, 0.05), (2000, 900), 40, 5.0, 5.2, (0.3, 0.5, 1.0))
# Side on, flat, the silhouette.
shoot(RENDERS / "side.png", (1, 0, 0), (0, 1, 0), (2400, 800), 60, 5.0, 5.4, (0.2, 0.6, 1.0))
# The handle close up.
shoot(RENDERS / "handle.png", (0.6, 0.3, 0.75), (0.35, 1.0, 0.1), (1400, 1000), 45, 2.2, 1.35, (0.5, 0.4, 1.0))
rod.matrix_world = Matrix.Identity(4)

bpy.ops.wm.save_as_mainfile(filepath=str(HERE / "base_rod.blend"))
tris = 0
deps = bpy.context.evaluated_depsgraph_get()
for o in parts:
    m = o.evaluated_get(deps).to_mesh()
    tris += sum(len(p.vertices) - 2 for p in m.polygons)
    o.evaluated_get(deps).to_mesh_clear()
print(f"BUILT {len(parts)} parts, {tris} tris")
