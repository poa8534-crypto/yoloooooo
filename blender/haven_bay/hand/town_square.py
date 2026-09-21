# Town Square, by hand: the social hub at (0, 0), z 8.
import math

import bpy

kit.plaza("Plaza_TownSquare", 0, 0, 34)

# A terracotta ring laid on the cobbles.
ring = kit.Part()
top = kit.ground(0, 0) + 0.95
for k in range(16):
    a0, a1 = k / 16 * 2 * math.pi + 0.03, (k + 1) / 16 * 2 * math.pi - 0.03
    pts = [(math.cos(a0) * 20, math.sin(a0) * 20, top), (math.cos(a0) * 26, math.sin(a0) * 26, top),
           (math.cos(a1) * 26, math.sin(a1) * 26, top), (math.cos(a1) * 20, math.sin(a1) * 20, top)]
    ring.poly(pts, "terracotta", both_sides=True)
kit.obj("Plaza_Terracotta", ring.finish("Plaza_Terracotta"), "06_BUILDINGS")

kit.totem_bell("Totem_IslandBell", 0, 0)

# Path exits, as compass bearings (maths angles): the plots lie south-west
# and west, the farm north-west, the forest north, the sanctuary east, the
# dock south, the beach south-east.
EXITS = {
    "Sign_ToDock": -90, "Sign_ToPlots": 200, "Sign_ToFarm": 145, "Sign_ToForest": 90,
    "Sign_ToSanctuary": 42, "Sign_ToBeach": -40,
}
open_bearings = [math.radians(b) for b in EXITS.values()]


def clear_of_exits(a: float, margin: float = 0.28) -> bool:
    return all(abs((a - b + math.pi) % (2 * math.pi) - math.pi) > margin for b in open_bearings)


sign = kit.sign_mesh()
for name, bearing in EXITS.items():
    a = math.radians(bearing)
    x, y = math.cos(a) * 32, math.sin(a) * 32
    # Turned to face someone walking out along that path.
    kit.place(sign, name, x + math.cos(a + 0.35) * 5, y + math.sin(a + 0.35) * 5, "09_PROPS", rz=a + math.pi / 2)

lantern, bench, flowers = kit.lantern_mesh(), kit.bench_mesh(), kit.flowers_mesh(0)
placed = 0
for k in range(24):
    a = k / 24 * 2 * math.pi
    if not clear_of_exits(a):
        continue
    if placed % 3 == 0:
        kit.place(lantern, f"Square_Lantern_{placed // 3 + 1:02d}", math.cos(a) * 30, math.sin(a) * 30, "09_PROPS")
    elif placed % 3 == 1:
        # Benches face the totem.
        kit.place(bench, f"Square_Bench_{placed // 3 + 1:02d}", math.cos(a) * 29, math.sin(a) * 29, "09_PROPS",
                  rz=a - math.pi / 2)
    else:
        kit.place(flowers, f"Square_Flowers_{placed // 3 + 1:02d}", math.cos(a) * 30, math.sin(a) * 30, "09_PROPS")
    placed += 1

# Elder Rowan's bench, right beside the totem and facing it.
kit.place(bench, "Bench_ElderRowan", 7, -7, "09_PROPS", rz=math.atan2(7, -7) + math.pi)
kit.npc_spot("NPC_ElderRowan", 6, -4)
kit.npc_spot("NPC_Villager_Square", -10, 8)

# The crafting bench, where CraftingService expects it: Roblox (14, 8, -6),
# which is Blender (14, 6).
bench_top = kit.Part()
bench_top.box(0, 0, 3.0, 6, 3, 0.6, "wood_light")
for lx in (-2.6, 2.6):
    for ly in (-1.2, 1.2):
        bench_top.box(lx, ly, 1.35, 0.5, 0.5, 2.7, "wood_dark")
bench_top.box(0, 0, 1.0, 5.6, 2.6, 0.3, "wood")
bench_top.box(-1.5, 0.6, 3.6, 1.2, 0.8, 0.6, "metal")
bench_top.box(1.4, -0.4, 3.45, 1.6, 0.9, 0.3, "wood_dark")
kit.obj("Crafting_Bench", bench_top.finish("Crafting_Bench"), "09_PROPS", 14, 6, kit.ground(14, 6), 0.3)
