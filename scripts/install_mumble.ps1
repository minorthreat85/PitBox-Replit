#Requires -RunAsAdministrator
<#
.SYNOPSIS
    PitBox — Mumble Server 1.3.4 install helper (Fastest Lap internal deployment).

.DESCRIPTION
    PitBox uses the ZeroC Ice interface to control Mumble Server.
    Ice was REMOVED in Mumble 1.5.x. The required version is 1.3.4.

    This script:
      1. Detects whether a Mumble Server binary exists on this machine.
      2. If found, checks its version and warns if it is NOT 1.3.x.
      3. If a 1.5.x (or other non-1.3.x) build is found, it advises
         uninstalling it first so it cannot conflict.
      4. If Mumble is absent (or only a wrong version is present), it
         prints the exact 1.3.4 download URL and install steps.

    After a successful install, run configure_mumble.ps1 to write
    the correct ICE settings.

.NOTES
    Target version  : Mumble Server (Murmur) 1.3.4
    Reason          : 1.3.4 is the last stable release with ZeroC Ice support.
                      Mumble 1.4.x deprecated Ice; 1.5.x removed it entirely.
    Mumble license  : BSD 2-Clause  |  https://www.mumble.info/
    ZeroC Ice       : separately installed via pip install zeroc-ice
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Target version constraint
# ---------------------------------------------------------------------------
$RequiredMajor = 1
$RequiredMinor = 3    # must be 1.3.x — 1.4.x or 1.5.x will NOT expose ICE

# Direct download URL for the 1.3.4 Windows installer
$Download134Url  = "https://github.com/mumble-voip/mumble/releases/download/1.3.4/murmur-static_x86-1.3.4.zip"
$Download134Page = "https://github.com/mumble-voip/mumble/releases/tag/1.3.4"

# ---------------------------------------------------------------------------
# Known install locations (1.3.x installs to Program Files\Mumble\murmur.exe
# or ships as a zip/static build in a custom folder).
# ---------------------------------------------------------------------------
$SearchPaths = @(
    # 1.5.x default — checked so we can detect and warn about it
    "C:\Program Files\Mumble\server\mumble-server.exe",
    "C:\Program Files\Mumble\server\murmur.exe",
    # Common 1.3.x MSI install location
    "C:\Program Files\Mumble\mumble-server.exe",
    "C:\Program Files\Mumble\murmur.exe",
    "C:\Program Files (x86)\Mumble\mumble-server.exe",
    "C:\Program Files (x86)\Mumble\murmur.exe",
    # Manual/zip extract locations
    "C:\Mumble\mumble-server.exe",
    "C:\Mumble\murmur.exe",
    "C:\PitBox\mumble\mumble-server.exe",
    "C:\PitBox\mumble\murmur.exe"
)

