--!strict

--[[
	What a spec is given: a way to load a game module, and a fake Player.

	Modules here are written for the Roblox instance tree -- `require(script.Parent.X)`,
	services reached through the generated Services module -- and neither exists
	under Lune. So `script` and `require` are supplied, resolving to the files on
	disk, and the services are stubs a spec can inspect.

	Nothing here is a Roblox emulator. It covers what these modules actually use.
	A module reaching for something absent fails loudly rather than passing
	quietly, which is the point of the check.
]]

local fs = require("@lune/fs")
local luau = require("@lune/luau")

local harness = {}

-- A player is a UserId to every module in this project.
function harness.player(userId: number): any
	-- Said here rather than inside string.format, whose own complaint names
	-- this line and not the call that was wrong.
	if type(userId) ~= "number" then
		error(
			string.format(
				"harness.player expects a numeric UserId, e.g. harness.player(1); got %s (%s). "
					.. "The player's name is derived from it: read .Name if you need one.",
				typeof(userId),
				tostring(userId)
			),
			2
		)
	end
	return { UserId = userId, Name = string.format("Player_%d", userId) }
end

local function signal(): any
	local handlers: { (...any) -> () } = {}
	return {
		Connect = function(_self: any, handler: (...any) -> ()): any
			table.insert(handlers, handler)
			return { Disconnect = function() end }
		end,
		fire = function(...)
			for _, handler in handlers do
				handler(...)
			end
		end,
	}
end

--[[
	An Instance, as far as this project uses one.

	Children are registered when something is parented into it, so
	`remote.Parent = folder` then `folder:FindFirstChild("remote")` behaves the
	way the server code expects. A RemoteEvent additionally carries the signal a
	handler connects to, and `fire` on that signal is how a spec plays the part
	of a client.
]]
-- Classes that count as a BasePart for IsA, so a module asking "is this a
-- part?" of a Part, a SpawnLocation or a MeshPart gets the answer Roblox gives.
local BASE_PARTS = { Part = true, SpawnLocation = true, MeshPart = true, WedgePart = true }

function harness.instance(className: string, name: string?): any
	local children: { [string]: any } = {}
	local attributes: { [string]: any } = {}
	local self: any = {
		ClassName = className,
		Name = name or className,
		__children = children,
	}
	self.FindFirstChild = function(_self: any, key: string): any
		return children[key]
	end
	self.WaitForChild = function(_self: any, key: string): any
		return children[key]
	end
	self.GetChildren = function(_self: any): { any }
		local out = {}
		for _, child in children do
			table.insert(out, child)
		end
		return out
	end
	self.GetDescendants = function(_self: any): { any }
		local out = {}
		for _, child in children do
			table.insert(out, child)
			for _, below in child:GetDescendants() do
				table.insert(out, below)
			end
		end
		return out
	end
	self.FindFirstChildWhichIsA = function(_self: any, wanted: string): any
		for _, child in children do
			if child:IsA(wanted) then
				return child
			end
		end
		return nil
	end
	self.IsA = function(_self: any, wanted: string): boolean
		return wanted == className
			or wanted == "Instance"
			or (wanted == "BasePart" and BASE_PARTS[className] == true)
			or (wanted == "LuaSourceContainer" and (className == "Script" or className == "ModuleScript"))
	end
	self.GetAttribute = function(_self: any, key: string): any
		return attributes[key]
	end
	self.SetAttribute = function(_self: any, key: string, value: any)
		attributes[key] = value
	end
	self.Destroy = function(_self: any)
		self.Parent = nil
	end
	if className == "RemoteEvent" or className == "RemoteFunction" then
		self.OnServerEvent = signal()
		self.OnClientEvent = signal()
		self.FireClient = function(_self: any, _player: any, ...) end
		self.FireAllClients = function(_self: any, ...) end
		self.FireServer = function(_self: any, ...) end
	end
	-- Parent is never stored on the table itself, so every assignment -- a
	-- second parent, or nil to take it out of the world -- comes through here
	-- and the old parent forgets it. Several systems remove a prop by
	-- unparenting it, so a stand-in that only handled the first parenting
	-- would report a removed prop as still standing.
	setmetatable(self, {
		__index = function(_table: any, key: string): any
			if key == "Parent" then
				return rawget(self, "__parent")
			end
			return nil
		end,
		__newindex = function(table_: any, key: string, value: any)
			if key == "Parent" then
				local old = rawget(table_, "__parent")
				local leaf = rawget(table_, "Name")
				if old ~= nil and old.__children[leaf] == table_ then
					old.__children[leaf] = nil
				end
				rawset(table_, "__parent", value)
				if value ~= nil then
					value.__children[leaf] = table_
				end
				return
			end
			rawset(table_, key, value)
		end,
	})
	return self
