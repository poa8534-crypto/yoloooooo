# Forest and Foraging Bluff, by hand: two tiers north of the square, a pond on
# the peak, a stream and two falls down to the sea, and the woods.
import math

kit.stairs("Stairs_Forest_Lower", (0, 30), (0, 56), width=9)
kit.stairs("Stairs_Forest_Upper", (-14, 62), (-14, 86), width=9)

kit.pond("Forest_Pond", 6, 118, radius=9)
kit.stream("Forest_Stream", [(6, 127), (4, 140), (0, 146)], width=4)
kit.waterfall("Forest_Waterfall_Upper", (0, 146, 24), 16, width=5, heading=math.pi / 2)
kit.stream("Forest_Stream_Lower", [(0, 150), (0, 158)], width=5)
kit.waterfall("Forest_Waterfall_Lower", (0, 160, 16), 6, width=6, heading=math.pi / 2)
kit.stream("Forest_Stream_ToSea", [(0, 166), (0, 172), (0, 180)], width=6)

keep = [(6, 118, 13), (-20, 125, 10), (0, 148, 8), (0, 45, 7), (-14, 74, 7), (0, 158, 6)]
kit.scatter([kit.pine_mesh(0), kit.pine_mesh(1), kit.pine_mesh(2)], "Tree_Pine", (0, 110), 58, 40,
            coll="13_FOREST", seed=11, keep_out=keep, min_height=15, spacing=8)
kit.scatter([kit.round_tree_mesh(0), kit.round_tree_mesh(1), kit.round_tree_mesh(2)], "Tree_Fruit", (0, 110), 58,
            30, coll="13_FOREST", seed=12, keep_out=keep, min_height=15, spacing=9)
kit.scatter([kit.rock_mesh(0, 3), kit.rock_mesh(1, 5), kit.rock_mesh(2, 4)], "Forest_Boulder", (0, 110), 58, 14,
            coll="13_FOREST", seed=13, keep_out=keep, min_height=15, spacing=10)
kit.scatter([kit.log_mesh()], "Forest_Log", (0, 110), 55, 6, coll="13_FOREST", seed=14, keep_out=keep,
            min_height=15, spacing=12)
kit.scatter([kit.mushroom_mesh()], "Forest_Mushrooms", (0, 110), 55, 12, coll="13_FOREST", seed=15,
            keep_out=keep, min_height=15, spacing=6)
kit.scatter([kit.fern_mesh()], "Forest_Fern", (0, 110), 58, 24, coll="13_FOREST", seed=16, keep_out=keep,
            min_height=15, spacing=5)
