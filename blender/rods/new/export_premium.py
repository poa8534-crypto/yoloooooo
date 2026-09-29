"""Export every premium rod for Roblox, in one FBX.

    blender -b --python blender/rods/new/export_premium.py

For each skin in SKINS it opens the skin's .blend and makes, in one FBX
(blender/rods/new/out/Roblox_Import_Premium_Rods.fbx) that Studio's 3D
Importer brings in as one model:

  * the still parts, merged per section and material into one MeshPart each,
    named  <SkinId>__<Section>__<Prefix>_<Material>
  * every animated part (floating shards, orbit rings) as its own MeshPart,
    named  <SkinId>__<Section>__<Prefix>_<Material>__<Vfx><nn>
    where <Vfx> is Float, OrbitAxial or OrbitSide (tools/world/SetupRodModels
    turns that into the part's "Vfx" attribute; RodVfx.client animates it).

Pattern textures (Royal, Frost, Inferno) are embedded in the FBX and the UVs
travel with the meshes. Particles and the render-only line are left out: in
the game, particles are ParticleEmitters and the line is the game's own. The
skins stand side by side, 3 units apart, each with its own materials.

Also writes Roblox_Import_Premium_Rods.json and prints the Luau MATERIALS rows
for SetupRodModels (colour, Roblox material, transparency, texture).
"""

import json
import tempfile
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.gettempdir()) / "premium_rod_parts"
TMP.mkdir(parents=True, exist_ok=True)

N, G, M, P = "Neon", "Glass", "Metal", "SmoothPlastic"
# skin id -> blend, material prefix, Roblox material (+ transparency) per Blender material.
SKINS = {
    "Skin_Arena": ("base_rod.blend", "Arena", {
        "Shell": (P, 0), "Dark": (P, 0), "Accent": (P, 0), "Metal": (M, 0), "Glow": (N, 0), "Line": (N, 0)}),
    "Skin_Void": ("void_rod.blend", "Void", {
        "Shell": (P, 0), "Dark": (P, 0), "Accent": (N, 0), "Metal": (M, 0), "Glow": (N, 0), "Line": (N, 0),
        "VoidCrystal": (G, 0.12), "VoidCrystalHot": (N, 0), "VoidOrb": (P, 0)}),
    "Skin_Royal": ("royal_rod.blend", "Royal", {
        "Shell": (P, 0), "Dark": (P, 0), "Accent": (M, 0), "Metal": (M, 0), "Glow": (N, 0), "Line": (N, 0),
        "Panel": (P, 0), "Sapphire": (G, 0.1), "Ruby": (G, 0.1)}),
    "Skin_Frost": ("frost_rod.blend", "Frost", {
        "Shell": (P, 0), "Dark": (P, 0), "Accent": (P, 0), "Metal": (M, 0), "Glow": (N, 0), "Line": (N, 0),
        "IceCrystal": (G, 0.2), "IceCrystalDeep": (G, 0.15), "Snowflake": (N, 0)}),
    "Skin_Inferno": ("inferno_rod.blend", "Inferno", {
        "Shell": (P, 0), "Dark": (P, 0), "Accent": (M, 0), "Metal": (M, 0), "Glow": (N, 0), "Line": (N, 0),
        "RedChrome": (M, 0), "EmberCrystal": (N, 0), "HexStud": (N, 0)}),
}
TAG = {"float": "Float", "orbit_axial": "OrbitAxial", "orbit_side": "OrbitSide"}


def srgb(c):
    return [round(255 * (max(0.0, x) ** (1 / 2.2))) for x in c[:3]]


def describe(mat):
    """(rgb, texture file or None) for a material, as Roblox should show it."""
    b = mat.node_tree.nodes.get("Principled BSDF")
    base = b.inputs["Base Color"]
    if base.is_linked:
        node = base.links[0].from_node
        if node.type == "TEX_IMAGE" and node.image:
            return [255, 255, 255], Path(node.image.filepath).name
    strength = 0.0 if b.inputs["Emission Strength"].is_linked else b.inputs["Emission Strength"].default_value
    emit = list(b.inputs["Emission Color"].default_value)
    # A glowing material shows its emission colour; in Roblox that is the Neon's colour.
    return srgb(emit if strength > 0.5 else list(base.default_value)), None


sidecar = {"skins": {}, "materials": {}}
written = []

