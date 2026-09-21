# The Sanctuary, by hand: a grand white-marble hall with cyan glass domes on
# the east cliff, 1.6 times its first size, its doors facing the town.
import math

SIZE = 1.6
kit.museum("Building_Sanctuary", 80, 20, rz=-math.pi / 2, width=34, depth=24, dome_colour="glass",
           wings=True, scale=SIZE)
floor = kit.ground(80, 20)

# Inside: the walk-in aquarium along the north wall, the terrarium along the
# south, and Barnaby's front desk just inside the doors.
aquarium = kit.Part()
aquarium.box(0, 0, 0.3, 14, 4, 0.6, "white_stone")
for k in range(3):
    aquarium.box(-4.5 + k * 4.5, 0, 3.2, 4, 3, 5, "glass")
    aquarium.blob(-4.5 + k * 4.5, 0, 3.0, 0.5, "flower_yellow", stretch=1.8)
kit.obj("Sanctuary_Aquarium", aquarium.finish("Sanctuary_Aquarium"), "11_SANCTUARY", 84, 32, floor)

terrarium = kit.Part()
terrarium.box(0, 0, 0.3, 14, 4, 0.6, "white_stone")
for k in range(3):
    terrarium.box(-4.5 + k * 4.5, 0, 1.2, 3.6, 3, 1.2, "dirt")
    terrarium.blob(-4.5 + k * 4.5, 0, 2.6, 1.3, ("leaf", "leaf_light", "flower_pink")[k])
kit.obj("Sanctuary_Terrarium", terrarium.finish("Sanctuary_Terrarium"), "11_SANCTUARY", 84, 8, floor)

desk = kit.Part()
desk.box(0, 0, 1.6, 2.4, 7, 3.2, "wood")
desk.box(0, 0, 3.3, 2.8, 7.4, 0.3, "wood_light")
desk.box(0, 2.5, 3.8, 1.2, 1.4, 0.8, "cloth_white")
kit.obj("Sanctuary_FrontDesk", desk.finish("Sanctuary_FrontDesk"), "11_SANCTUARY", 67, 20, floor)
kit.npc_spot("NPC_Barnaby", 69, 20, z=floor)

# Outside: statues flanking the approach, flowers, benches, trees behind.
kit.statue("Sanctuary_Statue_01", 50, 29, rz=-math.pi / 2)
kit.statue("Sanctuary_Statue_02", 50, 11, rz=-math.pi / 2)
flowers = kit.flowers_mesh(2)
bench = kit.bench_mesh()
for k, (x, y) in enumerate(((54, 34), (54, 6), (104, 40), (104, 0)), 1):
    kit.place(flowers, f"Sanctuary_Garden_{k:02d}", x, y, "11_SANCTUARY")
for k, (x, y, turn) in enumerate(((58, 38, math.pi), (58, 2, 0.0)), 1):
    kit.place(bench, f"Sanctuary_Bench_{k:02d}", x, y, "11_SANCTUARY", rz=turn)
kit.scatter([kit.round_tree_mesh(0), kit.palm_mesh(1)], "Tree_Sanctuary", (100, 20), 30, 6,
            seed=61, spacing=10, keep_out=[(80, 20, 22)], min_height=14)
