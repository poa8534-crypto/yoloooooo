"""A leaping koi for the Town Square fountain, built from code in Blender.

Called by export_rods.py (so it travels in the same FBX as the rods, one
import in Studio). It builds an Empty "FishStatue" with one mesh per material
(Body, Fin, Eye, Pupil, Mouth), and returns where the mouth is and which way
it points, so Studio can aim the fountain's jet out of it.

The body is lofted: rings of an ellipse (flattened side to side, like a real
koi) along a curved spine from the raised tail to the head, which points up
and forward with its mouth left open. Fins are thin solid plates.
"""

import math

import bmesh
import bpy
from mathutils import Vector


def _bezier(p0, p1, p2, p3, t):
    u = 1 - t
    return p0 * u**3 + p1 * 3 * u * u * t + p2 * 3 * u * t * t + p3 * t**3


# The spine in the fish's plane (Y forward, Z up), tail end to mouth.
P0, P1, P2, P3 = Vector((0, -3.3, 2.4)), Vector((0, -2.4, -0.4)), Vector((0, 0.9, 0.2)), Vector((0, 1.7, 4.1))
RINGS, SIDES = 44, 28


def _radius(t):
    """How thick the body is along the spine: a slim tail stalk, fullest a
    little behind the head, then the head tapering to the open mouth."""
    if t < 0.1:
        return 0.2 + (t / 0.1) * 0.15
    if t < 0.6:
        return 0.35 + math.sin((t - 0.1) / 0.5 * math.pi / 2) * 0.82
    if t < 0.84:
        return 1.17 - ((t - 0.6) / 0.24) ** 2 * 0.25
    return 0.92 - math.sin((t - 0.84) / 0.16 * math.pi / 2) * 0.5


def _frame(t):
    """Point on the spine, its direction, and the "up" (back) of the fish there."""
    point = _bezier(P0, P1, P2, P3, t)
    ahead = (_bezier(P0, P1, P2, P3, min(1, t + 0.01)) - _bezier(P0, P1, P2, P3, max(0, t - 0.01))).normalized()
    side = Vector((1, 0, 0))
    # The spine stays in one plane, so this "back" never flips: the rings do
    # not twist. It is the outside of the leap's curve.
    back = side.cross(ahead).normalized()
    return point, ahead, side, back


def _material(name, rgb, metallic=0.0, emission=0.0):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (*rgb, 1)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = 0.35
    if "Emission Strength" in bsdf.inputs:
        bsdf.inputs["Emission Strength"].default_value = emission
    return m


def _object(name, bm, material, collection, parent, smooth=True, subdivide=0, thickness=0.0):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    if smooth:
        for p in mesh.polygons:
            p.use_smooth = True
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.parent = parent
    if thickness:
        mod = obj.modifiers.new("Solid", "SOLIDIFY")
        mod.thickness = thickness
        mod.offset = 0
    if subdivide:
        mod = obj.modifiers.new("Smooth", "SUBSURF")
        mod.levels = subdivide
        mod.render_levels = subdivide
    return obj


def _plate(outline, frame_point, u, v, bm=None):
    """A flat plate: a fan of `outline` points given as (along u, along v) from a point."""
    bm = bm or bmesh.new()
    verts = [bm.verts.new(frame_point + u * a + v * b) for a, b in outline]
    bm.faces.new(verts)
    return bm


