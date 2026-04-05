#Requires -RunAsAdministrator
<#
.SYNOPSIS
    PitBox — Verify Mumble 1.3.x + ICE integration (Fastest Lap internal deployment).

.DESCRIPTION
    Runs five checks and prints a summary:

      1. mumble-server.exe / murmur.exe exists on disk.
      1b. Version is 1.3.x (Ice was removed in 1.5.x — wrong version = FAIL).
      2. Port 6502 is listening on 127.0.0.1 (ICE endpoint).
      3. The PitBox Python environment can `import Ice`.
      4. mumble-server.ini contains the expected ICE settings.

    Exits with code 0 if all checks pass, 1 if any check fails.

.NOTES
    Target version  : Mumble Server (Murmur) 1.3.4
    Mumble license  : BSD 2-Clause  |  https://www.mumble.info/
    ZeroC Ice       : separately installed via pip install zeroc-ice
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Expected values (must match configure_mumble.ps1)
# ---------------------------------------------------------------------------
$ExpectedIcePort         = 6502
$ExpectedIceHost         = "127.0.0.1"
$ExpectedIceEndpoint     = "tcp -h 127.0.0.1 -p 6502"
$ExpectedIceSecretRead   = "fastestlap"
$ExpectedIceSecretWrite  = "fastestlap"
$RequiredMumbleMajor     = 1
$RequiredMumbleMinor     = 3    # 1.3.x only — 1.4+ deprecated/removed Ice

# Path to the Python used by PitBox (update if the install location differs)
$PitBoxPython = "C:\PitBox\installed\python\python.exe"
if (-not (Test-Path $PitBoxPython)) {
    $PitBoxPython = (Get-Command python -ErrorAction SilentlyContinue)?.Source
    if (-not $PitBoxPython) {
        $PitBoxPython = (Get-Command python3 -ErrorAction SilentlyContinue)?.Source
    }
}

# Known ini locations (keep in sync with configure_mumble.ps1)
$IniSearchPaths = @(
    # 1.3.x static build default (murmur.ini lives next to the exe)
    "C:\PitBox\mumble\murmur.ini",
    "C:\PitBox\mumble\mumble-server.ini",
    # 1.5.x default (kept so we can detect a misconfigured machine)
    "C:\Program Files\Mumble\server\mumble-server.ini",
    "C:\ProgramData\Mumble Server\mumble-server.ini",
    "C:\ProgramData\Mumble\mumble-server.ini",
    "C:\Program Files\Mumble\mumble-server.ini",
    "C:\Program Files (x86)\Mumble\mumble-server.ini",
    "C:\Mumble\mumble-server.ini"
)

# Known exe locations (keep in sync with install_mumble.ps1)
$ExeSearchPaths = @(
    "C:\PitBox\mumble\mumble-server.exe",
    "C:\PitBox\mumble\murmur.exe",
    # 1.5.x default — listed so version-mismatch is caught
    "C:\Program Files\Mumble\server\mumble-server.exe",
    "C:\Program Files\Mumble\server\murmur.exe",
    "C:\Program Files\Mumble\mumble-server.exe",
    "C:\Program Files\Mumble\murmur.exe",
    "C:\Program Files (x86)\Mumble\mumble-server.exe",
    "C:\Program Files (x86)\Mumble\murmur.exe",
    "C:\Mumble\mumble-server.exe",
    "C:\Mumble\murmur.exe"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
$pass  = 0
$fail  = 0

function Show-Pass([string] $msg) {
    Write-Host "  [PASS]  $msg" -ForegroundColor Green
    $script:pass++
}

function Show-Fail([string] $msg) {
    Write-Host "  [FAIL]  $msg" -ForegroundColor Red
    $script:fail++
}

function Show-Warn([string] $msg) {
    Write-Host "  [WARN]  $msg" -ForegroundColor Yellow
}

function Get-ExeVersion([string] $exePath) {
    try {
        $v = (Get-Item $exePath).VersionInfo.FileVersionRaw
        if ($v) { return "$($v.Major).$($v.Minor).$($v.Build)" }
    } catch {}
    try {
        $out = & $exePath "--version" 2>&1 | Select-Object -First 3
        if ($out -match "(\d+\.\d+[\.\d]*)") { return $Matches[1] }
    } catch {}
    return $null
}

# ---------------------------------------------------------------------------
# Check 1 — mumble-server.exe / murmur.exe exists
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== PitBox — Mumble 1.3.x + ICE Integration Check ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Check 1: Mumble Server binary" -ForegroundColor White

$mumbleExe = $null
foreach ($p in $ExeSearchPaths) {
    if (Test-Path $p) { $mumbleExe = $p; break }
}
if (-not $mumbleExe) {
    $roots = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    foreach ($root in $roots) {
        $entries = Get-ItemProperty $root -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match "(?i)mumble" }
        foreach ($e in $entries) {
            if ($e.InstallLocation) {
                foreach ($name in @("mumble-server.exe", "murmur.exe")) {
                    $c = Join-Path $e.InstallLocation $name
                    if (Test-Path $c) { $mumbleExe = $c; break }
                    $c = Join-Path $e.InstallLocation "server\$name"
                    if (Test-Path $c) { $mumbleExe = $c; break }
                }
            }
            if ($mumbleExe) { break }
        }
        if ($mumbleExe) { break }
    }
}

if ($mumbleExe) {
    Show-Pass "Found: $mumbleExe"
} else {
    Show-Fail "mumble-server.exe / murmur.exe not found. Run install_mumble.ps1."
}

# ---------------------------------------------------------------------------
# Check 1b — version must be 1.3.x
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Check 1b: Version is 1.3.x (required for ICE support)" -ForegroundColor White

