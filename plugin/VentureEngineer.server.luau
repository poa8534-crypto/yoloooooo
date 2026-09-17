--!nonstrict
--[[
	Venture Engineer -- the Studio plugin.

	This is the execution adapter. The backend decides what to build, the bridge
	carries it here, and this file is the only thing that touches the DataModel.

	It polls a loopback HTTP service exactly as Rojo's plugin does, which is the
	one integration route Studio supports for a plugin talking to a local
	process. There is no keyboard automation and no screen-coordinate clicking
	anywhere in here: every change is made through a documented API, so a build
	either applies or reports why it did not.

	It applies a CLOSED SET of operations. There is no operation that carries
	Lua for this plugin to load and run, and adding one would turn a localhost
	port into a way to execute code inside someone's Studio.

	--!nonstrict rather than --!strict: a plugin runs against the real Roblox
	API surface with no sourcemap and no definitions file, and strict mode here
	produces errors about Studio's own types rather than about this code. The
	generated game code is held to --!strict; this is the harness.
]]

local HttpService = game:GetService("HttpService")
local ScriptEditorService = game:GetService("ScriptEditorService")
local RunService = game:GetService("RunService")
local Selection = game:GetService("Selection")

--[[
	Fetched when it is needed, not at load.

	`game:GetService` throws for a service this Studio build does not have, and a
	throw at the top of a plugin means no toolbar, no panel, and no clue why --
	the plugin simply is not in the Plugins menu. Anything optional is looked up
	inside a pcall at the point of use.
]]
local function studioTestService(): Instance?
	local ok, service = pcall(function()
		return game:GetService("StudioTestService")
	end)
	return if ok then service else nil
end

local PLUGIN_VERSION = "0.1.0"
local PROTOCOL_VERSION = 1
local DEFAULT_BRIDGE = "http://127.0.0.1:34873"
local HEARTBEAT_SECONDS = 5
-- Settings are per-install, not per-place: the token pairs this machine's
-- Studio with this machine's bridge, and re-entering it for every place would
-- be the kind of friction that gets a security control turned off.
local TOKEN_KEY = "VentureEngineer_Token"
local URL_KEY = "VentureEngineer_BridgeUrl"

local toolbar = plugin:CreateToolbar("Venture Engineer")
local toggleButton = toolbar:CreateButton(
	"VentureEngineerPanel",
	"Connect this place to the Venture Engineer bridge",
	"rbxasset://textures/ui/GuiImagePlaceholder.png",
	"Venture Engineer"
)

local widget = plugin:CreateDockWidgetPluginGui(
	"VentureEngineerPanel",
	DockWidgetPluginGuiInfo.new(Enum.InitialDockState.Right, false, false, 320, 260, 280, 200)
)
widget.Title = "Venture Engineer"

-- ---------------------------------------------------------------- state ----

local connected = false
local running = false
local lastError = ""
local statusText = "Not connected"

local function bridgeUrl(): string
	return plugin:GetSetting(URL_KEY) or DEFAULT_BRIDGE
end

local function token(): string
	return plugin:GetSetting(TOKEN_KEY) or ""
end

-- ------------------------------------------------------------------ ui -----

local root = Instance.new("Frame")
root.Size = UDim2.fromScale(1, 1)
root.BackgroundColor3 = Color3.fromRGB(28, 28, 30)
root.BorderSizePixel = 0
root.Parent = widget

local padding = Instance.new("UIPadding")
padding.PaddingTop = UDim.new(0, 12)
padding.PaddingLeft = UDim.new(0, 12)
padding.PaddingRight = UDim.new(0, 12)
padding.Parent = root

local layout = Instance.new("UIListLayout")
layout.Padding = UDim.new(0, 8)
layout.SortOrder = Enum.SortOrder.LayoutOrder
layout.Parent = root

local function label(text: string, order: number, colour: Color3?): TextLabel
	local item = Instance.new("TextLabel")
	item.Size = UDim2.new(1, 0, 0, 20)
	item.BackgroundTransparency = 1
	item.Font = Enum.Font.Code
	item.TextSize = 13
	item.TextXAlignment = Enum.TextXAlignment.Left
	item.TextColor3 = colour or Color3.fromRGB(220, 220, 225)
	item.Text = text
	item.LayoutOrder = order
	item.Parent = root
	return item
