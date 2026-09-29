"""Look at the owner's raw rod skins before (or after) repairing them.

Run: blender -b --python blender/rods/inspect_skins.py -- [--repaired] <folder> [<folder> ...]

For each folder (holding output.obj and its textures; with --repaired, the
repaired blender/rods/out/repaired/<folder>.obj instead) it welds the texture
seams, prints the mesh's real defects -- loose islands and how far each is
from the main body, holes (boundary loops) and their sizes, non-manifold
edges, degenerate faces, size -- and renders the whole rod from four sides
plus close-ups of its bottom, middle and top thirds into
blender/rods/out/inspect/<folder>[_repaired]_<view>.png.
"""

import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "blender" / "rods" / "out" / "inspect"
OUT.mkdir(parents=True, exist_ok=True)

args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
repaired = "--repaired" in args
folders = [a for a in args if not a.startswith("--")] or ["Lava rod", "Void rod"]


def clear():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def weld(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    size = max(obj.dimensions)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=size * 1e-5)
    bm.to_mesh(obj.data)
    bm.free()


def islands_of(bm):
    seen = {}
    groups = []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack, group = [f], []
        seen[f.index] = len(groups)
        while stack:
            cur = stack.pop()
            group.append(cur)
            for e in cur.edges:
                for nf in e.link_faces:
                    if nf.index not in seen:
                        seen[nf.index] = len(groups)
                        stack.append(nf)
        groups.append(group)
    return groups


def boundary_loops(bm):
    edges = {e for e in bm.edges if e.is_boundary}
    loops = []
    while edges:
        start = edges.pop()
        loop = [start]
        frontier = [start]
        while frontier:
            e = frontier.pop()
            for v in e.verts:
                for ne in v.link_edges:
                    if ne in edges:
                        edges.remove(ne)
                        loop.append(ne)
                        frontier.append(ne)
        loops.append(loop)
    return loops


def stats(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    groups = islands_of(bm)
    groups.sort(key=len, reverse=True)
    main = groups[0]
    main_verts = {v for f in main for v in f.verts}
    main_lo = Vector((min(v.co[i] for v in main_verts) for i in range(3)))
    main_hi = Vector((max(v.co[i] for v in main_verts) for i in range(3)))
    size = max(main_hi - main_lo)

    def gap(group):
        c = sum((v.co for f in group for v in f.verts), Vector()) / max(1, sum(len(f.verts) for f in group))
        clamped = Vector((min(max(c[i], main_lo[i]), main_hi[i]) for i in range(3)))
        return (c - clamped).length / size

    floating = [(len(g), round(gap(g), 3)) for g in groups[1:] if gap(g) > 0.01]
    loops = boundary_loops(bm)
    loop_sizes = sorted((len(l) for l in loops), reverse=True)
    nonmanifold = sum(1 for e in bm.edges if not e.is_manifold and not e.is_boundary)
    degenerate = sum(1 for f in bm.faces if f.calc_area() < 1e-12)
    lo = Vector((min(v.co[i] for v in bm.verts) for i in range(3)))
    hi = Vector((max(v.co[i] for v in bm.verts) for i in range(3)))
    bm.free()
    return {
        "tris": sum(len(p.vertices) - 2 for p in obj.data.polygons),
        "islands": len(groups),
        "main_faces": len(main),
        "floating_outside_body(faces,gap)": floating[:12],
        "floating_count": len(floating),
        "holes": len(loops),
        "biggest_holes(edges)": loop_sizes[:10],
        "nonmanifold_edges": nonmanifold,
        "degenerate_faces": degenerate,
        "size": tuple(round(x, 3) for x in (hi - lo)),
    }


def setup_scene():
    scene = bpy.context.scene
    engines = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    scene.render.resolution_x = 900
    scene.render.resolution_y = 900
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.55, 0.6, 0.66, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 1.2
    scene.world = world
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3
    sun.rotation_euler = (math.radians(50), 0, math.radians(30))
    scene.collection.objects.link(sun)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.type = "ORTHO"
    scene.collection.objects.link(cam)
    scene.camera = cam
    return scene, cam


def shoot(scene, cam, centre, radius, direction, path):
    cam.data.ortho_scale = radius * 2.2
    cam.location = centre + direction * radius * 4
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


for folder in folders:
    clear()
    tag = folder.replace(" ", "_") + ("_repaired" if repaired else "")
    obj_path = (ROOT / "blender" / "rods" / "out" / "repaired" / folder / "output.obj") if repaired else (ROOT / folder / "output.obj")
    if not obj_path.exists():
        print("MISSING", obj_path)
        continue
    bpy.ops.wm.obj_import(filepath=str(obj_path))
    obj = [o for o in bpy.data.objects if o.type == "MESH"][0]
    weld(obj)
    print("=== ", tag, stats(obj))
    scene, cam = setup_scene()
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    centre = sum(corners, Vector()) / 8
    radius = max((c - centre).length for c in corners)
    views = {
        "front": Vector((0, -1, 0)),
        "side": Vector((1, 0, 0)),
        "top": Vector((0, 0.001, 1)),
        "threequarter": Vector((1, -1, 0.6)).normalized(),
    }
    for view, direction in views.items():
        shoot(scene, cam, centre, radius, direction, OUT / f"{tag}_{view}.png")
    # Close-ups along the longest axis: bottom, middle and top thirds.
    dims = obj.dimensions
    axis = max(range(3), key=lambda i: dims[i])
    lo = min(c[axis] for c in corners)
    hi = max(c[axis] for c in corners)
    for k, name in enumerate(("closeA", "closeB", "closeC")):
        c = centre.copy()
        c[axis] = lo + (hi - lo) * (k + 0.5) / 3
        side = Vector((1, -1, 0.5)).normalized()
        shoot(scene, cam, c, (hi - lo) / 5, side, OUT / f"{tag}_{name}.png")
print("done")
