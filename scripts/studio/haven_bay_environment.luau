--[[
	Haven Bay's environment, in one paste: the tropical sea, the light and the
	post-processing from the owner's spec.

	Run it ONCE in Roblox Studio's Command Bar (View > Command Bar), with the
	place open. It changes the place itself, so save afterwards. Running it
	again is safe: effects are replaced by name, not stacked.

	Our Studio bridge cannot do this for you on purpose -- it only builds
	scripts and a fixed list of instances, and has no way to touch Lighting
	or fill terrain -- so this is the one step done by hand.
]]

local Lighting = game:GetService("Lighting")
local Terrain = workspace.Terrain

-- The sea, split in two because the tide moves. Terrain water cannot rise
-- and fall, and TideCycleService moves IslandService's WaterPlane part to
-- make the tide. So terrain water is filled only up to LOW tide (Y = -6),
-- the deepest the sea ever gets, and the moving WaterPlane is the clear
-- surface above it between -6 and high tide at 0. At low tide the part meets
-- the terrain water and the sandbars (crests at -3) stand in the air.
local SIZE = 1400
local LOW_TIDE = -6
Terrain:FillBlock(CFrame.new(0, -28, 0), Vector3.new(SIZE, 8, SIZE), Enum.Material.Sand)
Terrain:FillBlock(CFrame.new(0, (LOW_TIDE - 24) / 2, 0), Vector3.new(SIZE, LOW_TIDE + 24, SIZE),
	Enum.Material.Water)

Terrain.WaterColor = Color3.fromRGB(80, 220, 240)
Terrain.WaterTransparency = 0.85
Terrain.WaterReflectance = 0.35
Terrain.WaterWaveSize = 0.1
Terrain.WaterWaveSpeed = 8

-- Warm tropical afternoon light with crisp shadows.
Lighting.Technology = Enum.Technology.Future
Lighting.Brightness = 2.8
Lighting.ClockTime = 14.5
Lighting.GeographicLatitude = 25
Lighting.OutdoorAmbient = Color3.fromRGB(115, 125, 140)

local function effect(className: string, props: { [string]: any })
	local existing = Lighting:FindFirstChildOfClass(className)
	if existing then
		existing:Destroy()
	end
	local made = Instance.new(className)
	for key, value in props do
		(made :: any)[key] = value
	end
	made.Parent = Lighting
	return made
end

effect("Atmosphere", { Haze = 2, Density = 0.28, Color = Color3.fromRGB(200, 230, 255) })
effect("ColorCorrectionEffect", { Saturation = 0.15, Contrast = 0.08 })
effect("BloomEffect", { Intensity = 0.35, Size = 24, Threshold = 0.85 })
effect("SunRaysEffect", { Intensity = 0.1, Spread = 0.8 })

print("Haven Bay environment applied: sea, Future lighting, atmosphere, colour, bloom, sun rays")