end

local statusLabel = label("Not connected", 1, Color3.fromRGB(240, 170, 90))
local placeLabel = label("", 2, Color3.fromRGB(150, 150, 160))
local errorLabel = label("", 3, Color3.fromRGB(240, 120, 120))
errorLabel.TextWrapped = true
errorLabel.Size = UDim2.new(1, 0, 0, 40)

local tokenBox = Instance.new("TextBox")
tokenBox.Size = UDim2.new(1, 0, 0, 28)
tokenBox.PlaceholderText = "Paste the bridge pairing token"
tokenBox.Text = token()
tokenBox.Font = Enum.Font.Code
tokenBox.TextSize = 12
tokenBox.TextColor3 = Color3.fromRGB(235, 235, 240)
tokenBox.BackgroundColor3 = Color3.fromRGB(44, 44, 48)
tokenBox.BorderSizePixel = 0
tokenBox.ClearTextOnFocus = false
tokenBox.LayoutOrder = 4
tokenBox.Parent = root

local connectButton = Instance.new("TextButton")
connectButton.Size = UDim2.new(1, 0, 0, 30)
connectButton.Text = "Connect"
connectButton.Font = Enum.Font.GothamMedium
connectButton.TextSize = 13
connectButton.TextColor3 = Color3.fromRGB(255, 255, 255)
connectButton.BackgroundColor3 = Color3.fromRGB(58, 110, 220)
connectButton.BorderSizePixel = 0
connectButton.LayoutOrder = 5
connectButton.Parent = root

local function render()
	statusLabel.Text = statusText
	statusLabel.TextColor3 = if connected then Color3.fromRGB(120, 210, 140) else Color3.fromRGB(240, 170, 90)
	placeLabel.Text = if connected then `place: {game.Name}` else ""
	errorLabel.Text = lastError
	connectButton.Text = if running then "Disconnect" else "Connect"
end

-- --------------------------------------------------------------- http ------

local function request(method: string, path: string, body: any): (boolean, any)
	local options = {
		Url = bridgeUrl() .. path,
		Method = method,
		Headers = {
			["Content-Type"] = "application/json",
			["X-Bridge-Token"] = token(),
		},
	}
	if body ~= nil then
		options.Body = HttpService:JSONEncode(body)
	end

	local ok, response = pcall(function()
		return HttpService:RequestAsync(options)
	end)
	if not ok then
		-- The message here is almost always "Http requests are not enabled",
		-- which is a setting, not a bug, so it is shown verbatim.
		return false, tostring(response)
	end
	if not response.Success then
		return false, `{response.StatusCode} {response.StatusMessage}: {tostring(response.Body):sub(1, 300)}`
	end
	if response.Body == nil or response.Body == "" then
		return true, nil
	end
	local decoded, parsed = pcall(function()
		return HttpService:JSONDecode(response.Body)
	end)
	if not decoded then
		return false, "the bridge sent something that is not JSON"
	end
	return true, parsed
end

-- ------------------------------------------------------- the DataModel -----

local ALLOWED_ROOTS = {
	ReplicatedStorage = true,
	ServerScriptService = true,
	ServerStorage = true,
	StarterGui = true,
	StarterPlayer = true,
	Workspace = true,
	Lighting = true,
	SoundService = true,
	ReplicatedFirst = true,
}

local function split(path: string): { string }
	local parts = {}
	for part in string.gmatch(path, "[^/]+") do
		table.insert(parts, part)
	end
	return parts
end

--[[
	Resolve a path, creating missing Folders along the way when asked.

	The root check is repeated from the backend on purpose. This side is the one
	holding the DataModel, and a bridge is only as trustworthy as everything
	that can reach it.
]]
local function resolve(path: string, createMissing: boolean): (Instance?, string?)
	local parts = split(path)
	if #parts < 2 then
		return nil, `{path}: a build never writes a service itself, only inside one`
	end
	if not ALLOWED_ROOTS[parts[1]] then
		return nil, `{path}: {parts[1]} is not a service this plugin will write into`
	end

	local ok, service = pcall(function()
		return game:GetService(parts[1])
	end)
	if not ok or service == nil then
		return nil, `{path}: could not reach {parts[1]}`
	end

	local current: Instance = service
	for index = 2, #parts - 1 do
		local name = parts[index]
		local child = current:FindFirstChild(name)
		if child == nil then
			if not createMissing then
				return nil, `{path}: {name} does not exist`
			end
			local folder = Instance.new("Folder")
			folder.Name = name
			folder.Parent = current
			child = folder
		end
		current = child
	end
	return current, nil
