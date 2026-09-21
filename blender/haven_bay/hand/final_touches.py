# The last round of storytelling props, and soft edges on every path.
import math
import random

import bpy

# -- The freshwater pond on the bluff: lily pads and smooth river stones, so
# it reads as the freshwater fishing spot (Golden Trout, Catfish, Frogs).
# The pond's layout position, not its object's: positions read back out of the
# scene are already spread, and handing one to the kit would move it twice.
px, py = 6.0, 118.0
pond = bpy.data.objects.get("Forest_Pond")
surface = pond.location.z if pond is not None else kit.ground(px + 11, py) - 0.4
pads = kit.Part()
for k, (dx, dy, r) in enumerate(((-3, 2, 1.4), (2.5, -3, 1.1), (3.5, 3, 1.2))):
    pads.cylinder(dx, dy, 0.08, r, 0.12, "leaf_light", sides=8)
    if k == 0:
        pads.blob(dx + 0.3, dy, 0.4, 0.4, "flower_pink")
kit.obj("Forest_Pond_LilyPads", pads.finish("Forest_Pond_LilyPads"), "13_FOREST", px, py, surface)
stones = kit.Part()
rng = random.Random(7)
for k in range(5):
    a = rng.uniform(0, 2 * math.pi)
    stones.blob(math.cos(a) * 10.5, math.sin(a) * 10.5, 0.2, rng.uniform(0.9, 1.4), "rock", squash=0.45, stretch=1.4)
kit.obj("Forest_Pond_RiverStones", stones.finish("Forest_Pond_RiverStones"), "13_FOREST", px, py, kit.ground(px + 12, py))
kit.npc_spot("Fishing_Spot_Freshwater", px - 11, py - 4)

# -- The fishing jetty's end: a tackle box, a bait barrel and a hanging lantern.
tackle = kit.Part()
tackle.box(0, 0, 0.6, 2.4, 1.4, 1.2, "flower_red")
tackle.box(0, 0, 1.3, 2.5, 1.5, 0.2, "metal")
tackle.box(0, 0, 1.7, 1.0, 0.2, 0.6, "metal")
kit.obj("Jetty_TackleBox", tackle.finish("Jetty_TackleBox"), "14_FISHING", 196, -39.5, 2.5)
kit.place(kit.barrel_mesh(), "Jetty_BaitBarrel", 196.5, -44.5, "14_FISHING", z=2.5)
hang = kit.Part()
hang.box(0, 0, 3.6, 0.35, 0.35, 7.2, "wood_dark")
hang.box(0.8, 0, 7.0, 1.8, 0.3, 0.3, "wood_dark")
hang.box(1.5, 0, 6.1, 0.9, 0.9, 1.3, "lamp")
kit.obj("Jetty_HangingLantern", hang.finish("Jetty_HangingLantern"), "14_FISHING", 199.2, -38.4, 2.5, math.pi)

# -- The farm: a cast-iron hand pump over a wooden trough by the fence, where
# watering cans get refilled.
pump = kit.Part()
pump.box(0, 0, 0.9, 6, 2.2, 1.8, "wood")
pump.box(0, 0, 1.75, 5.4, 1.6, 0.1, "shallows")
pump.cylinder(-3.6, 0, 0, 0.45, 4.2, "black", sides=8)
pump.box(-3.0, 0, 3.7, 1.3, 0.3, 0.3, "black")
pump.box(-4.3, 0, 4.5, 0.25, 0.25, 1.8, "black", ry=0.6)
pump.blob(-3.6, 0, 4.3, 0.5, "black")
kit.obj("Farm_WaterPump", pump.finish("Farm_WaterPump"), "12_FARM", -46, 60, kit.ground(-46, 60), math.pi / 2)
kit.place(kit.crate_mesh(), "Farm_WateringCans", -46, 52, "12_FARM", rz=0.3)

# -- Soft path edges: every path strip gets small flat stepping stones and
# grass tufts breaking its border, so it reads as a worn trail, not a sticker.
bpy.context.view_layer.update()
for path in [o for o in bpy.data.objects if o.name.startswith("Path_") and not o.name.endswith("_Edge")]:
    world = [path.matrix_world @ v.co for v in path.data.vertices]
    if len(world) < 4:
        continue
    rng = random.Random(path.name)
    edge = kit.Part()
    for i in range(0, len(world), 2):
        v = world[i]
        if rng.random() < 0.55:
            ox, oy = rng.uniform(-1.2, 1.2), rng.uniform(-1.2, 1.2)
            if rng.random() < 0.5:
                edge.cylinder(v.x + ox, v.y + oy, v.z - 0.3, rng.uniform(0.6, 1.1), 0.45, "pebble", sides=6)
            else:
                edge.blob(v.x + ox, v.y + oy, v.z + 0.1, rng.uniform(0.5, 0.8), "grass", squash=0.7, jitter=0.2,
                          seed=i)
    if edge.bm.faces:
        kit.obj(f"{path.name}_Edge", edge.finish(f"{path.name}_Edge"), "03_ROADS_PATHS")
    else:
        edge.bm.free()
