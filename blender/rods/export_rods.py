"""Turn the owner's "Fishing rods.blend" (and the fountain's koi statue,
fish_statue.py) into one FBX that Roblox Studio's 3D
Importer can take in a single click, plus a JSON sidecar with each piece's
colour.

Why each step:
- A held rod cannot flex as one MeshPart. The blank is cut longitudinally into
  Handle, Segment1, Segment2, Segment3 and Tip, preserving the source geometry,
  UVs and material slots. Roblox joins those sections with Motor6Ds at runtime.
- A Roblox MeshPart has one colour and one material. Each longitudinal section
  is then split once more by material. Pieces are named
  <Rod>__<Section>__<Material>, so Studio can colour them from the sidecar.
  (Plain colours, no textures: nothing is lost.)
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

# From the butt to the tip. The reel and grip remain inside the rigid Handle;
# progressively shorter upper sections keep the visible joints subtle.
sections = [
    ("Handle", 0.00, 0.35),
    ("Segment1", 0.35, 0.55),
    ("Segment2", 0.55, 0.72),
    ("Segment3", 0.72, 0.87),
    ("Tip", 0.87, 1.00),
]

# Work on copies in a fresh collection, so the owner's file is never touched.
export = bpy.data.collections.new("RobloxExport")
bpy.context.scene.collection.children.link(export)

for index, rod in enumerate(rods):
    # Bake the source once. Each section below starts from an exact copy so the
    # cut does not accumulate numerical damage up the rod.
    source = rod.data.copy()
    source.transform(rod.matrix_world)
    xs = [v.co.x for v in source.vertices]
    ys = [v.co.y for v in source.vertices]
    zs = [v.co.z for v in source.vertices]
    base = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs)))
    for v in source.vertices:
        v.co -= base
    length = max(zs) - min(zs)

    holder = bpy.data.objects.new(rod.name, None)
    export.objects.link(holder)
    holder.location = Vector((index * 2.0, 0, 0))

    names = []
    section_names = {}
    for section, lo, hi in sections:
        piece = bpy.data.objects.new(f"{rod.name}__{section}", source.copy())
        export.objects.link(piece)

        # Keep only this longitudinal interval. Bisect creates boundary faces
        # while retaining all UV and material data on the source faces.
        bpy.ops.object.select_all(action="DESELECT")
        piece.select_set(True)
        bpy.context.view_layer.objects.active = piece
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        if lo > 0:
            bpy.ops.mesh.bisect(
                plane_co=(0, 0, length * lo),
                plane_no=(0, 0, 1),
                clear_inner=True,
                clear_outer=False,
            )
        if hi < 1:
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.bisect(
                plane_co=(0, 0, length * hi),
                plane_no=(0, 0, 1),
                clear_inner=False,
                clear_outer=True,
            )
        bpy.ops.object.mode_set(mode="OBJECT")

        # Empty sections can occur only on malformed source rods; omitting one
        # is visible in the sidecar and is refused by the runtime constructor.
        if not piece.data.vertices:
            bpy.data.objects.remove(piece, do_unlink=True)
            continue

        # One MeshPart per material within this section.
        before = {o.name for o in export.objects}
        bpy.context.view_layer.objects.active = piece
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.separate(type="MATERIAL")
        bpy.ops.object.mode_set(mode="OBJECT")
        material_pieces = [piece] + [o for o in export.objects if o.type == "MESH" and o.name not in before]
        section_names[section] = []
        for material_piece in material_pieces:
            used = [s.material for s in material_piece.material_slots if s.material]
            material = used[0].name if used else "None"
            # The hanging line is not cut: it goes whole with the Tip (below),
            # so it hangs from the tip and the game hides it in one piece.
            if "line" in material.lower():
                bpy.data.objects.remove(material_piece, do_unlink=True)
                continue
            material_piece.data.materials.clear()
            if used:
                material_piece.data.materials.append(used[0])
            material_piece.name = f"{rod.name}__{section}__{material}"
            material_piece.data.name = material_piece.name
            material_piece.parent = holder
            material_piece.location = Vector((0, 0, 0))
            names.append(material_piece.name)
            section_names[section].append(material_piece.name)

    # The whole hanging line, uncut, rides with the Tip.
    line_slots = [i for i, m in enumerate(source.materials) if m and "line" in m.name.lower()]
    if line_slots:
        line_mesh = source.copy()
        line = bpy.data.objects.new(f"{rod.name}__Tip__line", line_mesh)
        export.objects.link(line)
        bpy.ops.object.select_all(action="DESELECT")
        line.select_set(True)
        bpy.context.view_layer.objects.active = line
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.object.mode_set(mode="OBJECT")
        for poly in line_mesh.polygons:
            poly.select = poly.material_index not in line_slots
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.delete(type="FACE")
        bpy.ops.object.mode_set(mode="OBJECT")
        if line_mesh.vertices:
            material = line_mesh.materials[line_slots[0]]
            line_mesh.materials.clear()
            line_mesh.materials.append(material)
            line.name = f"{rod.name}__Tip__{material.name}"
            line.data.name = line.name
            line.parent = holder
            names.append(line.name)
            section_names.setdefault("Tip", []).append(line.name)
        else:
            bpy.data.objects.remove(line, do_unlink=True)

    sidecar["rods"][rod.name] = {
        "order": index + 1,
        "length": round(length, 3),
        "tris": sum(len(p.vertices) - 2 for p in rod.data.polygons),
        "pieces": sorted(names),
        "sections": section_names,
    }

    bpy.data.meshes.remove(source)

# The owner's rod skins (textured meshes, each in its own folder next to the
# .blend): stood up along +Z with the handle at the bottom, cleared of loose
# crumbs, scaled to a rod's length, lightened for Roblox, and cut into the same
# five sections as the rods so a skinned rod bends too. Each keeps its one
# textured material; the texture is embedded in the FBX.
import math  # noqa: E402

from mathutils import Matrix  # noqa: E402

SKINS = [
    # (id, folder, rotation that puts the rod's length on +Z with the handle down)
    ("Skin_Lava", "Lava rod", Matrix.Identity(4)),
    ("Skin_Void", "Void rod", Matrix.Rotation(math.radians(-90), 4, "Y")),
]
SKIN_LENGTH = 3.4
SKIN_TRIS = 18000
project = Path(bpy.data.filepath).parent
sidecar["skins"] = {}
for skin_index, (skin_id, folder, rotation) in enumerate(SKINS):
    # The repaired skin (repair_skins.py: welded, cleaned, stood up straight,
    # handle down, cleaned texture) when there is one; the raw one otherwise.
    repaired = project / "blender" / "rods" / "out" / "repaired" / folder / "output.obj"
    obj_path = repaired if repaired.exists() else project / folder / "output.obj"
    print("SKIN SOURCE", skin_id, obj_path)
    if not obj_path.exists():
        print("SKIN MISSING", obj_path)
        continue
    before = set(bpy.data.objects)
    if obj_path == repaired:
        # Written Z-up by repair_skins.py; read it back the same way, already standing.
        bpy.ops.wm.obj_import(filepath=str(obj_path), forward_axis="Y", up_axis="Z")
        rotation = Matrix.Identity(4)
    else:
        bpy.ops.wm.obj_import(filepath=str(obj_path))
    imported = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    if not imported:
        continue
    skin = imported[0]
    # Every skin's texture file is called textured_mesh.jpg; in one FBX they
    # would overwrite each other, so each is copied under the skin's own name.
    import shutil  # noqa: E402

    for slot in skin.material_slots:
        if slot.material and slot.material.use_nodes:
            slot.material.name = skin_id
            for node in slot.material.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image and node.image.filepath:
                    src = Path(bpy.path.abspath(node.image.filepath))
                    dst = out_dir / f"{skin_id}{src.suffix}"
                    if src.exists():
                        shutil.copyfile(src, dst)
                        node.image.filepath = str(dst)
                        node.image.name = skin_id + ("" if "roughness" not in src.name else "_roughness")
                        node.image.reload()
    mesh = skin.data
    mesh.transform(skin.matrix_world)
    skin.matrix_world = Matrix.Identity(4)
    mesh.transform(rotation)
    # Loose crumbs: keep only islands with at least 0.3% of the vertices.
    bpy.ops.object.select_all(action="DESELECT")
    skin.select_set(True)
    bpy.context.view_layer.objects.active = skin
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.separate(type="LOOSE")
    bpy.ops.object.mode_set(mode="OBJECT")
    parts = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    total = sum(len(o.data.vertices) for o in parts)
    keep = [o for o in parts if len(o.data.vertices) >= total * 0.003]
    for o in parts:
        if o not in keep:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.object.select_all(action="DESELECT")
    for o in keep:
        o.select_set(True)
    bpy.context.view_layer.objects.active = keep[0]
    if len(keep) > 1:
        bpy.ops.object.join()
    skin = bpy.context.view_layer.objects.active
    mesh = skin.data
    # Handle at the bottom, centred, scaled to a rod's length.
    zs = [v.co.z for v in mesh.vertices]
    xs = [v.co.x for v in mesh.vertices]
    ys = [v.co.y for v in mesh.vertices]
    base = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs)))
    scale = SKIN_LENGTH / (max(zs) - min(zs))
    mesh.transform(Matrix.Translation(-base))
    mesh.transform(Matrix.Scale(scale, 4))
    # Lighter, for Roblox's triangle limit per MeshPart.
    tris = sum(len(p.vertices) - 2 for p in mesh.polygons)
    if tris > SKIN_TRIS:
        dec = skin.modifiers.new("Lighter", "DECIMATE")
        dec.ratio = SKIN_TRIS / tris
        bpy.ops.object.modifier_apply(modifier=dec.name)
    source = mesh.copy()
    bpy.data.objects.remove(skin, do_unlink=True)
    holder = bpy.data.objects.new(skin_id, None)
    export.objects.link(holder)
    holder.location = Vector(((len(rods) + skin_index) * 2.0, 0, 0))
    names = []
    for section, lo, hi in sections:
        piece = bpy.data.objects.new(f"{skin_id}__{section}__Skin", source.copy())
        export.objects.link(piece)
        bpy.ops.object.select_all(action="DESELECT")
        piece.select_set(True)
        bpy.context.view_layer.objects.active = piece
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        if lo > 0:
            bpy.ops.mesh.bisect(plane_co=(0, 0, SKIN_LENGTH * lo), plane_no=(0, 0, 1), clear_inner=True, clear_outer=False)
        if hi < 1:
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.bisect(plane_co=(0, 0, SKIN_LENGTH * hi), plane_no=(0, 0, 1), clear_inner=False, clear_outer=True)
        bpy.ops.object.mode_set(mode="OBJECT")
        if not piece.data.vertices:
            bpy.data.objects.remove(piece, do_unlink=True)
            continue
        piece.data.name = piece.name
        piece.parent = holder
        piece.location = Vector((0, 0, 0))
        names.append(piece.name)
    sidecar["skins"][skin_id] = {
        "folder": folder,
        "length": SKIN_LENGTH,
        "tris": sum(len(p.vertices) - 2 for p in source.polygons),
        "pieces": sorted(names),
    }
    bpy.data.meshes.remove(source)

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
    path_mode="COPY",
    embed_textures=True,
)
(out_dir / "Roblox_Import_Rods_and_Fountain.json").write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
print("EXPORTED>" + json.dumps({"fbx": str(fbx), "bytes": fbx.stat().st_size, "rods": len(sidecar["rods"]),
                                  "pieces": sum(len(r["pieces"]) for r in sidecar["rods"].values())}))
