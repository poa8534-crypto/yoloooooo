"""Model the ocean creatures (a shark and a piranha) from scratch, render them, export for Roblox.

    blender -b --python blender/creatures/build_creatures.py

Both are built in Blender units = Roblox studs, nose along +Y and up +Z (the FBX
export turns +Y into Roblox's -Z, which is where the game's creatures face).
Each creature's middle is at the origin, so its pivot is its middle.

  * the body is lofted from cross-sections (width, height and centre line
    along the length, a rounded back and a flatter belly), UV'd as a cylinder
    (u around, v along) and painted with a generated texture: countershading
    (dark back, pale belly, a wavy line between), mottling, gill slits;
  * every fin is lofted as a thin airfoil between a leading edge, a curved
    trailing edge and a tip, with its own painted texture (darker tips);
  * eyes, mouth and teeth are their own small meshes.

Writes creatures.blend, renders/<creature>_*.png, textures/*.png and
out/Roblox_Import_Creatures.fbx with parts named <Creature>__<Part>
(Shark__Body, Shark__DorsalFin, Shark__Fins, ...). In the game the parts go
into ServerStorage.CreatureModels.Shark / .Piranha, renamed to <Part>.
"""

import math
import random
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Vector

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
RENDERS = HERE / "renders"
TEXTURES = HERE / "textures"
for d in (OUT, RENDERS, TEXTURES):
    d.mkdir(parents=True, exist_ok=True)

rng = random.Random(7)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


# ---------------------------------------------------------------- geometry


def mesh_object(name, bm, material, smooth=True):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = smooth
    me.materials.append(material)
    o = bpy.data.objects.new(name, me)
    scene.collection.objects.link(o)
    return o


def loft(rings, uv=True, cap_start=None, cap_end=None):
    """A bmesh through closed rings of equal size; optional pole caps (points)."""
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")
    n = len(rings[0])
    verts = [[bm.verts.new(p) for p in ring] for ring in rings]
    last = len(rings) - 1

    def face(vs, uvs):
        f = bm.faces.new(vs)
        for loop, coord in zip(f.loops, uvs):
            loop[uv_layer].uv = coord
        return f

    for j in range(last):
        v0, v1 = j / last, (j + 1) / last
        for k in range(n):
            k2 = (k + 1) % n
            u0, u1 = k / n, (k + 1) / n
            face(
                [verts[j][k], verts[j][k2], verts[j + 1][k2], verts[j + 1][k]],
                [(u0, v0), (u1, v0), (u1, v1), (u0, v1)],
            )
    if cap_start is not None:
        pole = bm.verts.new(cap_start)
        for k in range(n):
            k2 = (k + 1) % n
            face([pole, verts[0][k2], verts[0][k]], [((k + 0.5) / n, 0.0), ((k + 1) / n, 0.0), (k / n, 0.0)])
    if cap_end is not None:
        pole = bm.verts.new(cap_end)
        for k in range(n):
            k2 = (k + 1) % n
            face([verts[last][k], verts[last][k2], pole], [(k / n, 1.0), ((k + 1) / n, 1.0), ((k + 0.5) / n, 1.0)])
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bm.normal_update()
    return bm


def body_rings(length_front, length_back, width, height, centre, belly_flat, sections=64, around=40, t0=0.004):
    """Cross-sections of a fish body from the snout (t=0) to the tail stalk (t=1)."""
    rings, meta = [], []
    for j in range(sections):
        t = t0 + (1 - t0) * j / (sections - 1)
        y = length_front - (length_front + length_back) * t
        w, h, c = width(t), height(t), centre(t)
        ring = []
        for k in range(around):
            a = 2 * math.pi * k / around  # 0 = top, pi/2 = +x side
            ca, sa = math.cos(a), math.sin(a)
            z = h * ca if ca > 0 else h * belly_flat * -((-ca) ** 0.85)
            ring.append(Vector((w * sa, y, c + z)))
        rings.append(ring)
        meta.append((t, y, w, h, c))
    return rings, meta