end

-- A character standing at a position: a Model whose HumanoidRootPart is there.
-- Modules find a player's position the way they would in a live game, through
-- `player.Character`, so a spec puts one there rather than faking the lookup.
function harness.character(player: any, position: any): any
	local model = harness.instance("Model", player.Name)
	local root = harness.instance("Part", "HumanoidRootPart")
	root.Position = position
	root.Parent = model
	model.PrimaryPart = root
	player.Character = model
	return model
end

-- The services the generated module exposes, as far as this code uses them.
function harness.services(): any
	-- Who is in the server. A spec that needs "everyone here" puts players in
	-- `list`; GetPlayers hands back a copy, as Roblox does.
	local players: any = {
		PlayerAdded = signal(),
		PlayerRemoving = signal(),
		list = {},
	}
	players.GetPlayers = function(_self: any): { any }
		return table.clone(players.list)
	end
	players.GetPlayerByUserId = function(_self: any, userId: number): any
		for _, player in players.list do
			if player.UserId == userId then
				return player
			end
		end
		return nil
	end
	return {
		Players = players,
		DataModel = {
			JobId = "",
			BindToClose = function(_self: any, _callback: () -> ()) end,
		},
		ReplicatedStorage = harness.instance("ReplicatedStorage"),
		-- Where anything the server puts in the world goes; without it a
		-- module cannot put a single part into the world.
		Workspace = harness.instance("Workspace"),
		-- Tags, as far as this project uses them: an object flagged for the
		-- client to find, and the list of what carries a tag.
		CollectionService = (function()
			local tagged: { [string]: { [any]: boolean } } = {}
			return {
				AddTag = function(_self: any, object: any, tag: string)
					tagged[tag] = tagged[tag] or {}
					tagged[tag][object] = true
				end,
				RemoveTag = function(_self: any, object: any, tag: string)
					if tagged[tag] then
						tagged[tag][object] = nil
					end
				end,
				HasTag = function(_self: any, object: any, tag: string): boolean
					return tagged[tag] ~= nil and tagged[tag][object] == true
				end,
				GetTagged = function(_self: any, tag: string): { any }
					local out = {}
					for object in tagged[tag] or {} do
						table.insert(out, object)
					end
					return out
				end,
			}
		end)(),
		-- Filtering is Roblox's to do; here it passes text through unchanged,
		-- which is what lets a spec check where the text went.
		TextService = {
			FilterStringAsync = function(_self: any, text: string, _userId: number): any
				return {
					GetNonChatStringForBroadcastAsync = function(_result: any): string
						return text
					end,
				}
			end,
		},
		-- Ids, not randomness: unique and repeatable, which is what a spec
		-- wants from an id.
		HttpService = (function()
			local issued = 0
			return {
				GenerateGUID = function(_self: any, _braces: boolean?): string
					issued += 1
					return string.format("00000000-0000-4000-8000-%012d", issued)
				end,
			}
		end)(),
	}
end

--[[
	A stand-in for `script`, and the tree above it.

	The Rojo project maps src/server to ServerScriptService.Server and src/shared
	to ReplicatedStorage.Shared, so `script.Parent.Parent.Parent` from a server
	module is the DataModel and the walk back down has to work.

	Every node is built eagerly, before anything indexes it. Lune's `fs` calls
	yield, and a yield inside an `__index` metamethod is
	`attempt to yield across metamethod/C-call boundary` -- so the tree cannot be
	discovered lazily as it is walked.
]]
local function folder(path: string?): any
	local self: any = { __path = path, __services = false, __children = {} }
	if path ~= nil and fs.isDir(path) then
		for _, entry in fs.readDir(path) do
			local child = path .. "/" .. entry
			if fs.isFile(child) and string.match(entry, "%.luau$") then
				local name = string.gsub(entry, "%.luau$", "")
				self.__children[name] = {
					__path = child,
					__services = child == "src/shared/Services.luau" or child == "src/client/Services.luau",
					__children = {},
				}
			end
		end
	end
	return self
end

local function link(parent: any, name: string, child: any)
	parent.__children[name] = child
	child.__parent = parent
end

