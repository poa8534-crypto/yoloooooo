"""The island shell, generated rather than asked for.

Six rounds of measured feedback did not get a model past a smooth oval with
vertical sides: it kept either scaling the disc it had already made or adding
another one beside it. A coastline is not a judgement call, though -- it is a
radius as a function of angle -- so it is written here, once, and the model's
time is spent on the blocky pieces it does get right.

The shape is polar. One radius function describes the coast, and every ring of
the mesh is that same function scaled, so the bays and the headland run all
the way down the underwater skirt instead of being a detail at the waterline.

Everything here is deterministic and pure: `outline` and `predict` can be run
without Blender, which is the point. The measurements the brief cares about --
how far the coast departs from an oval, how far the skirt draws in, how much
the high ground rolls -- are computed from the same numbers the mesh is built
from, so the shape is checked before Blender is started rather than after the
file is saved.
"""

from __future__ import annotations

import math

STEPS = 48
SECTORS = 16

# The ellipse the coast starts from, in studs, before it is deformed.
HALF_X = 131.0
HALF_Y = 121.0

# Where the coast is pulled in and pushed out, as (degrees, span, amount).
# Negative pulls in: a bay. Positive pushes out: the headland.
# The amounts were searched for, not chosen by eye: the first set that read
# as "two bays and a headland" scored 0.044 on the coast measure when the
# brief wants 0.06 at least, because a sector's furthest vertex hides a bay
# that only dents part of it. These clear it with room -- 0.146 against 0.06.
FEATURES: tuple[tuple[float, float, float], ...] = (
    (40.0, 85.0, -0.50),
    (200.0, 79.0, -0.47),
    (300.0, 51.0, 0.30),
)

# Long, slow wander, so no stretch of coast is a clean arc. Each is
# (cycles around the compass, amount, phase in degrees).
WANDER: tuple[tuple[float, float, float], ...] = (
    (3.0, 0.110, 20.0),
    (5.0, 0.073, 140.0),
    (7.0, 0.044, 260.0),
)

# The vertical profile: (height in studs, how much of the full radius).
# The waterline is the full radius; everything below it draws inward, which is
# what stops the island reading as a disc floating in the sea.
RINGS: tuple[tuple[float, float], ...] = (
    (-12.0, 0.55),
    (-6.0, 0.78),
    (0.0, 1.00),
    (2.0, 0.93),
    (4.0, 0.86),
)

# The grass cap inside the rim, as fractions of the rim radius.
CAP_RINGS: tuple[float, ...] = (0.66, 0.42, 0.18)
GRASS_LOW = 4.6
GRASS_HIGH = 5.7


def _feature_shift(degrees: float) -> float:
    """How much the coast moves at this angle, from bays and the headland."""
    shift = 0.0
    for centre, span, amount in FEATURES:
        away = abs((degrees - centre + 180.0) % 360.0 - 180.0)
        if away < span / 2.0:
            # Raised cosine, so a bay eases in and out rather than arriving as
            # a notch cut in the coast.
            shift += amount * 0.5 * (1.0 + math.cos(math.pi * away / (span / 2.0)))
    return shift


def radius(degrees: float) -> float:
    """The coastline: an ellipse, deformed."""
    angle = math.radians(degrees)
    base = 1.0 / math.sqrt((math.cos(angle) / HALF_X) ** 2 + (math.sin(angle) / HALF_Y) ** 2)
    wander = sum(amount * math.sin(cycles * angle + math.radians(phase))
                 for cycles, amount, phase in WANDER)
    return base * (1.0 + _feature_shift(degrees) + wander)


def outline(steps: int = STEPS) -> list[tuple[float, float]]:
    """The waterline as points, which is what everything else is scaled from."""
    points = []
    for step in range(steps):
        degrees = step * 360.0 / steps
        length = radius(degrees)
        points.append((length * math.cos(math.radians(degrees)),
                       length * math.sin(math.radians(degrees))))
    return points


