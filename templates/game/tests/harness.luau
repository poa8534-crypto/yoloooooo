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
function harness.instance(className: string, name: string?): any
	local children: { [string]: any } = {}
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
	self.IsA = function(_self: any, wanted: string): boolean
		return wanted == className
	end
	self.Destroy = function(_self: any)
		local parent = rawget(self, "__parent")
		if parent ~= nil then
			parent.__children[self.Name] = nil
		end
	end
	if className == "RemoteEvent" or className == "RemoteFunction" then
		self.OnServerEvent = signal()
		self.FireClient = function(_self: any, _player: any, ...) end
		self.FireAllClients = function(_self: any, ...) end
	end
	setmetatable(self, {
		__newindex = function(table_: any, key: string, value: any)
			if key == "Parent" and value ~= nil then
				rawset(table_, "__parent", value)
				value.__children[rawget(table_, "Name")] = table_
			end
			rawset(table_, key, value)
		end,
	})
	return self
end

-- The services the generated module exposes, as far as this code uses them.
function harness.services(): any
	return {
		Players = {
			PlayerRemoving = signal(),
			GetPlayers = function(): { any }
				return {}
			end,
		},
		DataModel = {
			JobId = "",
			BindToClose = function(_self: any, _callback: () -> ()) end,
		},
		ReplicatedStorage = harness.instance("ReplicatedStorage"),
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
					__services = child == "src/shared/Services.luau"
						or child == "src/client/Services.luau",
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

-- The globals a module may use. Deliberately short: anything missing is a
-- module reaching for something this project has not needed before.
function harness.environment(): any
	return {
		assert = assert, error = error, ipairs = ipairs, next = next, pairs = pairs,
		pcall = pcall, print = print, select = select, tonumber = tonumber,
		tostring = tostring, type = type, typeof = typeof, unpack = unpack,
		setmetatable = setmetatable, getmetatable = getmetatable, rawget = rawget,
		math = math, os = os, string = string, table = table, warn = print,
		Instance = {
			new = function(className: string): any
				return harness.instance(className)
			end,
		},
		-- Roblox's seeded generator, as far as this project uses it. A linear
		-- congruential generator is enough: what the specs check is that the
		-- same seed gives the same rolls, not the quality of the randomness.
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
