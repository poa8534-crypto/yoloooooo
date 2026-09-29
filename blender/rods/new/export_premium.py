"""Export the premium skins for Roblox.

    blender -b --python blender/rods/new/export_premium.py

For each skin .blend (SKINS) it writes, into one FBX
(blender/rods/new/out/Roblox_Import_Premium_Rods.fbx) that Studio's 3D
Importer brings in as one model:

  * the still parts, merged per section and material into one MeshPart each,
    named  <SkinId>__<Section>__<Skin>_<Material>
  * every animated part (floating shards, orbit rings) as its own MeshPart,
    named  <SkinId>__<Section>__<Skin>_<Material>__<Vfx><nn>
    where <Vfx> is Float, OrbitAxial or OrbitSide (tools/world/SetupRodModels
    turns that into the part's "Vfx" attribute; RodVfx.client animates it).

Particles and the render-only line are left out: in the game, particles are
ParticleEmitters and the line is the game's own cast line. The bevels are
applied, so the chamfers the renders show are in the meshes.

Also writes Roblox_Import_Premium_Rods.json (the materials in sRGB with the
Roblox material each one becomes, and the parts) and prints a Luau table of
the materials for SetupRodModels.
"""

import json
import math
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)

# skin id -> (blend file, material prefix, Roblox material per material name, transparency)
SKINS = {
    "Skin_Void": {
        "blend": HERE / "void_rod.blend",
        "prefix": "Void",
        "roblox": {
            "Shell": ("SmoothPlastic", 0.0),
            "Dark": ("SmoothPlastic", 0.0),
            "Accent": ("Neon", 0.0),
            "Metal": ("Metal", 0.0),
            "Glow": ("Neon", 0.0),
            "Line": ("Neon", 0.0),
            "VoidCrystal": ("Glass", 0.12),
            "VoidCrystalHot": ("Neon", 0.0),
            "VoidOrb": ("SmoothPlastic", 0.0),
        },
    },
}
TAG = {"float": "Float", "orbit_axial": "OrbitAxial", "orbit_side": "OrbitSide"}


def srgb(c):
    return [round(255 * (max(0.0, x) ** (1 / 2.2))) for x in c[:3]]


def colour_of(mat):
    b = mat.node_tree.nodes.get("Principled BSDF")
    base = list(b.inputs["Base Color"].default_value)
    strength = b.inputs["Emission Strength"].default_value if not b.inputs["Emission Strength"].is_linked else 0.0
    emit = list(b.inputs["Emission Color"].default_value)
    # A glowing material shows its emission colour; in Roblox that is the Neon's colour.
    return srgb(emit if strength > 0.5 else base)


export = bpy.data.collections.new("Export")
sidecar = {"skins": {}, "materials": {}}
meshes_out = []

for skin_id, spec in SKINS.items():
    bpy.ops.wm.open_mainfile(filepath=str(spec["blend"]))
    scene = bpy.context.scene
    export = bpy.data.collections.new("Export")
    scene.collection.children.link(export)
    rod = bpy.data.objects["Rod"]
    rod.matrix_world = Matrix.Identity(4)
    bpy.context.view_layer.update()
    deps = bpy.context.evaluated_depsgraph_get()
    groups = {}
    animated = []
    for o in rod.children:
        if o.type != "MESH" or o.hide_render or o.get("vfx") == "particle" or o.name == "FishingLine":
            continue
        mat = o.data.materials[0] if o.data.materials else None
        if mat is None:
            continue
        mesh = bpy.data.meshes.new_from_object(o.evaluated_get(deps))
        mesh.transform(o.matrix_world)
        section = o.get("section", "Handle")
        vfx = o.get("vfx", "static")
        if vfx in TAG:
            animated.append((section, mat, vfx, mesh))
        else:
            groups.setdefault((section, mat.name), (mat, []))[1].append(mesh)

    holder = bpy.data.objects.new(skin_id, None)
    export.objects.link(holder)
    parts = []

    def emit(name, mat, meshes):
        bm = bmesh.new()
        for m in meshes:
            bm.from_mesh(m)
        joined = bpy.data.meshes.new(name)
        bm.to_mesh(joined)
        bm.free()
        joined.materials.append(mat)
        o = bpy.data.objects.new(name, joined)
        export.objects.link(o)
        o.parent = holder
        tris = sum(len(p.vertices) - 2 for p in joined.polygons)
        parts.append({"name": name, "tris": tris})
        return o

    prefix = spec["prefix"]
    for (section, mat_name), (mat, meshes) in sorted(groups.items()):
        emit(f"{skin_id}__{section}__{prefix}_{mat_name}", mat, meshes)
    counters = {}
    for section, mat, vfx, mesh in animated:
        tag = TAG[vfx]
        counters[tag] = counters.get(tag, 0) + 1
        emit(f"{skin_id}__{section}__{prefix}_{mat.name}__{tag}{counters[tag]:02d}", mat, [mesh])

    # Materials under the skin's own names, so skins never share a colour entry.
    for m in bpy.data.materials:
        if m.name in spec["roblox"] and m.users:
            roblox, transparency = spec["roblox"][m.name]
            key = f"{prefix}_{m.name}"
            sidecar["materials"][key] = {"rgb": colour_of(m), "material": roblox, "transparency": transparency}
            m.name = key
    worst = max(p["tris"] for p in parts)
    sidecar["skins"][skin_id] = {"parts": parts, "max_tris_per_part": worst, "length": 10.0}
    print(f"SKIN {skin_id}: {len(parts)} parts, worst {worst} tris, animated {counters}")

    bpy.ops.object.select_all(action="DESELECT")
    for o in export.objects:
        o.select_set(True)
    bpy.ops.export_scene.fbx(
        filepath=str(OUT / "Roblox_Import_Premium_Rods.fbx"),
        use_selection=True,
        object_types={"EMPTY", "MESH"},
        apply_unit_scale=True,
        axis_forward="-Z",
        axis_up="Y",
        mesh_smooth_type="FACE",
        use_mesh_modifiers=True,
        path_mode="COPY",
        embed_textures=True,
    )

(OUT / "Roblox_Import_Premium_Rods.json").write_text(json.dumps(sidecar, indent=1))
print("LUAU MATERIALS")
for key, m in sidecar["materials"].items():
    r, g, b = m["rgb"]
    print(f'\t["{key}"] = {{ rgb = {{ {r}, {g}, {b} }}, metallic = 0, glow = 0, material = "{m["material"]}", transparency = {m["transparency"]} }},')
