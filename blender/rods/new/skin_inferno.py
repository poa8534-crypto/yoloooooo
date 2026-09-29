"""The Inferno skin, on the base rod (run build_base_rod.py and textures.py first).

    blender -b --python blender/rods/new/skin_inferno.py

Red chrome armour in a honeycomb whose cells glow white-hot in clusters,
crimson-black grip and blank, rose-gold trim and fittings, molten-orange light.
Swept flame blades fan back from the receiver and the handguard, a row of
glowing hex studs runs down the stock, and a three-pronged claw holds an ember
crystal at the tip. It moves: ember shards spiral and drift up the blank,
molten rings turn round it, and embers rise off the rod.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skinkit as kit  # noqa: E402

kit.start(seed=41)

ROSE_GOLD = (0.95, 0.33, 0.2)
FIRE = (1.0, 0.32, 0.06)
kit.textured("Shell", "inferno_hex.png", tile=1.1, metallic=0.75, rough=0.18, emission=2.2,
             glow_image="inferno_hex_glow.png")
kit.recolour("Dark", (0.14, 0.01, 0.015), 0.7, 0.2, specular=0.4)
ACCENT = kit.recolour("Accent", ROSE_GOLD, 1.0, 0.22)
kit.recolour("Metal", (0.85, 0.38, 0.26), 1.0, 0.2)
GLOW = kit.recolour("Glow", (1.0, 0.45, 0.15), 0.0, 0.3, FIRE, 5.0)
kit.recolour("Line", (1.0, 0.6, 0.35), 0.0, 0.3, (1.0, 0.45, 0.2), 1.5)

CHROME = kit.material("RedChrome", (0.55, 0.015, 0.02), 1.0, 0.12)
kit.rim_glow(CHROME, (1.0, 0.25, 0.1), 1.8)
kit.assign(["ReceiverPanel", "StockPanel"], CHROME)
EMBER = kit.material("EmberCrystal", (1.0, 0.4, 0.1), 0.0, 0.1, (1.0, 0.3, 0.05), 4.0)
HOT = kit.material("HexStud", (1.0, 0.85, 0.85), 0.0, 0.3, (1.0, 0.6, 0.6), 6.0)
SPARK_A = kit.material("EmberSpark", (1, 0.5, 0.2), 0.0, 0.3, (1.0, 0.3, 0.02), 3.0)
SPARK_B = kit.material("EmberSparkRed", (1, 0.2, 0.2), 0.0, 0.3, (1.0, 0.1, 0.04), 3.0)

kit.hide("TipSpear", "StockGem", "ReceiverStripe")

# Flame blades fanning back off the receiver's top and the handguard: flat,
# sharp and swept towards the butt, in red chrome with hot edges behind them.
for side in (1, -1):
    for k, (z, length, spread) in enumerate(((2.95, 0.9, 38), (2.55, 0.75, 55), (2.15, 0.6, 72))):
        a = math.radians(spread) * side
        base = (0.2 * math.cos(a), 0.2 * math.sin(a), z)
        direction = (math.cos(a) * 0.55, math.sin(a) * 0.55, -1)
        kit.shard(f"FlameBlade{side}_{k}", base, direction, length, 0.08, CHROME, vfx="static", sides=3)
    for k, z in enumerate((4.35, 3.85)):
        a = math.radians(65) * side
        kit.shard(f"GuardBlade{side}_{k}", (0.1 * math.cos(a), 0.1 * math.sin(a), z),
                  (math.cos(a) * 0.45, math.sin(a) * 0.45, -1), 0.55 - 0.1 * k, 0.035, CHROME, vfx="static", sides=3)
# Glowing hex studs down both sides of the stock and the grip.
for side in (1, -1):
    for k, z in enumerate((0.0, 0.12, 0.24)):
        for j, x in enumerate((-0.07, 0.07)):
            kit.gem(f"StockStud{side}_{k}_{j}", (x + (0.035 if k % 2 else 0), 0.185 * side, z), 0.03, (0, side, 0),
                    HOT, facets=6)
# The claw at the tip: three curved prongs around an ember crystal.
tip = 9.98
for k in range(3):
    a = 2 * math.pi * k / 3
    kit.shard(f"Claw{k}", (math.cos(a) * 0.03, math.sin(a) * 0.03, tip - 0.08),
              (math.cos(a) * 0.5, math.sin(a) * 0.5, 1), 0.22, 0.022, ACCENT, vfx="static", sides=4, lean=-0.35)
kit.shard("TipEmber", (0, 0, tip - 0.02), (0, 0, 1), 0.3, 0.035, EMBER, vfx="static", sides=6)
# Molten rings turning round the reel and the blank.
kit.ring("MoltenReel", (-0.58, 0, 2.52), 0.28, 0.009, (0, 0, 1), GLOW, vfx="orbit_axial", dashes=4, gap=0.4)
kit.ring("MoltenA", (0, 0, 5.5), 0.33, 0.011, (0, 0, 1), GLOW, vfx="orbit_axial", dashes=3, gap=0.4)
kit.ring("MoltenB", (0, 0, 7.4), 0.24, 0.009, (0, 0, 1), GLOW, vfx="orbit_axial", dashes=5, gap=0.45)
for side in (1, -1):
    kit.ring(f"CoreRing{side}", (0.0, 0.2 * side, 2.55), 0.15, 0.008, (0, side, 0), GLOW, vfx="orbit_side",
             dashes=3, gap=0.3)
    kit.gem(f"Core{side}", (0.0, 0.2 * side, 2.55), 0.09, (0, side, 0), EMBER, facets=6)
# Ember shards spiralling up the blank.
for k in range(12):
    t = k / 11
    z = 5.0 + t * 4.5
    a = k * math.radians(137.5)
    radius = 0.3 - 0.17 * t
    centre = (math.cos(a) * radius, math.sin(a) * radius, z)
    direction = (math.cos(a) * 0.3 - math.sin(a) * 0.4, math.sin(a) * 0.3 + math.cos(a) * 0.4, 1)
    kit.shard(f"EmberSpiral{k}", centre, direction, 0.3 - 0.16 * t, 0.04 - 0.02 * t,
              EMBER if k % 3 else CHROME, vfx="float", sides=4)

kit.sparks(95, [SPARK_A, SPARK_A, SPARK_B], size=(0.007, 0.02))
kit.finish("inferno")
