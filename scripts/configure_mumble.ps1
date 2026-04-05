#Requires -RunAsAdministrator
<#
.SYNOPSIS
    PitBox -- Write Mumble Server ICE configuration (Fastest Lap internal deployment).

.DESCRIPTION
    Locates murmur.ini (or mumble-server.ini) and writes (or updates) the ICE
    settings required for PitBox to connect to the running Mumble Server:

        ice="tcp -h 127.0.0.1 -p 6502"
        icesecretread=fastestlap
        icesecretwrite=fastestlap

    Commented-out keys (e.g. ;icesecretread=) are uncommented and set.
    Active keys are updated in place.
    Keys not present are appended at the end of the file.
    The original file is backed up before any changes are written.
    After writing, the script restarts Mumble with an explicit -ini argument.

.NOTES
    Target version  : Mumble Server (Murmur) -- must support ZeroC Ice (1.3.x).
                      Mumble 1.5.x removed Ice; do not use that version.
    Mumble license  : BSD 2-Clause  |  https://www.mumble.info/
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Target ICE settings for PitBox integration
# ---------------------------------------------------------------------------
$IceEndpoint     = "tcp -h 127.0.0.1 -p 6502"
$IceSecretRead   = "fastestlap"
$IceSecretWrite  = "fastestlap"
$MumbleServiceName = "Murmur"

# ---------------------------------------------------------------------------
# Known exe locations (murmur.exe before mumble-server.exe; x86 first)
# ---------------------------------------------------------------------------
$MumbleExePaths = @(
    "C:\Program Files (x86)\Mumble\murmur.exe",
    "C:\Program Files (x86)\Mumble\mumble-server.exe",
    "C:\Program Files (x86)\Mumble\server\murmur.exe",
    "C:\Program Files (x86)\Mumble\server\mumble-server.exe",
    "C:\Program Files\Mumble\murmur.exe",
    "C:\Program Files\Mumble\mumble-server.exe",
    "C:\Program Files\Mumble\server\murmur.exe",
    "C:\Program Files\Mumble\server\mumble-server.exe",
    "C:\PitBox\mumble\murmur.exe",
    "C:\PitBox\mumble\mumble-server.exe",
    "C:\Mumble\murmur.exe",
    "C:\Mumble\mumble-server.exe"
)

# ---------------------------------------------------------------------------
# Known ini locations (murmur.ini before mumble-server.ini; x86 first)
# ---------------------------------------------------------------------------
$IniSearchPaths = @(
    "C:\Program Files (x86)\Mumble\murmur.ini",
    "C:\Program Files (x86)\Mumble\mumble-server.ini",
    "C:\Program Files (x86)\Mumble\server\murmur.ini",
    "C:\Program Files (x86)\Mumble\server\mumble-server.ini",
    "C:\Program Files\Mumble\murmur.ini",
    "C:\Program Files\Mumble\mumble-server.ini",
    "C:\Program Files\Mumble\server\murmur.ini",
    "C:\Program Files\Mumble\server\mumble-server.ini",
    "C:\ProgramData\Mumble Server\mumble-server.ini",
    "C:\ProgramData\Mumble\mumble-server.ini",
    "C:\PitBox\mumble\murmur.ini",
    "C:\PitBox\mumble\mumble-server.ini",
    "C:\Mumble\murmur.ini",
    "C:\Mumble\mumble-server.ini"
)