if ($mumbleExe) {
    $ver = Get-ExeVersion $mumbleExe
    if ($ver) {
        $parts = $ver -split "\."
        $maj   = [int]$parts[0]
        $min   = if ($parts.Count -gt 1) { [int]$parts[1] } else { 0 }

        if ($maj -eq $RequiredMumbleMajor -and $min -eq $RequiredMumbleMinor) {
            Show-Pass "Version $ver — correct (1.3.x supports ICE)."
        } else {
            Show-Fail "Version $ver detected — ICE requires 1.3.x."
            Show-Warn "Mumble $ver does NOT expose ZeroC Ice on port 6502."
            Show-Warn "Uninstall $ver, install 1.3.4 from:"
            Show-Warn "  https://github.com/mumble-voip/mumble/releases/tag/1.3.4"
        }
    } else {
        Show-Warn "Could not read version. Confirm manually that this is 1.3.x."
    }
} else {
    Show-Warn "Skipped (binary not found — see Check 1)."
}

# ---------------------------------------------------------------------------
# Check 2 — port 6502 is listening on 127.0.0.1
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Check 2: ICE port $ExpectedIcePort listening on $ExpectedIceHost" -ForegroundColor White

$listening = Get-NetTCPConnection -LocalAddress $ExpectedIceHost `
                                  -LocalPort $ExpectedIcePort `
                                  -State Listen `
                                  -ErrorAction SilentlyContinue

if ($listening) {
    $pid_ = ($listening | Select-Object -First 1).OwningProcess
    $proc = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
    Show-Pass "Port $ExpectedIcePort is listening (PID $pid_$(if ($proc) { ' — ' + $proc.ProcessName } else { '' }))."
} else {
    Show-Fail "Port $ExpectedIcePort not listening on $ExpectedIceHost."
    Show-Warn "If version check passed, start murmur.exe and confirm it loads the right ini."
    Show-Warn "If version check failed, port 6502 will never open until 1.3.4 is installed."
}

# ---------------------------------------------------------------------------
# Check 3 — PitBox Python can import Ice
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Check 3: Python can import Ice" -ForegroundColor White

if ($PitBoxPython -and (Test-Path $PitBoxPython)) {
    $iceTest = & $PitBoxPython -c "import Ice; print(Ice.__version__)" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Show-Pass "import Ice succeeded — version: $iceTest  |  Python: $PitBoxPython"
    } else {
        Show-Fail "import Ice failed. Run: pip install zeroc-ice  |  Python: $PitBoxPython"
        Show-Warn "Output: $iceTest"
    }
} else {
    Show-Fail "Python interpreter not found. Expected: $PitBoxPython"
    Show-Warn "Update `$PitBoxPython in this script or ensure Python is on PATH."
}

# ---------------------------------------------------------------------------
# Check 4 — mumble-server.ini has expected ICE settings
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Check 4: mumble-server.ini ICE settings" -ForegroundColor White

$iniPath = $null
foreach ($p in $IniSearchPaths) {
    if (Test-Path $p) { $iniPath = $p; break }
}

if (-not $iniPath) {
    Show-Fail "mumble-server.ini / murmur.ini not found. Run configure_mumble.ps1."
} else {
    Write-Host "         INI file: $iniPath" -ForegroundColor DarkGray
    $content = Get-Content $iniPath -Encoding UTF8

    function Get-IniValue([string] $key) {
        $line = $content | Where-Object { $_ -match "^\s*$([regex]::Escape($key))\s*=" } | Select-Object -First 1
        if ($line) { return ($line -replace "^\s*$([regex]::Escape($key))\s*=\s*", "").Trim('"').Trim() }
        return $null
    }

    $actualIce   = Get-IniValue "ice"
    $actualRead  = Get-IniValue "icesecretread"
    $actualWrite = Get-IniValue "icesecretwrite"

    if ($actualIce -eq $ExpectedIceEndpoint) {
        Show-Pass "ice=`"$actualIce`""
    } else {
        Show-Fail "ice mismatch. Expected: `"$ExpectedIceEndpoint`"  Got: `"$actualIce`""
    }

    if ($actualRead -eq $ExpectedIceSecretRead) {
        Show-Pass "icesecretread=$actualRead"
    } else {
        Show-Fail "icesecretread mismatch. Expected: $ExpectedIceSecretRead  Got: $actualRead"
    }

    if ($actualWrite -eq $ExpectedIceSecretWrite) {
        Show-Pass "icesecretwrite=$actualWrite"
    } else {
        Show-Fail "icesecretwrite mismatch. Expected: $ExpectedIceSecretWrite  Got: $actualWrite"
    }
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "-------------------------------------------" -ForegroundColor DarkGray
$total = $pass + $fail
Write-Host "Result: $pass / $total checks passed." -ForegroundColor $(if ($fail -eq 0) { "Green" } else { "Yellow" })

if ($fail -eq 0) {
    Write-Host "All checks passed. PitBox <-> Mumble ICE integration is ready." -ForegroundColor Green
} else {
    Write-Host "$fail check(s) failed. Common fixes:" -ForegroundColor Red
    Write-Host "  - Wrong Mumble version : uninstall 1.5.x, install 1.3.4 from"
    Write-Host "      https://github.com/mumble-voip/mumble/releases/tag/1.3.4"
    Write-Host "  - ICE not configured   : run configure_mumble.ps1"
    Write-Host "  - Ice not in Python    : pip install zeroc-ice"
    Write-Host "  - Port not listening   : start murmur.exe / mumble-server.exe"
}

Write-Host ""
exit $(if ($fail -eq 0) { 0 } else { 1 })
