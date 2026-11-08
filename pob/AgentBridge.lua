-- Path of Building
--
-- Module: AgentBridge
-- Local HTTP/JSON bridge used by the external AI agent service.
--
-- This module is loaded once from Launch.lua after Main is initialised, and its
-- Poll() method is called once per frame from launch:OnFrame.  It must never
-- throw: every entry point is protected with pcall so a malformed request can
-- not take the game client down.
--
-- Transport: HTTP/1.1 over 127.0.0.1, one request per connection.
-- Endpoints:
--   GET  /ping                -> liveness probe
--   GET  /state               -> compact build summary
--   POST /rpc  {"cmd": ...}   -> command dispatcher
--
-- The protocol is intentionally small; the authoritative description lives in
-- docs/02-bridge-protocol.md.

local socket = require("socket")
local json = require("dkjson")

local AgentBridge = { }
AgentBridge.__index = AgentBridge

local PORT = 49090
local HOST = "127.0.0.1"
local MAX_BODY = 1024 * 1024

local function log(fmt, ...)
	local ok, file = pcall(io.open, GetScriptPath() .. "/agent_bridge.log", "a")
	if ok and file then
		file:write(os.date("[%Y-%m-%d %H:%M:%S] ") .. string.format(fmt, ...) .. "\n")
		file:close()
	end
end

-- dkjson will recurse forever on cyclic tables and can blow up on very large
-- engine tables.  Every outgoing payload goes through this depth/size limiter.
local function sanitize(value, depth, state)
	if value == nil then
		return nil
	end
	state.count = state.count + 1
	if state.count > 5000 then
		return "..."
	end
	local valueType = type(value)
	if valueType == "number" or valueType == "string" or valueType == "boolean" then
		return value
	elseif valueType ~= "table" then
		return tostring(value)
	end
	if depth >= 10 then
		return "..."
	end
	if state.visited[value] then
		return "<cycle>"
	end
	state.visited[value] = true
	local result = { }
	local count = 0
	for key, child in pairs(value) do
		if type(key) == "string" or type(key) == "number" then
			result[key] = sanitize(child, depth + 1, state)
		end
		count = count + 1
		if count >= 100 then
			break
		end
	end
	state.visited[value] = nil
	return result
end

local function jsonEncode(value)
	local ok, result = pcall(json.encode, sanitize(value, 0, { count = 0, visited = { } }))
	if ok then
		return result
	end
	return '{"ok":false,"error":"json encode failed"}'
end

local function getBuild()
	if not main or main.mode ~= "BUILD" then
		return nil
	end
	return main.modes and main.modes.BUILD
end

local function buildState()
	local build = getBuild()
	if not build or not build.spec then
		return { ok = false, error = "No build is currently open" }
	end
	local used, ascUsed, secondaryAscUsed, sockets = build.spec:CountAllocNodes()
	local output = build.calcsTab and build.calcsTab.mainOutput or { }
	local stats = { }
	for _, key in ipairs({
		"TotalDPS", "CombinedDPS", "WithDotDPS", "Life", "EnergyShield", "Mana",
		"TotalEHP", "Armour", "Evasion", "Ward",
		"FireResist", "ColdResist", "LightningResist", "ChaosResist",
		"PhysicalMaximumHitTaken", "FireMaximumHitTaken", "ColdMaximumHitTaken",
		"LightningMaximumHitTaken", "ChaosMaximumHitTaken",
	}) do
		stats[key] = output[key]
	end
	return {
		ok = true,
		data = {
			buildName = build.buildName,
			className = build.spec.curClassName,
			ascendancy = build.spec.curAscendClassName,
			level = build.characterLevel,
			treeVersion = build.spec.treeVersion,
			pointsUsed = used,
			ascendancyPointsUsed = ascUsed,
			secondaryAscendancyPointsUsed = secondaryAscUsed,
			jewelSockets = sockets,
			stats = stats,
			outputRevision = build.outputRevision,
		},
	}
end

