# A straight, grand way in: from the Town Square up stone stairs to the
# Sanctuary's front doors, which face west at y = 20.
kit.stairs("Stairs_Sanctuary", (28, 20), (44, 20), width=12, colour="white_stone")
kit.path("Path_Sanctuary_Doors", [(44, 20), (50, 20), (54, 20)], width=10, colour="stone_path")
kit.path("Path_Square_Sanctuary", [(22, 20), (28, 20)], width=10, colour="stone_path")
lantern = kit.lantern_mesh()
for n, (x, y) in enumerate(((46, 27), (46, 13), (27, 27), (27, 13)), 1):
    kit.place(lantern, f"Sanctuary_Lantern_{n:02d}", x, y, "11_SANCTUARY")
flowers = kit.flowers_mesh(1)
for n, (x, y) in enumerate(((50, 28), (50, 12)), 1):
    kit.place(flowers, f"Sanctuary_Flowers_Front_{n:02d}", x, y, "11_SANCTUARY")
