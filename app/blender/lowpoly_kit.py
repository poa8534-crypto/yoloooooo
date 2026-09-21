"""A low-poly building kit that runs INSIDE Blender.

The bridge puts this on the path of every script it runs, so a model writing a
pass does not have to invent a palm tree, a pier or a way of finding the ground
from bare bmesh calls -- which is where its scripts kept dying. It calls these
instead, and spends its answer on WHERE things go.

Nothing here is specific to one island. The island's shape, heights, railway
and colours arrive as data through `load_island(spec)`; a second island is a
second spec.

Conventions, the same as the bridge's:
- 1 Blender unit = 1 Roblox stud. +X east, +Y north, Z up, sea level z = 0.
- Every mesh is built with bmesh, triangulated, flat shaded, and carries no
  object scale. Placement is by object location and a rotation about Z only.
- Repeated props share one mesh (`place` links the same mesh data), so a
  hundred palms cost one palm.

Only `bpy`, `bmesh` and `mathutils` are used; no operators, so nothing depends
on an active object or a context the background run does not have.
"""

from __future__ import annotations

import math
import random

import bmesh
import bpy
from mathutils import Matrix, Vector

# --------------------------------------------------------------------------
# Colours. Overridable per island: spec["palette"] entries replace these.
# --------------------------------------------------------------------------
PALETTE: dict[str, tuple[float, float, float]] = {
    "sand": (0.93, 0.80, 0.52),
    "wet_sand": (0.80, 0.68, 0.45),
    "seabed": (0.55, 0.78, 0.72),
    "grass": (0.40, 0.72, 0.30),
    "grass_dark": (0.35, 0.66, 0.27),
    "rock": (0.56, 0.54, 0.52),
    "rock_dark": (0.40, 0.39, 0.40),
    "cliff": (0.62, 0.46, 0.32),
    "dirt": (0.62, 0.45, 0.28),
    "stone_path": (0.78, 0.74, 0.66),
    "wood": (0.66, 0.44, 0.24),
    "wood_light": (0.82, 0.62, 0.38),
    "wood_dark": (0.42, 0.27, 0.15),
    "trunk": (0.52, 0.36, 0.22),
    "palm_trunk": (0.70, 0.55, 0.36),
    "leaf": (0.30, 0.68, 0.28),
    "leaf_light": (0.46, 0.80, 0.32),
    "leaf_dark": (0.18, 0.48, 0.24),
    "pine": (0.16, 0.46, 0.30),
    "ocean": (0.02, 0.62, 0.80),
    "shallows": (0.42, 0.93, 0.88),
    "deep": (0.04, 0.30, 0.52),
    "foam": (0.95, 0.98, 1.00),
    "white_stone": (0.94, 0.93, 0.88),
    "teal_roof": (0.14, 0.62, 0.66),
    "blue_roof": (0.20, 0.45, 0.78),
    "roof_red": (0.80, 0.34, 0.28),
    "metal": (0.36, 0.30, 0.28),
    "rail": (0.30, 0.28, 0.27),
    "lamp": (1.00, 0.86, 0.45),
    "glass": (0.62, 0.86, 0.95),
    "coconut": (0.40, 0.28, 0.16),
    "flower_pink": (0.98, 0.50, 0.70),
    "flower_yellow": (1.00, 0.85, 0.25),
    "flower_red": (0.92, 0.26, 0.24),
    "coral": (0.98, 0.50, 0.44),
    "cloth_red": (0.90, 0.30, 0.28),
    "cloth_white": (0.97, 0.96, 0.92),
    "cloth_blue": (0.30, 0.55, 0.90),
    "gold": (0.95, 0.75, 0.25),
    "pastel_pink": (0.98, 0.76, 0.78),
    "pastel_blue": (0.70, 0.84, 0.96),
    "pastel_yellow": (0.99, 0.92, 0.66),
    "pastel_green": (0.74, 0.92, 0.74),
    "pastel_peach": (0.99, 0.82, 0.66),
    "pastel_lilac": (0.84, 0.78, 0.96),
    "train_red": (0.86, 0.24, 0.22),
    "train_cream": (0.98, 0.93, 0.78),
    "train_green": (0.20, 0.58, 0.44),
    "black": (0.12, 0.12, 0.13),
}
PASTELS = ["pastel_pink", "pastel_blue", "pastel_yellow", "pastel_green", "pastel_peach", "pastel_lilac"]

COLLECTIONS = ["01_TERRAIN", "02_OCEAN", "03_ROADS_PATHS", "04_RAILWAY", "05_TRAIN", "06_BUILDINGS",
               "07_HOUSING", "08_VEGETATION", "09_PROPS", "10_DOCKS", "11_SANCTUARY", "12_FARM",
               "13_FOREST", "14_FISHING", "15_LIGHTING", "16_EXPORT"]

# A MeshPart may hold 10,000 triangles; the bridge polices 8,000.
TRIANGLE_BUDGET = 7500

# Soft edges: boxes at least BEVEL_MIN_SIZE on every side get their edges
# chamfered by BEVEL studs, so nothing reads as a razor-sharp 90 degree block.
# Smaller boxes stay sharp -- a bevel on a fence rail is triangles nobody sees.
# Set per island with spec["bevel"]; 0 turns it off.
BEVEL = 0.0
BEVEL_MIN_SIZE = 2.5


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _smooth(edge0: float, edge1: float, x: float) -> float:
    if edge1 == edge0:
        return 1.0 if x >= edge1 else 0.0
    t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _angle_diff(a: float, b: float) -> float:
    return (a - b + math.pi) % (2.0 * math.pi) - math.pi


def material(name: str) -> bpy.types.Material:
    """A flat-coloured material, made once and reused by name."""
    found = bpy.data.materials.get(name)
    if found is not None:
        return found
    rgb = PALETTE.get(name, (0.8, 0.8, 0.8))
    made = bpy.data.materials.new(name)
    made.diffuse_color = (rgb[0], rgb[1], rgb[2], 1.0)
    made.roughness = 0.9
    tree = getattr(made, "node_tree", None)
    if tree is not None:
        shader = tree.nodes.get("Principled BSDF")
        if shader is not None:
            shader.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
            shader.inputs["Roughness"].default_value = 0.9
    return made


def collection(name: str) -> bpy.types.Collection:
    """The named collection under the scene, created if it is missing."""
    found = bpy.data.collections.get(name)
    if found is None:
        found = bpy.data.collections.new(name)
    scene_root = bpy.context.scene.collection
    if found.name not in scene_root.children:
        scene_root.children.link(found)
    return found


def ensure_collections(names: list[str] | None = None) -> None:
    for name in names or COLLECTIONS:
        collection(name)


def remove(name_prefix: str) -> int:
    """Delete every object whose name starts with this, so a pass can be rerun."""
    gone = 0
    for obj in list(bpy.data.objects):
        if obj.name.startswith(name_prefix):
            bpy.data.objects.remove(obj, do_unlink=True)
            gone += 1
    return gone


# --------------------------------------------------------------------------
# Part: one mesh under construction
# --------------------------------------------------------------------------
class Part:
    """Primitives added into one bmesh, each with its own colour.

    All sizes are in studs and all positions are LOCAL to the finished
    object. `finish` triangulates, flat shades and returns mesh data, which
    `place` or `obj` turns into objects.
    """

    def __init__(self) -> None:
        self.bm = bmesh.new()
        self.materials: list[str] = []

    def _index(self, colour: str) -> int:
        if colour not in self.materials:
            self.materials.append(colour)
        return self.materials.index(colour)

    def _paint(self, verts, colour: str) -> None:
        index = self._index(colour)
        for face in {f for v in verts for f in v.link_faces}:
            face.material_index = index

    @staticmethod
    def _matrix(x, y, z, rz=0.0, sx=1.0, sy=1.0, sz=1.0, rx=0.0, ry=0.0) -> Matrix:
        rot = Matrix.Rotation(rz, 4, "Z") @ Matrix.Rotation(ry, 4, "Y") @ Matrix.Rotation(rx, 4, "X")
        return Matrix.Translation((x, y, z)) @ rot @ Matrix.Diagonal((sx, sy, sz, 1.0))

    def box(self, x, y, z, sx, sy, sz, colour, rz=0.0, rx=0.0, ry=0.0):
        """A box centred at (x, y, z) measuring sx by sy by sz."""
        made = bmesh.ops.create_cube(self.bm, size=1.0,
                                     matrix=self._matrix(x, y, z, rz, sx, sy, sz, rx, ry))
        self._paint(made["verts"], colour)
        if BEVEL > 0 and min(sx, sy, sz) >= BEVEL_MIN_SIZE:
            edges = list({e for v in made["verts"] for e in v.link_edges})
            # material=-1: the chamfer takes the colour of the faces it joins.
            bmesh.ops.bevel(self.bm, geom=edges, offset=min(BEVEL, min(sx, sy, sz) * 0.2),
                            segments=1, affect="EDGES", clamp_overlap=True, material=-1)
        return made["verts"]

    def cylinder(self, x, y, z0, radius, height, colour, sides=8, top=None, rz=0.0, rx=0.0, ry=0.0):
        """A prism standing on z0 (a cone or frustum when `top` differs).

        With rx or ry it is tipped over about its own base centre, which is
        how wheels and horizontal barrels are made.
        """
        top = radius if top is None else top
        base = self._matrix(x, y, z0, rz, 1, 1, 1, rx, ry)
        made = bmesh.ops.create_cone(self.bm, cap_ends=True, cap_tris=False, segments=sides,
                                     radius1=radius, radius2=top, depth=height,
                                     matrix=base @ Matrix.Translation((0, 0, height / 2.0)))
        self._paint(made["verts"], colour)
        return made["verts"]

    def blob(self, x, y, z, radius, colour, squash=1.0, jitter=0.0, seed=0, stretch=1.0):
        """A chunky low-poly ball: foliage, rocks, bushes."""
        made = bmesh.ops.create_icosphere(self.bm, subdivisions=1, radius=radius,
                                          matrix=self._matrix(x, y, z, 0, stretch, 1.0, squash))
        if jitter:
            rng = random.Random(seed)
            for vert in made["verts"]:
                offset = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)))
                vert.co += offset * radius * jitter
        self._paint(made["verts"], colour)
        return made["verts"]

    def poly(self, points, colour, both_sides=False):
        """One face through the given points (a leaf, a gable, a sail)."""
        verts = [self.bm.verts.new(p) for p in points]
        faces = [self.bm.faces.new(verts)]
        if both_sides:
            back = [self.bm.verts.new(p) for p in reversed(points)]
            faces.append(self.bm.faces.new(back))
            verts += back
        index = self._index(colour)
        for face in faces:
            face.material_index = index
        return verts

    def loft(self, rings, colour, cap=True):
        """Bridge a list of closed rings (each a list of points, same count)."""
        made = [[self.bm.verts.new(p) for p in ring] for ring in rings]
        index = self._index(colour)
        count = len(rings[0])
        for a, b in zip(made, made[1:]):
            for i in range(count):
                j = (i + 1) % count
                self.bm.faces.new((a[i], a[j], b[j], b[i])).material_index = index
        if cap:
            self.bm.faces.new(list(reversed(made[0]))).material_index = index
            self.bm.faces.new(made[-1]).material_index = index
        return [v for ring in made for v in ring]

    def finish(self, name: str) -> bpy.types.Mesh:
        if not self.bm.is_valid:
            raise RuntimeError(f"this Part was already finished; make a new kit.Part() for {name!r}")
        bmesh.ops.triangulate(self.bm, faces=self.bm.faces[:])
        for face in self.bm.faces:
            face.smooth = False
        mesh = bpy.data.meshes.new(name)
        self.bm.to_mesh(mesh)
        self.bm.free()
        for colour in self.materials:
            mesh.materials.append(material(colour))
        return mesh


def obj(name: str, mesh: bpy.types.Mesh, coll: str, x=0.0, y=0.0, z=0.0, rz=0.0) -> bpy.types.Object:
    """An object for this mesh, in this collection, at this spot.

    An existing object of the same name is replaced, so a pass can be run
    again without leaving `.001` copies behind.
    """
    old = bpy.data.objects.get(name)
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    made = bpy.data.objects.new(name, mesh)
    made.location = (x, y, z)
    made.rotation_euler = (0.0, 0.0, rz)
    collection(coll).objects.link(made)
    return made


def triangles(mesh: bpy.types.Mesh) -> int:
    return len(mesh.polygons)


