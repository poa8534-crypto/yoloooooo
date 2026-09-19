--!strict

--[[
	Runs every `tests/*.spec.luau` under Lune and exits non-zero if any fails.

	The five checks in scripts/verify.ps1 read the code; not one of them runs it.
	SalvageNode passed all five while reporting a node full immediately after it
	was harvested and empty once it had refilled -- inverted at both ends, and
	invisible to a type checker. This is the check that executes.

	A spec is a function taking the harness and returning a table of named
	cases, so nothing is reached through a global and a spec cannot quietly
	depend on another spec having run.
]]

local fs = require("@lune/fs")
local luau = require("@lune/luau")
local process = require("@lune/process")

local harness = require("./harness")

local failures = 0
local checks = 0

for _, entry in fs.readDir("tests") do
	if not string.match(entry, "%.spec%.luau$") then
		continue
	end
	local name = string.gsub(entry, "%.spec%.luau$", "")
	local environment = harness.environment()
	local factory = luau.load(fs.readFile("tests/" .. entry), { environment = environment })()
	if type(factory) ~= "function" then
		failures += 1
		print(string.format("   FAIL  %s: a spec must return a function taking the harness", name))
		continue
	end

	for label, case in factory(harness) do
		checks += 1
		local ok, err = pcall(case)
		if ok then
			print(string.format("   PASS  %s: %s", name, label))
		else
			failures += 1
			print(string.format("   FAIL  %s: %s", name, label))
			print(string.format("         %s", tostring(err)))
		end
	end
end

print("")
if failures > 0 then
	print(string.format("%d of %d behaviour check(s) failed.", failures, checks))
	process.exit(1)
end
print(string.format("%d behaviour check(s) passed.", checks))
