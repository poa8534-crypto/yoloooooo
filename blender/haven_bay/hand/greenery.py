# Final planting, by hand: soft framing that leaves paths, plots and the square open.
plots = [(-118, 92, 28), (-130, 5, 28), (-120, -75, 28), (-60, -125, 28), (0, 0, 40), (72, 20, 34)]
kit.scatter([kit.round_tree_mesh(0), kit.round_tree_mesh(1), kit.round_tree_mesh(2)], "Tree_Round", (0, 0), 170,
            18, seed=41, min_inland=25, spacing=16, max_height=9, keep_out=plots)
kit.scatter([kit.bush_mesh(0), kit.bush_mesh(1), kit.bush_mesh(2), kit.bush_mesh(3)], "Bush", (0, 0), 170, 50,
            seed=42, min_inland=15, spacing=8, keep_out=plots)
kit.scatter([kit.flowers_mesh(0), kit.flowers_mesh(1), kit.flowers_mesh(2)], "Flowers", (0, 0), 120, 30,
            seed=43, min_inland=20, spacing=7, keep_out=plots)