def fin(root_le, root_te, tip, thickness, sweep=0.0, span_sections=14, around=14, trail_curve=0.6):
    """A fin lofted along its span: thin airfoil sections from the root chord to the tip."""
    root_le, root_te, tip = Vector(root_le), Vector(root_te), Vector(tip)
    normal = (root_te - root_le).cross(tip - root_le).normalized()
    rings = []
    for j in range(span_sections):
        s = j / (span_sections - 1)
        s = min(s, 0.985)
        # The leading edge bows forward a little; the trailing edge is concave.
        le = root_le.lerp(tip, s)
        bow = (tip - root_le).cross(normal).normalized()
        le += bow * sweep * math.sin(math.pi * s)
        te = root_te.lerp(tip, s ** trail_curve)
        chord = te - le
        thick = thickness * (1 - 0.8 * s) * max(chord.length, 1e-4) / max((root_te - root_le).length, 1e-4)
        ring = []
        for k in range(around):
            phi = 2 * math.pi * k / around
            along = (1 - math.cos(phi)) / 2
            # An airfoil: thickest a third of the way back.
            profile = math.sin(phi) * (1.0 - 0.35 * along) * (0.3 + 0.7 * math.sin(math.pi * min(1, along * 1.4 + 0.1)))
            ring.append(le + chord * along + normal * thick * 0.5 * profile)
        rings.append(ring)
    return loft(rings, cap_end=tip)


def sphere(centre, radius, scale=(1, 1, 1), segments=20, rings=12):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=radius)
    for v in bm.verts:
        v.co = Vector((v.co.x * scale[0], v.co.y * scale[1], v.co.z * scale[2])) + Vector(centre)
    return bm


def merge(bms):
    out = bmesh.new()
    me = bpy.data.meshes.new("tmp")
    for b in bms:
        b.to_mesh(me)
        out.from_mesh(me)
        b.free()
    bpy.data.meshes.remove(me)
    return out


def tooth(base, down, size):
    """A small pointed tooth: a triangular pyramid pointing `down`."""
    base, down = Vector(base), Vector(down).normalized()
    side = down.orthogonal().normalized()
    other = down.cross(side).normalized()
    bm = bmesh.new()
    a = bm.verts.new(base + side * size * 0.5)
    b = bm.verts.new(base - side * size * 0.5 + other * size * 0.3)
    c = bm.verts.new(base - side * size * 0.5 - other * size * 0.3)
    p = bm.verts.new(base + down * size * 1.4)
    for f in ((a, b, c), (a, p, b), (b, p, c), (c, p, a)):
        bm.faces.new(f)
    bm.normal_update()
    return bm


def ribbon(points, half_width, normal_of, lift):
    """A thin strip along a curve (for mouths), lifted a little off the surface."""
    bm = bmesh.new()
    rows = []
    for i, p in enumerate(points):
        prev, nxt = points[max(0, i - 1)], points[min(len(points) - 1, i + 1)]
        tangent = (nxt - prev).normalized()
        n = normal_of(p)
        side = tangent.cross(n).normalized()
        base = p + n * lift
        rows.append((bm.verts.new(base + side * half_width), bm.verts.new(base - side * half_width)))
    for i in range(len(rows) - 1):
        bm.faces.new((rows[i][0], rows[i + 1][0], rows[i + 1][1], rows[i][1]))
    bm.normal_update()
    return bm


# ---------------------------------------------------------------- textures


def value_noise(h, w, cells, seed):
    r = np.random.default_rng(seed)
    grid = r.random((cells + 1, cells + 1))
    ys = np.linspace(0, cells, h, endpoint=False)
    xs = np.linspace(0, cells, w, endpoint=False)
    y0, x0 = ys.astype(int), xs.astype(int)
    fy, fx = ys - y0, xs - x0
    fy, fx = fy * fy * (3 - 2 * fy), fx * fx * (3 - 2 * fx)
    a = grid[np.ix_(y0, x0)]
    b = grid[np.ix_(y0, (x0 + 1) % (cells + 1))]
    c = grid[np.ix_((y0 + 1) % (cells + 1), x0)]
    d = grid[np.ix_((y0 + 1) % (cells + 1), (x0 + 1) % (cells + 1))]
    top = a + (b - a) * fx[None, :]
    bottom = c + (d - c) * fx[None, :]
    return top + (bottom - top) * fy[:, None]


