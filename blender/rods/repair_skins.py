"""Repair the owner's raw rod skins (image-to-3D meshes) into clean rods.

    blender -b --python blender/rods/repair_skins.py -- "Lava rod" "Void rod"

Run texture_clean.py first: it writes the cleaned texture this script uses.
For each skin (settings in SKINS) it:

  1. welds the texture seams (the OBJ splits every vertex along them);
  2. removes floating fragments (loose islands outside the rod's body) and
     tiny crumbs;
  3. fills small holes and dissolves degenerate faces, and fixes the normals;
  4. stands the rod up: its length along +Z with the handle at the bottom;
  5. straightens the shaft: a raw rod wanders and droops, so the rod's centre
     line is measured along its length (from the slim shaft only, not the reel)
     and every slice is shifted back onto one straight line;
  6. smooths the lumpy, faceted surface (a volume-keeping Laplacian smooth)
     while hard edges -- spikes, rims, the tip -- stay crisp, and shades it
     smooth by angle;
  7. can add a tip ring and side-mounted line guides (a second material,
     "Guide") for a skin that needs them -- off for Lava and Void: on their
     spiked, stylised shafts added rings only cluttered the design;

and writes blender/rods/out/repaired/<folder>/output.obj (+ .mtl) using the
cleaned texture, ready for export_rods.py.
"""

import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "blender" / "rods" / "out" / "repaired"

# folder -> how to stand it up (rotation putting the length on +Z, handle down),
# and whether it needs guides added.
SKINS = {
    "Lava rod": {"rotation": Matrix.Identity(4), "guides": False, "tip_ring": False},
    "Void rod": {"rotation": Matrix.Rotation(math.radians(-90), 4, "Y"), "guides": False, "tip_ring": False},
}
CRUMB_SHARE = 0.004  # islands with fewer faces than this share of the rod are crumbs
FLOAT_GAP = 0.01  # islands whose middle is this far outside the body (share of length) are fragments
HOLE_EDGES = 64  # holes up to this many edges are filled
SLICES = 60  # slices along the rod for the centre line
SMOOTH_LAMBDA = 0.35
SMOOTH_PASSES = 3
SHARP_ANGLE = math.radians(48)  # edges sharper than this stay crisp
GUIDE_COUNT = 4  # line guides added along the plain shaft, when the skin has none
STRAIGHTEN_ABOVE = 0.01  # straighten only when the rod droops more than this share of its length
GUIDE_COLOR = (0.93, 0.72, 0.28, 1.0)