# ---------------------------------------------------------------------------
# Helper: detect Mumble from Windows registry uninstall entries
# ---------------------------------------------------------------------------
function Find-MumbleViaRegistry {
    $roots = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    foreach ($root in $roots) {
        $entries = Get-ItemProperty $root -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match "(?i)mumble" }
        foreach ($e in $entries) {
            $loc = $e.InstallLocation
            if ($loc -and (Test-Path $loc)) {
                foreach ($name in @("mumble-server.exe", "murmur.exe")) {
                    $candidate = Join-Path $loc $name
                    if (Test-Path $candidate) { return $candidate }
                    $candidate = Join-Path $loc "server\$name"
                    if (Test-Path $candidate) { return $candidate }
                }
            }
        }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Helper: read file version from an exe (returns "major.minor.build.rev" string)
# ---------------------------------------------------------------------------
function Get-ExeVersion([string] $exePath) {
    try {
        $v = (Get-Item $exePath).VersionInfo.FileVersionRaw
        if ($v) { return "$($v.Major).$($v.Minor).$($v.Build)" }
    } catch {}
    # Fallback: run the binary with --version and parse output
    try {
        $out = & $exePath "--version" 2>&1 | Select-Object -First 3
        if ($out -match "(\d+\.\d+\.\d+)") { return $Matches[1] }
    } catch {}
    return $null
}

# ---------------------------------------------------------------------------
# Main detection logic
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== PitBox — Mumble Server 1.3.4 Install Check ===" -ForegroundColor Cyan
Write-Host "    (PitBox requires 1.3.x — Ice was removed in 1.5.x)" -ForegroundColor DarkGray
Write-Host ""

$found = $null

foreach ($path in $SearchPaths) {
    if (Test-Path $path) { $found = $path; break }
}
if (-not $found) { $found = Find-MumbleViaRegistry }
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
    Write-Host "Mumble Server binary found:" -ForegroundColor White
    Write-Host "  $found" -ForegroundColor White
    Write-Host ""

    $ver = Get-ExeVersion $found
    if ($ver) {
        Write-Host "Version detected: $ver" -ForegroundColor White
        $parts  = $ver -split "\."
        $major  = [int]$parts[0]
        $minor  = if ($parts.Count -gt 1) { [int]$parts[1] } else { 0 }

        if ($major -eq $RequiredMajor -and $minor -eq $RequiredMinor) {
            Write-Host "[OK]  Version is 1.3.x — correct for PitBox ICE integration." -ForegroundColor Green
            Write-Host ""
            Write-Host "Run configure_mumble.ps1 to apply PitBox ICE settings."
        } else {
            Write-Host "[!!]  Version is $ver — this is NOT 1.3.x." -ForegroundColor Red
            Write-Host ""
            Write-Host "IMPORTANT: Mumble $ver does NOT support ZeroC Ice." -ForegroundColor Red
            Write-Host "PitBox cannot connect to this version." -ForegroundColor Red
            Write-Host ""
            Write-Host "--- Action required ---" -ForegroundColor Yellow
            Write-Host "1. Uninstall Mumble $ver via Windows Settings > Add/Remove Programs." -ForegroundColor Yellow
            Write-Host "   Search for 'Mumble' and uninstall any listed entry." -ForegroundColor Yellow
            Write-Host "   Also delete any leftover folders:" -ForegroundColor Yellow
            Write-Host "     C:\Program Files\Mumble\" -ForegroundColor Yellow
            Write-Host "     C:\Program Files (x86)\Mumble\" -ForegroundColor Yellow
            Write-Host "2. Download and install Mumble Server 1.3.4 from:" -ForegroundColor Yellow
            Write-Host "   $Download134Page" -ForegroundColor Cyan
            Write-Host "   File: murmur-static_x86-1.3.4.zip  (extract to C:\PitBox\mumble\)" -ForegroundColor Cyan
            Write-Host "3. Re-run this script to confirm 1.3.4 is detected." -ForegroundColor Yellow
            Write-Host "4. Then run configure_mumble.ps1." -ForegroundColor Yellow
        }
    } else {
        Write-Host "[??]  Could not read version from binary." -ForegroundColor Yellow
        Write-Host "      Verify manually that this is Mumble Server 1.3.x." -ForegroundColor Yellow
        Write-Host "      If it is 1.4.x or 1.5.x, uninstall it and install 1.3.4 instead." -ForegroundColor Yellow
    }
} else {
    Write-Host "[!!]  Mumble Server NOT found on this machine." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "PitBox requires Mumble Server (Murmur) version 1.3.4." -ForegroundColor White
    Write-Host "Do NOT install the latest 1.5.x — it removed the ICE interface." -ForegroundColor Red
    Write-Host ""
    Write-Host "Download Mumble Server 1.3.4 (Windows, static 32-bit build):" -ForegroundColor White
    Write-Host "  $Download134Page" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Recommended file:" -ForegroundColor White
    Write-Host "  murmur-static_x86-1.3.4.zip" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Install steps:" -ForegroundColor White
    Write-Host "  1. Extract the zip to C:\PitBox\mumble\"
    Write-Host "  2. Run murmur.exe once as Administrator to generate the default ini."
    Write-Host "  3. Close murmur.exe."
    Write-Host "  4. Run:  .\configure_mumble.ps1  (applies ICE settings)."
    Write-Host "  5. Run murmur.exe again — port 6502 should now be listening."
    Write-Host "  6. Run:  .\check_mumble_integration.ps1  to verify."
    Write-Host ""

    $openPage = Read-Host "Open the Mumble 1.3.4 release page now? [Y/n]"
    if ($openPage -ne "n" -and $openPage -ne "N") {
        Start-Process $Download134Page
    }
}

Write-Host ""