# ---------------------------------------------------------------------------
# Helper: find the ini file
# ---------------------------------------------------------------------------
function Find-MumbleIni {
    foreach ($p in $IniSearchPaths) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Helper: find the exe
# ---------------------------------------------------------------------------
function Find-MumbleExe {
    foreach ($p in $MumbleExePaths) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Helper: verify ICE port is listening
# ---------------------------------------------------------------------------
function Test-IcePort {
    $conn = Get-NetTCPConnection -LocalAddress "127.0.0.1" `
                                 -LocalPort 6502 `
                                 -State Listen `
                                 -ErrorAction SilentlyContinue
    return ($null -ne $conn)
}

# ---------------------------------------------------------------------------
# Helper: set or replace a key inside an INI file (PS 5.1 compatible)
# Matches both active (key=) and commented-out (;key=) forms.
# Returns an updated string array.
# ---------------------------------------------------------------------------
function Set-IniKey {
    param(
        [string[]] $Lines,
        [string]   $Key,
        [string]   $Value
    )
    # ;? matches an optional leading semicolon so commented keys get replaced too
    $pattern  = "^\s*;?\s*$([regex]::Escape($Key))\s*="
    $newLine  = "$Key=$Value"
    $replaced = $false
    $result   = New-Object System.Collections.Generic.List[string]

    foreach ($line in $Lines) {
        if ($line -match $pattern) {
            $result.Add($newLine)
            $replaced = $true
        } else {
            $result.Add($line)
        }
    }

    if (-not $replaced) {
        $result.Add($newLine)
    }

    return $result.ToArray()
}

# ===========================================================================
# Main
# ===========================================================================
Write-Host ""
Write-Host "=== PitBox -- Mumble Server ICE Configuration ===" -ForegroundColor Cyan
Write-Host ""

# 1. Locate the ini file
$iniPath = Find-MumbleIni

if (-not $iniPath) {
    Write-Host "[?]  murmur.ini / mumble-server.ini not found in default locations." -ForegroundColor Yellow
    $iniPath = Read-Host "Enter full path to murmur.ini (or press Enter to create at default)"
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

# 2. Back up the original
if (Test-Path $iniPath) {
    $backupPath = "$iniPath.bak_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    Copy-Item $iniPath $backupPath
    Write-Host "[OK] Backup saved: $backupPath" -ForegroundColor Green
    $lines = Get-Content $iniPath -Encoding UTF8
} else {
    Write-Host "[+]  Creating new file (no existing file to back up)." -ForegroundColor White
    $lines = @()
}

# 3. Apply the three ICE settings (uncomments if commented, updates if active, appends if absent)
Write-Host ""
Write-Host "Applying ICE settings..." -ForegroundColor White

$lines = Set-IniKey -Lines $lines -Key "ice"           -Value "`"$IceEndpoint`""
$lines = Set-IniKey -Lines $lines -Key "icesecretread"  -Value $IceSecretRead
$lines = Set-IniKey -Lines $lines -Key "icesecretwrite" -Value $IceSecretWrite

# 4. Write the updated file
Set-Content -Path $iniPath -Value $lines -Encoding UTF8
Write-Host "[OK] Written:" -ForegroundColor Green
Write-Host "       ice=`"$IceEndpoint`""
Write-Host "       icesecretread=$IceSecretRead"
Write-Host "       icesecretwrite=$IceSecretWrite"

# 5. Restart Mumble with explicit -ini so ICE settings are guaranteed to load
Write-Host ""
$restart = Read-Host "Restart Mumble Server now so settings take effect? [Y/n]"

if ($restart -eq "n" -or $restart -eq "N") {
    Write-Host "Skipped restart. Changes will take effect on next Mumble start." -ForegroundColor Yellow
} else {
    $mumbleExe = Find-MumbleExe

    if (-not $mumbleExe) {
        Write-Host "[!!] Mumble exe not found -- cannot restart automatically." -ForegroundColor Red
        Write-Host "     Start Mumble manually with: -ini `"$iniPath`""
    } else {
        $svc = Get-Service -Name $MumbleServiceName -ErrorAction SilentlyContinue

        if ($svc) {
            # Windows service path: update ImagePath so every future start uses -ini
            Write-Host "Mumble Windows service found (status: $($svc.Status))." -ForegroundColor White
            $binPath  = "`"$mumbleExe`" -ini `"$iniPath`""
            Write-Host "Updating service ImagePath to: $binPath" -ForegroundColor White
            $scResult = & sc.exe config $MumbleServiceName binPath= $binPath 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[!!] sc.exe config failed: $scResult" -ForegroundColor Yellow
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
            # No service: kill any running instance and relaunch with -ini
            Write-Host "No Mumble Windows service found." -ForegroundColor Yellow
            Write-Host "Stopping any running murmur / mumble-server process..." -ForegroundColor White
            @("murmur", "mumble-server") | ForEach-Object {
                Get-Process -Name $_ -ErrorAction SilentlyContinue | Stop-Process -Force
            }
            Start-Sleep -Seconds 2

            Write-Host "Starting: $mumbleExe -ini `"$iniPath`"" -ForegroundColor White
            Start-Process -FilePath $mumbleExe -ArgumentList "-ini", $iniPath -WindowStyle Hidden
            Start-Sleep -Seconds 3
            Write-Host "[OK] Mumble launched." -ForegroundColor Green
        }

        # Verify ICE port is now listening
        Write-Host ""
        Write-Host "Verifying ICE port 6502..." -ForegroundColor White
        if (Test-IcePort) {
            Write-Host "[OK] 127.0.0.1:6502 is listening. ICE integration ready." -ForegroundColor Green
        } else {
            Write-Host "[FAIL] Port 6502 is NOT listening after restart." -ForegroundColor Red
            Write-Host "       Check that this is Mumble 1.3.x (not 1.5.x)."
            Write-Host "       Run:  netstat -an | findstr 6502  to investigate."
        }
    }
}

Write-Host ""
Write-Host "Done. Run check_mumble_integration.ps1 to verify." -ForegroundColor Cyan
Write-Host ""