local function searchNodes(params)
	local build = getBuild()
	if not build or not build.spec then
		return nil, "No build is currently open"
	end
	local query = tostring(params.q or ""):lower()
	local limit = tonumber(params.limit) or 30
	local nodeType = params.type
	local ascendancy = params.ascendancy
	local result = { }
	for _, node in pairs(build.spec.nodes) do
		if node.name and (query == "" or node.name:lower():find(query, 1, true)) then
			local matchesType = not nodeType or nodeType == "" or node.type == nodeType
			local matchesAsc = not ascendancy or ascendancy == "" or node.ascendancyName == ascendancy
			if matchesType and matchesAsc then
				local stats = { }
				for i, stat in ipairs(node.stats or { }) do
					stats[i] = stat
				end
				result[#result + 1] = {
					id = node.id,
					name = node.name,
					type = node.type,
					ascendancy = node.ascendancyName,
					allocated = node.alloc and true or false,
					stats = stats,
				}
				if #result >= limit then
					break
				end
			end
		end
	end
	return result
end

local function allocateNode(params)
	local build = getBuild()
	if not build or not build.spec then
		return nil, "No build is currently open"
	end
	local id = tonumber(params.id)
	if not id then
		return nil, "id must be a number"
	end
	local node = build.spec.nodes[id]
	if not node then
		return nil, "Unknown passive node id: " .. tostring(id)
	end
	if node.alloc then
		return { id = id, name = node.name, allocated = true, alreadyAllocated = true }
	end
	build.spec:AllocNode(node)
	build.buildFlag = true
	return { id = id, name = node.name, allocated = node.alloc and true or false }
end

local function deallocateNode(params)
	local build = getBuild()
	if not build or not build.spec then
		return nil, "No build is currently open"
	end
	local id = tonumber(params.id)
	if not id then
		return nil, "id must be a number"
	end
	local node = build.spec.nodes[id]
	if not node then
		return nil, "Unknown passive node id: " .. tostring(id)
	end
	build.spec:DeallocNode(node)
	build.buildFlag = true
	return { id = id, name = node.name, allocated = false }
end

local function findGem(build, spec)
	if type(spec) == "number" then
		return build.data.gems[spec]
	end
	if type(spec) ~= "string" then
		return nil
	end
	-- Exact name first (case insensitive), so "Fireball" never resolves to "Vaal Fireball".
	local wanted = spec:lower()
	for _, gem in pairs(build.data.gems) do
		if gem.name and gem.name:lower() == wanted then
			return gem
		end
	end
	local err, gem = build.skillsTab:FindSkillGem(spec)
	if gem then
		return gem
	end
	return nil, err
end

local function listSkills()
	local build = getBuild()
	if not build or not build.skillsTab then
		return nil, "No build is currently open"
	end
	local result = { }
	for index, group in ipairs(build.skillsTab.socketGroupList) do
		local gems = { }
		for _, gem in ipairs(group.gemList or { }) do
			gems[#gems + 1] = {
				name = gem.nameSpec,
				level = gem.level,
				quality = gem.quality,
				enabled = gem.enabled,
				support = gem.gemData and gem.gemData.tags and gem.gemData.tags.support or false,
			}
		end
		result[#result + 1] = {
			index = index,
			enabled = group.enabled,
			includeInFullDPS = group.includeInFullDPS,
			mainActiveSkill = group.mainActiveSkill,
			gems = gems,
		}
	end
	return result
end

local function listItems()
	local build = getBuild()
	if not build or not build.itemsTab then
		return nil, "No build is currently open"
	end
	local slotByItem = { }
	for slotName, slot in pairs(build.itemsTab.slots or { }) do
		if slot.selItemId and slot.selItemId ~= 0 then
			slotByItem[slot.selItemId] = slotName
		end
	end
	local result = { }
	for _, id in ipairs(build.itemsTab.itemOrderList or { }) do
		local item = build.itemsTab.items[id]
		if item then
			result[#result + 1] = {
				id = id,
				name = item.name,
				baseName = item.baseName,
				rarity = item.rarity,
				slot = slotByItem[id],
			}
		end
	end
	return result
end