def fbm(h, w, seed, octaves=4, base=6):
    total, amp, norm = np.zeros((h, w)), 1.0, 0.0
    for o in range(octaves):
        total += value_noise(h, w, base * 2 ** o, seed + o) * amp
        norm += amp
        amp *= 0.5
    return total / norm


def smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def save_image(name, rgb):
    """rgb: (h, w, 3) floats 0..1, row 0 = v 0. Writes textures/<name>.png and returns the image."""
    h, w, _ = rgb.shape
    img = bpy.data.images.new(name, width=w, height=h, alpha=False)
    rgba = np.ones((h, w, 4), dtype=np.float32)
    rgba[:, :, :3] = np.clip(rgb, 0, 1) ** 2.2  # sRGB-ish into linear buffer
    img.pixels.foreach_set(rgba.ravel())
    path = TEXTURES / f"{name}.png"
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    img.save()
    img.filepath = str(path)
    return img


def rgb(*c):
    return np.array(c, dtype=np.float32) / 255.0


def body_texture(name, back, flank, belly, boundary, seed, extra=None, size=(1024, 1024)):
    """Countershading around a cylinder UV: u = around (0 = top), v = along (0 = snout)."""
    h, w = size
    v = (np.arange(h) + 0.5)[:, None] / h * np.ones((1, w))
    u = (np.arange(w) + 0.5)[None, :] / w * np.ones((h, 1))
    up = np.cos(2 * np.pi * u)  # 1 on the back, -1 on the belly
    wave = 0.06 * np.sin(v * 23 + 1.3) + 0.04 * np.sin(v * 57) + 0.05 * (fbm(h, w, seed, 3, 5) - 0.5)
    pale = smooth(boundary + 0.08, boundary - 0.08, up + wave)  # 1 = belly
    dark_back = smooth(0.55, 0.95, up)
    col = back[None, None, :] * (1 - pale[..., None]) + belly[None, None, :] * pale[..., None]
    col = col * (1 - 0.25 * dark_back[..., None]) + flank[None, None, :] * 0.25 * dark_back[..., None] * 0
    mott = fbm(h, w, seed + 10, 4, 8)
    col *= (0.9 + 0.2 * mott[..., None]) * (1 - 0.35 * pale[..., None]) + 1.0 * 0.35 * pale[..., None]
    if extra:
        col = extra(col, u, v, up, pale)
    return save_image(name, col)


def fin_texture(name, base, edge, seed, size=(256, 256), tip_dark=0.45):
    h, w = size
    v = (np.arange(h) + 0.5)[:, None] / h * np.ones((1, w))  # span: 0 root .. 1 tip
    u = (np.arange(w) + 0.5)[None, :] / w * np.ones((h, 1))  # around the airfoil: 0.5 = trailing edge
    trailing = smooth(0.25, 0.5, 0.5 - np.abs(u - 0.5)) * 0 + smooth(0.32, 0.5, 1 - np.abs(u - 0.5) * 2 * 0.5)
    tipness = smooth(0.45, 1.0, v)
    k = np.clip(tip_dark * tipness + 0.35 * smooth(0.8, 1.0, 1 - np.abs(u - 0.5) * 2), 0, 1)
    col = base[None, None, :] * (1 - k[..., None]) + edge[None, None, :] * k[..., None]
    col *= 0.92 + 0.16 * fbm(h, w, seed, 3, 6)[..., None]
    return save_image(name, col)


# ---------------------------------------------------------------- materials