function harness.tree(directory: string): any
	local shared = folder("src/shared")
	local server = folder("src/server")
	local client = folder("src/client")
	local replicated = folder(nil)
	local serverScriptService = folder(nil)
	-- The client folder sits under StarterPlayerScripts in the sourcemap and
	-- under PlayerScripts in a live game. The harness models the sourcemap,
	-- which is safe here precisely because the guard refuses any client require
	-- that walks above the folder, so nothing a spec loads can tell them apart.
	local starterPlayerScripts = folder(nil)
	local starterPlayer = folder(nil)
	local root = folder(nil)

	link(replicated, "Shared", shared)
	link(serverScriptService, "Server", server)
	link(starterPlayerScripts, "Client", client)
	link(starterPlayer, "StarterPlayerScripts", starterPlayerScripts)
	link(root, "ReplicatedStorage", replicated)
	link(root, "ServerScriptService", serverScriptService)
	link(root, "StarterPlayer", starterPlayer)

	local here = shared
	if directory == "src/server" then
		here = server
	elseif directory == "src/client" then
		here = client
	end
	local script = folder(nil)
	script.__parent = here

	-- One metatable for the whole tree, reading only what is already in memory.
	local walk
	walk = function(node: any): any
		setmetatable(node, {
			__index = function(_t, key: string): any
				if key == "Parent" then
					-- Walked on the way out too: a parent handed back raw has
					-- no metatable, so the next `.Parent` in the chain is nil.
					return if node.__parent ~= nil then walk(node.__parent) else nil
				end
				local child = node.__children[key]
				return if child ~= nil then walk(child) else nil
			end,
		})
		return node
	end
	return walk(script)
end

-- Vector3, as far as this project uses one: built, added, subtracted, scaled
-- and measured. Several modules lay their world out with Vector3.new at module
-- scope, so without it nothing that requires them can be loaded at all.
local vector3 = (function()
	local meta = {}
	local function make(x: number, y: number, z: number): any
		return setmetatable({ X = x, Y = y, Z = z }, meta)
	end
	meta.__index = function(self: any, key: string): any
		if key == "Magnitude" then
			return math.sqrt(self.X * self.X + self.Y * self.Y + self.Z * self.Z)
		end
		if key == "Unit" then
			local length = self.Magnitude
			if length == 0 then
				return make(0, 0, 0)
			end
			return make(self.X / length, self.Y / length, self.Z / length)
		end
		if key == "Dot" then
			return function(a: any, b: any): number
				return a.X * b.X + a.Y * b.Y + a.Z * b.Z
			end
		end
		return nil
	end
	meta.__add = function(a: any, b: any): any
		return make(a.X + b.X, a.Y + b.Y, a.Z + b.Z)
	end
	meta.__sub = function(a: any, b: any): any
		return make(a.X - b.X, a.Y - b.Y, a.Z - b.Z)
	end
	meta.__mul = function(a: any, b: any): any
		if type(a) == "number" then
			return make(a * b.X, a * b.Y, a * b.Z)
		end
		if type(b) == "number" then
			return make(a.X * b, a.Y * b, a.Z * b)
		end
		return make(a.X * b.X, a.Y * b.Y, a.Z * b.Z)
	end
	meta.__div = function(a: any, b: any): any
		if type(b) == "number" then
			return make(a.X / b, a.Y / b, a.Z / b)
		end
		return make(a.X / b.X, a.Y / b.Y, a.Z / b.Z)
	end
	meta.__unm = function(a: any): any
		return make(-a.X, -a.Y, -a.Z)
	end
	meta.__eq = function(a: any, b: any): boolean
		return a.X == b.X and a.Y == b.Y and a.Z == b.Z
	end
	meta.__tostring = function(a: any): string
		return `{a.X}, {a.Y}, {a.Z}`
	end
	return {
		new = function(x: number?, y: number?, z: number?): any
			return make(x or 0, y or 0, z or 0)
		end,
		zero = make(0, 0, 0),
		one = make(1, 1, 1),
	}
end)()

-- CFrame, as a position and a turn about the vertical axis. Composing two adds
-- the positions and the turns, which is all the world building here asks of
-- it; a spec reads `.Position`, never an orientation matrix.
local cframe = (function()
	local meta = {}
	local function make(position: any, yaw: number): any
		return setmetatable({ Position = position, Yaw = yaw }, meta)
	end
	meta.__mul = function(a: any, b: any): any
		if getmetatable(b) == meta then
			return make(a.Position + b.Position, a.Yaw + b.Yaw)
		end
		return a.Position + b
	end
	meta.__add = function(a: any, b: any): any
		return make(a.Position + b, a.Yaw)
	end
	return {
		new = function(x: any, y: number?, z: number?): any
			if type(x) == "table" then
				return make(x, 0)
			end
			return make(vector3.new(x or 0, y or 0, z or 0), 0)
		end,
		Angles = function(_x: number, y: number, _z: number): any
			return make(vector3.zero, y or 0)
		end,
		fromEulerAnglesYXZ = function(_x: number, y: number, _z: number): any
			return make(vector3.zero, y or 0)
		end,
		lookAt = function(from: any, _at: any): any
			return make(from, 0)
		end,
	}
end)()

