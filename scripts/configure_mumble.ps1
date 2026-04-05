#Requires -RunAsAdministrator
<#
.SYNOPSIS
    PitBox — Write Mumble Server ICE configuration (Fastest Lap internal deployment).

.DESCRIPTION
    Locates mumble-server.ini and writes (or updates) the ICE settings
    required for PitBox to connect to the running Mumble Server:

        ice="tcp -h 127.0.0.1 -p 6502"
        icesecretread=fastestlap
        icesecretwrite=fastestlap

    If a [murmur] section does not exist, the keys are appended to the end
    of the file.  If any key already exists, it is updated in place.
    The original file is backed up before any changes are written.

    After writing the config, the script optionally restarts the
    Mumble Server Windows service so the new settings take effect.

.NOTES
    Mumble is an external dependency — it is NOT bundled with PitBox.
    License: BSD 2-Clause  |  https://www.mumble.info/
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Target ICE settings for PitBox integration
# ---------------------------------------------------------------------------
$IceEndpoint      = "tcp -h 127.0.0.1 -p 6502"
$IceSecretRead    = "fastestlap"
$IceSecretWrite   = "fastestlap"

# Service name used by the Mumble Server Windows installer
$MumbleServiceName = "Murmur"

# ---------------------------------------------------------------------------
# Known locations for mumble-server.ini on Windows
# ---------------------------------------------------------------------------
$IniSearchPaths = @(
    "C:\ProgramData\Mumble Server\mumble-server.ini",
    "C:\ProgramData\Mumble\mumble-server.ini",
    "C:\Program Files\Mumble\mumble-server.ini",
    "C:\Program Files (x86)\Mumble\mumble-server.ini",
    "C:\Mumble\mumble-server.ini"
)

# ---------------------------------------------------------------------------
# Helper: find existing ini file
# ---------------------------------------------------------------------------
function Find-MumbleIni {
    foreach ($p in $IniSearchPaths) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Helper: set or replace a key inside an INI file (simple line-based)
# Returns updated lines array.
# ---------------------------------------------------------------------------
function Set-IniKey {
    param(
        [string[]] $Lines,
        [string]   $Key,
        [string]   $Value
    )
    $pattern = "^\s*$([regex]::Escape($Key))\s*="
    $newLine  = "$Key=$Value"
    $replaced = $false
    $out = for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match $pattern) {
            $newLine
            $replaced = $true
        } else {
            $Lines[$i]
        }
    }
    if (-not $replaced) {
        # Key not found — append it at the end
        $out += $newLine
    }
    return $out
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== PitBox — Mumble Server ICE Configuration ===" -ForegroundColor Cyan
Write-Host ""

# 1. Find the ini file
$iniPath = Find-MumbleIni

if (-not $iniPath) {
    # If not found in known locations, ask the operator to provide the path
    Write-Host "[?]  mumble-server.ini not found in default locations." -ForegroundColor Yellow
    $iniPath = Read-Host "Enter full path to mumble-server.ini (or press Enter to create at default)"
    if (-not $iniPath) {
        $iniPath = "C:\ProgramData\Mumble Server\mumble-server.ini"
        $dir = Split-Path $iniPath
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        Write-Host "[+]  Will create new ini at: $iniPath" -ForegroundColor White
    }
}

Write-Host "[OK] INI file: $iniPath" -ForegroundColor Green

# 2. Back up the original file before making any changes
if (Test-Path $iniPath) {
    $backupPath = "$iniPath.bak_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    Copy-Item $iniPath $backupPath
    Write-Host "[OK] Backup saved: $backupPath" -ForegroundColor Green
    $lines = Get-Content $iniPath -Encoding UTF8
} else {
    Write-Host "[+]  Creating new file (no existing file to back up)." -ForegroundColor White
    $lines = @()
}

# 3. Apply the three ICE settings
Write-Host ""
Write-Host "Applying ICE settings..." -ForegroundColor White

$lines = Set-IniKey -Lines $lines -Key "ice"            -Value "`"$IceEndpoint`""
$lines = Set-IniKey -Lines $lines -Key "icesecretread"  -Value $IceSecretRead
$lines = Set-IniKey -Lines $lines -Key "icesecretwrite" -Value $IceSecretWrite

# 4. Write the updated file
Set-Content -Path $iniPath -Value $lines -Encoding UTF8
Write-Host "[OK] Written:" -ForegroundColor Green
Write-Host "       ice=`"$IceEndpoint`""
Write-Host "       icesecretread=$IceSecretRead"
Write-Host "       icesecretwrite=$IceSecretWrite"

# 5. Optionally restart the Mumble service
Write-Host ""
$svc = Get-Service -Name $MumbleServiceName -ErrorAction SilentlyContinue
if ($svc) {
    Write-Host "Mumble Server service found (status: $($svc.Status))." -ForegroundColor White
    $restart = Read-Host "Restart the Mumble service now so settings take effect? [Y/n]"
    if ($restart -ne "n" -and $restart -ne "N") {
        Write-Host "Restarting $MumbleServiceName..." -ForegroundColor White
        Restart-Service -Name $MumbleServiceName -Force
        Start-Sleep -Seconds 2
        $svc.Refresh()
        if ($svc.Status -eq "Running") {
            Write-Host "[OK] Service restarted and running." -ForegroundColor Green
        } else {
            Write-Host "[!!] Service status: $($svc.Status). Check Windows Event Viewer." -ForegroundColor Yellow
        }
    } else {
        Write-Host "Skipped restart. Changes will take effect on next service start." -ForegroundColor Yellow
    }
} else {
    Write-Host "[?]  Mumble service '$MumbleServiceName' not found." -ForegroundColor Yellow
    Write-Host "     Start Mumble Server manually or check the service name in services.msc."
}

Write-Host ""
Write-Host "Done. Run check_mumble_integration.ps1 to verify." -ForegroundColor Cyan
Write-Host ""