def material(name, color=(1, 1, 1), image=None, rough=0.45, metallic=0.0, spec=0.5, emission=None, strength=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metallic
    if b.inputs.get("Specular IOR Level") is not None:
        b.inputs["Specular IOR Level"].default_value = spec
    if image is not None:
        tex = m.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = image
        m.node_tree.links.new(tex.outputs["Color"], b.inputs["Base Color"])
    else:
        b.inputs["Base Color"].default_value = (*[c ** 2.2 for c in color], 1)
    if emission is not None:
        b.inputs["Emission Color"].default_value = (*[c ** 2.2 for c in emission], 1)
        b.inputs["Emission Strength"].default_value = strength
    return m


# ---------------------------------------------------------------- the shark


def profile(t, peak, rise, fall, end):
    """A smooth body profile 0..1..end: a beta curve (no kinks) peaking at `peak`, plus a tail stalk."""
    a = rise
    b = a * (1 - peak) / peak
    f = (t ** a) * ((1 - t) ** b) / ((peak ** a) * ((1 - peak) ** b))
    return f * (1 - end * t ** 2) + end * t ** 2 * (1 - (1 - t) ** fall)


def build_shark():
    F, B = 6.2, 4.3  # snout ahead of the middle, tail stalk behind it

    def width(t):
        return 1.62 * profile(t, 0.36, 0.62, 3.0, 0.1)

    def height(t):
        return 1.38 * profile(t, 0.34, 0.7, 3.0, 0.2)

    def centre(t):
        return 0.12 * math.sin(math.pi * t) - 0.25 * (1 - t) ** 5

    rings, meta = body_rings(F, B, width, height, centre, belly_flat=0.78, sections=72, around=44)
    # The snout tip is a little rounded point; the tail stalk closes into the tail.
    tip = Vector((0, F + 0.05, centre(0) - 0.05))
    end = Vector((0, -B - 0.08, centre(1)))

    def gills(col, u, v, up, pale):
        # Five slits each side, just behind the head, curving down the flank.
        for side_u in (0.25, 0.75):
            for i in range(5):
                v0 = 0.245 + i * 0.022
                curve = v0 + 0.012 * np.cos((u - side_u) * 9)
                slit = smooth(0.0045, 0.0015, np.abs(v - curve)) * smooth(0.11, 0.07, np.abs(u - side_u))
                col = col * (1 - 0.55 * slit[..., None])
        # A soft darker line along each side where back meets belly.
        return col

    tex = body_texture(
        "shark_body", rgb(92, 110, 128), rgb(70, 86, 104), rgb(236, 238, 240), boundary=-0.12, seed=3, extra=gills
    )
    body_mat = material("Shark_Skin", image=tex, rough=0.55, spec=0.35)
    body = mesh_object("Shark__Body", loft(rings, cap_start=tip, cap_end=end), body_mat)

    fin_tex = fin_texture("shark_fin", rgb(104, 122, 140), rgb(62, 74, 90), seed=4, tip_dark=0.35)
    fin_mat = material("Shark_Fin", image=fin_tex, rough=0.55, spec=0.35)

    def surface_z(y, top=True):
        # Where the body's back (or belly) is at a given y, for rooting the fins.
        t = (F - y) / (F + B)
        t = min(max(t, 0.0), 1.0)
        return centre(t) + (height(t) if top else -0.78 * height(t))

    dorsal = fin((0, 1.45, surface_z(1.45) - 0.12), (0, -0.55, surface_z(-0.55) - 0.12), (0, -1.05, 3.45),
                 thickness=0.34, sweep=0.18)
    mesh_object("Shark__DorsalFin", dorsal, fin_mat)

    others = []
    others.append(fin((0, -2.55, surface_z(-2.55) - 0.05), (0, -3.15, surface_z(-3.15) - 0.05), (0, -3.4, 1.12),
                      thickness=0.12, sweep=0.05))
    for s in (-1, 1):
        others.append(fin((s * 1.05, 2.75, -0.62), (s * 1.0, 0.95, -0.74), (s * 3.25, 0.15, -1.7),
                          thickness=0.22, sweep=0.25, trail_curve=0.85))
        others.append(fin((s * 0.35, -1.55, surface_z(-1.55, False) + 0.1), (s * 0.3, -2.3, surface_z(-2.3, False) + 0.1),
                          (s * 0.95, -2.7, surface_z(-2.5, False) - 0.55), thickness=0.1, sweep=0.05))
    others.append(fin((0, -2.9, surface_z(-2.9, False) + 0.05), (0, -3.45, surface_z(-3.45, False) + 0.05),
                      (0, -3.7, -1.02), thickness=0.08, sweep=0.03))
    # The tail: a long upper lobe and a shorter lower one.
    others.append(fin((0, -3.55, surface_z(-3.55) - 0.08), (0, -4.6, -0.05), (0, -6.2, 2.6), thickness=0.3,
                      sweep=0.3, trail_curve=0.7))
    others.append(fin((0, -3.7, surface_z(-3.7, False) + 0.08), (0, -4.6, 0.0), (0, -5.5, -1.95), thickness=0.24,
                      sweep=0.15, trail_curve=0.75))
    mesh_object("Shark__Fins", merge(others), fin_mat)

    # Eyes: glossy black, set into the head's sides.
    eye_mat = material("Shark_Eye", color=(0.02, 0.02, 0.03), rough=0.08, spec=1.0)
    t_eye = 0.155
    y_eye = F - (F + B) * t_eye
    eyes = []
    for s in (-1, 1):
        w, h, c = width(t_eye), height(t_eye), centre(t_eye)
        eyes.append(sphere((s * w * 0.9, y_eye, c + h * 0.28), 0.17, (0.7, 1.0, 1.0)))
    mesh_object("Shark__Eyes", merge(eyes), eye_mat)

    # The mouth: a dark crescent under the snout, with a row of teeth along it.
    mouth_mat = material("Shark_Mouth", color=(0.32, 0.06, 0.08), rough=0.6)
    tooth_mat = material("Shark_Teeth", color=(0.97, 0.96, 0.92), rough=0.3)
    points = []
    for i in range(25):
        f = i / 24
        ang = math.pi * (f - 0.5) * 0.92
        y = 4.75 - 0.95 * math.cos(ang) + 0.95
        t = (F - y) / (F + B)
        w, h, c = width(t), height(t), centre(t)
        x = math.sin(ang) * w * 0.86
        z = c - 0.78 * h * (1 - (abs(x) / max(w, 1e-3)) ** 2) ** 0.5 * 0.97
        points.append(Vector((x, y, z)))

    def down_normal(p):
        return Vector((p.x * 0.6, 0.15, -1)).normalized()

    mesh_object("Shark__Mouth", ribbon(points, 0.11, down_normal, 0.02), mouth_mat)
    teeth = [tooth(p + down_normal(p) * 0.04, Vector((0, 0.25, -1)), 0.07) for p in points[2:-2:2]]
    mesh_object("Shark__Teeth", merge(teeth), tooth_mat, smooth=False)
    return "Shark"


# ---------------------------------------------------------------- the piranha


def build_piranha():
    F, B = 1.05, 0.72

    def width(t):
        return 0.3 * profile(t, 0.3, 0.55, 3.0, 0.2)

    def height(t):
        return 0.64 * profile(t, 0.38, 0.5, 3.0, 0.2)

    def centre(t):
        # A blunt, deep head; the jaw juts forward below.
        return 0.06 * math.sin(math.pi * t) - 0.12 * (1 - t) ** 4

    rings, meta = body_rings(F, B, width, height, centre, belly_flat=1.0, sections=56, around=36)
    tip = Vector((0, F + 0.02, centre(0) - 0.08))
    end = Vector((0, -B - 0.03, centre(1)))

    def spots(col, u, v, up, pale):
        # Silver flanks speckled dark; the throat and belly flushed red-orange toward the front.
        speck = (fbm(col.shape[0], col.shape[1], 21, 2, 60) > 0.72).astype(np.float32)
        flank = smooth(0.8, 0.2, np.abs(up))
        col = col * (1 - 0.35 * (speck * flank)[..., None])
        red = smooth(-0.15, -0.6, up) * smooth(0.85, 0.35, v)
        col = col * (1 - red[..., None]) + rgb(226, 72, 38)[None, None, :] * red[..., None]
        return col

    tex = body_texture(
        "piranha_body", rgb(74, 88, 84), rgb(170, 180, 184), rgb(196, 204, 208), boundary=0.35, seed=12, extra=spots
    )
    body_mat = material("Piranha_Skin", image=tex, rough=0.45, spec=0.5)
    mesh_object("Piranha__Body", loft(rings, cap_start=tip, cap_end=end), body_mat)

    grey = fin_texture("piranha_fin", rgb(96, 104, 104), rgb(35, 38, 40), seed=13, tip_dark=0.6)
    fin_mat = material("Piranha_Fin", image=grey, rough=0.5)
    red_tex = fin_texture("piranha_redfin", rgb(214, 70, 40), rgb(120, 24, 16), seed=14, tip_dark=0.5)
    red_mat = material("Piranha_RedFin", image=red_tex, rough=0.5)

    def top(y):
        t = min(max((F - y) / (F + B), 0.0), 1.0)
        return centre(t) + height(t)

    def bottom(y):
        t = min(max((F - y) / (F + B), 0.0), 1.0)
        return centre(t) - height(t)

    grey_fins = [
        fin((0, 0.05, top(0.05) - 0.03), (0, -0.35, top(-0.35) - 0.03), (0, -0.42, top(-0.2) + 0.42), 0.05, 0.05),
        fin((0, -0.52, top(-0.52) - 0.02), (0, -0.62, top(-0.62) - 0.02), (0, -0.66, top(-0.6) + 0.12), 0.03),
        # Forked tail: two lobes.
        fin((0, -0.62, 0.1), (0, -0.78, 0.0), (0, -1.12, 0.52), 0.05, 0.08, trail_curve=0.5),
        fin((0, -0.62, -0.1), (0, -0.78, 0.0), (0, -1.12, -0.52), 0.05, 0.08, trail_curve=0.5),
    ]
    for s in (-1, 1):
        grey_fins.append(fin((s * 0.27, 0.3, -0.26), (s * 0.26, 0.16, -0.3), (s * 0.44, 0.0, -0.44), 0.03))
    mesh_object("Piranha__Fins", merge(grey_fins), fin_mat)
    red_fins = [fin((0, -0.18, bottom(-0.18) + 0.03), (0, -0.6, bottom(-0.6) + 0.03), (0, -0.52, bottom(-0.45) - 0.3),
                    0.04, 0.04)]
    for s in (-1, 1):
        red_fins.append(fin((s * 0.1, 0.12, bottom(0.12) + 0.04), (s * 0.08, -0.02, bottom(-0.02) + 0.04),
                            (s * 0.2, -0.08, bottom(0.0) - 0.2), 0.025))
    mesh_object("Piranha__RedFins", merge(red_fins), red_mat)

    # Big eyes: a red iris with a black pupil.
    iris_mat = material("Piranha_Iris", color=(0.85, 0.12, 0.08), rough=0.2, emission=(1.0, 0.2, 0.1), strength=0.6)
    pupil_mat = material("Piranha_Pupil", color=(0.01, 0.01, 0.01), rough=0.05, spec=1.0)
    t_eye = 0.17
    y_eye = F - (F + B) * t_eye
    w, h, c = width(t_eye), height(t_eye), centre(t_eye)
    irises, pupils = [], []
    for s in (-1, 1):
        at = Vector((s * w * 0.82, y_eye, c + h * 0.3))
        irises.append(sphere(at, 0.1, (0.55, 1, 1)))
        pupils.append(sphere(at + Vector((s * 0.045, 0.005, 0)), 0.055, (0.5, 1, 1)))
    mesh_object("Piranha__Eyes", merge(irises), iris_mat)
    mesh_object("Piranha__Pupils", merge(pupils), pupil_mat)

    # The underbite: a jutting lower jaw lined with sharp triangular teeth.
    jaw_mat = material("Piranha_Jaw", color=(0.55, 0.18, 0.12), rough=0.5)
    tooth_mat = material("Piranha_Teeth", color=(0.98, 0.97, 0.93), rough=0.25)
    jaw_rings = []
    for j in range(12):
        f = j / 11
        y = F + 0.06 - 0.42 * f
        half = 0.07 + 0.2 * math.sin(0.5 * math.pi * f)
        z0 = centre(0.03) - 0.12 - 0.12 * f
        ring = []
        for k in range(16):
            a = 2 * math.pi * k / 16
            ring.append(Vector((half * math.sin(a), y, z0 + (0.07 if math.cos(a) > 0 else 0.12) * math.cos(a))))
        jaw_rings.append(ring)
    mesh_object("Piranha__Jaw", loft(jaw_rings, cap_start=Vector((0, F + 0.08, centre(0.03) - 0.12))), jaw_mat)
    teeth = []
    # Along the jaw's top rim: lower teeth point up; the upper row hangs just above them, interlocking.
    for j in range(1, 6):
        f = j / 11
        y = F + 0.06 - 0.42 * f
        half = 0.07 + 0.2 * math.sin(0.5 * math.pi * f)
        z_rim = centre(0.03) - 0.12 - 0.12 * f + 0.07
        for side in (-1, 1):
            x = side * half * 0.8
            teeth.append(tooth((x, y, z_rim - 0.01), (0, 0.15, 1), 0.04))
            teeth.append(tooth((x * 0.92, y - 0.035, z_rim + 0.075), (0, 0.15, -1), 0.032))
    mesh_object("Piranha__Teeth", merge(teeth), tooth_mat, smooth=False)
    return "Piranha"


# ---------------------------------------------------------------- render + export


def render_setup():
    engines = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    for attr, value in (("taa_render_samples", 64), ("use_raytracing", True)):
        if hasattr(scene.eevee, attr):
            setattr(scene.eevee, attr, value)
    scene.view_settings.view_transform = "AgX" if "AgX" in [
        i.identifier for i in scene.view_settings.bl_rna.properties["view_transform"].enum_items
    ] else "Filmic"
    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.05, 0.16, 0.26, 1)
    bg.inputs["Strength"].default_value = 0.7
    scene.world = world
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    for name, energy, rot in (("Key", 4.0, (0.9, 0.2, 0.6)), ("Fill", 1.2, (1.2, 0.0, -2.2)), ("Rim", 3.0, (-2.4, 0.0, 3.0))):
        light = bpy.data.objects.new(name, bpy.data.lights.new(name, "SUN"))
        light.data.energy = energy
        light.rotation_euler = rot
        scene.collection.objects.link(light)


