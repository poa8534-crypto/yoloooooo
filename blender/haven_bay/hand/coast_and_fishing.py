# Shoreline and Fishing Coast, by hand.
import math

kit.pier("Dock_FishingJetty", (148, -42), (200, -42), width=8, deck_z=2.5, coll="14_FISHING", railings=False)
for n, (x, y) in enumerate(((196, -36), (196, -48), (150, -58)), 1):
    kit.platform(f"Fishing_Spot_{n:02d}", x, y, 4, 4, z=2.5, coll="14_FISHING", legs=(n == 3))

isl = kit.island()
rocks = [kit.rock_mesh(0, 4), kit.rock_mesh(1, 6), kit.rock_mesh(2, 3)]
kit.scatter(rocks, "Coast_Boulder", (120, -80), 110, 14, coll="09_PROPS", seed=21, min_inland=-2,
            max_inland=12, spacing=14, keep_out=[(140, -50, 210, -34)])
kit.scatter([kit.palm_mesh(0), kit.palm_mesh(1), kit.palm_mesh(2)], "Tree_Palm", (0, 0), 200, 44,
            min_inland=3, max_inland=20, spacing=20, seed=22, keep_out=[(-25, -185, 25, -95), (140, -50, 210, -34)])
kit.scatter([kit.log_mesh()], "Driftwood", (60, -110), 120, 8, coll="09_PROPS", seed=23, min_inland=1,
            max_inland=14, spacing=18, keep_out=[(-25, -185, 25, -95)])
kit.scatter([kit.shell_mesh()], "Beach_Shell", (0, 0), 200, 40, coll="09_PROPS", seed=24, min_inland=1,
            max_inland=12, spacing=10, keep_out=[(-25, -185, 25, -95)])
# Driftwood names as the brief asks: Driftwood_01.. rather than scatter's _001.
import bpy
for obj in list(bpy.data.objects):
    if obj.name.startswith("Driftwood_") and len(obj.name) == len("Driftwood_001"):
        obj.name = f"Driftwood_{int(obj.name[-3:]):02d}"

for n, (x, y) in enumerate(((118, -118), (128, -108)), 1):
    kit.place(kit.umbrella_mesh(n), f"Beach_Umbrella_{n:02d}", x, y, "09_PROPS")
    kit.place(kit.beach_chair_mesh(), f"Beach_Chair_{n:02d}", x + 3, y - 4, "09_PROPS", rz=0.4)
kit.npc_spot("NPC_Villager_Beach", 110, -120)
