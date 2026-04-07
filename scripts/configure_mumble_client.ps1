<#
.SYNOPSIS
    PitBox -- Configure Mumble CLIENT on a sim PC for Race Control voice comms.

.DESCRIPTION
    Called automatically by PitBoxInstaller.exe (pitbox.iss) after Mumble 1.3.4 is
    installed.  Must run in the LOGGED-IN USER's context (not SYSTEM) so that
    HKCU registry writes land in the correct hive.

    What this script does:
      1. Reads the PitBox agent config to obtain the sim's agent_id (e.g. "Sim1").
      2. Builds a mumble:// URL that auto-connects and auto-joins Race Control.
      3. Writes that URL into the agent config as `mumble_server_url` so the
         PitBox agent uses it when the controller calls POST /launch-mumble.
      4. Writes Mumble client registry keys (HKCU) so:
           - The correct username is set.
           - The Race Control server appears as a saved favourite.
      5. Logs every action taken and verifies the result.

    This script is idempotent -- safe to re-run on an already-configured sim.

.NOTES
    Mumble 1.3.4 stores client settings in:
      HKCU\Software\Mumble\Mumble\1.2\
    Server favourites use the QSettings array format:
      HKCU\Software\Mumble\Mumble\1.2\FavoriteServers\1\...
#>

