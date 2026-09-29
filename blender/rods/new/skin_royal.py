"""The Royal skin, on the base rod (run build_base_rod.py and textures.py first).

    blender -b --python blender/rods/new/skin_royal.py

Crimson lacquer with a gold filigree lattice, midnight-navy grip and blank,
polished gold trim, iridescent violet-blue panels with white flourishes on the
receiver and the stock, cut sapphires set in gold bezels on the receiver,
rubies on the stock and along the rail, a gold crown at the tip. It moves:
gold halo rings turn round the receiver and the blank, loose gems drift beside
the rod, and gold dust (with a few red sparks) rises off it.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skinkit as kit  # noqa: E402

kit.start(seed=21)

GOLD = (1.0, 0.7, 0.26)
kit.textured("Shell", "royal_filigree.png", tile=0.55, metallic=0.25, rough=0.3)
kit.recolour("Dark", (0.006, 0.01, 0.045), 0.35, 0.22, specular=0.35)
kit.recolour("Accent", GOLD, 1.0, 0.16)
kit.recolour("Metal", (0.78, 0.52, 0.18), 1.0, 0.24)
kit.recolour("Glow", (1.0, 0.75, 0.35), 0.0, 0.3, (1.0, 0.62, 0.18), 2.6)
kit.recolour("Line", (1.0, 0.85, 0.5), 0.0, 0.3, (1.0, 0.7, 0.3), 1.0)

panel = kit.material("Panel", (0.4, 0.2, 0.9), 0.35, 0.15)
kit.assign(["ReceiverPanel", "StockPanel"], panel)
kit.textured(panel, "royal_panel.png", tile=0.45, metallic=0.35, rough=0.15, emission=0.35)

SAPPHIRE = kit.material("Sapphire", (0.04, 0.12, 0.85), 0.0, 0.04)
kit.rim_glow(SAPPHIRE, (0.35, 0.55, 1.0), 4.0)
RUBY = kit.material("Ruby", (0.75, 0.02, 0.08), 0.0, 0.04)
kit.rim_glow(RUBY, (1.0, 0.25, 0.3), 4.0)
GOLD_M = kit.recolour("Accent", GOLD, 1.0, 0.16)
DUST = kit.material("GoldDust", (1, 0.75, 0.3), 0.0, 0.3, (1.0, 0.62, 0.15), 3.5)
EMBER = kit.material("RedSpark", (1, 0.3, 0.3), 0.0, 0.3, (1.0, 0.12, 0.15), 4.0)

kit.hide("StockGlow", "TipSpear", "StockGem", "ReceiverStripe")

# Sapphires on both sides of the receiver, each in a gold bezel with a halo.
for side in (1, -1):
    c = (0.0, 0.2 * side, 2.55)
    kit.gem(f"ReceiverSapphire{side}", c, 0.1, (0, side, 0), SAPPHIRE)
    kit.ring(f"Bezel{side}", c, 0.11, 0.014, (0, side, 0), GOLD_M, vfx="static", dashes=1, gap=0)
    kit.ring(f"Halo{side}", (0.0, 0.205 * side, 2.55), 0.17, 0.006, (0, side, 0), GOLD_M, vfx="orbit_side", dashes=6, gap=0.5)
    # Rubies on the stock, and a pair in the grip's crown band.
    kit.gem(f"StockRuby{side}", (0.0, 0.19 * side, 0.15), 0.06, (0, side, 0), RUBY)
    kit.ring(f"StockBezel{side}", (0.0, 0.19 * side, 0.15), 0.066, 0.009, (0, side, 0), GOLD_M, vfx="static", dashes=1, gap=0)
# Rubies set along the top rail.
for k, z in enumerate((3.35, 3.8, 4.25, 4.7)):
    x = 0.255 - 0.035 * max(0, z - 3.6)
    kit.gem(f"RailRuby{k}", (x, 0.0, z), 0.032, (1, 0, 0), RUBY)

# A gold crown at the tip: five prongs round a ruby.
tip = 10.0
for k in range(5):
    a = 2 * math.pi * k / 5
    base = (math.cos(a) * 0.035, math.sin(a) * 0.035, tip - 0.05)
    kit.shard(f"CrownProng{k}", base, (math.cos(a) * 0.35, math.sin(a) * 0.35, 1), 0.16, 0.018, GOLD_M,
              vfx="static", sides=4)
kit.gem("CrownRuby", (0, 0, tip + 0.05), 0.04, (0, 0, 1), RUBY)

# Halo rings that turn round the receiver and the blank.
kit.ring("ReceiverHalo", (0, 0, 2.45), 0.48, 0.008, (0, 0, 1), GOLD_M, vfx="orbit_axial", dashes=8, gap=0.5)
kit.ring("BlankHaloA", (0, 0, 5.4), 0.3, 0.008, (0, 0, 1), GOLD_M, vfx="orbit_axial", dashes=4, gap=0.4)
kit.ring("BlankHaloB", (0, 0, 7.5), 0.22, 0.007, (0, 0, 1), GOLD_M, vfx="orbit_axial", dashes=3, gap=0.4)

# Loose gems drifting beside the rod.
for k, (a, z, r) in enumerate(((40, 2.3, 0.62), (160, 2.9, 0.55), (260, 1.9, 0.6), (90, 4.9, 0.42),
                                (220, 6.2, 0.36), (320, 7.9, 0.3))):
    ang = math.radians(a)
    kit.gem(f"FloatGem{k}", (math.cos(ang) * r, math.sin(ang) * r, z), 0.05 - 0.004 * k,
            (math.cos(ang), math.sin(ang), 0.4), SAPPHIRE if k % 2 else RUBY, vfx="float")

kit.sparks(85, [DUST, DUST, EMBER])
kit.finish("royal")