local function listAllocated()
	local build = getBuild()
	if not build or not build.spec then
		return nil, "No build is currently open"
	end
	local result = { }
	for _, node in pairs(build.spec.allocNodes) do
		if node.type ~= "ClassStart" and node.type ~= "AscendClassStart" then
			local stats = { }
			for i, stat in ipairs(node.stats or { }) do
				stats[i] = type(stat) == "string" and stat or tostring(stat)
				if i >= 20 then
					break
				end
			end
			result[#result + 1] = {
				id = node.id,
				name = node.name and tostring(node.name) or "?",
				type = node.type,
				ascendancy = node.ascendancyName,
				stats = stats,
			}
			if #result >= 200 then
				break
			end
		end
	end
	table.sort(result, function(a, b)
		return (a.name or "") < (b.name or "")
	end)
	return result
end

local function equipAddedItem(build, item, params)
	if params.equip == false then
		return
	end
	local itemsTab = build.itemsTab
	if params.slot and itemsTab.slots[params.slot] then
		itemsTab.slots[params.slot]:SetSelItemId(item.id)
		itemsTab:PopulateSlots()
		itemsTab:AddUndoState()
		build.buildFlag = true
		return
	end
	local activeSet = itemsTab.activeItemSetId or 1
	pcall(function()
		itemsTab:EquipItemInSet(item, activeSet)
	end)
end