for index, (skin_id, (blend, prefix, roblox)) in enumerate(SKINS.items()):
    bpy.ops.wm.open_mainfile(filepath=str(HERE / blend))
    scene = bpy.context.scene
    rod = bpy.data.objects["Rod"]
    rod.matrix_world = Matrix.Identity(4)
    bpy.context.view_layer.update()
    deps = bpy.context.evaluated_depsgraph_get()
    groups, animated = {}, []
    for o in rod.children:
        if o.type != "MESH" or o.hide_render or o.get("vfx") == "particle" or o.name == "FishingLine":
            continue
        mat = o.data.materials[0] if o.data.materials else None
        if mat is None:
            continue
        mesh = bpy.data.meshes.new_from_object(o.evaluated_get(deps))
        mesh.transform(Matrix.Translation((index * 3.0, 0, 0)) @ o.matrix_world)
        section = o.get("section", "Handle")
        vfx = o.get("vfx", "static")
        if vfx in TAG:
            animated.append((section, mat, vfx, mesh))
        else:
            groups.setdefault((section, mat.name), (mat, []))[1].append(mesh)

    made, parts = [], []

    def emit(name, mat, meshes):
        bm = bmesh.new()
        for m in meshes:
            bm.from_mesh(m)
        joined = bpy.data.meshes.new(name)
        bm.to_mesh(joined)
        bm.free()
        joined.materials.append(mat)
        o = bpy.data.objects.new(name, joined)
        made.append(o)
        parts.append({"name": name, "tris": sum(len(p.vertices) - 2 for p in joined.polygons)})

    for (section, mat_name), (mat, meshes) in sorted(groups.items()):
        emit(f"{skin_id}__{section}__{prefix}_{mat_name}", mat, meshes)
    counters = {}
    for section, mat, vfx, mesh in animated:
        tag = TAG[vfx]
        counters[tag] = counters.get(tag, 0) + 1
        emit(f"{skin_id}__{section}__{prefix}_{mat.name}__{tag}{counters[tag]:02d}", mat, [mesh])

    # Materials under the skin's own names, so skins never share an entry.
    for m in {o.data.materials[0] for o in made}:
        rblx, transparency = roblox.get(m.name, (P, 0))
        rgb, texture = describe(m)
        key = f"{prefix}_{m.name}"
        sidecar["materials"][key] = {"rgb": rgb, "material": rblx, "transparency": transparency, "texture": texture}
        m.name = key
    for img in bpy.data.images:
        if img.source == "FILE" and img.filepath:
            img.filepath = str(Path(bpy.path.abspath(img.filepath)).resolve())
            img.pack()
    worst = max(p["tris"] for p in parts)
    sidecar["skins"][skin_id] = {"parts": parts, "max_tris_per_part": worst, "length": 10.0}
    print(f"SKIN {skin_id}: {len(parts)} parts, worst {worst} tris, animated {counters}")
    path = TMP / f"{skin_id}.blend"
    bpy.data.libraries.write(str(path), set(made), fake_user=True, compress=False)
    written.append((path, [o.name for o in made]))

# One scene with every skin, exported once.
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
for path, names in written:
    with bpy.data.libraries.load(str(path), link=False) as (src, dst):
        dst.objects = names
    for o in dst.objects:
        scene.collection.objects.link(o)
for o in scene.objects:
    o.select_set(True)
bpy.ops.export_scene.fbx(
    filepath=str(OUT / "Roblox_Import_Premium_Rods.fbx"),
    use_selection=True,
    object_types={"MESH"},
    apply_unit_scale=True,
    axis_forward="-Z",
    axis_up="Y",
    mesh_smooth_type="FACE",
    path_mode="COPY",
    embed_textures=True,
)
(OUT / "Roblox_Import_Premium_Rods.json").write_text(json.dumps(sidecar, indent=1))
print("TOTAL", len(scene.objects), "parts")
print("LUAU MATERIALS")
for key, m in sidecar["materials"].items():
    r, g, b = m["rgb"]
    tex = f', texture = "{m["texture"]}"' if m["texture"] else ""
    print(f'\t["{key}"] = {{ rgb = {{ {r}, {g}, {b} }}, metallic = 0, glow = 0, material = "{m["material"]}", '
          f'transparency = {m["transparency"]}{tex} }},')
