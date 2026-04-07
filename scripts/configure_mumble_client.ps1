<#
.SYNOPSIS
    PitBox -- Configure Mumble CLIENT on a sim PC for Race Control voice comms.

.DESCRIPTION
    Called automatically by PitBoxInstaller.exe after Mumble 1.3.4 is installed.
    Runs as the LOGGED-IN USER (runasoriginaluser in Inno Setup [Run]) so that the
    Windows Startup folder shortcut is created in the correct user profile.

    Steps:
      1. Read agent_id from PitBox agent config  (e.g. "Sim1")
      2. Build  mumble://Sim1@192.168.1.200:64738/Race%20Control
      3. Write  mumble_server_url  into agent_config.json so the PitBox agent
         uses it when the controller calls POST /launch-mumble.
      4. Find   mumble.exe  from standard install locations.
      5. Create a Windows Startup folder shortcut so Mumble auto-launches
         at every login with the Race Control URL.
      6. Log every step and verify the result.

    Idempotent -- safe to re-run on an already-configured sim.
    Does NOT touch mumble.sqlite, the registry, or any Mumble config file.

.NOTES
    Startup shortcut path (per-user):
      %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PitBox-Mumble.lnk
    This shortcut is created in the profile of whoever runs the installer.
#>

param(
    [string] $AgentConfigPath  = "C:\PitBox\Agent\config\agent_config.json",
    [string] $MumbleServerHost = "192.168.1.200",
    [int]    $MumbleServerPort = 64738,
    [string] $MumbleChannel    = "Race Control"
)

$ErrorActionPreference = "Continue"

function Log([string]$msg)  { Write-Host "[PitBox/Mumble] $msg" }
function LogOK([string]$msg)  { Write-Host "[PitBox/Mumble] [OK]   $msg" -ForegroundColor Green }
function LogWarn([string]$msg){ Write-Host "[PitBox/Mumble] [WARN] $msg" -ForegroundColor Yellow }
function LogFail([string]$msg){ Write-Host "[PitBox/Mumble] [FAIL] $msg" -ForegroundColor Red }

Log ""
Log "=== PitBox Mumble Client Setup ==="
Log "Server  : ${MumbleServerHost}:${MumbleServerPort}"
Log "Channel : $MumbleChannel"
Log ""

# ---------------------------------------------------------------------------
# Step 1  --  Resolve sim name from agent config
# ---------------------------------------------------------------------------
Log "Step 1: Resolving sim name..."

$simName = $env:COMPUTERNAME

if (Test-Path $AgentConfigPath) {
    try {
        $cfg = Get-Content $AgentConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($cfg.agent_id -and $cfg.agent_id.Trim() -ne "") {
            $simName = $cfg.agent_id.Trim()
        }
        LogOK "Sim name from agent config: '$simName'"
    } catch {
        LogWarn "Could not parse agent config -- falling back to COMPUTERNAME: $simName"
    }
} else {
    LogWarn "Agent config not found at $AgentConfigPath -- using COMPUTERNAME: $simName"
}

# ---------------------------------------------------------------------------
# Step 2  --  Build mumble:// URL
# ---------------------------------------------------------------------------
Log ""
Log "Step 2: Building mumble:// URL..."

$channelEncoded = $MumbleChannel -replace ' ', '%20'
$mumbleUrl = "mumble://${simName}@${MumbleServerHost}:${MumbleServerPort}/${channelEncoded}"

LogOK "URL: $mumbleUrl"

# ---------------------------------------------------------------------------
# Step 3  --  Write mumble_server_url into agent_config.json
# ---------------------------------------------------------------------------
Log ""
Log "Step 3: Writing mumble_server_url to agent config..."

