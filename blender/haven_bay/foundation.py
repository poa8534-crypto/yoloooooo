# Haven Bay's foundation, written by hand: the ground and the water, and the
# light. Everything the zone passes stand on. `kit` and the island spec are
# loaded by the bridge's prelude; the spec's coordinates come from the game's
# own IslandService, converted to Blender (Blender y = -Roblox z).

kit.remove("Terrain_")
tiles = kit.build_terrain("Terrain_MainIsland", spacing=4.0)
kit.build_ocean(1500.0)
kit.daylight(camera_at=(0, -156, 9), look_at=(0, 0, 14))
print("foundation:", len(tiles), "terrain tiles")
