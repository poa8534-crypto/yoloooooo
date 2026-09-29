"""Shared tools for building a premium rod skin on the base rod.

A skin script does:

    import skinkit as kit
    kit.start()                       # opens base_rod.blend
    kit.recolour("Shell", ...)        # restyle the base materials
    kit.textured("Shell", "royal_filigree.png", tile=0.6)   # a pattern on a material
    kit.shard(...), kit.ring(...), kit.gem(...), kit.sparks(...)   # additions
    kit.finish("royal")               # renders renders/royal_*.png, saves royal_rod.blend

Every added part carries `section` (where it sits on the rod) and `vfx`
("static", "float", "orbit_axial", "orbit_side" or "particle") -- see
export_premium.py and the game's RodVfx.client for what they become.
"""

import math
import random
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
RENDERS = HERE / "renders"
TEXTURES = HERE / "textures"
SECTIONS = [("Handle", -1.0), ("Segment1", 4.62), ("Segment2", 5.95), ("Segment3", 7.2), ("Tip", 8.45)]

scene = None
rod = None
rng = random.Random(11)
added = []


def start(seed=11):
    global scene, rod, rng
    bpy.ops.wm.open_mainfile(filepath=str(HERE / "base_rod.blend"))
    scene = bpy.context.scene
    rod = bpy.data.objects["Rod"]
    rng = random.Random(seed)
    added.clear()
    # Fog glow keeps the darks dark (Bloom hazes the whole frame).
    comp = getattr(scene, "compositing_node_group", None)
    if comp:
        for n in comp.nodes:
            if n.bl_idname == "CompositorNodeGlare":
                for key, value in (("Type", "Fog Glow"), ("Strength", 1.0), ("Size", 0.5), ("Threshold", 1.5)):
                    if n.inputs.get(key) is not None:
                        n.inputs[key].default_value = value


def section_of(z):
    s = "Handle"
    for name, z0 in SECTIONS:
        if z >= z0:
            s = name
    return s


def part(name):
    return bpy.data.objects[name]


def parts_named(prefix):
    return [o for o in rod.children if o.name.startswith(prefix)]


def hide(*names):
    for n in names:
        for o in parts_named(n):
            o.hide_render = True
            o["skin_hidden"] = True


# ------------------------------------------------------------------ materials


def bsdf(mat):
    return mat.node_tree.nodes.get("Principled BSDF")


def _set(b, color, metallic, rough, emission, strength):
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    if emission:
        b.inputs["Emission Color"].default_value = (*emission, 1)
    b.inputs["Emission Strength"].default_value = strength


def recolour(name, color, metallic=0.0, rough=0.4, emission=None, strength=0.0, specular=None):
    m = bpy.data.materials[name]
    b = bsdf(m)
    _set(b, color, metallic, rough, emission, strength)
    if specular is not None and "Specular IOR Level" in b.inputs:
        b.inputs["Specular IOR Level"].default_value = specular
    return m


def material(name, color, metallic=0.0, rough=0.4, emission=None, strength=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    _set(bsdf(m), color, metallic, rough, emission, strength)
    return m


def rim_glow(mat, color, power):
    """Emission that rises only at grazing angles: a thin glowing rim."""
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


def textured(mat_or_name, image, tile=0.6, metallic=0.0, rough=0.4, emission=0.0, glow_image=None):
    """Put a tileable pattern on a material (base colour; `emission` makes it
    shine a little by itself) and project UVs on every part that uses it, so
    the pattern repeats every `tile` units."""
    m = bpy.data.materials[mat_or_name] if isinstance(mat_or_name, str) else mat_or_name
    nt = m.node_tree
    b = bsdf(m)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(TEXTURES / image), check_existing=True)
    nt.links.new(tex.outputs["Color"], b.inputs["Base Color"])
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    if emission > 0:
        source = tex
        if glow_image:
            # A separate glow mask: only what is bright in it shines.
            source = nt.nodes.new("ShaderNodeTexImage")
            source.image = bpy.data.images.load(str(TEXTURES / glow_image), check_existing=True)
            m["glow_texture"] = glow_image
        nt.links.new(source.outputs["Color"], b.inputs["Emission Color"])
        b.inputs["Emission Strength"].default_value = emission
    m["texture"] = image
    for o in [o for o in bpy.data.objects if o.type == "MESH" and o.data.materials and o.data.materials[0] == m]:
        uv_cube(o, tile)
    return m


def uv_cube(obj, tile):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.cube_project(cube_size=tile, correct_aspect=True, clip_to_bounds=False, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode="OBJECT")


def assign(names, mat):
    """Give base parts (by name prefix) a different material."""
    for n in names:
        for o in parts_named(n):
            o.data.materials.clear()
            o.data.materials.append(mat)


# ------------------------------------------------------------------ additions


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