end

local function leafName(path: string): string
	local parts = split(path)
	return parts[#parts]
end

--[[
	Rebuild a Roblox value from the wire.

	`{0, 5, 0}` on its own could be a Vector3, a Color3 or a size, so values
	that are not scalars arrive tagged: `{type = "Vector3", value = {0, 5, 0}}`.
	An unknown tag is an error rather than a guess, because a guess here writes
	the wrong thing into a place and looks like it worked.
]]
local function decodeValue(value: any): (any, string?)
	if type(value) ~= "table" then
		return value, nil
	end
	local tag = value.type
	local parts = value.value
	if tag == nil or parts == nil then
		return nil, 'a table value must be tagged, e.g. {type = "Vector3", value = {0, 5, 0}}'
	end
	if tag == "Vector3" then
		return Vector3.new(parts[1], parts[2], parts[3]), nil
	elseif tag == "Color3" then
		return Color3.fromRGB(parts[1], parts[2], parts[3]), nil
	elseif tag == "UDim2" then
		return UDim2.new(parts[1], parts[2], parts[3], parts[4]), nil
	elseif tag == "CFrame" then
		return CFrame.new(parts[1], parts[2], parts[3]), nil
	end
	return nil, `{tostring(tag)} is not a value type this plugin rebuilds`
end

local function setProperty(instance: Instance, property: string, value: any): string?
	local decoded, problem = decodeValue(value)
	if problem ~= nil then
		return `could not set {property}: {problem}`
	end
	-- Material and Font arrive as names; the Enum they belong to is decided by
	-- the property, which is the only place that knows.
	if property == "Material" and type(decoded) == "string" then
		decoded = (Enum.Material :: any)[decoded]
	elseif property == "Font" and type(decoded) == "string" then
		decoded = (Enum.Font :: any)[decoded]
	end
	local ok, err = pcall(function()
		(instance :: any)[property] = decoded
	end)
	if not ok then
		return `could not set {property}: {tostring(err)}`
	end
	return nil
end

--[[
	Write a script's source.

	`ScriptEditorService:UpdateSourceAsync` is the supported route and is what
	keeps an open editor tab in step with the change; assigning `.Source`
	directly leaves a tab showing stale text. It is wrapped because the API
	errors rather than returning false when the script is not open to it, and a
	failed source write has to be reported rather than silently skipped.
]]
local function writeSource(target: LuaSourceContainer, source: string): string?
	local ok, err = pcall(function()
		ScriptEditorService:UpdateSourceAsync(target, function()
			return source
		end)
	end)
	if ok then
		return nil
	end
	local fallbackOk, fallbackErr = pcall(function()
		(target :: any).Source = source
	end)
	if fallbackOk then
		return nil
	end
	return `could not write source: {tostring(err)} / {tostring(fallbackErr)}`
end

local applyOperation

local function applyCreateInstance(operation: any): (string, string, boolean)
	local parent, err = resolve(operation.path, true)
	if parent == nil then
		return "failed", err or "unknown path", false
	end
	local name = leafName(operation.path)
	local existing = parent:FindFirstChild(name)
	if existing ~= nil then
		if existing.ClassName ~= operation.className then
			return "failed",
				`{operation.path} already exists as a {existing.ClassName}, not a {operation.className}`,
				false
		end
		-- Idempotent: a retried batch after a dropped connection must not build
		-- the project twice.
		for property, value in pairs(operation.properties or {}) do
			local problem = setProperty(existing, property, value)
			if problem ~= nil then
				return "failed", problem, false
			end
		end
		return "skipped", "already present", true
	end

	local ok, created = pcall(function()
		return Instance.new(operation.className)
	end)
	if not ok or created == nil then
		return "failed", `cannot create a {operation.className}`, false
	end
	created.Name = name
	for property, value in pairs(operation.properties or {}) do
		local problem = setProperty(created, property, value)
		if problem ~= nil then
			created:Destroy()
			return "failed", problem, false
		end
	end
	created.Parent = parent
	return "applied", "", false
end

local function applyCreateScript(operation: any): (string, string, boolean)
	local parent, err = resolve(operation.path, true)
	if parent == nil then
		return "failed", err or "unknown path", false
	end
	local name = leafName(operation.path)
	local existing = parent:FindFirstChild(name)
	if existing ~= nil and existing.ClassName ~= operation.scriptType then
		return "failed",
			`{operation.path} already exists as a {existing.ClassName}, not a {operation.scriptType}`,
			false
	end

	local target = existing
	local created = false
	if target == nil then
		target = Instance.new(operation.scriptType)
		target.Name = name
		created = true
	end
	if (target :: any).Source == operation.source then
		if created then
			(target :: any):Destroy()
		end
		return "skipped", "source already matches", true
	end

	local problem = writeSource(target :: LuaSourceContainer, operation.source)
	if problem ~= nil then
		if created then
			(target :: any):Destroy()
		end
		return "failed", problem, false
	end
	if operation.disabled ~= nil and target:IsA("BaseScript") then
		(target :: any).Disabled = operation.disabled
	end
	if created then
		target.Parent = parent
	end
	return "applied", if created then "created" else "updated", false
end

local function applyUpdateScript(operation: any): (string, string, boolean)
	local parent, err = resolve(operation.path, false)
	if parent == nil then
		return "failed", err or "unknown path", false
	end
	local target = parent:FindFirstChild(leafName(operation.path))
	if target == nil or not target:IsA("LuaSourceContainer") then
		return "failed", `{operation.path} is not a script`, false
	end
	if (target :: any).Source == operation.source then
		return "skipped", "source already matches", true
	end
	local problem = writeSource(target, operation.source)
	if problem ~= nil then
		return "failed", problem, false
	end
	return "applied", "updated", false
end

local function applySetProperty(operation: any): (string, string, boolean)
	local parent, err = resolve(operation.path, false)
	if parent == nil then
		return "failed", err or "unknown path", false
	end
	local target = parent:FindFirstChild(leafName(operation.path))
	if target == nil then
		return "failed", `{operation.path} does not exist`, false
	end
	local problem = setProperty(target, operation.property, operation.value)
	if problem ~= nil then
		return "failed", problem, false
	end
	return "applied", "", false
end

local function applyDelete(operation: any): (string, string, boolean)
	local parent, err = resolve(operation.path, false)
	if parent == nil then
		return "skipped", "parent does not exist", true
	end
	local target = parent:FindFirstChild(leafName(operation.path))
	if target == nil then
		return "skipped", "already absent", true
	end
	target:Destroy()
	return "applied", "", false
end

--[[
	Start a test session.

	`RunService:Run()` is what a plugin can start: it runs the place's scripts
	in Studio without spawning a character. A plugin cannot press Play Solo --
	Roblox exposes no API for it -- so `mode = "play"` is reported as unsupported
	rather than quietly doing something else and calling it a playtest.
]]
--[[
	Start a test session.

	`StudioTestService:ExecutePlayModeAsync` is the documented route for a
	plugin to start Play mode -- a character, the full client-server split --
	and is what "I see the game running" requires. `RunService:Run()` is the
	lesser option: scripts execute, no character.

	Whether the plugin keeps talking to the bridge once Play mode starts is
	recorded in docs/STUDIO_EXPERIMENTS.md rather than assumed here, which is
	why the result is reported before the call rather than after it.
]]
local function applyStartPlaytest(operation: any): (string, string, boolean)
	if RunService:IsRunning() then
		return "skipped", "already running", true
	end

	if operation.mode == "run" then
		local ok, err = pcall(function()
			RunService:Run()
		end)
		if not ok then
			return "failed", `could not start run mode: {tostring(err)}`, false
		end
		return "applied", "run mode started", false
	end

	local service = studioTestService()
	if service == nil then
		return "failed",
			"this Studio has no StudioTestService, so the plugin cannot start Play mode. "
				.. "Use mode 'run', or press Play in Studio",
			false
	end

	--[[
		`ExecutePlayModeAsync()` with no argument answered
		"Argument 1 missing or nil" on Studio 0.739. The signature is not
		something to guess at and ship, so the shapes are tried in order and the
		one that works is REPORTED -- the detail line names it, and that answer
		gets written into docs/STUDIO_EXPERIMENTS.md rather than left in code as
		folklore.

		Each is wrapped: a wrong argument type errors harmlessly inside pcall.
	]]
	local attempts = {
		{
			name = "callback",
			call = function()
				(service :: any):ExecutePlayModeAsync(function() end)
			end,
		},
		{
			name = "empty table",
			call = function()
				(service :: any):ExecutePlayModeAsync({})
			end,
		},
		{
			name = "one player",
			call = function()
				(service :: any):ExecutePlayModeAsync(1)
			end,
		},
		{
			name = "table with numPlayers",
			call = function()
				(service :: any):ExecutePlayModeAsync({ numPlayers = 1 })
			end,
		},
	}

	local reasons = {}
	for _, attempt in ipairs(attempts) do
		local ok, err = pcall(attempt.call)
		if ok then
			return "applied", `play mode started via ExecutePlayModeAsync({attempt.name})`, false
		end
		table.insert(reasons, `{attempt.name}: {tostring(err)}`)
	end

	return "failed", "ExecutePlayModeAsync refused every call shape tried -- " .. table.concat(reasons, " | "), false
end

local function applyStopPlaytest(_operation: any): (string, string, boolean)
	if not RunService:IsRunning() then
		return "skipped", "not running", true
	end
	local ok, err = pcall(function()
		RunService:Stop()
	end)
	if not ok then
		return "failed", `could not stop: {tostring(err)}`, false
	end
	return "applied", "stopped", false
end

local function applyCreateRemote(operation: any): (string, string, boolean)
	local parent, err = resolve(operation.path, true)
	if parent == nil then
		return "failed", err or "unknown path", false
	end
	local name = leafName(operation.path)
	local existing = parent:FindFirstChild(name)
	if existing ~= nil then
		if existing.ClassName ~= operation.remoteType then
			return "failed", `{operation.path} already exists as a {existing.ClassName}`, false
		end
		return "skipped", "already present", true
	end
	local remote = Instance.new(operation.remoteType)
	remote.Name = name
	remote.Parent = parent
	return "applied", "", false
end

local function applyOpenScript(operation: any): (string, string, boolean)
	local parent, err = resolve(operation.path, false)
	if parent == nil then
		return "failed", err or "unknown path", false
	end
	local target = parent:FindFirstChild(leafName(operation.path))
	if target == nil or not target:IsA("LuaSourceContainer") then
		return "failed", `{operation.path} is not a script`, false
	end
	local ok, problem = pcall(function()
		Selection:Set({ target })
		ScriptEditorService:OpenScriptDocumentAsync(target)
	end)
	if not ok then
		return "failed", `could not open it: {tostring(problem)}`, false
	end
	return "applied", "opened in the editor", false
end

local HANDLERS: { [string]: (any) -> (string, string, boolean) } = {
	create_instance = applyCreateInstance,
	create_script = applyCreateScript,
	update_script = applyUpdateScript,
	set_property = applySetProperty,
	delete_instance = applyDelete,
	create_remote = applyCreateRemote,
	open_script = applyOpenScript,
	start_playtest = applyStartPlaytest,
	stop_playtest = applyStopPlaytest,
}

applyOperation = function(operation: any): (string, string, boolean)
	local handler = HANDLERS[operation.operation]
	if handler == nil then
		-- The closed set, enforced on the side that holds the DataModel.
		return "failed", `{tostring(operation.operation)} is not an operation this plugin performs`, false
	end
	local ok, statusOrError, detail, unchanged = pcall(handler, operation)
	if not ok then
		return "failed", tostring(statusOrError), false
	end
	return statusOrError, detail or "", unchanged or false
end

local function applyBatch(batch: any)
	local results = {}
	local firstScript: Instance? = nil

	-- One ChangeHistoryService waypoint per batch, so a person can undo a build
	-- in Studio the way they undo anything else.
	local ChangeHistoryService = game:GetService("ChangeHistoryService")
	local recording = nil
	local recordingOk = pcall(function()
		recording = ChangeHistoryService:TryBeginRecording("VentureEngineerBuild", "Venture Engineer build")
	end)

	for _, operation in ipairs(batch.operations) do
		local status, detail, unchanged = applyOperation(operation)
		table.insert(results, {
			operation_id = operation.operation_id,
			status = status,
			detail = detail,
			unchanged = unchanged,
		})
		if status == "applied" and operation.operation == "create_script" and firstScript == nil then
			local parent = resolve(operation.path, false)
			if parent ~= nil then
				firstScript = parent:FindFirstChild(leafName(operation.path))
			end
		end
	end

	if recordingOk and recording ~= nil then
		pcall(function()
			ChangeHistoryService:FinishRecording(recording, Enum.FinishRecordingOperation.Commit)
		end)
	end

	-- Open what was built, so the person sees code rather than a message saying
	-- code exists.
	if firstScript ~= nil then
		pcall(function()
			Selection:Set({ firstScript })
			ScriptEditorService:OpenScriptDocumentAsync(firstScript)
		end)
	end

	local ok, err = request("POST", "/bridge/results", {
		build_id = batch.build_id,
		batch_id = batch.batch_id,
		results = results,
		place_name = game.Name,
		errors = {},
	})
	if not ok then
		lastError = `could not report results: {tostring(err)}`
		render()
	end
end

-- ------------------------------------------------------------- runtime -----

--[[
	Forward what Studio says while running.

	`LogService.MessageOut` is the only route to the output a running place
	produces, including errors from scripts this plugin wrote. Without it there
	is no repair loop, because nothing would know a build failed at run time.
]]
local LogService = game:GetService("LogService")
local currentBuildId = ""

LogService.MessageOut:Connect(function(message: string, messageType: Enum.MessageType)
	if not running or currentBuildId == "" then
		return
	end
	if messageType ~= Enum.MessageType.MessageError and messageType ~= Enum.MessageType.MessageWarning then
		return
	end
	local path, line = string.match(message, "^(%S+):(%d+):")
	task.spawn(function()
		request("POST", "/bridge/runtime-errors", {
			build_id = currentBuildId,
			message = string.sub(message, 1, 8000),
			script_path = path or "",
			line = if line ~= nil then tonumber(line) else nil,
			severity = if messageType == Enum.MessageType.MessageError then "error" else "warning",
			at = tostring(os.time()),
		})
	end)
end)

-- ------------------------------------------------------------- the loop ----

local function heartbeatOnce()
	local ok, err = request("POST", "/bridge/heartbeat", {
		plugin_version = PLUGIN_VERSION,
		studio_version = version(),
		place_name = game.Name,
		place_id = game.PlaceId,
		mode = if RunService:IsRunning() then "run" else "edit",
	})
	if ok then
		if not connected then
			connected = true
			statusText = "Connected"
			lastError = ""
			render()
		end
	else
		connected = false
		statusText = "Not connected"
		lastError = tostring(err)
		render()
	end
end

local function pollOnce()
	local ok, answer = request("GET", "/bridge/work", nil)
	if not ok then
		connected = false
		statusText = "Not connected"
		lastError = tostring(answer)
		render()
		return
	end
	if type(answer) == "table" and answer.batch ~= nil then
		currentBuildId = answer.batch.build_id
		statusText = `Applying {#answer.batch.operations} operation(s)`
		render()
		applyBatch(answer.batch)
		statusText = "Connected"
		render()
	end
end

local function start()
	if running then
		return
	end
	if token() == "" then
		lastError = "Paste the pairing token the bridge printed, then press Connect."
		render()
		return
	end
	running = true
	statusText = "Connecting"
	render()

	task.spawn(function()
		while running do
			heartbeatOnce()
			task.wait(HEARTBEAT_SECONDS)
		end
	end)
	task.spawn(function()
		while running do
			pollOnce()
			-- The bridge holds the request open, so this is not a busy loop; the
			-- wait is what keeps a failing bridge from being hammered.
			task.wait(if connected then 0.2 else 3)
		end
	end)
end

local function stop()
	running = false
	connected = false
	statusText = "Not connected"
	render()
end

connectButton.Activated:Connect(function()
	if running then
		stop()
		return
	end
	plugin:SetSetting(TOKEN_KEY, tokenBox.Text)
	start()
end)

toggleButton.Click:Connect(function()
	widget.Enabled = not widget.Enabled
end)

plugin.Unloading:Connect(stop)

render()
if token() ~= "" then
	start()
end