def build(collection, x_offset=0.0):
    holder = bpy.data.objects.new("FishStatue", None)
    collection.objects.link(holder)
    holder.location = Vector((x_offset, 0, 0))

    gold = _material("K_KoiGold", (1.0, 0.42, 0.04), metallic=0.35)
    fin = _material("K_KoiFin", (0.9, 0.12, 0.03), metallic=0.2)
    white = _material("K_EyeWhite", (0.95, 0.95, 0.95))
    black = _material("K_Pupil", (0.01, 0.01, 0.012))
    mouth_mat = _material("K_Mouth", (0.25, 0.02, 0.02))

    # ---- body: lofted rings, tail end capped, mouth end left open
    bm = bmesh.new()
    rings = []
    for i in range(RINGS):
        t = i / (RINGS - 1)
        point, ahead, side, back = _frame(t)
        r = _radius(t)
        ring = []
        for j in range(SIDES):
            a = 2 * math.pi * j / SIDES
            # Flattened side to side, like a koi.
            ring.append(bm.verts.new(point + side * math.cos(a) * r * 0.78 + back * math.sin(a) * r))
        rings.append(ring)
    for i in range(RINGS - 1):
        for j in range(SIDES):
            a, b = rings[i][j], rings[i][(j + 1) % SIDES]
            c, d = rings[i + 1][(j + 1) % SIDES], rings[i + 1][j]
            bm.faces.new((a, b, c, d))
    bm.faces.new(list(reversed(rings[0])))
    # The lip: the open end folds a little inwards, so the mouth has a rim.
    mouth_point, mouth_dir, side, back = _frame(1.0)
    inner = [bm.verts.new(mouth_point - mouth_dir * 0.35 + (v.co - mouth_point) * 0.62) for v in rings[-1]]
    for j in range(SIDES):
        bm.faces.new((rings[-1][j], rings[-1][(j + 1) % SIDES], inner[(j + 1) % SIDES], inner[j]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    # Kept as plain coordinates: the body's vertices are gone once it is a mesh.
    rim = [v.co.copy() for v in rings[-1]]
    _object("FishStatue__Body", bm, gold, collection, holder, subdivide=1)

    # ---- the inside of the mouth: a dark disc set back in the opening
    bm = bmesh.new()
    centre = mouth_point - mouth_dir * 0.45
    disc = [bm.verts.new(centre + (co - mouth_point) * 0.62) for co in rim]
    bm.faces.new(disc)
    _object("FishStatue__Mouth", bm, mouth_mat, collection, holder, smooth=False)

    # ---- eyes, on the sides of the head
    for sign in (-1, 1):
        point, ahead, side, back = _frame(0.86)
        r = _radius(0.86)
        at = point + side * sign * r * 0.62 + back * r * 0.28 + ahead * 0.1
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=10, radius=0.3)
        bmesh.ops.translate(bm, verts=bm.verts, vec=at)
        _object(f"FishStatue__EyeWhite{'L' if sign < 0 else 'R'}", bm, white, collection, holder)
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=0.15)
        bmesh.ops.translate(bm, verts=bm.verts, vec=at + side * sign * 0.22)
        _object(f"FishStatue__Pupil{'L' if sign < 0 else 'R'}", bm, black, collection, holder)

    # ---- fins: the forked tail, a dorsal fin along the back, two side fins
    fins = bmesh.new()
    tail_point, tail_dir, side, back = _frame(0.0)
    for sign in (-1, 1):
        # Each lobe of the forked tail: broad, sweeping back from the tail stalk
        # and out (towards the back or the belly) in the fish's own plane.
        _plate(
            [(0.15, 0), (-0.5, 0.45), (-1.6, 1.35), (-2.7, 2.0), (-2.4, 1.1), (-1.6, 0.4), (-0.9, 0.05)],
            tail_point,
            tail_dir,
            back * sign,
            fins,
        )
    # One flowing fin along the back: its root follows the body, its edge rises
    # and falls.
    base, edge = [], []
    for k in range(9):
        t0 = 0.34 + k * 0.045
        point, ahead, side, back = _frame(t0)
        root = point + back * _radius(t0) * 0.9
        rise = math.sin(k / 8 * math.pi) * 0.95 + 0.1
        base.append(fins.verts.new(root))
        edge.append(fins.verts.new(root + back * rise - ahead * 0.35))
    for k in range(8):
        fins.faces.new((base[k], base[k + 1], edge[k + 1], edge[k]))
    for sign in (-1, 1):
        # Rounded side fins behind the gills, swept back and down.
        point, ahead, side, back = _frame(0.72)
        root = point + side * sign * _radius(0.72) * 0.72 - back * _radius(0.72) * 0.3
        _plate(
            [(0.1, 0), (-0.3, 0.35), (-1.0, 0.75), (-1.55, 0.7), (-1.5, 0.3), (-0.8, 0.05)],
            root,
            ahead,
            (side * sign - back * 0.6).normalized(),
            fins,
        )
    _object("FishStatue__Fin", fins, fin, collection, holder, smooth=False, thickness=0.07)

    return {
        "mouth": [round(c, 4) for c in mouth_point],
        "direction": [round(c, 4) for c in mouth_dir],
        "height": round(max(P0.z, P3.z) - min(p.z for p in (P1, P2)) + 1.2, 3),
    }
