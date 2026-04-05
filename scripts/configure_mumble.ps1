#Requires -RunAsAdministrator
<#
.SYNOPSIS
    PitBox — Write Mumble Server 1.3.4 ICE configuration (Fastest Lap internal deployment).

.DESCRIPTION
    Locates mumble-server.ini (or murmur.ini for the 1.3.4 static build) and
    writes (or updates) the ICE settings required for PitBox to connect:

        ice="tcp -h 127.0.0.1 -p 6502"
        icesecretread=fastestlap
        icesecretwrite=fastestlap

    If any key already exists, it is updated in place.
    The original file is backed up before any changes are written.
    After writing the config, the script restarts Mumble with an explicit
    -ini argument so the new settings are guaranteed to load.

.NOTES
    Target version  : Mumble Server (Murmur) 1.3.4
                      Do NOT run against 1.5.x — Ice was removed in that version.
    Mumble license  : BSD 2-Clause  |  https://www.mumble.info/
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

# Known exe locations — used to launch Mumble directly if no service exists
# (keep in sync with install_mumble.ps1 / check_mumble_integration.ps1)
$MumbleExePaths = @(
    # x86 Program Files — active install on this machine (murmur.exe first)
    "C:\Program Files (x86)\Mumble\murmur.exe",
    "C:\Program Files (x86)\Mumble\mumble-server.exe",
    "C:\Program Files (x86)\Mumble\server\murmur.exe",
    "C:\Program Files (x86)\Mumble\server\mumble-server.exe",
    # 64-bit Program Files
    "C:\Program Files\Mumble\murmur.exe",
    "C:\Program Files\Mumble\mumble-server.exe",
    "C:\Program Files\Mumble\server\murmur.exe",
    "C:\Program Files\Mumble\server\mumble-server.exe",
    # Manual/zip extract locations
    "C:\PitBox\mumble\murmur.exe",
    "C:\PitBox\mumble\mumble-server.exe",
    "C:\Mumble\murmur.exe",
    "C:\Mumble\mumble-server.exe"
)