# --------------------------------------------------------------------------
# The island: height, coast and railway, all from one spec
# --------------------------------------------------------------------------
class Island:
    """Heights come from a function, so every pass agrees on the ground.

    The terrain mesh is built by sampling this, and every prop is placed on
    it, which is why nothing floats and nothing sinks: they read the same
    numbers. The railway is laid first and the ground is cut or banked along
    it, so the track neither floats nor buries itself.
    """

    def __init__(self, spec: dict) -> None:
        self.spec = spec
        self.rx = float(spec.get("radius_x", 245))
        self.ry = float(spec.get("radius_y", 215))
        self.beach = float(spec.get("beach_width", 14))
        self.beach_top = float(spec.get("beach_height", 1.2))
        self.land = float(spec.get("land_height", 4))
        self.seabed = float(spec.get("seabed", -12))
        self.skirt = float(spec.get("skirt", 45))
        # A superellipse when set: 2 is an oval, 4 a rounded square. A square
        # baseplate in the game wants a square-ish island, corners and all.
        self.power = float(spec.get("shape_power", 2.0))
        # How far inland the beach climbs to the land, and how much the land
        # rolls. A terrace look wants a short bank and almost no roll.
        self.bank = float(spec.get("bank_width", 30))
        self.roll = float(spec.get("roll", 1.0))
        self.sea_level = float(spec.get("sea_level", 0.0))
        self.bumps = [(math.radians(a), float(amount), math.radians(w))
                      for a, amount, w in spec.get("coast", [])]
        self.cliffs = [(math.radians(a), math.radians(w), float(h))
                       for a, w, h in spec.get("cliffs", [])]
        self.features = spec.get("features", [])
        self.islets = spec.get("islets", [])
        self.sandbars = spec.get("sandbars", [])
        self.rail_samples: list[tuple[float, float, float, float]] = []  # x, y, bed z, heading
        self.rail_raw: list[float] = []
        rail = spec.get("railway")
        if rail:
            self._lay_rail(rail)

    # ---- the coast
    def coast_radius(self, theta: float) -> float:
        c, s = math.cos(theta), math.sin(theta)
        n = self.power
        base = 1.0 / ((abs(c) / self.rx) ** n + (abs(s) / self.ry) ** n) ** (1.0 / n)
        f = 1.0 + 0.03 * math.sin(3 * theta + 1.1) + 0.02 * math.sin(7 * theta + 2.3)
        for angle, amount, width in self.bumps:
            f += amount * math.exp(-(_angle_diff(theta, angle) / width) ** 2)
        return base * f

    def _cliff(self, theta: float) -> tuple[float, float]:
        weight, rise = 0.0, 0.0
        for angle, width, height in self.cliffs:
            w = 1.0 - _smooth(width * 0.5, width, abs(_angle_diff(theta, angle)))
            if w > weight:
                weight, rise = w, height
        return weight, rise

    def inland(self, x: float, y: float) -> float:
        """Studs from the coast: positive on land, negative at sea."""
        return self.coast_radius(math.atan2(y, x)) - math.hypot(x, y)

    def _raw(self, x: float, y: float) -> float:
        e = self.inland(x, y)
        if e < 0:
            h = self.seabed * _smooth(0.0, self.skirt, -e)
            for ix, iy, r, top in self.islets:
                d = math.hypot(x - ix, y - iy)
                if d < r * 2.2:
                    h = max(h, top * (1 - (d / r) ** 2) if d < r else -(d - r) * 0.5)
            for bar in self.sandbars:
                # (x, y, half-length, half-width, turn, [top]): the top may sit
                # under the water, for a bar that only a low tide uncovers.
                bx, by, ax, ay, rot = bar[:5]
                top = float(bar[5]) if len(bar) > 5 else 0.7
                c, s = math.cos(-math.radians(rot)), math.sin(-math.radians(rot))
                lx, ly = (x - bx) * c - (y - by) * s, (x - bx) * s + (y - by) * c
                q = math.hypot(lx / ax, ly / ay)
                if q < 1.8:
                    # A gentle mound, its crest at `top`, falling away past its rim.
                    h = max(h, top - 0.5 * q * q if q < 1 else top - 0.5 - (q - 1) * 6.0)
            return h

        cliff, rise = self._cliff(math.atan2(y, x))
        width = _lerp(self.beach, 3.0, cliff)
        shore = _lerp(self.beach_top, self.land + rise, cliff)
        if e < width:
            h = shore * (e / width)
        else:
            h = shore + (self.land - shore) * _smooth(width, width + self.bank, e)
        inner = _smooth(width, width + self.bank * 0.7, e)
        h += inner * self.roll * (0.7 * math.sin(x * 0.045 + 1.3) * math.cos(y * 0.05 - 0.7)
                                  + 0.4 * math.sin(x * 0.11 + y * 0.07))
        for feature in self.features:
            h = self._feature(feature, x, y, h, inner)
        return h

    @staticmethod
    def _feature(f: dict, x: float, y: float, h: float, inner: float) -> float:
        kind = f.get("type", "level")
        if kind == "ramp":
            # A straight walkable slope from (x0, y0) at h0 to (x1, y1) at h1:
            # how a player gets from one terrace to the next.
            ax, ay, bx, by = float(f["x0"]), float(f["y0"]), float(f["x1"]), float(f["y1"])
            vx, vy = bx - ax, by - ay
            length2 = vx * vx + vy * vy
            t = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / length2))
            px, py = ax + vx * t, ay + vy * t
            side = math.hypot(x - px, y - py)
            half = float(f.get("width", 10)) / 2
            w = 1.0 - _smooth(half, half + float(f.get("edge", 1.5)), side)
            along = ((x - ax) * vx + (y - ay) * vy) / length2
            if w <= 0 or along < -0.02 or along > 1.02:
                return h
            return _lerp(h, _lerp(float(f["h0"]), float(f["h1"]), t), w)
        fx, fy = float(f["x"]), float(f["y"])
        if kind == "hill":
            d = math.hypot(x - fx, y - fy)
            r = float(f["radius"])
            if d < r:
                h += float(f["height"]) * (1 - (d / r) ** 2) ** 2 * inner
            return h
        # "level": hold the ground at a height, round or rectangular.
        edge = float(f.get("edge", 15))
        if "half" in f:
            hx, hy = f["half"]
            dx, dy = max(abs(x - fx) - hx, 0.0), max(abs(y - fy) - hy, 0.0)
            d = math.hypot(dx, dy)
            w = 1.0 - _smooth(0.0, edge, d)
        else:
            d = math.hypot(x - fx, y - fy)
            r = float(f["radius"])
            w = 1.0 - _smooth(r, r + edge, d)
        if w <= 0:
            return h
        # "hold": the height is the game's, not a suggestion -- a plot or a
        # terrace sits exactly there even out on the beach band.
        weight = w if f.get("hold") else w * max(inner, 0.35)
        return _lerp(h, float(f["height"]), weight)

    def height(self, x: float, y: float) -> float:
        """The ground at (x, y), after the railway has cut or banked it."""
        h = self._raw(x, y)
        if not self.rail_samples:
            return h
        near, bed = self._rail_near(x, y)
        if near is None:
            return h
        corridor = float(self.spec.get("railway", {}).get("corridor", 5))
        w = 1.0 - _smooth(corridor, corridor + 9, near)
        if w <= 0:
            return h
        target = bed - 0.35
        if h > target:
            return _lerp(h, target, w)  # a cutting
        if target - h < 3.0:
            return _lerp(h, target, w)  # a low bank; higher gaps get a trestle
        return h

    def _rail_near(self, x: float, y: float):
        best, bed = None, 0.0
        for rx, ry, rz, _heading in self.rail_samples[::2]:
            d = (rx - x) ** 2 + (ry - y) ** 2
            if best is None or d < best:
                best, bed = d, rz
        return (math.sqrt(best) if best is not None else None), bed

    def ground(self, x: float, y: float, radius: float = 0.0) -> float:
        """Where to stand something: the lowest ground under its footprint."""
        if radius <= 0:
            return self.height(x, y)
        return min(self.height(x + dx * radius, y + dy * radius)
                   for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)))

    # ---- the railway
    def _lay_rail(self, rail: dict) -> None:
        points = [Vector((float(p[0]), float(p[1]), 0.0)) for p in rail["points"]]
        n = len(points)
        fine: list[Vector] = []
        for i in range(n):
            p0, p1, p2, p3 = points[i - 1], points[i], points[(i + 1) % n], points[(i + 2) % n]
            for step in range(40):
                t = step / 40.0
                t2, t3 = t * t, t * t * t
                fine.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                                   + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
        spacing = float(rail.get("spacing", 2.0))
        even = [fine[0]]
        carried = 0.0
        for a, b in zip(fine, fine[1:] + fine[:1]):
            length = (b - a).length
            while carried + length >= spacing:
                t = (spacing - carried) / length
                a = a.lerp(b, t)
                length = (b - a).length
                even.append(a.copy())
                carried = 0.0
            carried += length
        if (even[-1] - even[0]).length < spacing * 0.5:
            even.pop()

        raw = [max(self._raw(p.x, p.y), float(rail.get("min_height", 2.0))) for p in even]
        self.rail_raw = [self._raw(p.x, p.y) for p in even]
        bed = raw[:]
        m = len(bed)
        window = int(rail.get("smooth", 12))
        for _ in range(4):
            bed = [sum(bed[(i + k) % m] for k in range(-window, window + 1)) / (2 * window + 1)
                   for i in range(m)]
        grade = float(rail.get("max_grade", 0.05)) * spacing
        for _ in range(6):
            for i in range(m):
                bed[i] = max(min(bed[i], bed[i - 1] + grade), bed[i - 1] - grade)
            for i in reversed(range(m)):
                bed[i] = max(min(bed[i], bed[(i + 1) % m] + grade), bed[(i + 1) % m] - grade)
        lift = float(rail.get("lift", 0.6))
        self.rail_samples = []
        for i, p in enumerate(even):
            q = even[(i + 1) % m]
            heading = math.atan2(q.y - p.y, q.x - p.x)
            self.rail_samples.append((p.x, p.y, bed[i] + lift, heading))

    def rail_point_near(self, x: float, y: float) -> tuple[float, float, float, float]:
        """The point of track nearest (x, y): (x, y, rail-top z, heading)."""
        return min(self.rail_samples, key=lambda s: (s[0] - x) ** 2 + (s[1] - y) ** 2)


ISLAND: Island | None = None


