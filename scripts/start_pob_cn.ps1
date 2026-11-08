param(
    [switch] $Force
)

$ErrorActionPreference = "Stop"
$bridge = "http://127.0.0.1:49090/ping"
$skillRoot = Split-Path $PSScriptRoot -Parent
if ($env:POB_AGENT_DATA) {
    $dataRoot = $env:POB_AGENT_DATA
} elseif ($env:LOCALAPPDATA) {
    $dataRoot = Join-Path $env:LOCALAPPDATA "pob-bd-builder"
} else {
    $dataRoot = Join-Path $env:USERPROFILE ".pob-bd-builder"
}
$runtimePath = Join-Path $dataRoot "runtime.json"
$discover = Join-Path $PSScriptRoot "discover_env.py"
$python = (Get-Command python -ErrorAction SilentlyContinue).Source

function Test-Bridge {
    try {
        $r = Invoke-RestMethod -Uri $bridge -TimeoutSec 2
        return [bool]$r.ok
    } catch {
        return $false
    }
}

function Write-Result($obj) {
    Write-Output ($obj | ConvertTo-Json -Compress)
}

if (-not $python -or -not (Test-Path $discover)) {
    Write-Result @{ ok = $false; error = "Python or discover_env.py not found"; python = $python; discover = $discover }
    exit 2
}
# Step 1: inspect the local runtime before starting anything.
$discoveryOut = & $python $discover --write --fix --quick 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $runtimePath)) {
    Write-Result @{ ok = $false; error = "Environment discovery failed"; detail = $discoveryOut.Trim() }
    exit 2
}
$runtime = Get-Content $runtimePath -Raw | ConvertFrom-Json
if (-not $runtime.ok) {
    Write-Result @{
        ok = $false
        error = "PoB / PoeCharm / zh-rCN / MCP runtime incomplete; if AgentBridge is missing run install/install-bridge.cmd"
        missing = $runtime.missing
        installer = $runtime.installer
        runtime = $runtime
    }
    exit 2
}

$charmExe = $runtime.charmExe
$pobDir = $runtime.pobDir
$mcpServer = $runtime.mcpServer
$pobCli = $runtime.pobCli
$agentDir = $runtime.agentDir
$python = if ($runtime.python) { $runtime.python } else { $python }

$charm = Get-Process -Name "PoeCharm3" -ErrorAction SilentlyContinue | Select-Object -First 1
$english = Get-Process -Name "Path of Building" -ErrorAction SilentlyContinue | Select-Object -First 1

if (Test-Bridge) {
    if ($charm -and $charm.MainWindowTitle -match "Path of Building") {
        Write-Result @{
            ok = $true; alreadyRunning = $true; launcher = "PoeCharm3"; title = $charm.MainWindowTitle
            pobDir = $pobDir; translate = $runtime.translate.dir; mcpServer = $mcpServer
        }
        exit 0
    }
    if ($english) {
        Write-Result @{ ok = $false; error = "当前运行的是英文版 Path of Building.exe；请先保存并关闭，再运行本脚本启动中文版"; englishPid = $english.Id }
        exit 2
    }
    Write-Result @{
        ok = $true; alreadyRunning = $true; title = "bridge is up"
        pobDir = $pobDir; translate = $runtime.translate.dir; mcpServer = $mcpServer
    }
    exit 0
}

if ($english -and -not $charm) {
    Write-Result @{ ok = $false; error = "当前运行的是英文版 Path of Building.exe；请先保存并关闭，再运行本脚本启动中文版"; englishPid = $english.Id }
    exit 2
}

if (-not $charm) {
    if (-not (Test-Path $charmExe)) {
        Write-Result @{ ok = $false; error = "找不到 PoeCharm3.exe: $charmExe" }
        exit 2
    }
    Start-Process -FilePath $charmExe -WorkingDirectory (Split-Path $charmExe) | Out-Null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        $charm = Get-Process -Name "PoeCharm3" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($charm -and $charm.MainWindowHandle -ne 0) { break }
    }
}

if (-not $charm) {
    Write-Result @{ ok = $false; error = "PoeCharm3 未能启动" }
    exit 2
}

# Bring the launcher forward so UI Automation can see it.
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class PobCnWin32 {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int cmd);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, int dx, int dy, uint data, UIntPtr extra);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@ | Out-Null
$charm.Refresh()
[PobCnWin32]::ShowWindow($charm.MainWindowHandle, 9) | Out-Null
[PobCnWin32]::SetForegroundWindow($charm.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 500

# Qt's accessibility tree can hang PowerShell UI Automation.  Use a topmost
# window + coordinate click instead: the launcher layout is fixed (Settings.conf).
[PobCnWin32]::SetWindowPos($charm.MainWindowHandle, [IntPtr](-1), 0, 0, 0, 0, 0x0001 -bor 0x0002 -bor 0x0040) | Out-Null
[PobCnWin32]::SetForegroundWindow($charm.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 500

$rect = New-Object PobCnWin32+RECT
[PobCnWin32]::GetWindowRect($charm.MainWindowHandle, [ref]$rect) | Out-Null
$w = $rect.Right - $rect.Left
$h = $rect.Bottom - $rect.Top
$candidates = @(
    @([int]($rect.Left + $w * 0.916), [int]($rect.Top + $h * 0.129)),
    @([int]($rect.Left + $w * 0.930), [int]($rect.Top + $h * 0.129)),
    @([int]($rect.Left + $w * 0.900), [int]($rect.Top + $h * 0.129))
)

foreach ($point in $candidates) {
    [PobCnWin32]::SetCursorPos($point[0], $point[1]) | Out-Null
    Start-Sleep -Milliseconds 150
    [PobCnWin32]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
    [PobCnWin32]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Bridge) {
            # Do not leave the launcher always-on-top.
            [PobCnWin32]::SetWindowPos($charm.MainWindowHandle, [IntPtr](-2), 0, 0, 0, 0, 0x0001 -bor 0x0002 -bor 0x0040) | Out-Null
            $mcpOk = $false
            $mcpDetail = $null
            try {
                Push-Location $agentDir
                $env:PYTHONUTF8 = "1"
                $mcpOut = & $python $pobCli --compact state 2>&1 | Out-String
                $mcpOk = ($LASTEXITCODE -eq 0 -and $mcpOut -match '"ok"\s*:\s*true')
                if (-not $mcpOk) { $mcpDetail = $mcpOut.Trim() }
            } catch {
                $mcpDetail = $_.Exception.Message
            } finally {
                Pop-Location
            }
            Write-Result @{
                ok = $true
                launched = $true
                launcher = "PoeCharm3"
                method = "coordinate"
                mcpOk = $mcpOk
                mcpServer = $mcpServer
                mcpDetail = $mcpDetail
                pobDir = $pobDir
                translate = $runtime.translate.dir
            }
            exit 0
        }
    }
}

[PobCnWin32]::SetWindowPos($charm.MainWindowHandle, [IntPtr](-2), 0, 0, 0, 0, 0x0001 -bor 0x0002 -bor 0x0040) | Out-Null
Write-Result @{ ok = $false; error = "PoeCharm3 is running but PoB did not start. Click the launch button manually, or check the update prompt." }
exit 2
