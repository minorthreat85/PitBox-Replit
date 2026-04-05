#Requires -RunAsAdministrator
<#
.SYNOPSIS
    PitBox — Verify Mumble + ICE integration (Fastest Lap internal deployment).

.DESCRIPTION
    Runs four checks and prints a summary:

      1. mumble-server.exe (or murmur.exe) is present on disk.
      2. Port 6502 is listening on 127.0.0.1 (ICE endpoint).
      3. The PitBox Python environment can `import Ice`.
      4. mumble-server.ini contains the expected ICE settings.

    Exits with code 0 if all checks pass, 1 if any check fails.

.NOTES
    Mumble is an external dependency — it is NOT bundled with PitBox.
    License: BSD 2-Clause  |  https://www.mumble.info/
    ZeroC Ice (zeroc-ice pip package) is also an external dependency.
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

# Path to the Python used by PitBox (update if the install location differs)
$PitBoxPython = "C:\PitBox\installed\python\python.exe"
# Fallback: just use whatever python3 / python is on PATH
if (-not (Test-Path $PitBoxPython)) {
    $PitBoxPython = (Get-Command python -ErrorAction SilentlyContinue)?.Source
    if (-not $PitBoxPython) {
        $PitBoxPython = (Get-Command python3 -ErrorAction SilentlyContinue)?.Source
    }
}

# Known ini locations (keep in sync with configure_mumble.ps1)
$IniSearchPaths = @(
    "C:\ProgramData\Mumble Server\mumble-server.ini",
    "C:\ProgramData\Mumble\mumble-server.ini",
    "C:\Program Files\Mumble\mumble-server.ini",
    "C:\Program Files (x86)\Mumble\mumble-server.ini",
    "C:\Mumble\mumble-server.ini"
)

# Known exe locations (keep in sync with install_mumble.ps1)
$ExeSearchPaths = @(
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

# ---------------------------------------------------------------------------
# Check 1 — mumble-server.exe exists
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== PitBox — Mumble Integration Check ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Check 1: Mumble Server binary" -ForegroundColor White

$mumbleExe = $null
foreach ($p in $ExeSearchPaths) {
    if (Test-Path $p) { $mumbleExe = $p; break }
}
if (-not $mumbleExe) {
    # Try registry
    $roots = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    foreach ($root in $roots) {
        $entries = Get-ItemProperty $root -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match "(?i)mumble" }
        foreach ($e in $entries) {
            if ($e.InstallLocation) {
                $candidate = Join-Path $e.InstallLocation "mumble-server.exe"
                if (Test-Path $candidate) { $mumbleExe = $candidate; break }
                $candidate = Join-Path $e.InstallLocation "murmur.exe"
                if (Test-Path $candidate) { $mumbleExe = $candidate; break }
            }
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
    Show-Fail "Port $ExpectedIcePort not listening on $ExpectedIceHost. Is Mumble Server running?"
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
    Show-Fail "mumble-server.ini not found. Run configure_mumble.ps1."
} else {
    Write-Host "         INI file: $iniPath" -ForegroundColor DarkGray
    $content = Get-Content $iniPath -Encoding UTF8

    # Helper: extract a value for a key
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
    Write-Host "$fail check(s) failed. Review the FAIL lines above and:" -ForegroundColor Red
    Write-Host "  1. Run install_mumble.ps1   if Mumble is missing."
    Write-Host "  2. Run configure_mumble.ps1 if ICE settings are wrong."
    Write-Host "  3. Run: pip install zeroc-ice  if Ice is missing."
}

Write-Host ""
exit $(if ($fail -eq 0) { 0 } else { 1 })
