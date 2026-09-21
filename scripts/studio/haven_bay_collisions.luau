--[[
	After importing HavenBay_Island.fbx: make the ground walkable and the
	decoration step-through. Run once in Studio's Command Bar with the imported
	model selected (or named HavenBay_Island in Workspace), then save.

	Ground, stairs, docks, plots and buildings get precise collision so
	players walk on the real shapes; foliage, reef and small props stop
	colliding so nobody snags on a flower.
]]

local Selection = game:GetService("Selection")
local model = Selection:Get()[1] or workspace:FindFirstChild("HavenBay_Island")
assert(model, "select the imported island model first")

-- Paths are NOT solid: they lie a quarter stud over the grass, and joined
-- into one island-wide mesh their collision bridged the gaps between them,
-- so players floated. Walking on the ground under them looks the same.
local SOLID = { "Terrain_", "Stairs_", "Dock_", "Spawn_", "Plot_", "Building_", "Plaza_", "Totem_", "Barter_", "Crafting_", "Farm_WaterPump", "Garden_", "Grove_" }
local GHOST = { "Foliage_", "Props_", "NPC_", "Sandbar_Finds_", "Path" }

local function starts(name: string, list: { string }): boolean
	for _, prefix in list do
		if string.sub(name, 1, #prefix) == prefix then
			return true
		end
	end
	return false
end

local solid, ghost = 0, 0
for _, part in model:GetDescendants() do
	if part:IsA("MeshPart") then
		part.Anchored = true
		if starts(part.Name, GHOST) then
			part.CanCollide = false
			ghost += 1
		elseif starts(part.Name, SOLID) then
			part.CollisionFidelity = Enum.CollisionFidelity.PreciseConvexDecomposition
			part.CanCollide = true
			solid += 1
		end
	end
end
print(`Haven Bay collisions: {solid} solid, {ghost} step-through`)
