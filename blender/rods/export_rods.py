"""Turn the owner's "Fishing rods.blend" (and the fountain's koi statue,
fish_statue.py) into one FBX that Roblox Studio's 3D
Importer can take in a single click, plus a JSON sidecar with each piece's
colour.

Why each step:
- A Roblox MeshPart has one colour and one material. Each rod is one mesh
  with several materials, so it is split into one piece per material. The
  pieces are named <Rod>__<Material>, so Studio can colour them from the
  sidecar afterwards. (Plain colours, no textures: nothing is lost.)
- Each rod's pieces hang under an Empty named after the rod, which the
  importer turns into a Model per rod.
- Each rod's origin is moved to the bottom of its handle, and the rods are
  laid out in a row so they do not overlap.
- Scale is left to Studio: the importer's unit guess is fixed there with
  Model:ScaleTo, which is measured rather than assumed.

Run: blender --background "Fishing rods.blend" --python export_rods.py -- <out_dir>
"""

import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

out_dir = Path(sys.argv[sys.argv.index("--") + 1]) if "--" in sys.argv else Path.cwd()
out_dir.mkdir(parents=True, exist_ok=True)

# Every rod is a mesh in a collection of its own name; the rest (the default
# cube-ish cylinder, camera, light) is not a rod.
rods = [
    o
    for o in bpy.context.scene.objects
    if o.type == "MESH" and any(c.name == o.name for c in o.users_collection)
]

def _centre(o):
    corners = [o.matrix_world @ Vector(c) for c in o.bound_box]
    return sum(corners, Vector()) / 8


# Rows in the owner's layout (bottom row first, left to right): the order the
# rods are numbered in the sidecar.
rods.sort(key=lambda o: (round(_centre(o).z), _centre(o).x))

sidecar = {"rods": {}, "materials": {}}

# Work on copies in a fresh collection, so the owner's file is never touched.
export = bpy.data.collections.new("RobloxExport")
bpy.context.scene.collection.children.link(export)

for index, rod in enumerate(rods):
    copy = rod.copy()
    copy.data = rod.data.copy()
    export.objects.link(copy)
    # Bake its transform into the mesh, then put the origin at the handle's base.
    copy.data.transform(copy.matrix_world)
    copy.matrix_world.identity()
    xs = [v.co.x for v in copy.data.vertices]
    ys = [v.co.y for v in copy.data.vertices]
    zs = [v.co.z for v in copy.data.vertices]
    base = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs)))
    for v in copy.data.vertices:
        v.co -= base
    length = max(zs) - min(zs)

    holder = bpy.data.objects.new(rod.name, None)
    export.objects.link(holder)
    holder.location = Vector((index * 2.0, 0, 0))

    # One piece per material.
    # The pieces are the copy plus whatever meshes the split adds (Blender
    # names them after the copy with its own suffixes, so they are found by
    # what is new, not by name).
    before = {o.name for o in export.objects}
    bpy.ops.object.select_all(action="DESELECT")
    copy.select_set(True)
    bpy.context.view_layer.objects.active = copy
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.separate(type="MATERIAL")
    bpy.ops.object.mode_set(mode="OBJECT")
    pieces = [copy] + [o for o in export.objects if o.type == "MESH" and o.name not in before]
    names = []
    for piece in pieces:
        used = [s.material for s in piece.material_slots if s.material]
        material = used[0].name if used else "None"
        # Only the material the piece actually uses.
        piece.data.materials.clear()
        if used:
            piece.data.materials.append(used[0])
        piece.name = f"{rod.name}__{material}"
        piece.data.name = piece.name
        piece.parent = holder
        piece.location = Vector((0, 0, 0))
        names.append(piece.name)

    sidecar["rods"][rod.name] = {
        "order": index + 1,
        "length": round(length, 3),
        "tris": sum(len(p.vertices) - 2 for p in rod.data.polygons),
        "pieces": sorted(names),
    }

# The fountain's koi statue travels in the same file: one import in Studio.
sys.path.insert(0, str(Path(__file__).parent))
import fish_statue  # noqa: E402

sidecar["fish"] = fish_statue.build(export, x_offset=len(rods) * 2.0 + 4)

for m in bpy.data.materials:
    if not m.use_nodes:
        continue
    bsdf = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        continue
    colour = list(bsdf.inputs["Base Color"].default_value)[:3]
    emission = bsdf.inputs["Emission Strength"].default_value if "Emission Strength" in bsdf.inputs else 0.0
    sidecar["materials"][m.name] = {
        # Linear to sRGB, so Studio shows the colour the owner picked.
        "rgb": [round(255 * (c ** (1 / 2.2) if c > 0 else 0)) for c in colour],
        "metallic": round(bsdf.inputs["Metallic"].default_value, 2),
        "roughness": round(bsdf.inputs["Roughness"].default_value, 2),
        "glow": round(emission, 2),
    }

# Only the export collection goes out.
bpy.ops.object.select_all(action="DESELECT")
for o in export.objects:
    o.select_set(True)
fbx = out_dir / "Roblox_Import_Rods_and_Fountain.fbx"
bpy.ops.export_scene.fbx(
    filepath=str(fbx),
    use_selection=True,
    object_types={"EMPTY", "MESH"},
    apply_unit_scale=True,
    axis_forward="-Z",
    axis_up="Y",
    mesh_smooth_type="FACE",
    use_mesh_modifiers=True,
    add_leaf_bones=False,
    bake_anim=False,
)
(out_dir / "Roblox_Import_Rods_and_Fountain.json").write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
print("EXPORTED>" + json.dumps({"fbx": str(fbx), "bytes": fbx.stat().st_size, "rods": len(sidecar["rods"]),
                                  "pieces": sum(len(r["pieces"]) for r in sidecar["rods"].values())}))