def grass_height(degrees: float, inward: float) -> float:
    """The rolling of the interior: rim height at the edge, rising inland.

    `inward` runs 0 at the rim to 1 at the middle. The roll is a function of
    both, so the ground is not a cone and not a plate.
    """
    angle = math.radians(degrees)
    swell = 0.5 + 0.5 * math.sin(2.0 * angle + 0.9) * math.sin(3.0 * angle - 0.4)
    rise = GRASS_LOW + (GRASS_HIGH - GRASS_LOW) * (0.45 * swell + 0.55 * inward)
    return min(GRASS_HIGH, max(GRASS_LOW, rise))


def _fit(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return min(xs), max(xs), min(ys), max(ys)


def scale_to(width: float, depth: float) -> tuple[float, float]:
    """What to multiply X and Y by so the island fills the brief's box.

    The deformation changes the extent, so the ellipse it started from is not
    the size it ends up. Rather than guess the constants, the shape is
    measured and then scaled to fit -- which keeps the bays exactly where they
    were put.
    """
    low_x, high_x, low_y, high_y = _fit(outline())
    return width / (high_x - low_x), depth / (high_y - low_y)


def predict(width: float, depth: float) -> dict:
    """The brief's measurements, computed from the maths, before any Blender.

    This mirrors what the epilogue measures in the file: the furthest point in
    each of sixteen sectors, the residual against a fitted oval, how far the
    lowest ring draws in, and the spread of the high ground.
    """
    scale_x, scale_y = scale_to(width, depth)
    points = [(x * scale_x, y * scale_y) for x, y in outline()]
    low_x, high_x, low_y, high_y = _fit(points)
    mid_x, mid_y = (low_x + high_x) / 2.0, (low_y + high_y) / 2.0
    half_x, half_y = (high_x - low_x) / 2.0, (high_y - low_y) / 2.0

    reach = [0.0] * SECTORS
    for x, y in points:
        dx, dy = x - mid_x, y - mid_y
        sector = int((math.atan2(dy, dx) + math.pi) / (2 * math.pi) * SECTORS) % SECTORS
        reach[sector] = max(reach[sector], math.hypot(dx, dy))

    off = []
    for sector, got in enumerate(reach):
        if got <= 0.0:
            continue
        angle = (sector + 0.5) / SECTORS * 2 * math.pi - math.pi
        fit = 1.0 / math.sqrt((math.cos(angle) / half_x) ** 2 + (math.sin(angle) / half_y) ** 2)
        off.append(abs(got - fit) / fit)

    grass = [grass_height(step * 360.0 / STEPS, inward)
             for step in range(STEPS) for inward in (0.0, *CAP_RINGS, 1.0)]
    # The rim ring sits at 4.0 and falls inside the band the epilogue looks
    # at, so it is the floor of the roll whatever the cap does.
    return {
        "size_x": round(high_x - low_x, 2),
        "size_y": round(high_y - low_y, 2),
        "coast_wobble": round(sum(off) / len(off), 3),
        "skirt_taper": round(1.0 - RINGS[0][1], 3),
        "top_roll": round(max(grass) - RINGS[-1][0], 2),
        "top_z": round(max(grass), 2),
        "bottom_z": RINGS[0][0],
        "triangles": STEPS * 2 * (len(RINGS) - 1 + len(CAP_RINGS)) + STEPS,
    }


def script(name: str, width: float, depth: float) -> str:
    """The Blender script that builds it, with the numbers already settled."""
    scale_x, scale_y = scale_to(width, depth)
    return BUILD.format(
        name=name, steps=STEPS, scale_x=scale_x, scale_y=scale_y,
        rings=repr(list(RINGS)), cap_rings=repr(list(CAP_RINGS)),
        half_x=HALF_X, half_y=HALF_Y, features=repr(list(FEATURES)),
        wander=repr(list(WANDER)), grass_low=GRASS_LOW, grass_high=GRASS_HIGH)


BUILD = '''
import math

import bmesh
import bpy

NAME = {name!r}
STEPS = {steps}
SCALE_X = {scale_x!r}
SCALE_Y = {scale_y!r}
RINGS = {rings}
CAP_RINGS = {cap_rings}
HALF_X = {half_x!r}
HALF_Y = {half_y!r}
FEATURES = {features}
WANDER = {wander}
GRASS_LOW = {grass_low!r}
GRASS_HIGH = {grass_high!r}


def feature_shift(degrees):
    shift = 0.0
    for centre, span, amount in FEATURES:
        away = abs((degrees - centre + 180.0) % 360.0 - 180.0)
        if away < span / 2.0:
            shift += amount * 0.5 * (1.0 + math.cos(math.pi * away / (span / 2.0)))
    return shift


def radius(degrees):
    angle = math.radians(degrees)
    base = 1.0 / math.sqrt((math.cos(angle) / HALF_X) ** 2 + (math.sin(angle) / HALF_Y) ** 2)
    wander = sum(amount * math.sin(cycles * angle + math.radians(phase))
                 for cycles, amount, phase in WANDER)
    return base * (1.0 + feature_shift(degrees) + wander)


def grass_height(degrees, inward):
    angle = math.radians(degrees)
    swell = 0.5 + 0.5 * math.sin(2.0 * angle + 0.9) * math.sin(3.0 * angle - 0.4)
    rise = GRASS_LOW + (GRASS_HIGH - GRASS_LOW) * (0.45 * swell + 0.55 * inward)
    return min(GRASS_HIGH, max(GRASS_LOW, rise))


# Every copy goes, including the .001 leftovers, so the file holds one island.
for existing in list(bpy.data.objects):
    if existing.name == NAME or existing.name.startswith(NAME + "."):
        bpy.data.objects.remove(existing, do_unlink=True)

bm = bmesh.new()


def ring_at(height, share):
    made = []
    for step in range(STEPS):
        degrees = step * 360.0 / STEPS
        length = radius(degrees) * share
        made.append(bm.verts.new((
            length * math.cos(math.radians(degrees)) * SCALE_X,
            length * math.sin(math.radians(degrees)) * SCALE_Y,
            height)))
    return made


rings = [ring_at(height, share) for height, share in RINGS]

rim_height, rim_share = RINGS[-1]
for inward, share in zip((0.34, 0.58, 0.82), CAP_RINGS):
    made = []
    for step in range(STEPS):
        degrees = step * 360.0 / STEPS
        length = radius(degrees) * rim_share * share
        made.append(bm.verts.new((
            length * math.cos(math.radians(degrees)) * SCALE_X,
            length * math.sin(math.radians(degrees)) * SCALE_Y,
            grass_height(degrees, inward))))
    rings.append(made)

middle = bm.verts.new((0.0, 0.0, grass_height(0.0, 1.0)))

for lower, upper in zip(rings, rings[1:]):
    for step in range(STEPS):
        following = (step + 1) % STEPS
        bm.faces.new((lower[step], lower[following], upper[following], upper[step]))

innermost = rings[-1]
for step in range(STEPS):
    following = (step + 1) % STEPS
    bm.faces.new((innermost[step], innermost[following], middle))

# The underside, so the skirt is closed rather than an open tube.
bm.faces.new(tuple(reversed(rings[0])))

# Deforming a coast moves its middle: a bay on one side and a headland on
# the other left the bounding box centred 7.9 studs east and 14.4 north of
# the world origin, and the game places everything from that origin. So the
# shape is built first and slid onto centre afterwards, in X and Y only --
# the heights are absolute, because sea level is z = 0.
xs = [vertex.co.x for vertex in bm.verts]
ys = [vertex.co.y for vertex in bm.verts]
drift_x = (min(xs) + max(xs)) / 2.0
drift_y = (min(ys) + max(ys)) / 2.0
for vertex in bm.verts:
    vertex.co.x -= drift_x
    vertex.co.y -= drift_y

bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
bmesh.ops.triangulate(bm, faces=bm.faces[:])

mesh = bpy.data.meshes.new(NAME)
bm.to_mesh(mesh)
bm.free()
for polygon in mesh.polygons:
    polygon.use_smooth = False
mesh.update()

island = bpy.data.objects.new(NAME, mesh)
bpy.context.scene.collection.objects.link(island)
'''