if (Test-Path $AgentConfigPath) {
    try {
        $raw = Get-Content $AgentConfigPath -Raw -Encoding UTF8
        $escapedUrl = $mumbleUrl -replace '"', '\"'

        if ($raw -match '"mumble_server_url"\s*:') {
            $raw = $raw -replace '"mumble_server_url"\s*:\s*"[^"]*"',
                                  "`"mumble_server_url`": `"$escapedUrl`""
            Log "        Updated existing mumble_server_url key."
        } else {
            $raw = $raw -replace '}\s*$',
                                  ",`n  `"mumble_server_url`": `"$escapedUrl`"`n}"
            Log "        Inserted mumble_server_url key."
        }

        Set-Content -Path $AgentConfigPath -Value $raw -Encoding UTF8 -NoNewline
        LogOK "Agent config saved: $AgentConfigPath"
        LogOK "mumble_server_url = $mumbleUrl"
    } catch {
        LogFail "Could not update agent config: $_"
    }
} else {
    LogWarn "Agent config not found -- mumble_server_url not written."
    LogWarn "The PitBox agent will NOT auto-connect Mumble until the config exists."
}

# ---------------------------------------------------------------------------
# Step 4  --  Find mumble.exe
# ---------------------------------------------------------------------------
Log ""
Log "Step 4: Locating mumble.exe..."

$mumbleCandidates = @(
    "C:\Program Files (x86)\Mumble\mumble.exe",
    "C:\Program Files\Mumble\mumble.exe",
    "C:\Program Files (x86)\Mumble\mumble-1.3\mumble.exe",
    "C:\Program Files\Mumble\mumble-1.3\mumble.exe"
)

$mumbleExe = $null
foreach ($candidate in $mumbleCandidates) {
    if (Test-Path $candidate) {
        $mumbleExe = $candidate
        break
    }
}

if ($mumbleExe) {
    LogOK "Found: $mumbleExe"
} else {
    LogFail "mumble.exe not found in standard locations."
    LogFail "Startup shortcut cannot be created without a valid mumble.exe path."
    LogFail "Ensure Mumble 1.3.4 MSI was installed before this script ran."
    exit 1
}

# ---------------------------------------------------------------------------
# Step 5  --  Create Windows Startup folder shortcut
# ---------------------------------------------------------------------------
Log ""
Log "Step 5: Creating Windows Startup shortcut..."

$startupFolder = [System.Environment]::GetFolderPath('Startup')
$shortcutPath  = Join-Path $startupFolder "PitBox-Mumble.lnk"

Log "        Startup folder  : $startupFolder"
Log "        Shortcut path   : $shortcutPath"
Log "        Target exe      : $mumbleExe"
Log "        Arguments       : $mumbleUrl"

try {
    $wsh = New-Object -ComObject WScript.Shell
    $sc  = $wsh.CreateShortcut($shortcutPath)
    $sc.TargetPath  = $mumbleExe
    $sc.Arguments   = $mumbleUrl
    $sc.Description = "PitBox Race Control voice comms (auto-connect)"
    $sc.WindowStyle = 7   # minimised
    $sc.Save()
    LogOK "Startup shortcut created: $shortcutPath"
} catch {
    LogFail "Could not create startup shortcut: $_"
    exit 1
}

# ---------------------------------------------------------------------------
# Step 6  --  Verify
# ---------------------------------------------------------------------------
Log ""
Log "Step 6: Verification..."

$allOk = $true

# Verify agent config contains the URL
if (Test-Path $AgentConfigPath) {
    try {
        $check = Get-Content $AgentConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($check.mumble_server_url -eq $mumbleUrl) {
            LogOK "agent_config.json  mumble_server_url = $($check.mumble_server_url)"
        } else {
            LogWarn "agent_config.json mumble_server_url mismatch."
            LogWarn "  Expected : $mumbleUrl"
            LogWarn "  Got      : $($check.mumble_server_url)"
            $allOk = $false
        }
    } catch {
        LogWarn "Could not re-read agent config for verification: $_"
    }
}

# Verify shortcut exists and points to the right exe
if (Test-Path $shortcutPath) {
    try {
        $wsh2   = New-Object -ComObject WScript.Shell
        $verify = $wsh2.CreateShortcut($shortcutPath)
        if ($verify.TargetPath -eq $mumbleExe -and $verify.Arguments -eq $mumbleUrl) {
            LogOK "Startup shortcut verified: target=$($verify.TargetPath) args=$($verify.Arguments)"
        } else {
            LogWarn "Startup shortcut content mismatch."
            $allOk = $false
        }
    } catch {
        LogWarn "Could not verify shortcut: $_"
    }
} else {
    LogFail "Startup shortcut not found at expected path: $shortcutPath"
    $allOk = $false
}

Log ""
if ($allOk) {
    LogOK "Setup complete."
    LogOK "On next login, Mumble will auto-launch and connect via:"
    LogOK "  $mumbleUrl"
    LogOK "The PitBox agent will also use this URL when the controller calls launch-mumble."
} else {
    LogWarn "Setup completed with warnings -- check output above."
}
Log ""