def _mesh_obj(name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    o = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(o)
    return o


def shard(name, base, direction, length, width, mat, vfx="float", sides=5, lean=0.0):
    """A crystal: a jagged bipyramid from `base` pointing along `direction`."""
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
    return adopt(_mesh_obj(name, bm), mat, vfx, smooth=5)


def gem(name, centre, radius, normal, mat, vfx="static", facets=8):
    """A cut gem: a flat table, a crown and a pointed pavilion, facing `normal`."""
    bm = bmesh.new()
    n = Vector(normal).normalized()
    u = n.orthogonal().normalized()
    w = n.cross(u)
    c = Vector(centre)
    table = [bm.verts.new(c + n * radius * 0.45 + (u * math.cos(a) + w * math.sin(a)) * radius * 0.55)
             for a in (2 * math.pi * i / facets + math.pi / facets for i in range(facets))]
    girdle = [bm.verts.new(c + (u * math.cos(a) + w * math.sin(a)) * radius)
              for a in (2 * math.pi * i / facets for i in range(facets))]
    point = bm.verts.new(c - n * radius * 0.85)
    bm.faces.new(table)
    for i in range(facets):
        j = (i + 1) % facets
        bm.faces.new((girdle[i], girdle[j], table[i]))
        bm.faces.new((table[i], girdle[j], table[j]))
        bm.faces.new((girdle[j], girdle[i], point))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return adopt(_mesh_obj(name, bm), mat, vfx, smooth=5)


def ring(name, centre, major, minor, normal, mat, vfx="orbit_axial", dashes=3, gap=0.45):
    """A dashed ring (arcs with gaps, so its spin shows). `vfx` "orbit_axial"
    spins it about the rod's direction, "orbit_side" about the line from the
    rod's axis out to it, "static" leaves it still (use dashes=1, gap=0 for a
    closed ring)."""
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
        steps = 24 if gap > 0 else 48
        pts = [Vector(centre) + (u * math.cos(a) + w * math.sin(a)) * major
               for a in (a0 + (a1 - a0) * i / steps for i in range(steps + 1))]
        sp.points.add(len(pts) - 1)
        for pt, co in zip(sp.points, pts):
            pt.co = (*co, 1)
        if gap == 0:
            sp.use_cyclic_u = True
    cobj = bpy.data.objects.new(name + "_c", cu)
    scene.collection.objects.link(cobj)
    deps = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(cobj.evaluated_get(deps))
    bpy.data.objects.remove(cobj, do_unlink=True)
    o = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(o)
    return adopt(o, mat, vfx, smooth=80)


def bar(name, a, b, width, mat, vfx="static"):
    """A thin square bar from a to b (snowflake arms, prongs)."""
    a, b = Vector(a), Vector(b)
    d = (b - a)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(a + b) / 2)
    o = bpy.context.active_object
    o.name = name
    o.scale = (width, width, d.length)
    o.rotation_euler = d.normalized().to_track_quat("Z", "Y").to_euler()
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return adopt(o, mat, vfx, smooth=20)


def sphere(name, centre, radius, mat, vfx="static", subdiv=3):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdiv, radius=radius, location=centre)
    o = bpy.context.active_object
    o.name = name
    return adopt(o, mat, vfx, smooth=80)


def sparks(count, mats, size=(0.006, 0.017), focus=(1.6, 3.4)):
    """Render stand-ins for the particles (the game uses ParticleEmitters)."""
    for k in range(count):
        z = rng.uniform(-0.3, 10.3) if k % 3 else rng.uniform(*focus)
        a = rng.uniform(0, 2 * math.pi)
        radius = rng.uniform(0.25, 0.85 - 0.04 * z)
        sphere(f"Spark{k}", (math.cos(a) * radius, math.sin(a) * radius, z), rng.uniform(*size),
               mats[k % len(mats)], vfx="particle", subdiv=1)


# ------------------------------------------------------------------ render


def pose(tip_dir):
    t = Vector(tip_dir).normalized()
    down = Vector((0, 0, -1))
    reel = (down - t * down.dot(t)).normalized()
    x = -reel
    y = t.cross(x).normalized()
    return Matrix((x, y, t)).transposed().to_4x4()


def shoot(path, tip_dir, look, res, lens, focus_z, radius, key_from):
    cam = scene.camera
    sun, fill, rim = bpy.data.objects["Sun"], bpy.data.objects["Fill"], bpy.data.objects["Rim"]
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


def finish(tag):
    bpy.context.view_layer.update()
    shoot(RENDERS / f"{tag}_hero.png", (0.85, 0.15, 0.5), (0.1, 1.0, 0.25), (1600, 900), 32, 3.0, 2.6, (0.4, 0.6, 1.0))
    shoot(RENDERS / f"{tag}_full.png", (0.95, 0.1, 0.3), (0.15, 1.0, 0.05), (2000, 900), 40, 4.7, 5.6, (0.3, 0.5, 1.0))
    shoot(RENDERS / f"{tag}_handle.png", (0.6, 0.3, 0.75), (0.35, 1.0, 0.1), (1400, 1000), 45, 2.3, 1.5, (0.5, 0.4, 1.0))
    rod.matrix_world = Matrix.Identity(4)
    bpy.ops.wm.save_as_mainfile(filepath=str(HERE / f"{tag}_rod.blend"))
    counts = {}
    for o in added:
        counts[o["vfx"]] = counts.get(o["vfx"], 0) + 1
    print(tag.upper(), counts)
