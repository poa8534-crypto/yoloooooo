# The low-tide sandbars and the reef, by hand.
#
# Each sandbar's crest sits at z -3: under the water at high tide (0),
# uncovered at low tide (-6) -- TideCycleService's range. On each: the finds
# ForagingService hands out (sea glass, pearls, a message in a bottle), a
# couple of rocks and one small palm that stands out of the water either way,
# so a player can spot the bar before the tide drops.
import random

import bpy

BARS = [(182, -108), (-196, 40), (-95, -180)]
for n, (x, y) in enumerate(BARS, 1):
    rng = random.Random(n)
    crest = kit.ground(x, y)
    part = kit.Part()
    for k in range(6):
        part.blob(rng.uniform(-8, 8), rng.uniform(-3, 3), 0.3, 0.5, "glass")
    for k in range(4):
        part.blob(rng.uniform(-8, 8), rng.uniform(-3, 3), 0.25, 0.3, "cloth_white")
    part.cylinder(2, 1, 0.3, 0.5, 1.6, "glass", sides=6, top=0.3, rx=1.4)
    part.cylinder(2, 2.4, 0.65, 0.22, 0.5, "wood", sides=5, rx=1.4)
    kit.obj(f"Sandbar_Finds_{n:02d}", part.finish(f"Sandbar_Finds_{n:02d}"), "02_OCEAN", x, y, crest)
    kit.place(kit.palm_mesh(n % 3), f"Sandbar_Palm_{n:02d}", x - 5, y + 1, "02_OCEAN", rz=n * 1.3, sink=0.3)
    for k in range(2):
        kit.place(kit.rock_mesh(k, 2 + k), f"Sandbar_Rock_{n:02d}_{k + 1}", x + 5 + k * 3, y - 2 + k * 3,
                  "02_OCEAN", rz=rng.uniform(0, 6.28), sink=0.4)

corals = [kit.coral_mesh(0), kit.coral_mesh(1), kit.coral_mesh(2)]
kit.scatter(corals, "Coral", (185, -30), 60, 12, coll="02_OCEAN", seed=31, min_inland=-30, max_inland=-6,
            spacing=6, avoid_paths=False, keep_out=[(140, -50, 210, -34)])
kit.scatter([kit.seaweed_mesh()], "Seaweed", (185, -30), 60, 12, coll="02_OCEAN", seed=32, min_inland=-30,
            max_inland=-6, spacing=5, avoid_paths=False, keep_out=[(140, -50, 210, -34)])
for obj in list(bpy.data.objects):
    if obj.name.startswith("Coral_") and len(obj.name) == len("Coral_001"):
        obj.name = f"Coral_{int(obj.name[-3:]):02d}"
