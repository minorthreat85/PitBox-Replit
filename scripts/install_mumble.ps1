#Requires -RunAsAdministrator
<#
.SYNOPSIS
    PitBox — Mumble Server install helper (Fastest Lap internal deployment).

.DESCRIPTION
    Checks whether Mumble Server (mumble-server.exe / murmur.exe) is already
    installed on this machine. If it is, reports the location and exits.
    If it is not, opens the official Mumble download page and prints the
    recommended installer filename so the operator can complete the install.

    After installing Mumble, run configure_mumble.ps1 to apply the
    correct ICE settings for PitBox integration.

.NOTES
    Mumble is an external dependency — it is NOT bundled with PitBox.
    License: BSD 2-Clause  |  https://www.mumble.info/
    ZeroC Ice (pip package) must also be installed in the Python environment.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Known install locations for Mumble Server on Windows
# (covers both legacy "Murmur" and current "mumble-server" naming)
# ---------------------------------------------------------------------------
$SearchPaths = @(
    "C:\Program Files\Mumble\mumble-server.exe",
    "C:\Program Files\Mumble\murmur.exe",
    "C:\Program Files (x86)\Mumble\mumble-server.exe",
    "C:\Program Files (x86)\Mumble\murmur.exe",
    "C:\Mumble\mumble-server.exe",
    "C:\Mumble\murmur.exe"
)

# ---------------------------------------------------------------------------
# Also search registry uninstall keys for a Mumble entry
# ---------------------------------------------------------------------------
function Find-MumbleViaRegistry {
    $roots = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    foreach ($root in $roots) {
        $entries = Get-ItemProperty $root -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match "(?i)mumble" }
        if ($entries) {
            foreach ($e in $entries) {
                $loc = $e.InstallLocation
                if ($loc -and (Test-Path $loc)) {
                    $exe = Join-Path $loc "mumble-server.exe"
                    if (-not (Test-Path $exe)) { $exe = Join-Path $loc "murmur.exe" }
                    if (Test-Path $exe) { return $exe }
                }
            }
        }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Main detection logic
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== PitBox — Mumble Server Install Check ===" -ForegroundColor Cyan
Write-Host ""

$found = $null

# 1. Check known fixed paths
foreach ($path in $SearchPaths) {
    if (Test-Path $path) {
        $found = $path
        break
    }
}

# 2. Check registry if not found yet
if (-not $found) {
    $found = Find-MumbleViaRegistry
}

# 3. Last resort: search PATH
if (-not $found) {
    $inPath = Get-Command "mumble-server.exe" -ErrorAction SilentlyContinue
    if ($inPath) { $found = $inPath.Source }
    else {
        $inPath = Get-Command "murmur.exe" -ErrorAction SilentlyContinue
        if ($inPath) { $found = $inPath.Source }
    }
}

# ---------------------------------------------------------------------------
# Report result
# ---------------------------------------------------------------------------
if ($found) {
    Write-Host "[OK]  Mumble Server found:" -ForegroundColor Green
    Write-Host "      $found" -ForegroundColor White
    Write-Host ""
    Write-Host "No installation needed."
    Write-Host "Run configure_mumble.ps1 to apply PitBox ICE settings."
} else {
    Write-Host "[!!]  Mumble Server NOT found on this machine." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Download the official Mumble installer from:" -ForegroundColor White
    Write-Host "  https://www.mumble.info/downloads/" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Recommended file (Windows 64-bit):" -ForegroundColor White
    Write-Host "  mumble_server-1.4.x.winx64.msi  (or latest stable)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Install steps:" -ForegroundColor White
    Write-Host "  1. Run the .msi installer as Administrator."
    Write-Host "  2. Accept defaults (installs to C:\Program Files\Mumble\)."
    Write-Host "  3. Let it run the first-time wizard; set a SuperUser password."
    Write-Host "  4. Then run:  .\configure_mumble.ps1"
    Write-Host ""

    # Open the download page automatically if running interactively
    $openPage = Read-Host "Open the Mumble download page now? [Y/n]"
    if ($openPage -ne "n" -and $openPage -ne "N") {
        Start-Process "https://www.mumble.info/downloads/"
    }
}

Write-Host ""
