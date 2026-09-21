ISLAND HAVEN: HAVEN BAY -- master world specification (owner-authored; this is the layout of record)

VIBE
Vibrant tropical paradise, endless sunny vacation, cozy cartoon low-poly. Crystal-clear turquoise/cyan water (#50DCF0), warm golden-peach sand (#F2D299), rich emerald/lime foliage with soft gradients, sun-bleached cedar wood. Beveled low-poly with soft edges, pastel gradients, no sharp 90-degree razor edges. About 260 x 260 studs of island (the baseplate may be bigger), for 1 to 4 players on personal plots around a shared hub.

THE ZONES (Roblox coordinates in the spec; Blender y = -Roblox z, so Blender +Y is north)
1. Arrival Dock (spawn and transit), far south shore, Roblox Z 100 to 180, elevation +3. A wide wooden timber pier into deep turquoise water, mooring posts with coiled ropes, a colourful wooden motorboat docked at the end, the Spawn Point, and the Dockside Barter Stall: a rustic wooden shack with striped canvas awnings.
2. Central Town Square (social hub), island centre (0, 0), elevation +8. A circular cobblestone and terracotta plaza joining every path; the Island Totem Bell, a carved wooden tiki-style totem; street lanterns and wooden signposts to the Dock, Plots, Sanctuary and Forest.
3. The Sanctuary (museum and conservatories), elevated east cliff at Roblox (70, -20), elevation +16. A white-marble pavilion with grand cyan glass conservatory domes; inside, a walk-in Aquarium and a Botanical Terrarium; reached from the Town Square by a grand wooden staircase built into the stone cliff.
4. Player Housing Plots (the Settler Village), south-west and north-west meadows, elevation +6. Four flat, clearly marked plots, 32 x 32 to 40 x 40, with low white-picket fences, a stone mailbox and a plot sign; flat grass for furniture and cottages.
5. Farm and Botany Gardens, mid-west terrace at Roblox (-70, -50), elevation +12. Tilled garden beds with irrigation furrows; crop and flower plots.
6. Forest and Foraging Bluff, north mountain peak at Roblox (0, -100), elevation +24. Dense pine and tropical fruit trees, mossy boulders, a freshwater mountain pond with a small waterfall flowing down into the sea.
7. Shoreline and Fishing Coast, east and south-east sandy coastline. Gentle golden beaches with coconut palms and coastal boulders; a small secondary wooden fishing jetty over the reef.
8. Dynamic Sandbars (low-tide mystery), offshore 15 to 40 studs off the beach: 3 sandy islets at elevation -3, submerged at high tide (0), exposed at low tide (-6).

NPC STANDS: Barnaby the curator at the Sanctuary front desk; Captain Finn at the Barter Stall; Flora in the Farm and Gardens; Elder Rowan on a bench beside the Totem Bell; Captain Cleo's houseboat berth at the end of the pier; resident villagers at the Town Square, the beach and the docks.

TECHNICAL
Elevation steps: water 0, beach and plots +6, plaza and farms +8 to +12, sanctuary +16, peak +24. Roblox water transparency 0.85, colour (80, 220, 240); Lighting Technology Future, warm sun brightness 2.5. Low poly, flat shaded, triangulated, under 8,000 triangles per object, modular named objects, reused meshes, 1 Blender unit = 1 stud. No railway, no train.

ROBLOX STUDIO IMPORT SETTINGS (owner's list; applied when the model is brought into Studio)
- Ocean: no mesh plane. Smooth Terrain water with WaterTransparency 0.85, WaterColor (80, 220, 240), WaterWaveSize 0.1. The Blender Ocean_* meshes are preview only and stay out of the export.
- Lighting.Technology = Future.
- Atmosphere: Haze 1.5, Density 0.3 (soft warm tropical horizon).
- ColorCorrectionEffect: Saturation 0.1, Contrast 0.05.

GAMEPLAY SPOTS THE ART PROVIDES (Blender coordinates; Roblox z = -Blender y)
- Freshwater fishing: Forest_Pond on the bluff peak at (6, 118), surface about z 23.6, with Fishing_Spot_Freshwater on its south-west bank. FishingService's freshwater pool (Golden Trout, Catfish, Frogs) belongs here.
- Ocean fishing: the arrival pier end and sides, and Fishing_Spot_01..03 on the east jetty.
- Low-tide sandbars, crests at z -3: (182, -108) south-east by the jetty, (-95, -180) south-west, (-196, 40) west.
- Farm water: Farm_WaterPump and trough at (-46, 60), where watering cans refill (BotanyService).
