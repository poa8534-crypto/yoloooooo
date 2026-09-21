# The south lawn between the dock and the square: a village green instead of
# an empty field. Winding pebble paths, a noticeboard at the dock exit, a
# fountain, a wishing well, picnic tables under trees, flowerbeds and shrubs
# along the walks.
import math

# Winding walks in pebble, replacing the straight strips.
dock_walk = [(0, -98), (4, -88), (8, -76), (5, -64), (-2, -52), (-4, -42), (0, -34)]
kit.path("Path_Square_Dock", dock_walk, width=9, colour="pebble")
beach_walk = [(28, -22), (48, -36), (72, -34), (96, -46), (122, -40), (146, -42)]
kit.path("Path_Square_Beach", beach_walk, width=8, colour="pebble")

kit.noticeboard("Noticeboard_Island", 14, -92, rz=math.pi - 0.3)
kit.fountain("Lawn_Fountain", -28, -62, radius=6)
kit.path("Path_Lawn_Fountain", [(-2, -56), (-14, -60), (-22, -62)], width=5, colour="pebble")
kit.well("Lawn_WishingWell", 32, -64, coll="09_PROPS")
kit.path("Path_Lawn_Well", [(6, -66), (20, -66), (28, -65)], width=5, colour="pebble")

table = kit.picnic_table_mesh()
tree = [kit.round_tree_mesh(0), kit.round_tree_mesh(1)]
for n, (x, y, turn) in enumerate(((-34, -84, 0.3), (38, -86, -0.4), (-44, -42, 1.2)), 1):
    kit.place(table, f"Lawn_Picnic_{n:02d}", x, y, "09_PROPS", rz=turn)
    kit.place(tree[n % 2], f"Lawn_ShadeTree_{n:02d}", x + 6, y + 5, "08_VEGETATION", rz=turn * 3)

# Flowerbeds and shrubs flanking the dock walk, both sides, every few steps.
flower_meshes = [kit.flowers_mesh(0), kit.flowers_mesh(1), kit.flowers_mesh(2)]
bush_meshes = [kit.bush_mesh(0), kit.bush_mesh(1)]
n = 0
for (ax, ay), (bx, by) in zip(dock_walk, dock_walk[1:]):
    heading = math.atan2(by - ay, bx - ax)
    nx, ny = -math.sin(heading), math.cos(heading)
    mx, my = (ax + bx) / 2, (ay + by) / 2
    for side in (-1, 1):
        n += 1
        mesh = flower_meshes[n % 3] if n % 2 else bush_meshes[n % 2]
        kit.place(mesh, f"Lawn_Bed_{n:02d}", mx + nx * side * 8, my + ny * side * 8, "08_VEGETATION",
                  rz=n * 0.7)
