"""Finish the island for Roblox and write the FBX Studio imports.

    python scripts/blender_export_roblox.py blender/haven_bay/stages.json

Works on a COPY of the .blend, so the file the passes build keeps every piece
separate and named. On the copy, driven by the stages file's `export` block:

1. The preview ocean is deleted -- Roblox's Terrain water takes its place.
2. Decoration is joined into a few logical meshes (all pine trees, all palms,
   the square's lanterns and benches ...), split wherever one would pass the
   triangle budget. Anything the game finds by name -- plots, spawn, NPC
   stands, fishing spots, buildings -- stays its own MeshPart.
3. Shading: every mesh is smoothed with sharp edges kept past an angle, so
   leaves and rocks go soft while boxes stay crisp; foliage, rocks and reef
   also get a weighted-normal pass.
4. Buildings, the arrival dock and the square's furniture get a small bevel
   on their hard edges, where the budget allows it.
5. The FBX is written with the meshes' own normals (so the smoothing reaches
   Roblox), -Z forward, Y up, and every object's triangle count is read back
   from the file that was written.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.blender import bridge  # noqa: E402

FINISH = r'''
import json
import math

import bmesh
import bpy

CONFIG = json.loads(__CONFIG__)
budget = CONFIG.get("triangle_budget", 7500)
report = {"deleted": 0, "joined": {}, "bevelled": [], "bevel_skipped": [], "over_budget": []}


def starts(name, prefixes):
    return any(name.startswith(p) for p in prefixes)


# 1. The preview water goes.
for obj in list(bpy.data.objects):
    if starts(obj.name, CONFIG.get("delete_prefixes", [])):
        bpy.data.objects.remove(obj, do_unlink=True)
        report["deleted"] += 1

# Bake every transform into its own mesh, one user each, so joining and
# bevelling work in world space and nothing carries a rotation or scale.
for obj in [o for o in bpy.data.objects if o.type == "MESH"]:
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    obj.data.transform(obj.matrix_world)
    obj.parent = None
    obj.matrix_world = obj.matrix_world.Identity(4)
for obj in [o for o in bpy.data.objects if o.type != "MESH"]:
    bpy.data.objects.remove(obj, do_unlink=True)


def tris(obj):
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


# 2. Join decoration into logical groups, each under the triangle budget.
groups = {}
for obj in [o for o in bpy.data.objects if o.type == "MESH"]:
    if starts(obj.name, CONFIG.get("keep_prefixes", [])):
        continue
    target = next((g for g, prefixes in CONFIG["join"].items() if starts(obj.name, prefixes)),
                  CONFIG.get("join_others_as"))
    if target:
        groups.setdefault(target, []).append(obj)

for target, members in groups.items():
    chunks, current, count = [], [], 0
    for obj in sorted(members, key=lambda o: o.name):
        t = tris(obj)
        if current and count + t > budget:
            chunks.append(current)
            current, count = [], 0
        current.append(obj)
        count += t
    if current:
        chunks.append(current)
    for n, chunk in enumerate(chunks, 1):
        name = target if n == 1 else f"{target}_{n:02d}"
        head = chunk[0]
        if len(chunk) > 1:
            with bpy.context.temp_override(active_object=head, object=head,
                                           selected_objects=chunk, selected_editable_objects=chunk):
                bpy.ops.object.join()
        head.name = name
        head.data.name = name
        report["joined"][name] = len(chunk)

meshes = [o for o in bpy.data.objects if o.type == "MESH"]

# 4. Bevel hard edges where it fits the budget.
for obj in meshes:
    if not starts(obj.name, CONFIG.get("bevel_prefixes", [])):
        continue
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    sharp = [e for e in bm.edges if len(e.link_faces) == 2 and e.calc_face_angle(0.0) > math.radians(30)]
    bmesh.ops.bevel(bm, geom=sharp, offset=CONFIG.get("bevel_width", 0.18),
                    segments=CONFIG.get("bevel_segments", 2), affect="EDGES", clamp_overlap=True, material=-1)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    if len(bm.faces) > budget:
        report["bevel_skipped"].append(f"{obj.name} ({len(bm.faces)} tris with a bevel)")
    else:
        bm.to_mesh(obj.data)
        report["bevelled"].append(obj.name)
    bm.free()

# 3. Smooth with sharp edges kept, and weighted normals on the organic pieces.
# Weld first: pieces built face by face (the terrain above all) share no
# vertices, and smoothing cannot blend across a seam that is not joined.
# Only what is listed: welding a two-sided leaf fuses its front and back into
# one face with no direction, and the palms went see-through.
for obj in [o for o in meshes if starts(o.name, CONFIG.get("weld_prefixes", ["Terrain_"]))]:
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=0.01)
    bm.to_mesh(obj.data)
    bm.free()
for obj in meshes:
    organic = starts(obj.name, CONFIG.get("smooth_prefixes", []))
    # Leaves and rocks are low-poly balls whose faces meet at about 42
    # degrees; a 35 degree limit kept every one of those edges hard.
    angle = CONFIG.get("organic_angle", 75) if organic else CONFIG.get("smooth_angle", 35)
    obj.data.shade_smooth()
    obj.data.set_sharp_from_angle(angle=math.radians(angle))
    if organic:
        mod = obj.modifiers.new("WeightedNormal", "WEIGHTED_NORMAL")
        mod.keep_sharp = True

for obj in meshes:
    if tris(obj) > budget:
        report["over_budget"].append(f"{obj.name}: {tris(obj)}")
report["objects"] = len(meshes)
report["triangles"] = sum(tris(o) for o in meshes)
bpy.ops.wm.save_mainfile()
print("FINISH_BEGIN")
print(json.dumps(report))
print("FINISH_END")
'''


def main(argv: list[str]) -> int:
    plan_path = Path(argv[1]) if len(argv) > 1 else Path("blender/haven_bay/stages.json")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    config = plan["export"]
    source = Path(plan["blend"]).resolve()
    copy = Path(config["copy"]).resolve()
    fbx = Path(config["fbx"]).resolve()

    shutil.copyfile(source, copy)
    script = FINISH.replace("__CONFIG__", repr(json.dumps(config)))
    ran, error, out = bridge._invoke(script, copy, name="last_finish", timeout=900)
    match = re.search(r"FINISH_BEGIN\s*(\{.*?\})\s*FINISH_END", out, flags=re.S)
    if not ran or not match:
        print(f"finishing failed: {error or 'no report'}")
        return 1
    report = json.loads(match.group(1))
    print(f"finished copy: {report['objects']} meshes, {report['triangles']} triangles, "
          f"{report['deleted']} preview-water meshes removed")
    for name, count in sorted(report["joined"].items()):
        print(f"  joined {count:4d} pieces into {name}")
    print(f"  bevelled: {', '.join(report['bevelled']) or 'none'}")
    for skipped in report["bevel_skipped"]:
        print(f"  bevel skipped, over budget: {skipped}")
    for over in report["over_budget"]:
        print(f"  OVER BUDGET: {over}")

    written = bridge.export_scene(copy, fbx, smoothing="OFF")
    if written["error"]:
        print(f"export failed: {written['error']}")
        return 1
    print(f"\nwrote {fbx}  ({written['bytes'] / 1e6:.1f} MB, {len(written['objects'])} meshes, "
          f"{written['total_triangles']} triangles)")
    heaviest = sorted(written["objects"].items(), key=lambda kv: -kv[1]["triangles"])[:5]
    for name, got in heaviest:
        print(f"  {name:32} {got['triangles']:>6} tris")
    return 0 if not report["over_budget"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