-- The globals a module may use. Deliberately short: anything missing is a
-- module reaching for something this project has not needed before.
function harness.environment(): any
	return {
		assert = assert,
		error = error,
		ipairs = ipairs,
		next = next,
		pairs = pairs,
		pcall = pcall,
		print = print,
		select = select,
		tonumber = tonumber,
		tostring = tostring,
		type = type,
		typeof = typeof,
		unpack = unpack,
		setmetatable = setmetatable,
		getmetatable = getmetatable,
		rawget = rawget,
		math = math,
		os = os,
		string = string,
		table = table,
		warn = print,
		Instance = {
			new = function(className: string): any
				return harness.instance(className)
			end,
		},
		-- Roblox's seeded generator, as far as this project uses it. A linear
		-- congruential generator is enough: what the specs check is that the
		-- same seed gives the same rolls, not the quality of the randomness.
		Vector3 = vector3,
		CFrame = cframe,
		-- Colours, positions and the enum names this project reaches for. Values
		-- rather than behaviour: nothing here is checked by a spec, but a module
		-- that builds one while loading cannot be required without it.
		Color3 = {
			new = function(r: number?, g: number?, b: number?): any
				return { R = r or 0, G = g or 0, B = b or 0 }
			end,
			fromRGB = function(r: number?, g: number?, b: number?): any
				return { R = (r or 0) / 255, G = (g or 0) / 255, B = (b or 0) / 255 }
			end,
			fromHex = function(hex: string): any
				local cleaned = string.gsub(hex, "^#", "")
				local value = tonumber(cleaned, 16) or 0
				return {
					R = (math.floor(value / 65536) % 256) / 255,
					G = (math.floor(value / 256) % 256) / 255,
					B = (value % 256) / 255,
				}
			end,
		},
		UDim2 = {
			new = function(sx: number?, ox: number?, sy: number?, oy: number?): any
				return { ScaleX = sx or 0, OffsetX = ox or 0, ScaleY = sy or 0, OffsetY = oy or 0 }
			end,
			fromScale = function(sx: number?, sy: number?): any
				return { ScaleX = sx or 0, OffsetX = 0, ScaleY = sy or 0, OffsetY = 0 }
			end,
			fromOffset = function(ox: number?, oy: number?): any
				return { ScaleX = 0, OffsetX = ox or 0, ScaleY = 0, OffsetY = oy or 0 }
			end,
		},
		-- Every enum reads as its own name, so `Enum.Material.Basalt` is a value
		-- that compares equal to itself and prints legibly when a case fails.
		Enum = setmetatable({}, {
			__index = function(_enums: any, category: string): any
				return setmetatable({}, {
					__index = function(_items: any, item: string): any
						return { EnumType = category, Name = item, Value = item }
					end,
				})
			end,
		}),
		Random = {
			new = function(seed: number?): any
				local state = (seed or 0) % 2147483647
				if state <= 0 then
					state += 2147483646
				end
				local function step(): number
					state = (state * 16807) % 2147483647
					return (state - 1) / 2147483646
				end
				return {
					NextNumber = function(_self: any, minimum: number?, maximum: number?): number
						local value = step()
						if minimum == nil or maximum == nil then
							return value
						end
						return minimum + value * (maximum - minimum)
					end,
					NextInteger = function(_self: any, minimum: number, maximum: number): number
						return minimum + math.floor(step() * (maximum - minimum + 1))
					end,
				}
			end,
		},
		task = {
			wait = function(_seconds: number?) end,
			spawn = function(callback: (...any) -> (), ...)
				callback(...)
			end,
			delay = function(_seconds: number, callback: (...any) -> (), ...)
				callback(...)
			end,
			defer = function(callback: (...any) -> (), ...)
				callback(...)
			end,
		},
	}
end

local loaded: { [string]: any } = {}

--[[
	Load a module by its path from the repository root, e.g.
	"src/server/SalvageNode.luau". Its own requires resolve the same way, so a
	module that requires a sibling gets the real sibling.
]]
function harness.load(path: string, services: any?): any
	if loaded[path] then
		return loaded[path]
	end
	local resolved = services or harness.services()
	local directory = string.match(path, "^(.*)/[^/]+$") or "."

	local environment = harness.environment()
	environment.require = function(target: any): any
		if type(target) == "table" and rawget(target, "__services") then
			return resolved
		end
		if type(target) == "table" and rawget(target, "__path") then
			return harness.load(rawget(target, "__path"), resolved)
		end
		error("the harness cannot resolve that require: " .. tostring(target))
	end
	environment.script = harness.tree(directory)

	local module = luau.load(fs.readFile(path), { environment = environment })()
	loaded[path] = module
	return module
end

function harness.reset()
	table.clear(loaded)
end

return harness