def colour_value(value) -> tuple[float, float, float]:
    """A palette entry: [r, g, b] in 0..1 linear, or "#RRGGBB" as a designer writes it.

    Hex is sRGB, which is what a colour picker shows, and Blender's material
    colours are linear, so the hex is converted -- otherwise "#50DCF0" comes
    out washed pale, which is how a tropical sea ends up looking like the
    North Sea.
    """
    if isinstance(value, str):
        text = value.lstrip("#")
        channels = [int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
        return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return tuple(float(c) for c in value)


def load_island(spec: dict) -> Island:
    global ISLAND
    global BEVEL
    for name, rgb in (spec.get("palette") or {}).items():
        PALETTE[name] = colour_value(rgb)
    BEVEL = float(spec.get("bevel", BEVEL))
    ISLAND = Island(spec)
    return ISLAND


def island() -> Island:
    if ISLAND is None:
        raise RuntimeError("no island is loaded; the bridge should have called load_island")
    return ISLAND


def ground(x: float, y: float, radius: float = 0.0) -> float:
    """Ground height at (x, y). Use this for EVERYTHING that stands on the island."""
    return island().ground(x, y, radius)


# --------------------------------------------------------------------------
# Terrain and water
# --------------------------------------------------------------------------
def build_terrain(prefix: str = "Terrain_MainIsland", spacing: float = 5.0,
                  max_tile: float = 0.0) -> list[str]:
    """The island as a height grid, split into tiles that each fit a MeshPart.

    `max_tile` caps a tile's width in studs. Roblox builds a mesh's collision
    from a limited number of convex pieces, so a 165-stud tile's collision
    bridged every dip and players floated over the low ground; tiles of about
    64 studs follow the surface closely.
    """
    isl = island()
    reach = max(isl.rx, isl.ry) * 1.3 + isl.skirt
    for ix, iy, r, _top in isl.islets:
        reach = max(reach, math.hypot(ix, iy) + r * 2.2)
    count = int(reach * 2 / spacing) + 1
    origin = -reach
    heights = [[isl.height(origin + i * spacing, origin + j * spacing) for i in range(count)]
               for j in range(count)]
    floor = isl.seabed + 0.05

    rng = random.Random(7)
    jitter = [[(rng.uniform(-0.3, 0.3) * spacing, rng.uniform(-0.3, 0.3) * spacing)
               for _ in range(count)] for _ in range(count)]

    def point(i, j):
        jx, jy = (jitter[j][i] if 0 < i < count - 1 and 0 < j < count - 1 else (0.0, 0.0))
        x, y = origin + i * spacing + jx, origin + j * spacing + jy
        return (x, y, isl.height(x, y) if (jx or jy) else heights[j][i])

    tris = []
    for j in range(count - 1):
        for i in range(count - 1):
            corners = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
            if all(heights[b][a] <= floor for a, b in corners):
                continue
            a, b, c, d = (point(*k) for k in corners)
            pair = ((a, b, c), (a, c, d)) if (i + j) % 2 == 0 else ((a, b, d), (b, c, d))
            tris.extend(pair)

    def colour(tri) -> str:
        zs = [p[2] for p in tri]
        normal = (Vector(tri[1]) - Vector(tri[0])).cross(Vector(tri[2]) - Vector(tri[0])).normalized()
        top = max(zs)
        if top < isl.sea_level - 0.6:
            return "seabed"
        if abs(normal.z) < 0.72:
            return "cliff"
        if top < isl.beach_top + 0.7:
            return "sand"
        return "grass"

    tiles = max(1, math.ceil(reach * 2 / max_tile)) if max_tile > 0 else 1
    while True:
        size = reach * 2 / tiles
        buckets: dict[tuple[int, int], list] = {}
        for tri in tris:
            cx = sum(p[0] for p in tri) / 3
            cy = sum(p[1] for p in tri) / 3
            key = (min(int((cx - origin) // size), tiles - 1), min(int((cy - origin) // size), tiles - 1))
            buckets.setdefault(key, []).append(tri)
        if max(len(v) for v in buckets.values()) <= TRIANGLE_BUDGET or tiles >= 16:
            break
        tiles += 1

    remove(prefix)
    made = []
    rows = "SN" if tiles == 2 else None
    for (tx, ty), group in sorted(buckets.items()):
        part = Part()
        for tri in group:
            part.poly(tri, colour(tri))
        if rows:
            name = f"{prefix}_{rows[ty]}{'WE'[tx]}"
        else:
            name = f"{prefix}_{ty:02d}_{tx:02d}"
        obj(name, part.finish(name), "01_TERRAIN")
        made.append(name)
    return made


def build_ocean(size: float = 1500.0) -> None:
    """Open water all round, a cyan shallows ring on the coast, a foam line."""
    isl = island()
    part = Part()
    h = size / 2
    sea = isl.sea_level
    part.poly([(-h, -h, sea), (h, -h, sea), (h, h, sea), (-h, h, sea)], "ocean")
    obj("Ocean_Main", part.finish("Ocean_Main"), "02_OCEAN")

    part = Part()
    part.poly([(-h, -h, -14), (h, -h, -14), (h, h, -14), (-h, h, -14)], "deep")
    obj("Ocean_Deep", part.finish("Ocean_Deep"), "02_OCEAN")

    def ring(name, inner, outer, z, colour, gaps=0):
        part = Part()
        steps = 120
        for k in range(steps):
            if gaps and (k // 3) % gaps == gaps - 1:
                continue
            a0, a1 = 2 * math.pi * k / steps, 2 * math.pi * (k + 1) / steps
            r0, r1 = isl.coast_radius(a0), isl.coast_radius(a1)
            pts = [((r0 + inner) * math.cos(a0), (r0 + inner) * math.sin(a0), z),
                   ((r0 + outer) * math.cos(a0), (r0 + outer) * math.sin(a0), z),
                   ((r1 + outer) * math.cos(a1), (r1 + outer) * math.sin(a1), z),
                   ((r1 + inner) * math.cos(a1), (r1 + inner) * math.sin(a1), z)]
            part.poly(pts, colour)
        obj(name, part.finish(name), "02_OCEAN")

    ring("Ocean_Shallows", -14.0, 38.0, sea + 0.2, "shallows")
    ring("Ocean_Foam", -3.0, 0.0, sea + 0.35, "foam", gaps=4)

    # See-through, as the game's water will be (Terrain water at 0.85
    # transparency): an opaque sea hid the sandbars at -3 and every reef, so
    # renders showed an empty ocean over things that were there. These
    # meshes are preview only and stay out of the Roblox export.
    for name, alpha in (("ocean", 0.55), ("shallows", 0.45)):
        mat = material(name)
        colour = list(mat.diffuse_color)
        colour[3] = alpha
        mat.diffuse_color = colour
        try:
            mat.surface_render_method = "BLENDED"
        except (AttributeError, TypeError):
            pass


# --------------------------------------------------------------------------
# Reusable props. Each returns MESH DATA, cached by name, for `place`.
# --------------------------------------------------------------------------
_MESHES: dict[str, bpy.types.Mesh] = {}


def _cached(name, build):
    mesh = _MESHES.get(name) or bpy.data.meshes.get(name)
    if mesh is None:
        mesh = build()
        mesh.name = name
    _MESHES[name] = mesh
    return mesh


def palm_mesh(variant: int = 0) -> bpy.types.Mesh:
    """A coconut palm, 14 to 20 studs, trunk leaning, drooping fronds."""
    def build():
        rng = random.Random(100 + variant)
        part = Part()
        height = 14 + variant * 2.5
        lean = rng.uniform(0.18, 0.32)
        segments = 6
        x = y = z = 0.0
        heading = rng.uniform(0, 2 * math.pi)
        for k in range(segments):
            seg = height / segments
            r = _lerp(0.95, 0.6, k / segments)
            part.cylinder(x, y, z, r, seg + 0.2, "palm_trunk", sides=6, top=r * 0.92)
            bend = lean * (k / segments) ** 1.5 * seg
            x += math.cos(heading) * bend
            y += math.sin(heading) * bend
            z += seg
        for n in range(7):
            a = n / 7 * 2 * math.pi + rng.uniform(-0.2, 0.2)
            ca, sa = math.cos(a), math.sin(a)
            length = rng.uniform(7.5, 9.5)
            pts = []
            for t, droop, wide in ((0.0, 0.3, 0.6), (0.45, 1.2, 1.6), (1.0, -2.8, 0.2)):
                cx, cy = x + ca * length * t, y + sa * length * t
                pts.append((cx - sa * wide, cy + ca * wide, z + droop))
                pts.append((cx + sa * wide, cy - ca * wide, z + droop))
            colour = "leaf" if n % 2 else "leaf_light"
            part.poly([pts[0], pts[1], pts[3], pts[2]], colour, both_sides=True)
            part.poly([pts[2], pts[3], pts[5], pts[4]], colour, both_sides=True)
        for n in range(3):
            a = n / 3 * 2 * math.pi
            part.blob(x + math.cos(a) * 0.8, y + math.sin(a) * 0.8, z - 0.6, 0.55, "coconut")
        return part.finish(f"Kit_Palm_{variant}")
    return _cached(f"Kit_Palm_{variant}", build)


def pine_mesh(variant: int = 0) -> bpy.types.Mesh:
    """A chunky stacked-cone conifer for the forest hill."""
    def build():
        part = Part()
        scale = 1.0 + 0.2 * variant
        part.cylinder(0, 0, 0, 0.9 * scale, 4 * scale, "trunk", sides=6)
        z = 3.0 * scale
        for tier, radius in enumerate((6.0, 4.8, 3.4)):
            part.cylinder(0, 0, z, radius * scale, 7 * scale, "pine" if tier % 2 == 0 else "leaf_dark",
                          sides=7, top=0.2)
            z += 4.2 * scale
        return part.finish(f"Kit_Pine_{variant}")
    return _cached(f"Kit_Pine_{variant}", build)


def round_tree_mesh(variant: int = 0) -> bpy.types.Mesh:
    """A broadleaf tree: short trunk, a cluster of chunky foliage balls."""
    def build():
        rng = random.Random(300 + variant)
        part = Part()
        scale = 1.0 + 0.18 * variant
        trunk = 6 * scale
        part.cylinder(0, 0, 0, 0.9 * scale, trunk, "trunk", sides=6, top=0.6 * scale)
        colours = ["leaf", "leaf_dark", "leaf_light"]
        for k in range(3 + variant % 2):
            a = rng.uniform(0, 2 * math.pi)
            off = 0 if k == 0 else rng.uniform(2.0, 3.2) * scale
            part.blob(math.cos(a) * off, math.sin(a) * off, trunk + rng.uniform(0.5, 3.0) * scale,
                      rng.uniform(3.6, 4.8) * scale, colours[k % 3], squash=0.85, jitter=0.12, seed=k + variant)
        return part.finish(f"Kit_RoundTree_{variant}")
    return _cached(f"Kit_RoundTree_{variant}", build)


def bush_mesh(variant: int = 0) -> bpy.types.Mesh:
    def build():
        rng = random.Random(400 + variant)
        part = Part()
        for k in range(3):
            a = k / 3 * 2 * math.pi
            part.blob(math.cos(a) * 1.3, math.sin(a) * 1.3, 1.2, rng.uniform(1.6, 2.2),
                      "leaf_light" if k % 2 else "leaf", squash=0.75, jitter=0.1, seed=k + variant)
        if variant % 2:
            for k in range(4):
                a = rng.uniform(0, 2 * math.pi)
                part.blob(math.cos(a) * 2.0, math.sin(a) * 2.0, 2.4, 0.45,
                          ("flower_pink", "flower_yellow", "flower_red")[k % 3])
        return part.finish(f"Kit_Bush_{variant}")
    return _cached(f"Kit_Bush_{variant}", build)


def rock_mesh(variant: int = 0, size: float = 3.0) -> bpy.types.Mesh:
    key = f"Kit_Rock_{variant}_{int(size)}"

    def build():
        part = Part()
        part.blob(0, 0, size * 0.35, size, "rock" if variant % 2 == 0 else "rock_dark",
                  squash=0.7, jitter=0.22, seed=variant * 13 + int(size), stretch=1.2)
        return part.finish(key)
    return _cached(key, build)


def flowers_mesh(variant: int = 0) -> bpy.types.Mesh:
    def build():
        rng = random.Random(500 + variant)
        part = Part()
        for k in range(7):
            x, y = rng.uniform(-2.5, 2.5), rng.uniform(-2.5, 2.5)
            part.cylinder(x, y, 0, 0.12, 1.1, "leaf_dark", sides=4)
            part.blob(x, y, 1.3, 0.45, ("flower_pink", "flower_yellow", "flower_red", "cloth_white")[k % 4])
        return part.finish(f"Kit_Flowers_{variant}")
    return _cached(f"Kit_Flowers_{variant}", build)


def fern_mesh() -> bpy.types.Mesh:
    def build():
        part = Part()
        for n in range(6):
            a = n / 6 * 2 * math.pi
            ca, sa = math.cos(a), math.sin(a)
            part.poly([(-sa * 0.4, ca * 0.4, 0.2), (ca * 3.2, sa * 3.2, 1.4), (sa * 0.4, -ca * 0.4, 0.2)],
                      "leaf_dark", both_sides=True)
        return part.finish("Kit_Fern")
    return _cached("Kit_Fern", build)


def mushroom_mesh() -> bpy.types.Mesh:
    def build():
        part = Part()
        for x, y, s in ((0, 0, 1.0), (1.2, 0.6, 0.7), (-0.9, 0.8, 0.55)):
            part.cylinder(x, y, 0, 0.35 * s, 1.4 * s, "cloth_white", sides=6)
            part.cylinder(x, y, 1.3 * s, 1.1 * s, 0.8 * s, "cloth_red", sides=7, top=0.15)
        return part.finish("Kit_Mushrooms")
    return _cached("Kit_Mushrooms", build)


def log_mesh() -> bpy.types.Mesh:
    def build():
        part = Part()
        part.cylinder(0, -4, 0.9, 0.9, 8, "trunk", sides=7, rx=-math.pi / 2)
        return part.finish("Kit_Log")
    return _cached("Kit_Log", build)


def crate_mesh() -> bpy.types.Mesh:
    def build():
        part = Part()
        part.box(0, 0, 1.5, 3, 3, 3, "wood_light")
        for z in (0.35, 2.65):
            part.box(0, 0, z, 3.1, 3.1, 0.4, "wood_dark")
        return part.finish("Kit_Crate")
    return _cached("Kit_Crate", build)


def barrel_mesh() -> bpy.types.Mesh:
    def build():
        part = Part()
        part.cylinder(0, 0, 0, 1.2, 3.0, "wood", sides=8)
        for z in (0.5, 2.3):
            part.cylinder(0, 0, z, 1.28, 0.25, "metal", sides=8)
        return part.finish("Kit_Barrel")
    return _cached("Kit_Barrel", build)


def lantern_mesh() -> bpy.types.Mesh:
    def build():
        part = Part()
        part.cylinder(0, 0, 0, 0.35, 8.5, "wood_dark", sides=6)
        part.box(0, 0, 9.2, 1.4, 1.4, 1.6, "lamp")
        part.cylinder(0, 0, 10.0, 1.1, 0.8, "wood_dark", sides=4, top=0.1, rz=math.pi / 4)
        return part.finish("Kit_Lantern")
    return _cached("Kit_Lantern", build)


def bench_mesh() -> bpy.types.Mesh:
    def build():
        part = Part()
        part.box(0, 0, 2.0, 6, 1.8, 0.4, "wood_light")
        part.box(0, 0.8, 3.1, 6, 0.3, 1.6, "wood_light")
        for x in (-2.5, 2.5):
            part.box(x, 0, 1.0, 0.4, 1.6, 2.0, "wood_dark")
        return part.finish("Kit_Bench")
    return _cached("Kit_Bench", build)


def sign_mesh() -> bpy.types.Mesh:
    def build():
        part = Part()
        part.cylinder(0, 0, 0, 0.3, 6, "wood_dark", sides=6)
        part.box(0, 0, 5.4, 4.5, 0.4, 1.8, "wood_light")
        return part.finish("Kit_Sign")
    return _cached("Kit_Sign", build)


def umbrella_mesh(variant: int = 0) -> bpy.types.Mesh:
    def build():
        part = Part()
        part.cylinder(0, 0, 0, 0.18, 7, "cloth_white", sides=5)
        colour = ("cloth_red", "cloth_blue", "flower_yellow")[variant % 3]
        part.cylinder(0, 0, 5.8, 4.2, 1.6, colour, sides=8, top=0.2)
        return part.finish(f"Kit_Umbrella_{variant}")
    return _cached(f"Kit_Umbrella_{variant}", build)


def beach_chair_mesh() -> bpy.types.Mesh:
    def build():
        part = Part()
        part.box(0, 0, 0.8, 2.2, 4.0, 0.3, "cloth_blue", rx=0.0)
        part.box(0, 2.2, 1.8, 2.2, 0.3, 2.2, "cloth_blue", rx=-0.35)
        for x in (-1.0, 1.0):
            part.box(x, 0.2, 0.4, 0.2, 4.4, 0.8, "wood_light")
        return part.finish("Kit_BeachChair")
    return _cached("Kit_BeachChair", build)


def chest_mesh() -> bpy.types.Mesh:
    def build():
        part = Part()
        part.box(0, 0, 0.9, 3, 2, 1.8, "wood")
        part.cylinder(-1.5, 0, 1.8, 1.0, 3, "wood_light", sides=6, ry=math.pi / 2)
        part.box(0, -1.05, 1.5, 0.6, 0.2, 0.8, "gold")
        return part.finish("Kit_Chest")
    return _cached("Kit_Chest", build)


def shell_mesh() -> bpy.types.Mesh:
    def build():
        part = Part()
        part.cylinder(0, 0, 0, 0.8, 0.5, "cloth_white", sides=5, top=0.2)
        return part.finish("Kit_Shell")
    return _cached("Kit_Shell", build)


def coral_mesh(variant: int = 0) -> bpy.types.Mesh:
    def build():
        rng = random.Random(600 + variant)
        part = Part()
        colour = ("coral", "flower_pink", "flower_yellow")[variant % 3]
        for k in range(5):
            a = rng.uniform(0, 2 * math.pi)
            r = rng.uniform(0, 1.6)
            part.cylinder(math.cos(a) * r, math.sin(a) * r, 0, 0.4, rng.uniform(1.5, 3.2), colour,
                          sides=5, top=0.15)
        part.blob(0, 0, 0.3, 1.4, "rock_dark", squash=0.5)
        return part.finish(f"Kit_Coral_{variant}")
    return _cached(f"Kit_Coral_{variant}", build)


def seaweed_mesh() -> bpy.types.Mesh:
    def build():
        part = Part()
        for k in range(4):
            a = k / 4 * 2 * math.pi
            x, y = math.cos(a) * 0.8, math.sin(a) * 0.8
            part.poly([(x - 0.3, y, 0), (x + 0.3, y, 0), (x + 0.5, y, 2.5), (x, y, 4.5)],
                      "leaf_dark", both_sides=True)
        return part.finish("Kit_Seaweed")
    return _cached("Kit_Seaweed", build)


def boat_mesh(length: float = 12.0, colour: str = "wood") -> bpy.types.Mesh:
    """A small rowing boat, bow toward +X, sitting on z = 0."""
    key = f"Kit_Boat_{int(length)}_{colour}"

    def build():
        part = Part()
        width, depth = length * 0.36, length * 0.16
        rings = []
        for t, w, lift in ((0.0, 0.25, 0.6), (0.2, 0.85, 0.15), (0.5, 1.0, 0.0), (0.8, 0.8, 0.15), (1.0, 0.1, 0.9)):
            x = (t - 0.5) * length
            half = width * w / 2
            rings.append([(x, -half, depth + lift * 0.4), (x, -half * 0.6, lift),
                          (x, half * 0.6, lift), (x, half, depth + lift * 0.4)])
        part.loft(rings, colour)
        part.box(0, 0, depth * 0.7, width * 0.5, width * 0.9, 0.3, "wood_light")
        part.box(-length * 0.25, 0, depth * 0.7, width * 0.5, width * 0.9, 0.3, "wood_light")
        return part.finish(key)
    return _cached(key, build)


def fence_mesh(length: float = 8.0, colour: str = "cloth_white") -> bpy.types.Mesh:
    """One fence run along +X from 0 to `length`."""
    key = f"Kit_Fence_{int(length)}_{colour}"

    def build():
        part = Part()
        posts = max(2, int(length // 2.5) + 1)
        for k in range(posts):
            x = k * length / (posts - 1)
            part.box(x, 0, 1.6, 0.4, 0.4, 3.2, colour)
        for z in (1.2, 2.6):
            part.box(length / 2, 0, z, length, 0.25, 0.4, colour)
        return part.finish(key)
    return _cached(key, build)


# --------------------------------------------------------------------------
# Placing things
# --------------------------------------------------------------------------
def place(mesh: bpy.types.Mesh, name: str, x: float, y: float, coll: str = "09_PROPS",
          rz: float = 0.0, z: float | None = None, sink: float = 0.2, footprint: float = 0.0):
    """Stand a shared mesh on the ground at (x, y); `z` overrides the ground."""
    at = (ground(x, y, footprint) - sink) if z is None else z
    return obj(name, mesh, coll, x, y, at, rz)


def reserved_hit(x: float, y: float, keep_out: list) -> bool:
    """Is (x, y) inside any keep-out: circles (x, y, r) or boxes (x0, y0, x1, y1)."""
    for zone in keep_out:
        if len(zone) == 3:
            if (x - zone[0]) ** 2 + (y - zone[1]) ** 2 < zone[2] ** 2:
                return True
        elif zone[0] <= x <= zone[2] and zone[1] <= y <= zone[3]:
            return True
    return False


def scatter(meshes: list, prefix: str, centre: tuple, radius: float, count: int,
            coll: str = "08_VEGETATION", seed: int = 1, keep_out: list | None = None,
            min_inland: float = 0.0, max_inland: float = 1e9, min_height: float = -1e9,
            max_height: float = 1e9, spacing: float = 6.0, avoid_rail: float = 7.0,
            avoid_paths: bool = True) -> list[str]:
    """Scatter shared meshes over a disc, keeping off paths, track and keep-outs.

    `min_inland`/`max_inland` bound the distance from the coast (a beach palm
    wants 2 to 25; a forest tree wants 30 and up). Returns the names placed.
    """
    isl = island()
    rng = random.Random(seed)
    keep_out = list(keep_out or []) + (list(_PATH_ZONES) if avoid_paths else [])
    remove(prefix)  # a rerun replaces the scatter rather than adding to it
    taken: list[tuple[float, float]] = []
    placed: list[str] = []
    tries = 0
    while len(placed) < count and tries < count * 40:
        tries += 1
        a = rng.uniform(0, 2 * math.pi)
        r = radius * math.sqrt(rng.random())
        x, y = centre[0] + math.cos(a) * r, centre[1] + math.sin(a) * r
        e = isl.inland(x, y)
        if not (min_inland <= e <= max_inland):
            continue
        h = isl.height(x, y)
        if not (min_height <= h <= max_height):
            continue
        if reserved_hit(x, y, keep_out):
            continue
        if avoid_rail and isl.rail_samples:
            near, _bed = isl._rail_near(x, y)
            if near is not None and near < avoid_rail:
                continue
        if any((x - tx) ** 2 + (y - ty) ** 2 < spacing ** 2 for tx, ty in taken):
            continue
        taken.append((x, y))
        name = f"{prefix}_{len(placed) + 1:03d}"
        place(meshes[rng.randrange(len(meshes))], name, x, y, coll, rz=rng.uniform(0, 2 * math.pi),
              sink=0.4)
        placed.append(name)
    return placed


# Paths register themselves here so scatter keeps trees off them.
_PATH_ZONES: list = []


def path(name: str, points: list, width: float = 10.0, colour: str = "stone_path",
         coll: str = "03_ROADS_PATHS", lift: float = 0.25, step: float = 3.0) -> str:
    """A walkable strip over the ground through `points`, `width` studs wide."""
    samples: list[Vector] = []
    for a, b in zip(points, points[1:]):
        a, b = Vector((a[0], a[1], 0)), Vector((b[0], b[1], 0))
        n = max(1, int((b - a).length / step))
        samples += [a.lerp(b, k / n) for k in range(n)]
    samples.append(Vector((points[-1][0], points[-1][1], 0)))
    rings = []
    for k, p in enumerate(samples):
        q = samples[min(k + 1, len(samples) - 1)] - samples[max(k - 1, 0)]
        side = Vector((-q.y, q.x, 0)).normalized() * (width / 2)
        left, right = p + side, p - side
        rings.append(((left.x, left.y, ground(left.x, left.y) + lift),
                      (right.x, right.y, ground(right.x, right.y) + lift)))
        _PATH_ZONES.append((p.x, p.y, width / 2 + 2))
    part = Part()
    for (l0, r0), (l1, r1) in zip(rings, rings[1:]):
        part.poly([l0, r0, r1, l1], colour)
    # A thin edge so the strip reads as built rather than painted on.
    obj(name, part.finish(name), coll)
    return name


# --------------------------------------------------------------------------
# Bigger builds
# --------------------------------------------------------------------------
def pier(prefix: str, start: tuple, end: tuple, width: float = 16.0, deck_z: float = 3.0,
         coll: str = "10_DOCKS", railings: bool = True) -> dict:
    """A plank pier from `start` (on the beach) to `end` (out at sea).

    Makes `<prefix>_Deck`, `<prefix>_Posts` and, if asked, `<prefix>_Railing`.
    Returns the deck's centre line, so props can be put on it.
    """
    a, b = Vector((start[0], start[1], 0)), Vector((end[0], end[1], 0))
    length = (b - a).length
    heading = math.atan2(b.y - a.y, b.x - a.x)
    mid = (a + b) / 2

    deck = Part()
    planks = int(length / 1.8)
    for k in range(planks):
        x = -length / 2 + (k + 0.5) * length / planks
        deck.box(x, 0, 0, length / planks - 0.25, width, 0.6, "wood_light" if k % 3 else "wood")
    obj(f"{prefix}_Deck", deck.finish(f"{prefix}_Deck"), coll, mid.x, mid.y, deck_z - 0.3, heading)

    posts = Part()
    for k in range(int(length // 8) + 1):
        x = -length / 2 + k * 8
        for y in (-width / 2 + 0.6, width / 2 - 0.6):
            posts.cylinder(x, y, -14, 0.6, 14 + deck_z + (1.4 if railings else 0.0), "wood_dark", sides=6)
    obj(f"{prefix}_Posts", posts.finish(f"{prefix}_Posts"), coll, mid.x, mid.y, 0.0, heading)

    if railings:
        rail = Part()
        for y in (-width / 2 + 0.6, width / 2 - 0.6):
            rail.box(0, y, deck_z + 1.2, length, 0.35, 0.35, "wood")
        obj(f"{prefix}_Railing", rail.finish(f"{prefix}_Railing"), coll, mid.x, mid.y, 0.0, heading)
    return {"start": tuple(a), "end": tuple(b), "heading": heading, "deck_z": deck_z, "length": length}


def house(prefix: str, x: float, y: float, rz: float = 0.0, width: float = 16.0, depth: float = 12.0,
          wall_colour: str = "pastel_pink", roof_colour: str = "roof_red", coll: str = "07_HOUSING") -> list[str]:
    """A modular starter house: every part its own object, all named `<prefix>_<Part>`.

    The front (door, porch) faces local -Y; turn the whole house with `rz`.
    Walls 10 tall, door 4 x 7, windows at eye height.
    """
    z0 = ground(x, y, max(width, depth) / 2) - 0.3
    names = []

    def put(part_name, part):
        name = f"{prefix}_{part_name}"
        obj(name, part.finish(name), coll, x, y, z0, rz)
        names.append(name)

    walls = Part()
    walls.box(0, 0, 0.6, width + 2, depth + 2, 1.2, "white_stone")
    walls.box(0, 0, 6.2, width, depth, 10, wall_colour)
    put("Walls", walls)

    roof = Part()
    over = 1.2
    ridge = 16.5
    hw, hd = width / 2 + over, depth / 2 + over
    roof.poly([(-hw, -hd, 11.0), (hw, -hd, 11.0), (hw, 0, ridge), (-hw, 0, ridge)], roof_colour, both_sides=True)
    roof.poly([(-hw, hd, 11.0), (-hw, 0, ridge), (hw, 0, ridge), (hw, hd, 11.0)], roof_colour, both_sides=True)
    for sx in (-width / 2, width / 2):
        roof.poly([(sx, -depth / 2, 11.2), (sx, depth / 2, 11.2), (sx, 0, ridge - 0.4)], wall_colour, both_sides=True)
    roof.box(width * 0.25, depth * 0.2, ridge - 0.5, 1.6, 1.6, 4, "white_stone")
    put("Roof", roof)

    door = Part()
    door.box(0, -depth / 2 - 0.15, 4.7, 4, 0.4, 7, "wood")
    door.blob(1.2, -depth / 2 - 0.45, 4.6, 0.25, "gold")
    put("Door", door)

    windows = Part()
    for wx in (-width / 2 + 3.2, width / 2 - 3.2):
        windows.box(wx, -depth / 2 - 0.12, 6.5, 2.8, 0.3, 2.8, "glass")
        windows.box(wx, -depth / 2 - 0.2, 5.0, 3.4, 0.5, 0.4, "cloth_white")
    for wy in (-1.5, 2.5):
        windows.box(width / 2 + 0.12, wy, 6.5, 0.3, 2.6, 2.6, "glass")
        windows.box(-width / 2 - 0.12, wy, 6.5, 0.3, 2.6, 2.6, "glass")
    put("Windows", windows)

    porch = Part()
    porch.box(0, -depth / 2 - 3, 1.0, 8, 5, 0.6, "wood_light")
    for px in (-3.6, 3.6):
        porch.box(px, -depth / 2 - 5.2, 4.5, 0.5, 0.5, 7, "wood")
    porch.box(0, -depth / 2 - 3, 8.3, 9, 6, 0.5, roof_colour)
    put("Porch", porch)
    return names


def plot(prefix: str, house_prefix: str | None, x: float, y: float, size: float = 36.0, rz: float = 0.0,
         wall_colour: str = "pastel_pink", seed: int = 0, coll: str = "07_HOUSING",
         fence_colour: str = "wood_light") -> list[str]:
    """A player plot: ground pad, a low fence with a gate, mailbox, garden, path.

    With `house_prefix` a starter house goes at the back half (local +Y);
    with None the plot is left empty for the player to build on. The gate
    faces local -Y. Names: `<prefix>_Ground`, `_Fence`, `_Mailbox`, `_Garden`,
    `_Path`, and the house's parts under `house_prefix`.
    """
    c, s = math.cos(rz), math.sin(rz)

    def world(lx, ly):
        return x + lx * c - ly * s, y + lx * s + ly * c

    z0 = ground(x, y, size / 2)
    half = size / 2
    names = []

    pad = Part()
    pad.box(0, 0, 0.3, size, size, 0.8, "grass")
    obj(f"{prefix}_Ground", pad.finish(f"{prefix}_Ground"), coll, x, y, z0 - 0.2, rz)
    names.append(f"{prefix}_Ground")

    fence = Part()
    for side in range(4):
        for k in range(int(size // 3)):
            t = -half + k * 3 + 1.5
            if side == 0 and abs(t) < 3.5:
                continue  # the gate, facing the street
            lx, ly = [(t, -half), (half, t), (t, half), (-half, t)][side]
            fence.box(lx, ly, 1.3, 0.5, 0.5, 2.6, fence_colour)
            fence.box(lx, ly, 2.7, 0.7, 0.7, 0.3, fence_colour)
        for z in (0.9, 2.0):
            if side == 0:
                for sign in (-1, 1):
                    fence.box(sign * (half + 3.5) / 2, -half, z, half - 3.5, 0.3, 0.35, fence_colour)
            else:
                lx, ly, sx, sy = [(0, 0, 0, 0), (half, 0, 0.3, size), (0, half, size, 0.3), (-half, 0, 0.3, size)][side]
                fence.box(lx, ly, z, sx, sy, 0.35, fence_colour)
    obj(f"{prefix}_Fence", fence.finish(f"{prefix}_Fence"), coll, x, y, z0, rz)
    names.append(f"{prefix}_Fence")

    mailbox = Part()
    mailbox.box(-5, -half + 1.2, 1.8, 0.4, 0.4, 3.6, "wood_dark")
    mailbox.box(-5, -half + 1.2, 4.0, 1.4, 2.0, 1.2, PASTELS[(seed + 2) % len(PASTELS)])
    obj(f"{prefix}_Mailbox", mailbox.finish(f"{prefix}_Mailbox"), coll, x, y, z0, rz)
    names.append(f"{prefix}_Mailbox")

    garden = Part()
    rng = random.Random(seed)
    for k in range(5):
        gx, gy = half - 5, -half + 4 + k * 3.2
        garden.box(gx, gy, 0.9, 3.5, 2.8, 0.8, "dirt")
        garden.blob(gx, gy, 1.9, 1.0, ("flower_pink", "flower_yellow", "flower_red", "leaf_light")[rng.randrange(4)])
    obj(f"{prefix}_Garden", garden.finish(f"{prefix}_Garden"), coll, x, y, z0, rz)
    names.append(f"{prefix}_Garden")

    walk = Part()
    walk.box(0, -half / 2 + 2, 0.85, 4, half, 0.3, "stone_path")
    obj(f"{prefix}_Path", walk.finish(f"{prefix}_Path"), coll, x, y, z0, rz)
    names.append(f"{prefix}_Path")

    if house_prefix:
        hx, hy = world(0, half / 2 - 2)
        names += house(house_prefix, hx, hy, rz, wall_colour=wall_colour, coll=coll)
    _PATH_ZONES.append((x, y, half * 1.42 + 3))
    return names


def pavilion(prefix: str, x: float, y: float, size: float = 48.0, top: float = 7.0, pillar_offset: float = 18.0,
             pillar_height: float = 12.0, pedestal: float = 8.0, coll: str = "06_BUILDINGS") -> list[str]:
    """An open pavilion at the game's own measurements.

    The foundation's top is at `top`, pillars stand at (+-pillar_offset,
    +-pillar_offset) and carry a teal hipped roof, and a pedestal sits in the
    middle for the totem. Makes `<prefix>_Foundation`, `_Pillars`, `_Roof`,
    `_Pedestal`, `_Steps`.
    """
    base = ground(x, y)
    names = []

    found = Part()
    found.box(0, 0, (top + base - 1) / 2 - base, size, size, top - base + 1, "white_stone")
    found.box(0, 0, top - base + 0.05, size - 4, size - 4, 0.1, "stone_path")
    obj(f"{prefix}_Foundation", found.finish(f"{prefix}_Foundation"), coll, x, y, base)
    names.append(f"{prefix}_Foundation")

    steps = Part()
    rise = top - base
    count = max(1, int(math.ceil(rise / 0.8)))
    for side in range(4):
        a = side * math.pi / 2
        for k in range(count):
            depth = size / 2 + 1.2 + k * 1.2
            h = rise - (k + 1) * rise / count
            steps.box(math.cos(a) * depth, math.sin(a) * depth, (h - 1) / 2, 1.2 if side % 2 == 0 else 14,
                      14 if side % 2 == 0 else 1.2, h + 1, "white_stone")
    obj(f"{prefix}_Steps", steps.finish(f"{prefix}_Steps"), coll, x, y, base)
    names.append(f"{prefix}_Steps")

    pillars = Part()
    for px in (-pillar_offset, pillar_offset):
        for py in (-pillar_offset, pillar_offset):
            pillars.box(px, py, top + 0.5, 4, 4, 1, "white_stone")
            pillars.cylinder(px, py, top + 1, 1.5, pillar_height - 2, "white_stone", sides=8)
            pillars.box(px, py, top + pillar_height - 0.5, 4, 4, 1, "white_stone")
    obj(f"{prefix}_Pillars", pillars.finish(f"{prefix}_Pillars"), coll, x, y, 0.0)
    names.append(f"{prefix}_Pillars")

    roof = Part()
    eave = top + pillar_height
    span = pillar_offset + 5
    roof.box(0, 0, eave + 0.6, span * 2, span * 2, 1.2, "white_stone")
    roof.cylinder(0, 0, eave + 1.2, span * 1.35, 9, "teal_roof", sides=4, top=2.0, rz=math.pi / 4)
    roof.cylinder(0, 0, eave + 10.2, 1.2, 3, "gold", sides=6, top=0.2)
    obj(f"{prefix}_Roof", roof.finish(f"{prefix}_Roof"), coll, x, y, 0.0)
    names.append(f"{prefix}_Roof")

    ped = Part()
    ped.box(0, 0, top + 2, pedestal, pedestal, 4, "white_stone")
    ped.box(0, 0, top + 4.1, pedestal + 0.6, pedestal + 0.6, 0.4, "gold")
    obj(f"{prefix}_Pedestal", ped.finish(f"{prefix}_Pedestal"), coll, x, y, 0.0)
    names.append(f"{prefix}_Pedestal")
    _PATH_ZONES.append((x, y, size * 0.72 + 4))
    return names


def museum(prefix: str, x: float, y: float, rz: float = 0.0, width: float = 36.0, depth: float = 24.0,
           wall: float = 14.0, coll: str = "11_SANCTUARY", dome_colour: str = "teal_roof",
           wings: bool = False, scale: float = 1.0) -> list[str]:
    """The museum: a white hall with a teal dome and a grand double-door entrance facing local -Y.

    Makes `<prefix>` (the hall), `_Dome`, `_Doors`, `_Columns`, `_Steps`.
    """
    base = ground(x, y, max(width, depth) / 2)
    names = []

    def put(name, part):
        obj(name, part.finish(name), coll, x, y, base, rz)
        names.append(name)

    hall = Part()
    hall.box(0, 0, 0.5, width + 4, depth + 4, 2, "white_stone")
    hall.box(0, 0, 1.5 + wall / 2, width, depth, wall, "white_stone")
    hall.box(0, 0, 1.5 + wall + 0.6, width + 2, depth + 2, 1.2, "cloth_white")
    for wx in (-width / 2 + 5, width / 2 - 5):
        hall.box(wx, -depth / 2 - 0.12, 8, 3.2, 0.3, 6, "glass")
    hall.box(0, -depth / 2 - 0.3, 1.5 + wall - 1.5, 16, 0.6, 3, "teal_roof")
    put(prefix, hall)

    dome = Part()
    rings = []
    radius = min(width, depth) * 0.42
    for k in range(6):
        a = k / 5 * (math.pi / 2)
        r = radius * math.cos(a) + 0.1
        z = 2.7 + wall + radius * 0.95 * math.sin(a)
        rings.append([(math.cos(i / 12 * 2 * math.pi) * r, math.sin(i / 12 * 2 * math.pi) * r, z) for i in range(12)])
    dome.cylinder(0, 0, 2.5 + wall, radius + 1.2, 2.5, "white_stone", sides=12)
    dome.loft(rings, dome_colour)
    dome.cylinder(0, 0, 2.7 + wall + radius * 0.95, 1.4, 4, "gold", sides=6, top=0.2)
    put(f"{prefix}_Dome", dome)

    if wings:
        # Two conservatories, one each side: a low marble drum under a glass
        # dome -- the aquarium on one side, the terrarium on the other.
        conserv = Part()
        for side in (-1, 1):
            cx = side * (width / 2 + 9)
            conserv.cylinder(cx, 2, 0, 10, 1.5, "white_stone", sides=12)
            conserv.cylinder(cx, 2, 1.5, 8.5, 7, "white_stone", sides=12)
            ring_set = []
            for k in range(5):
                a = k / 4 * (math.pi / 2)
                r = 8.8 * math.cos(a) + 0.1
                z = 8.5 + 8.0 * math.sin(a)
                ring_set.append([(cx + math.cos(i / 12 * 2 * math.pi) * r, 2 + math.sin(i / 12 * 2 * math.pi) * r, z)
                                 for i in range(12)])
            conserv.loft(ring_set, dome_colour)
            conserv.box(side * (width / 2 + 2), 2, 5, 6, 8, 7, "white_stone")
        put(f"{prefix}_Conservatories", conserv)

    doors = Part()
    doors.box(0, -depth / 2 - 0.5, 1.5 + 5.5, 11, 1.2, 11, "wood_dark")
    for side in (-1, 1):
        doors.box(side * 2.35, -depth / 2 - 0.9, 1.5 + 4.5, 4.5, 0.4, 9, "wood")
        doors.blob(side * 0.7, -depth / 2 - 1.2, 1.5 + 4.5, 0.35, "gold")
    put(f"{prefix}_Doors", doors)

    cols = Part()
    for k in (-3, -2, 2, 3):
        cols.cylinder(k * 3.6, -depth / 2 - 5, 1.5, 1.0, wall - 1, "white_stone", sides=8)
    cols.box(0, -depth / 2 - 5, 1.5 + wall, 26, 3, 1.4, "white_stone")
    cols.poly([(-13, -depth / 2 - 6.6, 2.2 + wall), (13, -depth / 2 - 6.6, 2.2 + wall), (0, -depth / 2 - 6.6, 7 + wall)],
              "white_stone", both_sides=True)
    put(f"{prefix}_Columns", cols)

    steps = Part()
    for k in range(3):
        steps.box(0, -depth / 2 - 4 - k * 1.6, 1.0 - k * 0.5, 22 - k * 0 , 2 + k * 1.6, 1.0, "white_stone")
    put(f"{prefix}_Steps", steps)
    if scale != 1.0:
        # Grown as a whole about its own footprint, so a grander hall keeps
        # every proportion -- the doors grow with the walls.
        for name in names:
            bpy.data.objects[name].data.transform(Matrix.Scale(scale, 4))
    _PATH_ZONES.append((x, y, max(width, depth) * 0.72 * scale + 4))
    return names


def stairs(name: str, start: tuple, end: tuple, width: float = 10.0, z0: float | None = None,
           z1: float | None = None, colour: str = "stone_path", coll: str = "03_ROADS_PATHS") -> str:
    """Stone steps from `start` to `end`, climbing from the ground (or z0) to z1.

    Laid over a ramp, they turn a slope into a stair a player reads at a glance.
    """
    a, b = Vector((start[0], start[1], 0)), Vector((end[0], end[1], 0))
    za = ground(a.x, a.y) if z0 is None else z0
    zb = ground(b.x, b.y) if z1 is None else z1
    length = (b - a).length
    heading = math.atan2(b.y - a.y, b.x - a.x)
    count = max(2, int(math.ceil(abs(zb - za) / 0.8)))
    part = Part()
    for k in range(count):
        t = (k + 0.5) / count
        z = _lerp(za, zb, (k + 1) / count)
        part.box(-length / 2 + t * length, 0, (z + za) / 2 - 0.5, length / count + 0.05, width,
                 z - za + 1.0, colour if k % 2 == 0 else "white_stone")
    for side in (-width / 2 - 0.6, width / 2 + 0.6):
        part.poly([(-length / 2, side, za), (length / 2, side, zb + 1.2), (length / 2, side, zb - 0.5),
                   (-length / 2, side, za - 0.5)], "white_stone", both_sides=True)
    mid = (a + b) / 2
    obj(name, part.finish(name), coll, mid.x, mid.y, 0.0, heading)
    for k in range(5):
        p = a.lerp(b, k / 4)
        _PATH_ZONES.append((p.x, p.y, width / 2 + 2))
    return name


def domed_hall(prefix: str, x: float, y: float, rz: float = 0.0, radius: float = 22.0, wall: float = 16.0,
               coll: str = "11_SANCTUARY") -> list[str]:
    """A round white hall with an entrance gap facing local -Y, a teal dome,
    a colonnade, steps and two small side pavilions. Walkable inside.

    Makes `<prefix>` (the hall), `<prefix>_Dome`, `<prefix>_Columns`,
    `<prefix>_Stairs`, `<prefix>_Pavilions`.
    """
    base = ground(x, y, radius)
    names = []

    hall = Part()
    hall.cylinder(0, 0, -2, radius + 6, 3, "white_stone", sides=16)
    segments = 20
    for k in range(segments):
        a = (k + 0.5) / segments * 2 * math.pi - math.pi / 2
        if abs(_angle_diff(a, -math.pi / 2)) < 0.42:
            continue  # the entrance
        hall.box(math.cos(a) * radius, math.sin(a) * radius, 1 + wall / 2,
                 2 * math.pi * radius / segments + 0.4, 2.0, wall, "white_stone", rz=a + math.pi / 2)
    hall.cylinder(0, 0, 1 + wall, radius + 1.5, 2, "white_stone", sides=16)
    hall.box(0, -radius, 1 + wall - 2.5, 12, 2.4, 5, "white_stone")
    obj(prefix, hall.finish(prefix), coll, x, y, base, rz)
    names.append(prefix)

    dome = Part()
    rings = []
    for k in range(6):
        t = k / 5 * (math.pi / 2)
        r = radius * math.cos(t) + 0.1
        z = 3 + wall + radius * 0.95 * math.sin(t)
        rings.append([(math.cos(a / 12 * 2 * math.pi) * r, math.sin(a / 12 * 2 * math.pi) * r, z) for a in range(12)])
    dome.loft(rings, "teal_roof")
    dome.cylinder(0, 0, 3 + wall + radius * 0.95, 1.6, 4, "gold", sides=6, top=0.2)
    obj(f"{prefix}_Dome", dome.finish(f"{prefix}_Dome"), coll, x, y, base, rz)
    names.append(f"{prefix}_Dome")

    columns = Part()
    for k in range(-3, 4):
        columns.cylinder(k * 3.2, -radius - 4.5, 1, 0.9, wall - 1, "white_stone", sides=8)
    columns.box(0, -radius - 4.5, wall + 0.8, 23, 4, 1.6, "white_stone")
    columns.poly([(-11.5, -radius - 6.6, wall + 1.6), (11.5, -radius - 6.6, wall + 1.6), (0, -radius - 6.6, wall + 7)],
                 "white_stone", both_sides=True)
    obj(f"{prefix}_Columns", columns.finish(f"{prefix}_Columns"), coll, x, y, base, rz)
    names.append(f"{prefix}_Columns")

    stairs = Part()
    for k in range(6):
        stairs.box(0, -radius - 8 - k * 1.8, 0.9 - k * 0.8, 16, 1.8 + k * 0.1, 1.6 + k * 0.8, "white_stone")
    obj(f"{prefix}_Stairs", stairs.finish(f"{prefix}_Stairs"), coll, x, y, base, rz)
    names.append(f"{prefix}_Stairs")

    wings = Part()
    for side in (-1, 1):
        px = side * (radius + 12)
        wings.cylinder(px, 4, -1, 8, 2, "white_stone", sides=10)
        for k in range(6):
            a = k / 6 * 2 * math.pi
            wings.cylinder(px + math.cos(a) * 6, 4 + math.sin(a) * 6, 1, 0.6, 9, "white_stone", sides=6)
        wings.cylinder(px, 4, 10, 7.5, 1, "white_stone", sides=10)
        wings.cylinder(px, 4, 11, 7, 5, "blue_roof", sides=10, top=1.0)
    obj(f"{prefix}_Pavilions", wings.finish(f"{prefix}_Pavilions"), coll, x, y, base, rz)
    names.append(f"{prefix}_Pavilions")
    _PATH_ZONES.append((x, y, radius + 22))
    return names


def fountain(name: str, x: float, y: float, radius: float = 5.0, coll: str = "09_PROPS") -> str:
    part = Part()
    part.cylinder(0, 0, 0, radius, 1.6, "white_stone", sides=12)
    part.cylinder(0, 0, 1.3, radius - 0.8, 0.4, "shallows", sides=12)
    part.cylinder(0, 0, 0, 1.0, 5, "white_stone", sides=8)
    part.cylinder(0, 0, 4.2, 2.4, 0.8, "white_stone", sides=10)
    part.blob(0, 0, 5.6, 0.9, "shallows")
    obj(name, part.finish(name), coll, x, y, ground(x, y, radius) - 0.2)
    return name


def statue(name: str, x: float, y: float, rz: float = 0.0, coll: str = "09_PROPS") -> str:
    part = Part()
    part.box(0, 0, 1.5, 4, 4, 3, "white_stone")
    part.cylinder(0, 0, 3, 1.1, 5, "white_stone", sides=8, top=0.8)
    part.blob(0, 0, 8.8, 1.4, "white_stone")
    part.box(1.5, 0, 6.8, 2.4, 0.8, 0.8, "white_stone", ry=-0.6)
    obj(name, part.finish(name), coll, x, y, ground(x, y, 2) - 0.2, rz)
    return name


def totem_bell(name: str, x: float, y: float, coll: str = "06_BUILDINGS", z: float | None = None) -> str:
    """The Town Square's landmark: a chunky carved totem, ~20 tall, bell on top.

    Stands on the ground, or at `z` when given (on a pedestal, say).
    """
    # Read before the stacking loop below; the loop keeps its own height.
    stand = (ground(x, y, 5) - 0.3) if z is None else z
    part = Part()
    part.cylinder(0, 0, 0, 6, 1.2, "stone_path", sides=10)
    part.cylinder(0, 0, 1.2, 4.5, 1.0, "white_stone", sides=10)
    colours = ["wood", "teal_roof", "wood_light", "roof_red"]
    level = 2.2
    for k, colour in enumerate(colours):
        part.box(0, 0, level + 1.8, 3.6, 3.6, 3.6, colour, rz=0.0 if k % 2 == 0 else math.pi / 4)
        part.box(0, -1.9, level + 2.2, 2.4, 0.3, 0.7, "black")
        part.blob(-0.8, -1.9, level + 2.9, 0.35, "cloth_white")
        part.blob(0.8, -1.9, level + 2.9, 0.35, "cloth_white")
        level += 3.6
    part.box(0, 0, level + 0.3, 7, 1.0, 0.6, "wood_dark")
    for sx in (-3.2, 3.2):
        part.box(sx, 0, level + 3.2, 0.7, 0.7, 5.8, "wood_dark")
    part.box(0, 0, level + 6.3, 8, 1.4, 0.8, "roof_red")
    part.cylinder(0, 0, level + 2.6, 1.9, 3.0, "gold", sides=8, top=0.8)
    part.blob(0, 0, level + 2.4, 0.5, "metal")
    obj(name, part.finish(name), coll, x, y, stand)
    return name


def plaza(name: str, x: float, y: float, radius: float = 40.0, coll: str = "06_BUILDINGS") -> str:
    """A round paved plaza with a darker inner ring, laid over the ground."""
    part = Part()
    part.cylinder(0, 0, -1.5, radius, 2.0, "stone_path", sides=32)
    part.cylinder(0, 0, 0.45, radius * 0.45, 0.2, "white_stone", sides=24)
    part.cylinder(0, 0, 0.45, radius + 1.2, 0.2, "white_stone", sides=32, top=radius + 1.2)
    obj(name, part.finish(name), coll, x, y, ground(x, y) + 0.1)
    _PATH_ZONES.append((x, y, radius + 3))
    return name


def stall(name: str, x: float, y: float, rz: float = 0.0, colour: str = "cloth_red",
          coll: str = "09_PROPS") -> str:
    part = Part()
    part.box(0, 0, 1.6, 7, 3.5, 3.2, "wood")
    part.box(0, 0, 3.3, 7.4, 3.8, 0.3, "wood_light")
    for sx in (-3.3, 3.3):
        for sy in (-1.6, 1.6):
            part.box(sx, sy, 4.5, 0.4, 0.4, 9, "wood_dark")
    for k in range(6):
        part.box(-3.5 + k * 1.4 + 0.7, 0, 9.2, 1.4, 5.2, 0.5, colour if k % 2 == 0 else "cloth_white", rx=0.12)
    for k in range(3):
        part.blob(-2 + k * 2, 0, 3.9, 0.7, ("flower_yellow", "coconut", "flower_red")[k])
    obj(name, part.finish(name), coll, x, y, ground(x, y, 4) - 0.2, rz)
    return name


def hut(name: str, x: float, y: float, rz: float = 0.0, size: float = 12.0, wall: str = "wood_light",
        roof: str = "roof_red", coll: str = "06_BUILDINGS") -> str:
    """A simple building: a barn, a shed, a fishing hut. Door faces local -Y."""
    part = Part()
    h = size * 0.75
    part.box(0, 0, h / 2, size, size * 0.8, h, wall)
    part.box(0, -size * 0.4 - 0.15, h * 0.35, size * 0.3, 0.4, h * 0.6, "wood_dark")
    hw, hd = size / 2 + 1, size * 0.4 + 1
    part.poly([(-hw, -hd, h), (hw, -hd, h), (hw, 0, h + size * 0.45), (-hw, 0, h + size * 0.45)], roof, both_sides=True)
    part.poly([(-hw, hd, h), (-hw, 0, h + size * 0.45), (hw, 0, h + size * 0.45), (hw, hd, h)], roof, both_sides=True)
    for sx in (-size / 2, size / 2):
        part.poly([(sx, -size * 0.4, h), (sx, size * 0.4, h), (sx, 0, h + size * 0.45 - 0.3)], wall, both_sides=True)
    obj(name, part.finish(name), coll, x, y, ground(x, y, size / 2) - 0.3, rz)
    return name


def well(name: str, x: float, y: float, coll: str = "12_FARM") -> str:
    part = Part()
    part.cylinder(0, 0, 0, 2.6, 3, "rock", sides=10)
    part.cylinder(0, 0, 2.9, 2.0, 0.2, "shallows", sides=10)
    for sx in (-2.3, 2.3):
        part.box(sx, 0, 4.5, 0.5, 0.5, 5, "wood_dark")
    part.poly([(-3, -2.4, 6.6), (3, -2.4, 6.6), (3, 0, 8.4), (-3, 0, 8.4)], "roof_red", both_sides=True)
    part.poly([(-3, 2.4, 6.6), (-3, 0, 8.4), (3, 0, 8.4), (3, 2.4, 6.6)], "roof_red", both_sides=True)
    obj(name, part.finish(name), coll, x, y, ground(x, y, 3) - 0.2)
    return name


def crop_plot(name: str, x: float, y: float, sx: float = 16.0, sy: float = 12.0, rz: float = 0.0,
              crop: str = "leaf_light", coll: str = "12_FARM") -> str:
    """Tilled rows with little sprouts, sunk slightly into the ground."""
    part = Part()
    rows = int(sy // 2.4)
    part.box(0, 0, 0.1, sx + 1.5, sy + 1.5, 0.6, "wood_dark")
    for k in range(rows):
        ry = -sy / 2 + (k + 0.5) * sy / rows
        part.box(0, ry, 0.5, sx, sy / rows - 0.5, 0.7, "dirt")
        if crop:
            for c in range(int(sx // 2.5)):
                part.blob(-sx / 2 + 1.2 + c * 2.5, ry, 1.3, 0.55, crop, squash=0.9)
    obj(name, part.finish(name), coll, x, y, ground(x, y, max(sx, sy) / 2) - 0.2, rz)
    return name


def fence_run(name: str, points: list, colour: str = "wood", coll: str = "12_FARM") -> str:
    """A fence along a polyline, posts every 3 studs, following the ground."""
    part = Part()
    for a, b in zip(points, points[1:]):
        a, b = Vector((a[0], a[1], 0)), Vector((b[0], b[1], 0))
        n = max(1, int((b - a).length // 3))
        heading = math.atan2(b.y - a.y, b.x - a.x)
        for k in range(n + 1):
            p = a.lerp(b, k / n)
            part.box(p.x, p.y, ground(p.x, p.y) + 1.5, 0.45, 0.45, 3.2, colour)
        for k in range(n):
            p = a.lerp(b, (k + 0.5) / n)
            z = ground(p.x, p.y)
            for rz in (1.2, 2.5):
                part.box(p.x, p.y, z + rz, (b - a).length / n, 0.3, 0.35, colour, rz=heading)
    obj(name, part.finish(name), coll)
    return name


def platform(name: str, x: float, y: float, sx: float, sy: float, z: float | None = None,
             rz: float = 0.0, colour: str = "wood_light", coll: str = "09_PROPS", legs: bool = True) -> str:
    """A flat deck on legs: fishing spots, lookouts, pads. Top at `z` (ground + 1 by default)."""
    top = (ground(x, y, max(sx, sy) / 2) + 1.0) if z is None else z
    part = Part()
    part.box(0, 0, -0.3, sx, sy, 0.6, colour)
    if legs:
        for lx in (-sx / 2 + 0.5, sx / 2 - 0.5):
            for ly in (-sy / 2 + 0.5, sy / 2 - 0.5):
                part.cylinder(lx, ly, -top - 8, 0.4, top + 8, "wood_dark", sides=6)
    obj(name, part.finish(name), coll, x, y, top, rz)
    return name


def lookout(name: str, x: float, y: float, height: float = 14.0, coll: str = "13_FOREST") -> str:
    part = Part()
    for lx in (-3, 3):
        for ly in (-3, 3):
            part.cylinder(lx, ly, 0, 0.5, height + 3, "wood_dark", sides=6)
    part.box(0, 0, height, 8, 8, 0.6, "wood_light")
    for side in range(4):
        a = side * math.pi / 2
        part.box(math.cos(a) * 4, math.sin(a) * 4, height + 1.2, 0.3 if side % 2 == 0 else 8,
                 8 if side % 2 == 0 else 0.3, 0.3, "wood")
    part.cylinder(0, 0, height + 3, 6.2, 3, "roof_red", sides=4, top=0.2, rz=math.pi / 4)
    for k in range(int(height // 1.5)):
        part.box(0, -4.5 - k * 0.9 * 0, k * 1.5 + 0.2, 2.2, 0.4, 0.3, "wood")
    obj(name, part.finish(name), coll, x, y, ground(x, y, 4) - 0.2)
    return name


def bridge(name: str, start: tuple, end: tuple, width: float = 6.0, coll: str = "03_ROADS_PATHS") -> str:
    """A small arched footbridge between two points."""
    a, b = Vector((start[0], start[1], 0)), Vector((end[0], end[1], 0))
    length = (b - a).length
    heading = math.atan2(b.y - a.y, b.x - a.x)
    za, zb = ground(a.x, a.y), ground(b.x, b.y)
    part = Part()
    n = max(4, int(length / 1.6))
    for k in range(n):
        t = (k + 0.5) / n
        z = _lerp(za, zb, t) + math.sin(t * math.pi) * 2.0 + 0.5
        part.box(-length / 2 + t * length, 0, z, length / n - 0.2, width, 0.5, "wood_light")
        for y in (-width / 2, width / 2):
            part.box(-length / 2 + t * length, y, z + 1.3, length / n, 0.3, 0.3, "wood")
    mid = (a + b) / 2
    obj(name, part.finish(name), coll, mid.x, mid.y, 0.0, heading)
    return name


def stream(name: str, points: list, width: float = 5.0, coll: str = "13_FOREST") -> str:
    """Water in a shallow bed along the ground."""
    return path(name, points, width=width, colour="shallows", coll=coll, lift=0.15)


# --------------------------------------------------------------------------
# Railway
# --------------------------------------------------------------------------
def build_railway(prefix: str = "Rail", segment_samples: int = 12, gauge: float = 5.0) -> dict:
    """Track along the island's rail loop, in modules, with bridges and a tunnel.

    Makes `<prefix>_Path` (a closed curve the train can follow),
    `<prefix>_Track_Segment_NNN`, `<prefix>_Bridge_NNN` where the ground falls
    away, and `<prefix>_Tunnel_NNN` where the loop runs through high ground.
    """
    isl = island()
    samples = isl.rail_samples
    if not samples:
        raise RuntimeError("the island spec has no railway")
    for stale in ("_Track_Segment_", "_Bridge_", "_Tunnel_"):
        remove(prefix + stale)

    curve = bpy.data.curves.new(f"{prefix}_Path", "CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new("POLY")
    spline.points.add(len(samples) - 1)
    for point, (x, y, z, _h) in zip(spline.points, samples):
        point.co = (x, y, z, 1.0)
    spline.use_cyclic_u = True
    old = bpy.data.objects.get(f"{prefix}_Path")
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    path_obj = bpy.data.objects.new(f"{prefix}_Path", curve)
    collection("04_RAILWAY").objects.link(path_obj)

    m = len(samples)
    segments = 0
    for start in range(0, m, segment_samples):
        part = Part()
        chunk = [samples[(start + k) % m] for k in range(segment_samples + 1)]
        ox, oy = chunk[0][0], chunk[0][1]
        for k, (x, y, z, heading) in enumerate(chunk[:-1]):
            nx, ny, nz, _ = chunk[k + 1]
            lx, ly = x - ox, y - oy
            part.box(lx, ly, z - 0.45, 1.3, gauge + 2.2, 0.45, "wood_dark", rz=heading)
            seg_len = math.hypot(nx - x, ny - y) + 0.1
            mx, my = (x + nx) / 2 - ox, (y + ny) / 2 - oy
            pitch = -math.atan2(nz - z, seg_len)
            for side in (-gauge / 2, gauge / 2):
                sx, sy = -math.sin(heading) * side, math.cos(heading) * side
                part.box(mx + sx, my + sy, (z + nz) / 2 - 0.05, seg_len, 0.4, 0.45, "rail", rz=heading, ry=pitch)
        segments += 1
        name = f"{prefix}_Track_Segment_{segments:03d}"
        obj(name, part.finish(name), "04_RAILWAY", ox, oy, 0.0)

    # Bridges and tunnels, from how far the bed sits above or below the
    # untouched ground.
    def spans(test):
        found, current = [], []
        for i in range(m):
            if test(i):
                current.append(i)
            elif current:
                found.append(current)
                current = []
        if current:
            found.append(current)
        return [s for s in found if len(s) >= 3]

    bridges = spans(lambda i: samples[i][2] - max(isl.rail_raw[i], -6.0) > 3.2)
    for n, span in enumerate(bridges, 1):
        part = Part()
        for i in span[::3]:
            x, y, z, heading = samples[i]
            for side in (-gauge / 2 - 0.4, gauge / 2 + 0.4):
                sx, sy = -math.sin(heading) * side, math.cos(heading) * side
                foot = min(isl.rail_raw[i], 0.0) - 2.0 if isl.rail_raw[i] < 0 else isl.rail_raw[i] - 1.0
                part.cylinder(x + sx, y + sy, foot, 0.5, z - 0.6 - foot, "wood", sides=6)
            part.box(x, y, z - 1.1, 1.0, gauge + 3.4, 0.6, "wood", rz=heading)
        for i in span:
            x, y, z, heading = samples[i]
            for side in (-gauge / 2 - 1.3, gauge / 2 + 1.3):
                sx, sy = -math.sin(heading) * side, math.cos(heading) * side
                part.box(x + sx, y + sy, z + 0.6, 2.2, 0.3, 1.2, "wood_light", rz=heading)
        name = f"{prefix}_Bridge_{n:03d}"
        obj(name, part.finish(name), "04_RAILWAY")

    tunnels = spans(lambda i: isl.rail_raw[i] - samples[i][2] > 4.0)
    for n, span in enumerate(tunnels, 1):
        part = Part()
        for i in span[::2]:
            x, y, z, heading = samples[i]
            for side in (-gauge / 2 - 2.2, gauge / 2 + 2.2):
                sx, sy = -math.sin(heading) * side, math.cos(heading) * side
                part.box(x + sx, y + sy, z + 4, 4.2, 1.6, 9, "rock_dark", rz=heading)
            part.box(x, y, z + 9.0, 4.2, gauge + 7.0, 1.6, "rock_dark", rz=heading)
        for i in (span[0], span[-1]):
            x, y, z, heading = samples[i]
            part.box(x, y, z + 10.5, 2.0, gauge + 10, 3.5, "rock", rz=heading)
            for side in (-gauge / 2 - 4.0, gauge / 2 + 4.0):
                sx, sy = -math.sin(heading) * side, math.cos(heading) * side
                part.box(x + sx, y + sy, z + 5, 2.4, 3.0, 11, "rock", rz=heading)
        name = f"{prefix}_Tunnel_{n:03d}"
        obj(name, part.finish(name), "04_RAILWAY")
    return {"segments": segments, "bridges": len(bridges), "tunnels": len(tunnels), "samples": m}


def station(prefix: str, near: tuple, side: float = 1.0, coll: str = "04_RAILWAY") -> list[str]:
    """A small tram stop beside the track nearest `near`, on the given side.

    Makes `<prefix>` (the platform) plus `_Shelter`, `_Sign`, `_Bench`,
    `_Lantern` and `_Signal`.
    """
    x, y, z, heading = island().rail_point_near(*near)
    nx, ny = -math.sin(heading) * side, math.cos(heading) * side
    px, py = x + nx * 7.0, y + ny * 7.0
    top = z + 0.6
    names = []

    plat = Part()
    plat.box(0, 0, top / 2 - 1.5, 24, 7, top + 3, "white_stone")
    plat.box(0, -3.3 * side, top + 0.05, 24, 0.6, 0.12, "flower_yellow")
    obj(prefix, plat.finish(prefix), coll, px, py, 0.0, heading)
    names.append(prefix)

    shelter = Part()
    for sx in (-5, 5):
        shelter.box(sx, 2.2 * side, top + 4.5, 0.5, 0.5, 9, "wood")
    shelter.box(0, 2.6 * side, top + 3.5, 10, 0.4, 7, "wood_light")
    shelter.poly([(-6.5, -1.5, top + 9.0), (6.5, -1.5, top + 9.0), (6.5, 3.8, top + 10.5), (-6.5, 3.8, top + 10.5)]
                 if side > 0 else
                 [(-6.5, 1.5, top + 9.0), (-6.5, -3.8, top + 10.5), (6.5, -3.8, top + 10.5), (6.5, 1.5, top + 9.0)],
                 "teal_roof", both_sides=True)
    obj(f"{prefix}_Shelter", shelter.finish(f"{prefix}_Shelter"), coll, px, py, 0.0, heading)
    names.append(f"{prefix}_Shelter")

    c, s = math.cos(heading), math.sin(heading)

    def at(lx, ly):
        return px + lx * c - ly * s, py + lx * s + ly * c

    for part_name, mesh, lx, ly in (("Bench", bench_mesh(), 0.0, 1.4 * side), ("Sign", sign_mesh(), -9.0, 1.5 * side),
                                     ("Lantern", lantern_mesh(), 9.5, 1.8 * side)):
        wx, wy = at(lx, ly)
        obj(f"{prefix}_{part_name}", mesh, coll, wx, wy, top, heading + (math.pi if side < 0 else 0.0))
        names.append(f"{prefix}_{part_name}")

    signal = Part()
    signal.cylinder(0, 0, 0, 0.3, 8, "black", sides=6)
    signal.box(0, 0, 7.5, 1.2, 0.8, 2.4, "black")
    signal.blob(0, -0.45, 8.1, 0.35, "leaf_light")
    signal.blob(0, -0.45, 7.0, 0.35, "cloth_red")
    sx, sy = at(13.5, -3.0 * side)
    obj(f"{prefix}_Signal", signal.finish(f"{prefix}_Signal"), coll, sx, sy, top - 1.0, heading)
    names.append(f"{prefix}_Signal")
    _PATH_ZONES.append((px, py, 14))
    return names


def train(prefix: str = "Train", at: tuple = (0.0, 0.0), cars: int = 2, coll: str = "05_TRAIN") -> list[str]:
    """A small rounded island train sitting on the track nearest `at`.

    Parent empty `TRAIN`; children `<prefix>_Locomotive`, `<prefix>_Car_01..`,
    `<prefix>_Wheels`. Each car is placed on the track behind the one before.
    """
    isl = island()
    samples = isl.rail_samples
    idx = min(range(len(samples)), key=lambda i: (samples[i][0] - at[0]) ** 2 + (samples[i][1] - at[1]) ** 2)
    names = []
    old = bpy.data.objects.get("TRAIN")
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    parent = bpy.data.objects.new("TRAIN", None)
    collection(coll).objects.link(parent)

    def spot(back: float):
        i = (idx - int(back / 2.0)) % len(samples)
        return samples[i]

    wheels = Part()

    def add_wheels(x, y, z, heading, length):
        for lx in (-length / 2 + 2, length / 2 - 2):
            for side in (-2.9, 2.9):
                wx = x + math.cos(heading) * lx - math.sin(heading) * side
                wy = y + math.sin(heading) * lx + math.cos(heading) * side
                wheels.cylinder(wx, wy, z + 1.2, 1.2, 0.6, "black", sides=8,
                                rx=math.pi / 2 if side < 0 else -math.pi / 2, rz=heading)

    loco = Part()
    loco.box(0, 0, 2.2, 14, 5.6, 1.2, "black")
    loco.cylinder(-6.8, 0, 4.6, 2.4, 9, "train_red", sides=10, ry=math.pi / 2)
    loco.cylinder(2.2, 0, 4.6, 2.7, 0.6, "gold", sides=10, ry=math.pi / 2)
    loco.box(4.4, 0, 6.0, 5.2, 5.8, 6.4, "train_cream")
    loco.box(4.4, 0, 9.5, 6.4, 6.6, 0.8, "train_red")
    loco.box(4.4, -2.95, 7.0, 3.2, 0.2, 2.2, "glass")
    loco.box(4.4, 2.95, 7.0, 3.2, 0.2, 2.2, "glass")
    loco.cylinder(-4.8, 0, 6.8, 0.9, 3.2, "black", sides=8, top=1.5)
    loco.blob(-1.8, 0, 7.2, 1.0, "gold")
    loco.box(-7.4, 0, 2.2, 1.0, 6.0, 1.4, "gold")
    x, y, z, heading = spot(0)
    obj(f"{prefix}_Locomotive", loco.finish(f"{prefix}_Locomotive"), coll, x, y, z, heading).parent = parent
    add_wheels(x, y, z, heading, 14)
    names.append(f"{prefix}_Locomotive")

    colours = ["train_green", "pastel_blue", "pastel_yellow"]
    back = 16.0
    for n in range(1, cars + 1):
        car = Part()
        car.box(0, 0, 2.2, 12, 5.6, 1.0, "black")
        car.box(0, 0, 5.2, 12, 5.8, 5.0, colours[(n - 1) % len(colours)])
        for k in range(3):
            for side in (-2.95, 2.95):
                car.box(-4 + k * 4, side, 5.8, 2.6, 0.2, 2.2, "glass")
        car.box(0, 0, 8.1, 13, 6.6, 0.8, "train_cream")
        car.cylinder(-6.0, 0, 8.4, 3.3, 12, "train_cream", sides=8, top=3.3, ry=math.pi / 2)
        x, y, z, heading = spot(back)
        name = f"{prefix}_Car_{n:02d}"
        obj(name, car.finish(name), coll, x, y, z, heading).parent = parent
        add_wheels(x, y, z, heading, 12)
        names.append(name)
        back += 14.0
    obj(f"{prefix}_Wheels", wheels.finish(f"{prefix}_Wheels"), coll).parent = parent
    names.append(f"{prefix}_Wheels")
    return names


# --------------------------------------------------------------------------
# Light and sky
# --------------------------------------------------------------------------
def daylight(camera_at: tuple = (0, -280, 12), look_at: tuple = (0, -110, 8)) -> None:
    """Warm early-afternoon sun, a bright sky, a hidden evening sun, and a spawn camera."""
    coll = collection("15_LIGHTING")

    def sun(name, strength, colour, rot, hide=False):
        old = bpy.data.objects.get(name)
        if old is not None:
            bpy.data.objects.remove(old, do_unlink=True)
        light = bpy.data.lights.new(name, "SUN")
        light.energy = strength
        light.color = colour
        light.angle = math.radians(8)
        made = bpy.data.objects.new(name, light)
        made.rotation_euler = rot
        made.hide_viewport = hide
        made.hide_render = hide
        coll.objects.link(made)
        return made

    sun("Sun_Day", 4.0, (1.0, 0.94, 0.84), (math.radians(40), math.radians(10), math.radians(35)))
    sun("Sun_Evening", 2.2, (1.0, 0.62, 0.38), (math.radians(78), 0.0, math.radians(-60)), hide=True)

    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.color = (0.55, 0.78, 0.98)
    tree = getattr(world, "node_tree", None)
    if tree is not None:
        background = tree.nodes.get("Background")
        if background is not None:
            background.inputs["Color"].default_value = (0.55, 0.78, 0.98, 1.0)
            background.inputs["Strength"].default_value = 1.0

    old = bpy.data.objects.get("Camera_SpawnView")
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    cam = bpy.data.cameras.new("Camera_SpawnView")
    cam.lens = 24
    cam.clip_end = 3000
    made = bpy.data.objects.new("Camera_SpawnView", cam)
    made.location = camera_at
    direction = Vector(look_at) - Vector(camera_at)
    made.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    coll.objects.link(made)
    bpy.context.scene.camera = made



# --------------------------------------------------------------------------
# Water features and markers
# --------------------------------------------------------------------------
def pond(name: str, x: float, y: float, radius: float = 10.0, z: float | None = None,
         coll: str = "13_FOREST") -> str:
    """A still pool with a stone rim, its surface just below the ground around it.

    The island spec should carve a basin here (a "level" feature a stud or two
    down); the water is set from the RIM's height, not the basin's, so it
    fills the hollow rather than sitting under the grass.
    """
    surface = (ground(x + radius + 2, y) - 0.4) if z is None else z
    part = Part()
    part.cylinder(0, 0, -1.5, radius, 1.6, "shallows", sides=14)
    for k in range(14):
        a = k / 14 * 2 * math.pi
        part.blob(math.cos(a) * radius, math.sin(a) * radius, 0.2, 1.6, "rock", squash=0.6,
                  jitter=0.15, seed=k)
    obj(name, part.finish(name), coll, x, y, surface)
    return name


def waterfall(name: str, top: tuple, bottom_z: float, width: float = 6.0, heading: float = 0.0,
              coll: str = "13_FOREST") -> str:
    """A falling sheet of water from `top` (x, y, z) down to `bottom_z`, facing `heading`.

    A stepped curtain with a foam splash at the foot: reads as a waterfall from
    any angle without a particle in sight.
    """
    x, y, z = top
    drop = z - bottom_z
    part = Part()
    steps = max(2, int(drop // 4))
    for k in range(steps):
        z0 = drop - (k + 1) * drop / steps
        part.box(0.6 + k * 0.5, 0, z0 + drop / steps / 2 - drop, 1.0, width, drop / steps + 0.2, "shallows")
    part.blob(steps * 0.5 + 1.5, 0, -drop, width * 0.6, "foam", squash=0.3)
    obj(name, part.finish(name), coll, x, y, z, heading)
    return name


def npc_spot(name: str, x: float, y: float, z: float | None = None, coll: str = "09_PROPS") -> str:
    """Where an NPC stands: a small round stone pad the game can find by name."""
    part = Part()
    part.cylinder(0, 0, -0.2, 2.2, 0.4, "stone_path", sides=10)
    obj(name, part.finish(name), coll, x, y, ground(x, y) if z is None else z)
    return name


# --------------------------------------------------------------------------
# Signs with words, and small village furniture
# --------------------------------------------------------------------------
def text_part(part: "Part", text: str, x: float, y: float, z: float, size: float = 1.2,
              colour: str = "wood_dark", depth: float = 0.15, facing: float = 0.0) -> None:
    """Real letters, as geometry, added into `part`: "Plot 1" on a sign, not a texture.

    Blender's font is turned into a mesh and copied in, standing upright and
    facing local -Y (turn with `facing`), centred on (x, y, z).
    """
    curve = bpy.data.curves.new("_text", "FONT")
    curve.body = text
    curve.size = size
    curve.extrude = depth / 2
    curve.align_x = "CENTER"
    curve.align_y = "CENTER"
    holder = bpy.data.objects.new("_text", curve)
    bpy.context.scene.collection.objects.link(holder)
    deps = bpy.context.evaluated_depsgraph_get()
    evaluated = holder.evaluated_get(deps)
    mesh = evaluated.to_mesh()
    place = (Matrix.Translation((x, y, z)) @ Matrix.Rotation(facing, 4, "Z")
             @ Matrix.Rotation(math.pi / 2, 4, "X"))
    index = part._index(colour)
    lookup = []
    for v in mesh.vertices:
        lookup.append(part.bm.verts.new(place @ v.co))
    for poly in mesh.polygons:
        try:
            face = part.bm.faces.new([lookup[i] for i in poly.vertices])
            face.material_index = index
        except ValueError:
            pass  # a duplicate face in the font outline; skip it
    evaluated.to_mesh_clear()
    bpy.data.objects.remove(holder, do_unlink=True)
    bpy.data.curves.remove(curve)


def plot_sign(name: str, label: str, x: float, y: float, rz: float = 0.0, coll: str = "07_HOUSING") -> str:
    """A wooden plot sign on two posts, with the label carved on its face (local -Y)."""
    part = Part()
    for sx in (-2.2, 2.2):
        part.box(sx, 0, 2.2, 0.5, 0.5, 4.4, "wood_dark")
    part.box(0, 0, 4.4, 6.0, 0.5, 2.4, "wood_light")
    part.box(0, 0, 5.75, 6.6, 0.8, 0.4, "roof_red")
    text_part(part, label, 0, -0.3, 4.4, size=1.3, colour="wood_dark")
    obj(name, part.finish(name), coll, x, y, ground(x, y), rz)
    return name


def mailbox(name: str, x: float, y: float, rz: float = 0.0, colour: str = "pastel_blue",
            coll: str = "07_HOUSING") -> str:
    """A cute wooden mailbox: a post, a round-topped box and a little red flag."""
    part = Part()
    part.box(0, 0, 1.6, 0.5, 0.5, 3.2, "wood_dark")
    part.box(0, 0, 3.6, 1.4, 2.2, 1.0, colour)
    part.cylinder(0, -1.1, 4.1, 0.7, 2.2, colour, sides=8, rx=-math.pi / 2)
    part.box(0.8, 0.3, 4.4, 0.1, 0.2, 1.2, "wood_dark")
    part.box(0.8, 0.6, 4.9, 0.1, 0.8, 0.5, "flower_red")
    obj(name, part.finish(name), coll, x, y, ground(x, y), rz)
    return name


def tent(name: str, x: float, y: float, rz: float = 0.0, colour: str = "cloth_white",
         coll: str = "07_HOUSING") -> str:
    """A small canvas starter tent on a wooden pad, its opening facing local -Y."""
    part = Part()
    part.box(0, 0, 0.25, 10, 12, 0.5, "wood_light")
    w, d, h = 4.2, 5.0, 6.0
    part.poly([(-w, -d, 0.5), (0, -d, h), (0, d, h), (-w, d, 0.5)], colour, both_sides=True)
    part.poly([(w, -d, 0.5), (w, d, 0.5), (0, d, h), (0, -d, h)], colour, both_sides=True)
    part.poly([(-w, d, 0.5), (0, d, h), (w, d, 0.5)], colour, both_sides=True)
    part.poly([(-w, -d, 0.5), (-1.2, -d, 0.5), (0, -d, h)], colour, both_sides=True)
    part.poly([(1.2, -d, 0.5), (w, -d, 0.5), (0, -d, h)], colour, both_sides=True)
    part.cylinder(0, -d - 0.2, 0.5, 0.15, h + 0.8, "wood_dark", sides=5)
    part.box(0, 0, h + 0.1, 0.4, 2 * d + 1, 0.3, "cloth_red")
    obj(name, part.finish(name), coll, x, y, ground(x, y, 6) - 0.1, rz)
    return name


def life_ring(name: str, x: float, y: float, z: float, rz: float = 0.0, coll: str = "10_DOCKS") -> str:
    """A red-and-white life ring hanging upright, facing local -Y."""
    part = Part()
    for k in range(8):
        a = k / 8 * 2 * math.pi
        part.box(math.cos(a) * 1.1, 0, math.sin(a) * 1.1, 0.95, 0.45, 0.5,
                 "cloth_red" if k % 2 == 0 else "cloth_white", ry=-a)
    obj(name, part.finish(name), coll, x, y, z, rz)
    return name


def rope_coil(part: "Part", x: float, y: float, z: float, radius: float = 1.3, turns: int = 3) -> None:
    """Coiled rope wound round a post, added into `part`."""
    for k in range(turns):
        part.cylinder(x, y, z + k * 0.35, radius, 0.3, "cloth_white", sides=10)


def noticeboard(name: str, x: float, y: float, rz: float = 0.0, title: str = "HAVEN BAY",
                coll: str = "09_PROPS") -> str:
    """The island noticeboard with a painted map, facing local -Y."""
    part = Part()
    for sx in (-3.6, 3.6):
        part.box(sx, 0, 3.2, 0.6, 0.6, 6.4, "wood_dark")
    part.box(0, 0, 4.2, 7.4, 0.5, 4.4, "wood_light")
    part.box(0, -0.3, 4.0, 5.4, 0.1, 3.0, "ocean")
    part.blob(0, -0.35, 4.0, 1.3, "grass", squash=0.1)
    part.poly([(-4.4, -0.9, 6.4), (4.4, -0.9, 6.4), (0, -0.2, 8.0)], "roof_red", both_sides=True)
    part.poly([(-4.4, 0.9, 6.4), (0, 0.2, 8.0), (4.4, 0.9, 6.4)], "roof_red", both_sides=True)
    text_part(part, title, 0, -0.4, 6.0, size=0.7, colour="wood_dark")
    obj(name, part.finish(name), coll, x, y, ground(x, y, 3), rz)
    return name


def picnic_table_mesh() -> bpy.types.Mesh:
    def build():
        part = Part()
        part.box(0, 0, 2.6, 6, 3, 0.4, "wood_light")
        for sy in (-2.4, 2.4):
            part.box(0, sy, 1.5, 6, 1.1, 0.35, "wood_light")
        for sx in (-2.3, 2.3):
            part.box(sx, 0, 1.3, 0.4, 5.2, 0.35, "wood_dark")
            part.box(sx, 0, 1.9, 0.4, 0.4, 1.4, "wood_dark")
        return part.finish("Kit_PicnicTable")
    return _cached("Kit_PicnicTable", build)


def fish_crate_mesh() -> bpy.types.Mesh:
    def build():
        part = Part()
        part.box(0, 0, 0.7, 3.4, 2.2, 1.4, "wood")
        part.box(0, 0, 1.45, 3.0, 1.8, 0.1, "shallows")
        for k in range(3):
            part.blob(-0.9 + k * 0.9, 0, 1.6, 0.45, "pastel_blue", stretch=2.0, squash=0.6)
        return part.finish("Kit_FishCrate")
    return _cached("Kit_FishCrate", build)


# --------------------------------------------------------------------------
# Spread: a bigger island without bigger props
# --------------------------------------------------------------------------
# Scripts are written in one set of coordinates (the island as first laid
# out). An island spec may ask for it to be SPREAD: {"scale": 2, "zones":
# [[x, y, radius], ...]}. A point inside a zone moves with its zone -- the
# zone's centre goes to scale x centre, and everything around it keeps its
# distance -- so a plot, the dock or the town keeps its layout and every prop
# its size. A point outside every zone is simply scaled, so the land between
# zones, the coast and the paths connecting them grow.
#
# Every public function that takes a world position spreads it once, at the
# call from a script. Calls the kit makes to itself are already in spread
# space and are left alone, which is what the depth counter is for.
import contextlib as _contextlib
import functools as _functools
import inspect as _inspect

_SPREAD = {"scale": 1.0, "zones": []}
_DEPTH = 0
_ABSOLUTE = False
TREE_SCALE = 1.0


def spread(x: float, y: float) -> tuple[float, float]:
    """Where a script's (x, y) lands on the spread island."""
    scale = _SPREAD["scale"]
    if _ABSOLUTE or scale == 1.0:
        return x, y
    best = None
    for zx, zy, zr in _SPREAD["zones"]:
        d = math.hypot(x - zx, y - zy)
        if d <= zr and (best is None or d < best[0]):
            best = (d, zx, zy)
    if best is not None:
        _d, zx, zy = best
        return x + zx * (scale - 1.0), y + zy * (scale - 1.0)
    return x * scale, y * scale


@_contextlib.contextmanager
def absolute():
    """Inside this, positions are taken as they are: already where they land."""
    global _ABSOLUTE
    before = _ABSOLUTE
    _ABSOLUTE = True
    try:
        yield
    finally:
        _ABSOLUTE = before


def _point(value):
    if value is None:
        return value
    x, y = spread(float(value[0]), float(value[1]))
    return (x, y, *value[2:])


def _spreading(fn, xy=(), pts=(), pt=(), centre=None, keep=None):
    signature = _inspect.signature(fn)

    @_functools.wraps(fn)
    def wrapper(*args, **kwargs):
        global _DEPTH
        if _DEPTH > 0 or _SPREAD["scale"] == 1.0 or _ABSOLUTE:
            _DEPTH += 1
            try:
                return fn(*args, **kwargs)
            finally:
                _DEPTH -= 1
        bound = signature.bind(*args, **kwargs)
        values = bound.arguments
        if xy and xy[0] in values and xy[1] in values:
            values[xy[0]], values[xy[1]] = spread(float(values[xy[0]]), float(values[xy[1]]))
        for name in pt:
            if name in values:
                values[name] = _point(values[name])
        for name in pts:
            if name in values:
                values[name] = [_point(p) for p in values[name]]
        if centre and centre in values:
            before = values[centre]
            values[centre] = _point(before)
            # A scatter over the whole island grows with it; one over a zone
            # (the forest, a garden) keeps the zone's size.
            if "radius" in values and float(values["radius"]) > 100:
                values["radius"] = float(values["radius"]) * _SPREAD["scale"]
        if keep and values.get(keep):
            zones = []
            for zone in values[keep]:
                if len(zone) == 3:
                    x, y = spread(zone[0], zone[1])
                    zones.append((x, y, zone[2]))
                else:
                    x0, y0 = spread(zone[0], zone[1])
                    x1, y1 = spread(zone[2], zone[3])
                    zones.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
            values[keep] = zones
        _DEPTH += 1
        try:
            return fn(*bound.args, **bound.kwargs)
        finally:
            _DEPTH -= 1

    return wrapper


def _grown(fn):
    """Tree meshes at TREE_SCALE, grown once however often they are asked for."""
    @_functools.wraps(fn)
    def wrapper(*args, **kwargs):
        mesh = fn(*args, **kwargs)
        had = float(mesh.get("haven_grown", 1.0))
        if abs(had - TREE_SCALE) > 1e-6:
            mesh.transform(Matrix.Scale(TREE_SCALE / had, 4))
            mesh["haven_grown"] = TREE_SCALE
        return mesh
    return wrapper


for _name in ("ground", "place", "obj", "house", "plot", "pavilion", "museum", "domed_hall", "fountain",
              "statue", "totem_bell", "plaza", "stall", "hut", "well", "crop_plot", "platform", "lookout",
              "pond", "npc_spot", "plot_sign", "mailbox", "tent", "noticeboard", "life_ring"):
    globals()[_name] = _spreading(globals()[_name], xy=("x", "y"))
for _name in ("path", "fence_run", "stream"):
    globals()[_name] = _spreading(globals()[_name], pts=("points",))
for _name in ("pier", "stairs", "bridge"):
    globals()[_name] = _spreading(globals()[_name], pt=("start", "end"))
waterfall = _spreading(waterfall, pt=("top",))
scatter = _spreading(scatter, centre="centre", keep="keep_out")
for _name in ("palm_mesh", "pine_mesh", "round_tree_mesh"):
    globals()[_name] = _grown(globals()[_name])

_load_island_unspread = load_island


def load_island(spec: dict) -> Island:
    """Load the island, spreading its features first when the spec asks."""
    global TREE_SCALE
    layout = spec.get("spread") or {}
    _SPREAD["scale"] = float(layout.get("scale", 1.0))
    _SPREAD["zones"] = [tuple(float(v) for v in zone) for zone in layout.get("zones", [])]
    TREE_SCALE = float(spec.get("tree_scale", 1.0))
    if _SPREAD["scale"] == 1.0:
        return _load_island_unspread(spec)
    s = _SPREAD["scale"]
    spread_spec = dict(spec)
    spread_spec["radius_x"] = float(spec.get("radius_x", 245)) * s
    spread_spec["radius_y"] = float(spec.get("radius_y", 215)) * s
    features = []
    for feature in spec.get("features", []):
        f = dict(feature)
        if f.get("type") == "ramp":
            f["x0"], f["y0"] = spread(f["x0"], f["y0"])
            f["x1"], f["y1"] = spread(f["x1"], f["y1"])
        else:
            f["x"], f["y"] = spread(f["x"], f["y"])
        features.append(f)
    spread_spec["features"] = features
    spread_spec["sandbars"] = [[*spread(b[0], b[1]), *b[2:]] for b in spec.get("sandbars", [])]
    spread_spec["islets"] = [[*spread(i[0], i[1]), *i[2:]] for i in spec.get("islets", [])]
    return _load_island_unspread(spread_spec)
