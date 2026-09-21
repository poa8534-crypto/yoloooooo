# Player plots and the paths between everything, by hand.
import math

PLOTS = {"Plot_1": (-118, 92), "Plot_2": (-130, 5), "Plot_3": (-120, -75), "Plot_4": (-60, -125)}

for n, (name, (x, y)) in enumerate(PLOTS.items(), 1):
    dx, dy = -x, -y
    rz = math.atan2(dx, -dy)  # the gate (local -Y) faces the town square
    kit.plot(name, None, x, y, size=32, rz=rz, seed=n, fence_colour="cloth_white")
    c, s = math.cos(rz), math.sin(rz)
    # Beside the gate: local (6, -18) turned into the world.
    sx, sy = x + 6 * c + 18 * s, y + 6 * s - 18 * c
    kit.place(kit.sign_mesh(), f"{name}_Sign", sx, sy, "07_HOUSING", rz=rz)
    base = kit.Part()
    base.box(0, 0, 1.25, 1.4, 1.4, 2.5, "white_stone")
    mx, my = x + (-5) * c + 14.8 * s, y + (-5) * s - 14.8 * c
    kit.obj(f"{name}_MailboxBase", base.finish(f"{name}_MailboxBase"), "07_HOUSING", mx, my, kit.ground(mx, my), rz)


def gate(x, y, reach=21):
    d = math.hypot(x, y)
    return (x - x / d * reach, y - y / d * reach)


def edge(x, y, r=36):
    d = math.hypot(x, y)
    return (x / d * r, y / d * r)


kit.path("Path_Square_Dock", [(0, -34), (0, -60), (0, -80), (0, -98)], width=9)
for name, (x, y) in PLOTS.items():
    gx, gy = gate(x, y)
    ex, ey = edge(gx, gy)
    mx, my = (ex + gx) / 2, (ey + gy) / 2
    bend = 8 if x < -100 else -8
    kit.path(f"Path_Square_{name}", [(ex, ey), (mx + bend * 0.4, my + bend), (gx, gy)], width=8)
kit.path("Path_Square_Farm", [(-26, 21), (-44, 30), (-56, 38)], width=8, colour="dirt")
kit.path("Path_Square_Forest", [(0, 30), (0, 56), (-10, 60), (-14, 64), (-14, 86), (-10, 100)], width=8,
         colour="dirt")
kit.path("Path_Square_Sanctuary", [(34, 10), (40, 13)], width=9)
kit.path("Path_Square_Beach", [(30, -20), (70, -32), (110, -40), (146, -42)], width=8)
