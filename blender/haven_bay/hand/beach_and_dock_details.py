# Environmental storytelling on the beaches and the dock.
import math

import bpy

# Along the high-water line: driftwood, shell clusters and sea glass -- the
# things ForagingService lets players pick up, where they would wash up.
glass = kit.Part()
glass.blob(0, 0, 0.2, 0.45, "glass")
glass.blob(0.7, 0.4, 0.15, 0.3, "flower_pink")
glass.blob(-0.6, 0.5, 0.15, 0.3, "leaf_light")
glass_mesh = glass.finish("Kit_SeaGlass")
shells = kit.Part()
for k in range(4):
    a = k / 4 * 2 * math.pi
    shells.cylinder(math.cos(a) * 0.9, math.sin(a) * 0.9, 0, 0.55, 0.35, "cloth_white", sides=5, top=0.15)
shell_mesh = shells.finish("Kit_ShellCluster")
lane = [(-25, -190, 25, -90), (140, -50, 210, -34)]
kit.scatter([kit.log_mesh()], "Beach_Driftwood", (0, 0), 200, 14, coll="09_PROPS", seed=51, min_inland=1,
            max_inland=5, spacing=20, keep_out=lane)
kit.scatter([shell_mesh], "Beach_ShellCluster", (0, 0), 200, 24, coll="09_PROPS", seed=52, min_inland=1,
            max_inland=5, spacing=12, keep_out=lane)
kit.scatter([glass_mesh], "Beach_SeaGlass", (0, 0), 200, 20, coll="09_PROPS", seed=53, min_inland=0.5,
            max_inland=4, spacing=14, keep_out=lane)

# The dock: fish crates stacked by the barter shack.
crate = kit.fish_crate_mesh()
for n, (x, y, z_up) in enumerate(((-12, -112, 0.0), (-12, -109.5, 0.0), (-12, -110.7, 1.45)), 1):
    kit.place(crate, f"Dock_FishCrate_{n:02d}", x, y, "10_DOCKS", rz=0.1 * n,
              z=kit.ground(x, y) - 0.1 + z_up)

# Rope coiled round every mooring post, and life rings and hanging lanterns
# on a few of them.
posts = sorted((o for o in bpy.data.objects if o.name.startswith("Dock_MooringPost")), key=lambda o: o.name)
if not posts:
    posts = sorted((o for o in bpy.data.objects if o.name.startswith("Dock_Piling")), key=lambda o: o.name)
coils = kit.Part()
bpy.context.view_layer.update()
for post in posts:
    corners = [post.matrix_world @ kit.Vector(c) for c in post.bound_box]
    px = sum(c.x for c in corners) / 8
    py = sum(c.y for c in corners) / 8
    top = max(c.z for c in corners)
    kit.rope_coil(coils, px, py, top - 1.4, radius=1.25)
kit.obj("Dock_RopeCoils", coils.finish("Dock_RopeCoils"), "10_DOCKS")

lantern = kit.Part()
lantern.box(0, 0, 3.6, 0.3, 0.3, 7.2, "wood_dark")
lantern.box(0.8, 0, 7.0, 1.8, 0.3, 0.3, "wood_dark")
lantern.box(1.5, 0, 6.1, 0.9, 0.9, 1.3, "lamp")
lantern_mesh = lantern.finish("Kit_HangingLantern")
for n, (x, y, face) in enumerate(((-10.6, -128, -math.pi / 2), (10.6, -160, math.pi / 2)), 1):
    kit.life_ring(f"Dock_LifeRing_{n:02d}", x, y, 4.6, rz=face)
    kit.place(lantern_mesh, f"Dock_HangingLantern_{n:02d}", x, y + 6, "10_DOCKS", rz=face + math.pi / 2, z=3)