# ---------------------------------------------------------------------------
# Known locations for murmur.ini / mumble-server.ini on Windows
# ---------------------------------------------------------------------------
$IniSearchPaths = @(
    # x86 Program Files — active install on this machine (murmur.ini first)
    "C:\Program Files (x86)\Mumble\murmur.ini",
    "C:\Program Files (x86)\Mumble\mumble-server.ini",
    "C:\Program Files (x86)\Mumble\server\murmur.ini",
    "C:\Program Files (x86)\Mumble\server\mumble-server.ini",
    # 64-bit Program Files
    "C:\Program Files\Mumble\murmur.ini",
    "C:\Program Files\Mumble\mumble-server.ini",
    "C:\Program Files\Mumble\server\murmur.ini",
    "C:\Program Files\Mumble\server\mumble-server.ini",
    # ProgramData (installer may place ini here)
    "C:\ProgramData\Mumble Server\mumble-server.ini",
    "C:\ProgramData\Mumble\mumble-server.ini",
    # Manual/zip extract locations
    "C:\PitBox\mumble\murmur.ini",
    "C:\PitBox\mumble\mumble-server.ini",
    "C:\Mumble\murmur.ini",
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
    # Match both active lines (key=) and commented-out lines (;key= or ; key=)
    # so that `;icesecretread=` gets replaced rather than left commented out.
    $pattern = "^\s*;?\s*$([regex]::Escape($Key))\s*="
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
        # Key not found anywhere — append it at the end
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

# ---------------------------------------------------------------------------
# Helper: find mumble-server.exe from the known paths list
# ---------------------------------------------------------------------------
function Find-MumbleExe {
    foreach ($p in $MumbleExePaths) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Helper: verify ICE port is listening after (re)start
# ---------------------------------------------------------------------------
function Test-IcePort {
    $conn = Get-NetTCPConnection -LocalAddress "127.0.0.1" `
                                 -LocalPort 6502 `
                                 -State Listen `
                                 -ErrorAction SilentlyContinue
    return ($null -ne $conn)
}

# ---------------------------------------------------------------------------
# 5. Restart Mumble with the explicit -ini path so ICE settings are loaded
# ---------------------------------------------------------------------------
Write-Host ""
$restart = Read-Host "Restart Mumble Server now so settings take effect? [Y/n]"
if ($restart -eq "n" -or $restart -eq "N") {
    Write-Host "Skipped restart. Changes will take effect on next Mumble start." -ForegroundColor Yellow
} else {
    $mumbleExe = Find-MumbleExe
    if (-not $mumbleExe) {
        Write-Host "[!!] mumble-server.exe not found — cannot restart automatically." -ForegroundColor Red
        Write-Host "     Restart Mumble manually and pass: -ini `"$iniPath`""
    } else {
        $svc = Get-Service -Name $MumbleServiceName -ErrorAction SilentlyContinue

        if ($svc) {
            # --- Windows service path ---
            # Update the service ImagePath so it always starts with -ini explicit.
            # This fixes the root cause: sc.exe stores the full command line that
            # the SCM uses, so future auto-starts will also pick up the right config.
            Write-Host "Mumble Windows service found (status: $($svc.Status))." -ForegroundColor White

            $binPath = "`"$mumbleExe`" -ini `"$iniPath`""
            Write-Host "Updating service ImagePath to: $binPath" -ForegroundColor White
            $scResult = & sc.exe config $MumbleServiceName binPath= $binPath 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[!!] sc.exe config failed: $scResult" -ForegroundColor Yellow
                Write-Host "     Proceeding with plain Restart-Service anyway." -ForegroundColor Yellow
            } else {
                Write-Host "[OK] Service ImagePath updated." -ForegroundColor Green
            }

            Write-Host "Stopping $MumbleServiceName..." -ForegroundColor White
            Stop-Service -Name $MumbleServiceName -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2

            Write-Host "Starting $MumbleServiceName..." -ForegroundColor White
            Start-Service -Name $MumbleServiceName
            Start-Sleep -Seconds 3

            $svc.Refresh()
            if ($svc.Status -eq "Running") {
                Write-Host "[OK] Service running." -ForegroundColor Green
            } else {
                Write-Host "[!!] Service status: $($svc.Status). Check Windows Event Viewer." -ForegroundColor Yellow
            }

        } else {
            # --- No Windows service — direct process launch ---
            Write-Host "No Mumble Windows service found." -ForegroundColor Yellow
            Write-Host "Stopping any running mumble-server / murmur process..." -ForegroundColor White

            # Kill existing instance so the new one can bind port 6502
            @("mumble-server", "murmur") | ForEach-Object {
                Get-Process -Name $_ -ErrorAction SilentlyContinue | Stop-Process -Force
            }
            Start-Sleep -Seconds 2

            # Launch with explicit -ini so ICE settings are loaded
            Write-Host "Starting: $mumbleExe -ini `"$iniPath`"" -ForegroundColor White
            Start-Process -FilePath $mumbleExe -ArgumentList '-ini', "`"$iniPath`"" -WindowStyle Hidden
            Start-Sleep -Seconds 3
            Write-Host "[OK] Mumble Server launched." -ForegroundColor Green
        }

        # --- Verify ICE port is now listening ---
        Write-Host ""
        Write-Host "Verifying ICE port 6502..." -ForegroundColor White
        if (Test-IcePort) {
            Write-Host "[OK] 127.0.0.1:6502 is listening. ICE integration ready." -ForegroundColor Green
        } else {
            Write-Host "[FAIL] Port 6502 is NOT listening after restart." -ForegroundColor Red
            Write-Host "       Possible causes:"
            Write-Host "         - Mumble is taking longer to start (wait 5s and re-run check_mumble_integration.ps1)"
            Write-Host "         - The ini file has a syntax error preventing ICE from loading"
            Write-Host "         - Another process has already bound port 6502"
            Write-Host "       Run:  netstat -an | findstr 6502  to investigate."
        }
    }
}

Write-Host ""
Write-Host "Done. Run check_mumble_integration.ps1 to verify." -ForegroundColor Cyan
Write-Host ""