def shoot(prefix, names, views, radius):
    cam = scene.camera
    for o in scene.objects:
        if o.type == "MESH":
            o.hide_render = not o.name.startswith(prefix)
    for tag, look, lens in views:
        d = Vector(look).normalized()
        cam.data.lens = lens
        dist = radius / math.tan(cam.data.angle / 2)
        cam.location = -d * dist
        cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
        scene.render.resolution_x, scene.render.resolution_y = 1600, 900
        scene.render.filepath = str(RENDERS / f"{prefix.strip('_').lower()}_{tag}.png")
        bpy.ops.render.render(write_still=True)


build_shark()
build_piranha()
# The piranha stands 3 units to the side of the shark in the saved file (and in the FBX),
# so both can be seen; the game re-centres each model on its own.
for o in scene.objects:
    if o.name.startswith("Piranha__"):
        o.location.x = 6.0

render_setup()
for o in scene.objects:
    if o.name.startswith("Piranha__"):
        o.location.x = 0.0
SIDE = (1.0, -0.15, -0.12)
shoot("Shark__", None, [("side", SIDE, 50), ("hero", (0.8, -0.55, -0.35), 45), ("top", (0.25, -0.2, -1.0), 45),
                        ("front", (-0.35, -1.0, -0.12), 40)], 7.2)
shoot("Piranha__", None, [("side", SIDE, 50), ("hero", (0.8, -0.55, -0.3), 45), ("front", (-0.4, -1.0, -0.05), 40)], 1.35)
for o in scene.objects:
    o.hide_render = False
    if o.name.startswith("Piranha__"):
        o.location.x = 6.0

bpy.ops.wm.save_as_mainfile(filepath=str(HERE / "creatures.blend"))

for o in scene.objects:
    o.select_set(o.type == "MESH")
for img in bpy.data.images:
    if img.filepath:
        img.pack()
bpy.ops.export_scene.fbx(
    filepath=str(OUT / "Roblox_Import_Creatures.fbx"),
    use_selection=True,
    object_types={"MESH"},
    apply_unit_scale=True,
    axis_forward="-Z",
    axis_up="Y",
    mesh_smooth_type="FACE",
    path_mode="COPY",
    embed_textures=True,
)
for o in scene.objects:
    if o.type == "MESH":
        print("PART", o.name, len(o.data.polygons), "faces", [round(x, 2) for x in o.dimensions])
print("DONE")
