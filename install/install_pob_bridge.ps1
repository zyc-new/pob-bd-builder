param(
    [string] $PobDir,
    [string] $SkillRoot
)

$ErrorActionPreference = "Stop"
if (-not $SkillRoot) {
    $SkillRoot = Split-Path $PSScriptRoot -Parent
}
if ($env:POB_AGENT_DATA) {
    $dataRoot = $env:POB_AGENT_DATA
} elseif ($env:LOCALAPPDATA) {
    $dataRoot = Join-Path $env:LOCALAPPDATA "pob-bd-builder"
} else {
    $dataRoot = Join-Path $env:USERPROFILE ".pob-bd-builder"
}
$runtimePath = Join-Path $dataRoot "runtime.json"

if (-not $PobDir) {
    $discover = Join-Path $SkillRoot "scripts\discover_env.py"
    if (-not (Test-Path $runtimePath) -and (Test-Path $discover)) {
        $python = (Get-Command python -ErrorAction SilentlyContinue).Source
        if ($python) {
            & $python $discover --write --fix 2>&1 | Out-Null
        }
    }
    if (Test-Path $runtimePath) {
        $runtime = Get-Content $runtimePath -Raw | ConvertFrom-Json
        $PobDir = $runtime.pobDir
    }
}
if (-not $PobDir) {
    Write-Output (@{ ok = $false; error = "PobDir not provided and runtime.json has no pobDir" } | ConvertTo-Json -Compress)
    exit 2
}
if (-not (Test-Path (Join-Path $PobDir "Path of Building.exe"))) {
    Write-Output (@{ ok = $false; error = "Not a PoB directory: $PobDir" } | ConvertTo-Json -Compress)
    exit 2
}

$launch = Join-Path $PobDir "Launch.lua"
$modules = Join-Path $PobDir "Modules"
if (-not (Test-Path $launch)) {
    Write-Output (@{ ok = $false; error = "Launch.lua not found in $PobDir" } | ConvertTo-Json -Compress)
    exit 2
}
New-Item -ItemType Directory -Force -Path $modules | Out-Null

$bridgeSource = Join-Path $SkillRoot "pob\AgentBridge.lua"
$bridgeTarget = Join-Path $modules "AgentBridge.lua"
if (-not (Test-Path $bridgeSource)) {
    Write-Output (@{ ok = $false; error = "Bundled AgentBridge.lua not found: $bridgeSource" } | ConvertTo-Json -Compress)
    exit 2
}
if ((Test-Path $bridgeTarget) -and -not (Test-Path "$bridgeTarget.bak-agent")) {
    Copy-Item $bridgeTarget "$bridgeTarget.bak-agent" -Force
}
Copy-Item $bridgeSource $bridgeTarget -Force

$text = Get-Content $launch -Raw
$alreadyLoad = $text.Contains("Modules/AgentBridge")
$alreadyPoll = $text.Contains("agentBridge.Poll")

$backup = "$launch.bak-agent"
if (-not $alreadyLoad -and -not (Test-Path $backup)) {
    Copy-Item $launch $backup -Force
}

if (-not $alreadyLoad) {
    $marker = "`tif not self.devMode and not firstRunFile then"
    $idx = $text.IndexOf($marker)
    if ($idx -lt 0) {
        Write-Output (@{ ok = $false; error = "Unsupported Launch.lua: update-check marker not found. Restore from $backup if needed." } | ConvertTo-Json -Compress)
        exit 2
    }
    $loadBlock = @"
	-- AI agent bridge (loaded from Modules/AgentBridge.lua)
	local agentBridge, agentErr = LoadModule("Modules/AgentBridge")
	if agentBridge then
		local initErr = PCall(agentBridge.Init, agentBridge)
		if initErr then
			self:ShowErrMsg("In AgentBridge Init: %s", initErr)
		else
			self.agentBridge = agentBridge
		end
	elseif agentErr then
		self:ShowErrMsg("Error loading AgentBridge: %s", agentErr)
	end

"@
    $text = $text.Insert($idx, $loadBlock)
}

if (-not $alreadyPoll) {
    $marker = "`tself.devModeAlt = self.devMode and IsKeyDown(`"ALT`")"
    $idx = $text.IndexOf($marker)
    if ($idx -lt 0) {
        Write-Output (@{ ok = $false; error = "Unsupported Launch.lua: OnFrame marker not found. Restore from $backup if needed." } | ConvertTo-Json -Compress)
        exit 2
    }
    $pollBlock = @"
	if self.agentBridge then
		local pollErr = PCall(self.agentBridge.Poll, self.agentBridge)
		if pollErr then
			ConPrintf("In AgentBridge Poll: %s", pollErr)
		end
	end
"@
    $text = $text.Insert($idx, $pollBlock + "`r`n")
}

[System.IO.File]::WriteAllText($launch, $text, (New-Object System.Text.UTF8Encoding($false)))
Write-Output (@{
    ok = $true
    pobDir = $PobDir
    bridge = $bridgeTarget
    launch = $launch
    backup = $backup
    alreadyInstalled = ($alreadyLoad -and $alreadyPoll)
} | ConvertTo-Json -Compress)