local commands = {
	ping = function()
		return { pong = true, time = GetTime() }
	end,
	state = function()
		return buildState()
	end,
	new_build = function(params)
		main:SetMode("BUILD", false, params.name or "Agent Build")
		return { queued = true, name = params.name or "Agent Build" }
	end,
	import_code = function(params)
		local code = params.code
		if type(code) ~= "string" or code == "" then
			return nil, "code must be a non-empty PoB export string"
		end
		local encoded = code:gsub("%s+", ""):gsub("-", "+"):gsub("_", "/")
		local ok, xmlText = pcall(function()
			return Inflate(common.base64.decode(encoded))
		end)
		if not ok or type(xmlText) ~= "string" or xmlText == "" then
			return nil, "Failed to decode PoB export code"
		end
		if not xmlText:match("<PathOfBuilding") then
			return nil, "Decoded data is not a Path of Building XML"
		end
		local name = params.name or "Imported Build"
		main:SetMode("BUILD", false, name, xmlText, false)
		return { queued = true, name = name, bytes = #xmlText }
	end,
	select_class = function(params)
		local build = getBuild()
		if not build or not build.spec then
			return nil, "No build is currently open"
		end
		local classId = tonumber(params.class)
		if not classId then
			classId = build.spec.tree.classNameMap[params.class]
		end
		if not classId then
			return nil, "Unknown class: " .. tostring(params.class)
		end
		local class = build.spec.tree.classes[classId]
		if not class then
			return nil, "Unknown class id: " .. tostring(classId)
		end
		build.spec:SelectClass(classId)
		local ascendancy = params.ascendancy
		if ascendancy and ascendancy ~= "" and ascendancy ~= "None" then
			local ascendId
			for id, asc in pairs(class.classes) do
				if id ~= 0 and (asc.name == ascendancy or asc.id == ascendancy) then
					ascendId = id
					break
				end
			end
			if not ascendId then
				return nil, "Unknown ascendancy for " .. class.name .. ": " .. tostring(ascendancy)
			end
			build.spec:SelectAscendClass(ascendId)
		end
		build.buildFlag = true
		return {
			className = build.spec.curClassName,
			ascendancy = build.spec.curAscendClassName,
		}
	end,
	set_level = function(params)
		local build = getBuild()
		if not build then
			return nil, "No build is currently open"
		end
		local level = tonumber(params.level)
		if not level then
			return nil, "level must be a number"
		end
		build.characterLevel = math.max(1, math.min(100, math.floor(level)))
		build.characterLevelAutoMode = params.auto == true
		build.buildFlag = true
		return { level = build.characterLevel, auto = build.characterLevelAutoMode }
	end,
	search_gems = function(params)
		local build = getBuild()
		if not build then
			return nil, "No build is currently open"
		end
		local query = tostring(params.q or ""):lower()
		local limit = tonumber(params.limit) or 30
		local supportFilter = params.support
		local result = { }
		for id, gem in pairs(build.data.gems) do
			local matches = query == ""
				or (gem.name and gem.name:lower():find(query, 1, true))
				or (gem.tagString and gem.tagString:lower():find(query, 1, true))
			local isSupport = gem.tags and gem.tags.support and true or false
			if matches and (supportFilter == nil or isSupport == supportFilter) then
				result[#result + 1] = {
					id = gem.id or id,
					name = gem.name,
					support = isSupport,
					tags = gem.tagString,
					naturalMaxLevel = gem.naturalMaxLevel,
					reqStr = gem.reqStr,
					reqDex = gem.reqDex,
					reqInt = gem.reqInt,
				}
			end
		end
		table.sort(result, function(a, b) return a.name < b.name end)
		while #result > limit do
			table.remove(result)
		end
		return { gems = result }
	end,
	add_skill_group = function(params)
		local build = getBuild()
		if not build or not build.skillsTab then
			return nil, "No build is currently open"
		end
		local gems = params.gems
		if type(gems) ~= "table" or #gems == 0 then
			return nil, "gems must be a non-empty array"
		end
		local socketGroup = {
			enabled = params.enabled ~= false,
			includeInFullDPS = params.includeInFullDPS == true,
			mainActiveSkill = tonumber(params.mainActiveSkill) or 1,
			groupCount = 1,
			gemList = { },
		}
		for _, spec in ipairs(gems) do
			if type(spec) == "string" then
				spec = { name = spec }
			end
			local gemData, err = findGem(build, spec.id or spec.name)
			if not gemData then
				return nil, err or ("Unknown gem: " .. tostring(spec.id or spec.name))
			end
			socketGroup.gemList[#socketGroup.gemList + 1] = {
				nameSpec = gemData.name,
				gemId = gemData.id,
				level = tonumber(spec.level) or gemData.naturalMaxLevel or 20,
				quality = tonumber(spec.quality) or 0,
				enabled = spec.enabled ~= false,
			}
		end
		build.skillsTab:ProcessSocketGroup(socketGroup)
		table.insert(build.skillsTab.socketGroupList, socketGroup)
		build.skillsTab:SetDisplayGroup(socketGroup)
		build.skillsTab:UpdateSocketGroups()
		build.buildFlag = true
		local names = { }
		for _, gem in ipairs(socketGroup.gemList) do
			names[#names + 1] = gem.nameSpec
		end
		return { index = #build.skillsTab.socketGroupList, gems = names }
	end,
	list_skills = function()
		local result, err = listSkills()
		if not result then
			return nil, err
		end
		return { groups = result }
	end,
	list_allocated = function()
		local result, err = listAllocated()
		if not result then
			return nil, err
		end
		return { nodes = result }
	end,
	search_uniques = function(params)
		local db = main and main.uniqueDB
		if not db or db.loading then
			return nil, "Unique database is still loading"
		end
		local query = tostring(params.q or ""):lower()
		local limit = tonumber(params.limit) or 30
		local result = { }
		for name, item in pairs(db.list) do
			if query == "" or name:lower():find(query, 1, true) then
				result[#result + 1] = {
					name = name,
					baseName = item.baseName,
					type = item.type,
					rarity = item.rarity,
				}
			end
		end
		table.sort(result, function(a, b) return a.name < b.name end)
		while #result > limit do
			table.remove(result)
		end
		return { uniques = result }
	end,
	add_unique = function(params)
		local build = getBuild()
		if not build or not build.itemsTab then
			return nil, "No build is currently open"
		end
		local db = main.uniqueDB
		if not db or db.loading then
			return nil, "Unique database is still loading"
		end
		local name = params.name
		local template = name and db.list[name]
		if not template and name then
			-- Allow the short display name (e.g. "Tabula Rasa") when it maps to exactly
			-- one database entry (the DB key is "Tabula Rasa, Simple Robe").
			local candidates = { }
			local wanted = tostring(name):lower()
			for candidateName, candidate in pairs(db.list) do
				if candidateName:lower():find(wanted, 1, true) or (candidate.name and candidate.name:lower() == wanted) then
					candidates[#candidates + 1] = candidateName
					if #candidates > 10 then
						break
					end
				end
			end
			if #candidates == 1 then
				template = db.list[candidates[1]]
			elseif #candidates > 1 then
				return nil, "Ambiguous unique '" .. tostring(name) .. "': " .. table.concat(candidates, ", ")
			end
		end
		if not template then
			return nil, "Unknown unique: " .. tostring(name)
		end
		local ok, item = pcall(function()
			return new("Item", template:BuildRaw())
		end)
		if not ok or not item then
			return nil, "Failed to build unique item"
		end
		build.itemsTab:AddItem(item, true)
		equipAddedItem(build, item, params)
		build.buildFlag = true
		return { name = item.name, baseName = item.baseName, rarity = item.rarity }
	end,
	add_item_raw = function(params)
		local build = getBuild()
		if not build or not build.itemsTab then
			return nil, "No build is currently open"
		end
		local raw = params.raw
		if type(raw) ~= "string" or raw == "" then
			return nil, "raw must be a non-empty string"
		end
		local ok, item = pcall(function()
			return new("Item", raw)
		end)
		if not ok or not item then
			return nil, "Failed to parse item text (English item text is required)"
		end
		build.itemsTab:AddItem(item, true)
		equipAddedItem(build, item, params)
		build.buildFlag = true
		return { name = item.name, baseName = item.baseName, rarity = item.rarity }
	end,
	list_items = function()
		local result, err = listItems()
		if not result then
			return nil, err
		end
		return { items = result }
	end,
	set_config = function(params)
		local build = getBuild()
		if not build or not build.configTab then
			return nil, "No build is currently open"
		end
		local key = params.key
		if type(key) ~= "string" or key == "" then
			return nil, "key must be a non-empty string"
		end
		local configTab = build.configTab
		local setId = configTab.activeConfigSetId or 1
		local configSet = configTab.configSets and configTab.configSets[setId]
		if not configSet then
			return nil, "No active config set"
		end
		configSet.input[key] = params.value
		configTab:BuildModList()
		build.buildFlag = true
		return { key = key, value = params.value }
	end,
	get_stats = function(params)
		local build = getBuild()
		if not build or not build.calcsTab or not build.calcsTab.mainOutput then
			return nil, "No build is currently open"
		end
		local output = build.calcsTab.mainOutput
		local keys = params.keys or {
			"TotalDPS", "CombinedDPS", "WithDotDPS", "Life", "EnergyShield", "Mana",
			"TotalEHP", "Armour", "Evasion", "Ward", "FireResist", "ColdResist",
			"LightningResist", "ChaosResist", "PhysicalMaximumHitTaken",
		}
		local stats = { }
		for _, key in ipairs(keys) do
			local value = output[key]
			if type(value) == "number" or type(value) == "string" or type(value) == "boolean" then
				stats[key] = value
			end
		end
		return { stats = stats, outputRevision = build.outputRevision }
	end,
	export_build = function()
		local build = getBuild()
		if not build then
			return nil, "No build is currently open"
		end
		local xml = build:SaveDB(build.dbFileName or "agent-build.xml")
		if not xml then
			return nil, "Failed to export build XML"
		end
		return { xml = xml, bytes = #xml }
	end,
	save_build = function(params)
		local build = getBuild()
		if not build then
			return nil, "No build is currently open"
		end
		local name = tostring(params.name or "agent-build"):gsub("[^%w _%-]", "")
		if name == "" then
			return nil, "Invalid build name"
		end
		local folder = main.buildPath or (GetScriptPath() .. "/Builds/")
		pcall(MakeDir, folder)
		local path = folder .. name .. ".xml"
		local xml = build:SaveDB(path)
		if not xml then
			return nil, "Failed to export build XML"
		end
		local file = io.open(path, "w")
		if not file then
			return nil, "Cannot write " .. path
		end
		file:write(xml)
		file:close()
		build.dbFileName = path
		build.buildName = name
		build.dbFileSubPath = ""
		build:ResetModFlags()
		return { path = path, bytes = #xml }
	end,
	search_nodes = function(params)
		local result, err = searchNodes(params)
		if not result then
			return nil, err
		end
		return { nodes = result }
	end,
	alloc_node = function(params)
		local result, err = allocateNode(params)
		if not result then
			return nil, err
		end
		return result
	end,
	dealloc_node = function(params)
		local result, err = deallocateNode(params)
		if not result then
			return nil, err
		end
		return result
	end,
}

local function dispatch(request)
	local body = request.body
	if body == "" then
		return { ok = false, error = "Empty request body" }
	end
	local decoded, pos, err = json.decode(body)
	if not decoded then
		return { ok = false, error = "Invalid JSON: " .. tostring(err) }
	end
	local cmd = decoded.cmd
	local handler = commands[cmd]
	if not handler then
		return { ok = false, error = "Unknown command: " .. tostring(cmd) }
	end
	log("RPC start %s", tostring(cmd))
	local ok, result, cmdErr = pcall(handler, decoded)
	if not ok then
		log("Command %s failed: %s", tostring(cmd), tostring(result))
		return { ok = false, error = tostring(result) }
	end
	if result == nil then
		log("Command %s returned nil: %s", tostring(cmd), tostring(cmdErr))
		return { ok = false, error = tostring(cmdErr or "Command failed") }
	end
	log("RPC done %s", tostring(cmd))
	return { ok = true, data = result }
end

local function readRequest(client)
	client:settimeout(1)
	local requestLine = client:receive("*l")
	if not requestLine then
		return nil
	end
	local method, path = requestLine:match("^(%S+)%s+(%S+)")
	if not method then
		return nil
	end
	local headers = { }
	while true do
		local line = client:receive("*l")
		if not line or line == "" then
			break
		end
		local key, value = line:match("^([^:]+):%s*(.*)$")
		if key then
			headers[key:lower()] = value
		end
	end
	local length = tonumber(headers["content-length"] or "0") or 0
	if length > MAX_BODY then
		return nil
	end
	local body = ""
	if length > 0 then
		body = client:receive(length) or ""
	end
	return { method = method, path = path, headers = headers, body = body }
end

local function writeResponse(client, status, contentType, body)
	local payload = body or ""
	local header = table.concat({
		"HTTP/1.1 " .. status,
		"Content-Type: " .. contentType,
		"Content-Length: " .. #payload,
		"Access-Control-Allow-Origin: *",
		"Access-Control-Allow-Headers: Content-Type",
		"Access-Control-Allow-Methods: GET, POST, OPTIONS",
		"Connection: close",
		"",
		"",
	}, "\r\n")
	client:send(header .. payload)
end

function AgentBridge:Poll()
	self.frames = (self.frames or 0) + 1
	if self.frames % 600 == 0 then
		log("heartbeat frames=%d mode=%s", self.frames, tostring(main and main.mode))
	end
	local client = self.server:accept()
	if not client then
		return
	end
	local ok, err = pcall(function()
		client:settimeout(0.25)
		local request = readRequest(client)
		if not request then
			return
		end
		if request.method == "OPTIONS" then
			writeResponse(client, "204 No Content", "text/plain", "")
			return
		end
		local path = request.path:match("^[^?]+") or request.path
		if request.method == "GET" and path == "/ping" then
			writeResponse(client, "200 OK", "application/json", jsonEncode({ ok = true, data = { pong = true, version = launch.versionNumber } }))
		elseif request.method == "GET" and path == "/state" then
			writeResponse(client, "200 OK", "application/json", jsonEncode(buildState()))
		elseif request.method == "POST" and path == "/rpc" then
			local response = dispatch(request)
			writeResponse(client, "200 OK", "application/json", jsonEncode(response))
		else
			writeResponse(client, "404 Not Found", "application/json", jsonEncode({ ok = false, error = "Not found" }))
		end
	end)
	if not ok then
		log("Poll error: %s", tostring(err))
	end
	client:close()
end

function AgentBridge:Init()
	local server, err = socket.bind(HOST, PORT)
	if not server then
		log("Failed to bind %s:%d: %s", HOST, PORT, tostring(err))
		return nil, err
	end
	server:settimeout(0)
	self.server = server
	self.port = PORT
	log("AgentBridge listening on %s:%d", HOST, PORT)
	ConPrintf("AgentBridge listening on %s:%d", HOST, PORT)

	-- Add a small always-available button that opens the local agent UI.
	if main and main.controls and not main.controls.agentButton then
		main.controls.agentButton = new("ButtonControl", nil, {
			function() return main.screenW - 100 end,
			function() return main.screenH - 26 end,
			88, 20,
		}, "AI Copilot", function()
			OpenURL("http://127.0.0.1:8765")
		end)
	end

	return self
end

return AgentBridge