def select_only(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def islands(bm):
    seen = {}
    groups = []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack, group = [f], []
        seen[f.index] = True
        while stack:
            cur = stack.pop()
            group.append(cur)
            for e in cur.edges:
                for nf in e.link_faces:
                    if nf.index not in seen:
                        seen[nf.index] = True
                        stack.append(nf)
        groups.append(group)
    return groups


def centre_line(verts, length):
    """The rod's centre line along Z as a smooth curve, and each slice's width.

    The shaft is tracked slice by slice from the handle up: each slice's centre
    is the middle of its points within reach of the centre below it, so eyelets,
    spikes and the reel (further out than the shaft is thick) cannot pull it
    aside. A cubic through those centres is the centre line.
    Returns (centre(z) -> Vector, widths, core).
    """
    import numpy as np

    buckets = [[] for _ in range(SLICES)]
    for v in verts:
        k = min(SLICES - 1, max(0, int(v.co.z / length * SLICES)))
        buckets[k].append(v.co)
    widths = []
    for b in buckets:
        if len(b) < 8:
            widths.append(None)
            continue
        xs = sorted(p.x for p in b)
        ys = sorted(p.y for p in b)
        widths.append(max(xs[-1] - xs[0], ys[-1] - ys[0]))
    known = sorted(w for w in widths if w is not None)
    core = known[len(known) // 3] if known else 0
    # Start from the slimmest slice of the bottom third (the handle).
    bottom = [k for k in range(SLICES // 3) if widths[k]]
    if not bottom:
        return (lambda z: Vector((0, 0))), widths, core
    start = min(bottom, key=lambda k: widths[k])
    b = buckets[start]
    centre = Vector((sum(p.x for p in b) / len(b), sum(p.y for p in b) / len(b)))
    reach = core * 0.75
    zs, cx, cy = [], [], []
    for direction in (range(start, SLICES), range(start - 1, -1, -1)):
        c = centre.copy()
        for k in direction:
            near = [p for p in buckets[k] if (Vector((p.x, p.y)) - c).length <= reach]
            if len(near) < 4:
                continue
            c = Vector((sum(p.x for p in near) / len(near), sum(p.y for p in near) / len(near)))
            zs.append((k + 0.5) / SLICES * length)
            cx.append(c.x)
            cy.append(c.y)
    if len(zs) < 4:
        return (lambda z: Vector((0, 0))), widths, core
    fx = np.polyfit(zs, cx, 3)
    fy = np.polyfit(zs, cy, 3)
    return (lambda z: Vector((float(np.polyval(fx, z)), float(np.polyval(fy, z))))), widths, core


def add_ring(bm, centre, radius, thickness, uv_layer, axis=Vector((0, 0, 1))):
    """A torus at `centre` around `axis` (the tip ring and the line guides)."""
    major, minor = 16, 8
    axis = axis.normalized()
    u = axis.orthogonal().normalized()
    w = axis.cross(u).normalized()
    grid = []
    for i in range(major):
        a = 2 * math.pi * i / major
        radial = u * math.cos(a) + w * math.sin(a)
        row = []
        for j in range(minor):
            b = 2 * math.pi * j / minor
            p = centre + radial * (radius + thickness * math.cos(b)) + axis * (thickness * math.sin(b))
            row.append(bm.verts.new(p))
        grid.append(row)
    for i in range(major):
        for j in range(minor):
            quad = [grid[i][j], grid[(i + 1) % major][j], grid[(i + 1) % major][(j + 1) % minor], grid[i][(j + 1) % minor]]
            f = bm.faces.new(quad)
            f.material_index = 1
            f.smooth = True
            for loop in f.loops:
                loop[uv_layer].uv = (0.0, 0.0)


def add_stalk(bm, a, b, radius, uv_layer):
    """A thin post from the shaft to a guide ring."""
    axis = (b - a)
    if axis.length < 1e-6:
        return
    n = axis.normalized()
    u = n.orthogonal().normalized()
    w = n.cross(u).normalized()
    sides = 6
    ring_a, ring_b = [], []
    for i in range(sides):
        t = 2 * math.pi * i / sides
        off = (u * math.cos(t) + w * math.sin(t)) * radius
        ring_a.append(bm.verts.new(a + off))
        ring_b.append(bm.verts.new(b + off))
    for i in range(sides):
        f = bm.faces.new([ring_a[i], ring_a[(i + 1) % sides], ring_b[(i + 1) % sides], ring_b[i]])
        f.material_index = 1
        f.smooth = True
        for loop in f.loops:
            loop[uv_layer].uv = (0.0, 0.0)


def repair(folder):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    settings = SKINS[folder]
    bpy.ops.wm.obj_import(filepath=str(ROOT / folder / "output.obj"))
    obj = [o for o in bpy.data.objects if o.type == "MESH"][0]
    select_only(obj)
    mesh = obj.data
    mesh.transform(obj.matrix_world)
    obj.matrix_world = Matrix.Identity(4)
    mesh.transform(settings["rotation"])

    bm = bmesh.new()
    bm.from_mesh(mesh)
    report = {}
    size = max((max(v.co[i] for v in bm.verts) - min(v.co[i] for v in bm.verts)) for i in range(3))

    # 1. Weld the seams.
    before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=size * 1e-5)
    report["welded_verts"] = before - len(bm.verts)

    # 2. Fragments and crumbs.
    bm.faces.ensure_lookup_table()
    groups = sorted(islands(bm), key=len, reverse=True)
    main = groups[0]
    mv = {v for f in main for v in f.verts}
    lo = Vector((min(v.co[i] for v in mv) for i in range(3)))
    hi = Vector((max(v.co[i] for v in mv) for i in range(3)))
    total = len(bm.faces)
    doomed = []
    for g in groups[1:]:
        c = sum((v.co for f in g for v in f.verts), Vector()) / sum(len(f.verts) for f in g)
        inside = Vector((min(max(c[i], lo[i]), hi[i]) for i in range(3)))
        if (c - inside).length / size > FLOAT_GAP or len(g) < total * CRUMB_SHARE:
            doomed.extend(g)
    report["fragment_faces_removed"] = len(doomed)
    bmesh.ops.delete(bm, geom=list(set(doomed)), context="FACES")

    # 3. Holes, degenerate faces, bad edges, normals.
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=size * 1e-6)
    holes_before = sum(1 for e in bm.edges if e.is_boundary)
    bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary], sides=HOLE_EDGES)
    report["hole_edges_filled"] = holes_before - sum(1 for e in bm.edges if e.is_boundary)
    wire = [e for e in bm.edges if e.is_wire]
    if wire:
        bmesh.ops.delete(bm, geom=wire, context="EDGES")
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    # 4. Stand it up: butt at z = 0, centred on the axis.
    zs = [v.co.z for v in bm.verts]
    z0, z1 = min(zs), max(zs)
    length = z1 - z0
    for v in bm.verts:
        v.co.z -= z0
    # Handle down: the reel (what sticks out furthest from the shaft) belongs
    # in the lower half. If it sits in the upper half, turn the rod over.
    mx = sum(v.co.x for v in bm.verts) / len(bm.verts)
    my = sum(v.co.y for v in bm.verts) / len(bm.verts)
    by_reach = sorted(bm.verts, key=lambda v: (v.co.x - mx) ** 2 + (v.co.y - my) ** 2, reverse=True)
    outer = by_reach[: max(1, len(by_reach) // 10)]
    reel_z = sum(v.co.z for v in outer) / len(outer)
    report["flipped"] = reel_z > length / 2
    if report["flipped"]:
        for v in bm.verts:
            v.co.z = length - v.co.z
            v.co.y = -v.co.y
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    # 5. Straighten: the centre line becomes one straight vertical line.
    centre_at, widths, core = centre_line(bm.verts, length)
    first, last = centre_at(0.0), centre_at(length)
    worst = max((centre_at(length * k / 40) - first.lerp(last, k / 40)).length for k in range(41))
    report["droop_share"] = round(worst / length, 4)
    if worst / length > STRAIGHTEN_ABOVE:
        for v in bm.verts:
            c = centre_at(v.co.z)
            v.co.x -= c.x
            v.co.y -= c.y
    else:
        lean = (last - first) / length
        for v in bm.verts:
            v.co.x -= first.x + lean.x * v.co.z
            v.co.y -= first.y + lean.y * v.co.z

    # 6. Smooth the lumps, keep the hard edges.
    sharp_verts = set()
    for e in bm.edges:
        if len(e.link_faces) == 2 and e.calc_face_angle(0) > SHARP_ANGLE:
            sharp_verts.update(e.verts)
    soft = [v for v in bm.verts if v not in sharp_verts]
    for _ in range(SMOOTH_PASSES):
        bmesh.ops.smooth_laplacian_vert(
            bm, verts=soft, lambda_factor=SMOOTH_LAMBDA, lambda_border=0.0,
            use_x=True, use_y=True, use_z=True, preserve_volume=True,
        )
    report["smoothed_verts"] = len(soft)
    report["kept_sharp_verts"] = len(sharp_verts)
    for f in bm.faces:
        f.smooth = True

    # 7. Tip ring, and guides when the skin has none.
    uv_layer = bm.loops.layers.uv.active
    guide_mat = bpy.data.materials.new("Guide")
    guide_mat.use_nodes = True
    bsdf = guide_mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = GUIDE_COLOR
    bsdf.inputs["Metallic"].default_value = 0.8
    bsdf.inputs["Roughness"].default_value = 0.3
    if settings["guides"] or settings.get("tip_ring"):
        mesh.materials.append(guide_mat)
    # The reel's side: where the most material sticks out of the shaft.
    reel_dir = Vector((0.0, 0.0, 0.0))
    for v in bm.verts:
        d = Vector((v.co.x, v.co.y, 0.0))
        if d.length > core * 1.2:
            reel_dir += d
    reel_dir = reel_dir.normalized() if reel_dir.length > 0 else Vector((1, 0, 0))
    ring_r = max(core * 0.42, length * 0.006)
    rings = []

    def half_width_towards(z_share):
        k = min(SLICES - 1, int(z_share * SLICES))
        lo_z, hi_z = k / SLICES * length, (k + 1) / SLICES * length
        reach = 0.0
        for v in bm.verts:
            if lo_z <= v.co.z <= hi_z:
                reach = max(reach, v.co.x * reel_dir.x + v.co.y * reel_dir.y)
        return reach

    def mount(z_share, scale):
        z = z_share * length
        out = half_width_towards(z_share)
        centre = reel_dir * (out + ring_r * scale * 1.6) + Vector((0, 0, z))
        add_ring(bm, centre, ring_r * scale, ring_r * scale * 0.28, uv_layer)
        add_stalk(bm, reel_dir * (out * 0.8) + Vector((0, 0, z)), centre - reel_dir * ring_r * scale, ring_r * 0.18, uv_layer)
        rings.append(round(z_share, 3))

    # Plain shaft above the reel: slices no wider than the core shaft allows.
    reel_top = max((k for k, w in enumerate(widths) if w and w > core * 1.8), default=0)
    plain = [k for k in range(reel_top + 2, SLICES - 2) if widths[k] and widths[k] <= core * 1.35]
    if settings["guides"] and plain:
        count = min(GUIDE_COUNT, len(plain))
        picks = [plain[round(i * (len(plain) - 1) / max(1, count - 1))] for i in range(count)]
        for n, k in enumerate(picks):
            mount((k + 0.5) / SLICES, 1.0 - 0.35 * n / max(1, count - 1))
    # The tip ring: the highest plain slice (below a spear or a knob at the tip).
    if settings.get("tip_ring"):
        tip_k = max((k for k in range(SLICES) if widths[k] and widths[k] <= core * 1.35), default=SLICES - 1)
        mount((tip_k + 0.5) / SLICES, 0.6)
    report["rings_added"] = len(rings)

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    # Smooth by angle (Blender 4.1+ keeps sharp edges sharp this way).
    select_only(obj)
    try:
        bpy.ops.object.shade_auto_smooth(angle=SHARP_ANGLE)
    except Exception:
        bpy.ops.object.shade_smooth()

    # Use the cleaned texture.
    cleaned = OUT / folder / "textured_mesh.png"
    if cleaned.exists():
        for m in mesh.materials:
            if m and m.use_nodes and m.name != "Guide":
                for node in m.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image and "roughness" not in node.image.name.lower():
                        node.image = bpy.data.images.load(str(cleaned))
    out_dir = OUT / folder
    out_dir.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.obj_export(
        filepath=str(out_dir / "output.obj"),
        export_selected_objects=True,
        export_materials=True,
        path_mode="COPY",
        export_normals=True,
        export_uv=True,
        forward_axis="Y",
        up_axis="Z",
    )
    report["tris"] = sum(len(p.vertices) - 2 for p in mesh.polygons)
    report["length"] = round(length, 3)
    print("REPAIRED", folder, report)


folders = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else list(SKINS)
for name in folders:
    repair(name)
print("done")
