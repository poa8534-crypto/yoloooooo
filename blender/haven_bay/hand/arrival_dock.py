# The Arrival Dock, by hand: the pier, the spawn, mooring posts, the motorboat,
# the Dockside Barter Stall, and where Captain Finn and Captain Cleo stand.
import math

kit.pier("Dock_Arrival", (0, -100), (0, -180), width=20, deck_z=3, railings=False)

spawn = kit.Part()
spawn.box(0, 0, 0.3, 12, 12, 0.6, "pastel_yellow")
spawn.box(0, 0, 0.62, 12.6, 12.6, 0.06, "white_stone")
spawn.box(0, 0, 0.64, 11, 11, 0.06, "pastel_yellow")
kit.obj("Spawn_ArrivalDock", spawn.finish("Spawn_ArrivalDock"), "10_DOCKS", 0, -150, 3.0)

pad = kit.Part()
pad.box(0, 0, 0.25, 8, 8, 0.5, "wood_dark")
kit.obj("Barter_StagingPad", pad.finish("Barter_StagingPad"), "10_DOCKS", 0, -120, 3.0)

# Mooring posts with coiled rope on top, both edges of the pier.
n = 0
for y in (-112, -132, -152, -172):
    for x in (-10.8, 10.8):
        n += 1
        post = kit.Part()
        post.cylinder(0, 0, -13, 1.0, 18.5, "wood_dark", sides=8)
        post.cylinder(0, 0, 5.5, 1.15, 0.4, "wood", sides=8)
        for k in range(3):
            post.cylinder(0, 0, 3.8 + k * 0.35, 1.35, 0.3, "cloth_white", sides=10)
        kit.obj(f"Dock_MooringPost_{n:02d}", post.finish(f"Dock_MooringPost_{n:02d}"), "10_DOCKS", x, y, 0.0)

# A colourful motorboat at the end of the pier.
kit.place(kit.boat_mesh(16, "train_red"), "Boat_Motor", 16, -174, "10_DOCKS", rz=math.pi / 2, z=0.3)
cabin = kit.Part()
cabin.box(0, 0, 3.4, 5.2, 4.4, 3.2, "cloth_white")
cabin.box(0, 0, 5.2, 6.0, 5.2, 0.5, "teal_roof")
cabin.box(0, -2.25, 3.8, 3.8, 0.2, 1.4, "glass")
kit.obj("Boat_Motor_Cabin", cabin.finish("Boat_Motor_Cabin"), "10_DOCKS", 16, -176, 0.3, math.pi / 2)

# The Dockside Barter Stall on the shore beside the pier head.
kit.hut("Building_BarterStall", -20, -116, rz=math.pi / 2, size=9, wall="wood_light", roof="cloth_red",
        coll="10_DOCKS")
kit.stall("Barter_Stall_Counter", -11, -116, rz=math.pi / 2, colour="cloth_blue", coll="10_DOCKS")
for k, (x, y) in enumerate(((-13, -110), (-13, -122), (-15, -123))):
    kit.place(kit.crate_mesh(), f"Barter_Crate_{k + 1:02d}", x, y, "09_PROPS", rz=k * 0.5)

kit.npc_spot("NPC_CaptainFinn", -10, -122)
kit.npc_spot("NPC_CaptainCleo_Berth", -6, -176, z=3)
kit.npc_spot("NPC_Villager_Dock", 6, -130, z=3)

lantern, barrel = kit.lantern_mesh(), kit.barrel_mesh()
for k, y in enumerate((-105, -125, -145, -165)):
    kit.place(lantern, f"Dock_Lantern_{k + 1:02d}", 8.5 if k % 2 else -8.5, y, "10_DOCKS", z=3)
for k, (x, y) in enumerate(((7.5, -112), (7.5, -115), (-7.5, -160))):
    kit.place(barrel, f"Dock_Barrel_{k + 1:02d}", x, y, "09_PROPS", z=3)
