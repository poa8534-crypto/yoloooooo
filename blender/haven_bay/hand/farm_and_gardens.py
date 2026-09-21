# Farm and Botany Gardens, by hand, on the west terrace (x -102..-38, y 18..82, z 12).
import math

beds = [(-86, 70), (-66, 70), (-86, 52), (-66, 52), (-86, 34), (-66, 34)]
for n, (x, y) in enumerate(beds, 1):
    kit.crop_plot(f"Farm_Bed_{n:02d}", x, y, sx=16, sy=12, crop="leaf_light" if n <= 4 else None)
for n, x in enumerate((-76, -56), 1):
    kit.stream(f"Farm_Furrow_{n:02d}", [(x, 28), (x, 76)], width=1.5, coll="12_FARM")
kit.crop_plot("Garden_Flowers_01", -48, 56, sx=10, sy=10, crop="flower_pink")
kit.crop_plot("Garden_Flowers_02", -48, 42, sx=10, sy=10, crop="flower_yellow")
kit.hut("Building_PottingShed", -94, 78, size=9, wall="pastel_green", roof="roof_red", coll="12_FARM")
kit.well("Farm_Well", -48, 72)
kit.fence_run("Farm_Fence", [(-44, 22), (-100, 22), (-100, 80), (-40, 80), (-40, 36)], colour="wood")
for n, (x, y) in enumerate(((-96, 64), (-96, 60), (-58, 78)), 1):
    kit.place(kit.crate_mesh(), f"Farm_Crate_{n:02d}", x, y, "12_FARM", rz=n * 0.4)
kit.npc_spot("NPC_Flora", -60, 40)
