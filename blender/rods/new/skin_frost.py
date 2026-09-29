"""The Frost skin, on the base rod (run build_base_rod.py and textures.py first).

    blender -b --python blender/rods/new/skin_frost.py

Ice-blue lacquer covered in white braided knotwork, deep-navy grip and blank,
carved-ivory trim with engraved spirals, silver fittings, cyan ice-light.
Clear ice crystals burst from the receiver, icicles hang under the reel side,
a snowflake emblem sits in each side of the receiver inside a turning frost
ring, an icicle forms the tip. It moves: frost rings turn round the blank,
ice shards spiral and drift along it, and snow falls off the rod.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skinkit as kit  # noqa: E402

kit.start(seed=31)

ICE = (0.55, 0.9, 1.0)
kit.textured("Shell", "frost_knot.png", tile=0.6, metallic=0.1, rough=0.22)
kit.textured("Accent", "frost_bone.png", tile=0.35, metallic=0.0, rough=0.55)
kit.recolour("Dark", (0.006, 0.02, 0.06), 0.35, 0.2, specular=0.35)
kit.recolour("Metal", (0.8, 0.86, 0.92), 1.0, 0.14)
kit.recolour("Glow", (0.6, 0.92, 1.0), 0.0, 0.3, (0.35, 0.8, 1.0), 3.5)
kit.recolour("Line", (0.8, 0.95, 1.0), 0.0, 0.3, (0.6, 0.9, 1.0), 1.2)

CRYSTAL = kit.material("IceCrystal", (0.22, 0.58, 0.95), 0.05, 0.03)
kit.rim_glow(CRYSTAL, ICE, 2.2)
CRYSTAL_DEEP = kit.material("IceCrystalDeep", (0.06, 0.26, 0.78), 0.1, 0.04)
kit.rim_glow(CRYSTAL_DEEP, ICE, 2.2)
FLAKE = kit.material("Snowflake", (0.85, 0.97, 1.0), 0.0, 0.3, (0.55, 0.9, 1.0), 4.0)
SNOW = kit.material("Snow", (1, 1, 1), 0.0, 0.5, (0.85, 0.95, 1.0), 2.5)
FROST_SPARK = kit.material("FrostSpark", (0.6, 0.9, 1), 0.0, 0.3, (0.35, 0.8, 1.0), 5.0)
GLOW = kit.recolour("Glow", (0.6, 0.92, 1.0), 0.0, 0.3, (0.35, 0.8, 1.0), 3.5)

kit.hide("TipSpear", "StockGem", "ReceiverStripe")

# Ice crystals bursting out of the receiver: a crown swept back, like frost wings.
for i, (ang, z, length) in enumerate([(30, 2.3, 0.95), (62, 2.62, 0.75), (100, 2.9, 0.55), (145, 2.45, 0.45),
                                      (-30, 2.3, 0.95), (-62, 2.62, 0.75), (-100, 2.9, 0.55), (-145, 2.45, 0.45)]):
    a = math.radians(ang)
    out_dir = (math.cos(a), math.sin(a), 0)
    base = (out_dir[0] * 0.26, out_dir[1] * 0.26, z)
    kit.shard(f"FrostCrown{i}", base, (out_dir[0] * 0.7, out_dir[1] * 0.7, -0.5), length, 0.085,
              CRYSTAL if i % 2 == 0 else CRYSTAL_DEEP, vfx="float", sides=6)
# Icicles hanging on the reel side (-X is down in the hand), under the receiver and handguard.
for k, (z, length) in enumerate(((1.85, 0.28), (2.0, 0.36), (3.3, 0.22), (3.6, 0.3), (3.95, 0.24), (4.3, 0.18))):
    x = -0.3 if z < 3.1 else -0.14
    kit.shard(f"Icicle{k}", (x, 0.04 * (1 if k % 2 else -1), z), (-1, 0, -0.12), length, 0.03, CRYSTAL,
              vfx="static", sides=5)
# A snowflake emblem in each side of the receiver, ringed by a turning frost ring.
for side in (1, -1):
    c = (0.0, 0.205 * side, 2.55)
    for k in range(6):
        a = 2 * math.pi * k / 6
        u = (math.cos(a), 0, math.sin(a))
        tip = (c[0] + u[0] * 0.12, c[1], c[2] + u[2] * 0.12)
        kit.bar(f"Flake{side}_{k}", c, tip, 0.016, FLAKE)
        for s in (1, -1):
            b = a + s * 0.7
            mid = (c[0] + u[0] * 0.07, c[1], c[2] + u[2] * 0.07)
            end = (mid[0] + math.cos(b) * 0.04, c[1], mid[2] + math.sin(b) * 0.04)
            kit.bar(f"FlakeTwig{side}_{k}_{s}", mid, end, 0.01, FLAKE)
    kit.ring(f"FrostRing{side}", c, 0.16, 0.008, (0, side, 0), GLOW, vfx="orbit_side", dashes=6, gap=0.35)
# The icicle tip.
kit.shard("IcicleTip", (0, 0, 9.92), (0, 0, 1), 0.42, 0.04, CRYSTAL, vfx="static", sides=6)
# Frost rings round the blank.
kit.ring("FrostOrbitA", (0, 0, 5.5), 0.32, 0.01, (0, 0, 1), GLOW, vfx="orbit_axial", dashes=3, gap=0.4)
kit.ring("FrostOrbitB", (0, 0, 7.4), 0.24, 0.008, (0, 0, 1), GLOW, vfx="orbit_axial", dashes=5, gap=0.45)
kit.ring("FrostOrbitReel", (-0.58, 0, 2.52), 0.28, 0.008, (0, 0, 1), GLOW, vfx="orbit_axial", dashes=4, gap=0.4)
# Ice shards spiralling up the blank.
for k in range(12):
    t = k / 11
    z = 5.0 + t * 4.5
    a = k * math.radians(137.5)
    radius = 0.3 - 0.17 * t
    centre = (math.cos(a) * radius, math.sin(a) * radius, z)
    direction = (math.cos(a) * 0.3 - math.sin(a) * 0.4, math.sin(a) * 0.3 + math.cos(a) * 0.4, 1)
    kit.shard(f"FrostSpiral{k}", centre, direction, 0.3 - 0.16 * t, 0.045 - 0.025 * t,
              CRYSTAL if k % 2 else CRYSTAL_DEEP, vfx="float", sides=6)

kit.sparks(95, [SNOW, SNOW, FROST_SPARK], size=(0.007, 0.02))
kit.finish("frost")