param(
    [string] $AgentConfigPath   = "C:\PitBox\Agent\config\agent_config.json",
    [string] $MumbleServerHost  = "192.168.1.200",
    [int]    $MumbleServerPort  = 64738,
    [string] $MumbleChannel     = "Race Control"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"   # non-fatal errors logged, not thrown

$LogPrefix = "[PitBox/Mumble]"

function Log([string] $msg) { Write-Host "$LogPrefix $msg" }
function LogOK([string] $msg) { Write-Host "$LogPrefix [OK]  $msg" -ForegroundColor Green }
function LogWarn([string] $msg) { Write-Host "$LogPrefix [WARN] $msg" -ForegroundColor Yellow }
function LogFail([string] $msg) { Write-Host "$LogPrefix [FAIL] $msg" -ForegroundColor Red }

Log ""
Log "=== PitBox Mumble Client Configuration ==="
Log "Server : $MumbleServerHost`:$MumbleServerPort"
Log "Channel: $MumbleChannel"
Log ""

# ---------------------------------------------------------------------------
# Step 1 -- Determine sim name from agent config
# ---------------------------------------------------------------------------
Log "Step 1: Reading agent config..."
Log "        Config path: $AgentConfigPath"

$simName    = $env:COMPUTERNAME
$agentConfig = $null

if (Test-Path $AgentConfigPath) {
    try {
        $agentConfig = Get-Content $AgentConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $fromConfig  = ($agentConfig.agent_id -or $agentConfig.display_name)
        if ($agentConfig.agent_id)      { $simName = $agentConfig.agent_id }
        elseif ($agentConfig.display_name) { $simName = $agentConfig.display_name }
        LogOK "agent_id = '$simName' (from config)"
    } catch {
        LogWarn "Could not parse agent config JSON: $_"
        LogWarn "Falling back to COMPUTERNAME: $simName"
    }
} else {
    LogWarn "Agent config not found at $AgentConfigPath"
    LogWarn "Falling back to COMPUTERNAME: $simName"
}

# ---------------------------------------------------------------------------
# Step 2 -- Build mumble:// URL
# ---------------------------------------------------------------------------
Log ""
Log "Step 2: Building mumble:// URL..."

# URL-encode spaces as %20 in the channel name
$channelEncoded = $MumbleChannel -replace ' ', '%20'
$mumbleUrl = "mumble://${simName}@${MumbleServerHost}:${MumbleServerPort}/${channelEncoded}"

LogOK "mumble_server_url = $mumbleUrl"

# ---------------------------------------------------------------------------
# Step 3 -- Write mumble_server_url into agent config JSON
# ---------------------------------------------------------------------------
Log ""
Log "Step 3: Updating agent config with mumble_server_url..."

if (Test-Path $AgentConfigPath) {
    try {
        # Read raw and manipulate as text so we don't strip unrecognised fields
        $raw = Get-Content $AgentConfigPath -Raw -Encoding UTF8

        $escapedUrl = $mumbleUrl -replace '"', '\"'

        if ($raw -match '"mumble_server_url"\s*:') {
            # Update existing key
            $raw = $raw -replace '"mumble_server_url"\s*:\s*"[^"]*"',
                                  "`"mumble_server_url`": `"$escapedUrl`""
            Log "        Updated existing mumble_server_url key."
        } else {
            # Append before closing brace
            $raw = $raw -replace '}\s*$',
                                  ",`n  `"mumble_server_url`": `"$escapedUrl`"`n}"
            Log "        Inserted mumble_server_url key."
        }

        Set-Content -Path $AgentConfigPath -Value $raw -Encoding UTF8 -NoNewline
        LogOK "Agent config saved: $AgentConfigPath"
        Log   "        mumble_server_url = $mumbleUrl"
    } catch {
        LogFail "Could not update agent config: $_"
    }
} else {
    LogWarn "Agent config not found -- mumble_server_url not written to config."
    LogWarn "The PitBox agent will not auto-connect Mumble until the config exists."
}

# ---------------------------------------------------------------------------
# Step 4 -- Write Mumble client registry keys (HKCU -- user context required)
# ---------------------------------------------------------------------------
Log ""
Log "Step 4: Writing Mumble client registry keys (HKCU)..."
Log "        Current user: $($env:USERNAME)"

$mumbleRegBase = "HKCU:\Software\Mumble\Mumble\1.2"

try {
    # Base key
    if (-not (Test-Path $mumbleRegBase)) {
        New-Item -Path $mumbleRegBase -Force | Out-Null
        Log "        Created registry key: $mumbleRegBase"
    }

    # -- Username -------------------------------------------------------
    $netKey = "$mumbleRegBase\net"
    if (-not (Test-Path $netKey)) { New-Item -Path $netKey -Force | Out-Null }
    Set-ItemProperty -Path $netKey -Name "username" -Value $simName -Type String -Force
    LogOK "Registry net\username = '$simName'"

    # -- Server favourites ----------------------------------------------
    $favKey = "$mumbleRegBase\FavoriteServers"
    if (-not (Test-Path $favKey)) { New-Item -Path $favKey -Force | Out-Null }
    Set-ItemProperty -Path $favKey -Name "size" -Value 1 -Type DWord -Force

    $fav1Key = "$favKey\1"
    if (-not (Test-Path $fav1Key)) { New-Item -Path $fav1Key -Force | Out-Null }
    Set-ItemProperty -Path $fav1Key -Name "name"     -Value $MumbleChannel     -Type String -Force
    Set-ItemProperty -Path $fav1Key -Name "host"     -Value $MumbleServerHost  -Type String -Force
    Set-ItemProperty -Path $fav1Key -Name "port"     -Value $MumbleServerPort  -Type DWord  -Force
    Set-ItemProperty -Path $fav1Key -Name "username" -Value $simName           -Type String -Force
    Set-ItemProperty -Path $fav1Key -Name "password" -Value ""                 -Type String -Force

    LogOK "Registry FavoriteServers\1 written:"
    Log   "        name     = $MumbleChannel"
    Log   "        host     = $MumbleServerHost"
    Log   "        port     = $MumbleServerPort"
    Log   "        username = $simName"

} catch {
    LogWarn "Registry write failed (non-fatal): $_"
    LogWarn "Mumble will still auto-connect via the mumble:// URL when the agent launches it."
}

# ---------------------------------------------------------------------------
# Step 5 -- Verify
# ---------------------------------------------------------------------------
Log ""
Log "Step 5: Verification..."

$ok = $true

# Verify mumble_server_url in config
if (Test-Path $AgentConfigPath) {
    try {
        $verify = Get-Content $AgentConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $written = $verify.mumble_server_url
        if ($written -eq $mumbleUrl) {
            LogOK "Agent config mumble_server_url verified: $written"
        } else {
            LogWarn "Agent config mumble_server_url mismatch."
            LogWarn "  Expected : $mumbleUrl"
            LogWarn "  Got      : $written"
            $ok = $false
        }
    } catch {
        LogWarn "Could not re-read agent config for verification: $_"
    }
}

# Verify registry username
try {
    $regUser = (Get-ItemProperty -Path "$mumbleRegBase\net" -Name "username" -ErrorAction SilentlyContinue).username
    if ($regUser -eq $simName) {
        LogOK "Registry username verified: $regUser"
    } else {
        LogWarn "Registry username mismatch. Got: $regUser"
    }
} catch {
    LogWarn "Could not verify registry username: $_"
}

# Verify server favourite
try {
    $regHost = (Get-ItemProperty -Path "$mumbleRegBase\FavoriteServers\1" -Name "host" -ErrorAction SilentlyContinue).host
    if ($regHost -eq $MumbleServerHost) {
        LogOK "Registry FavoriteServers\1\host verified: $regHost"
    } else {
        LogWarn "Registry favourite host mismatch. Got: $regHost"
    }
} catch {
    LogWarn "Could not verify registry favourite: $_"
}

Log ""
if ($ok) {
    LogOK "Mumble client configuration complete."
    LogOK "When the controller calls POST /launch-mumble the agent will run:"
    LogOK "  mumble.exe $mumbleUrl"
    LogOK "This auto-connects to Race Control server and joins channel '$MumbleChannel'."
} else {
    LogWarn "Configuration completed with warnings. Check output above."
}
Log ""
