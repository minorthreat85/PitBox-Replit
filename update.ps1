Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

# PitBox Dev Update Script
# PURPOSE: Dev iteration deployment for an EXISTING PitBox installation.
#
# SERVICE RESTART BEHAVIOR:
#   Always starts the controller service after a successful deploy.
#   If the service was running, it is stopped first, then restarted.
#   If it was not running, it is started.
#   The script always ends with an HTTP health check to verify the app is serving.

$ServiceName      = "PitBoxController"
$AgentExeDst      = "C:\PitBox\Agent\bin\PitBoxAgent.exe"
$UpdaterExeDst    = "C:\PitBox\updater\PitBoxUpdater.exe"
$ControllerPort   = 9630
$HealthUrl        = "http://localhost:$ControllerPort/health"

# Auto-detect controller EXE location from known installer layouts:
#   Standalone controller installer: C:\PitBox\Controller\PitBoxController.exe
#   Unified installer:              C:\PitBox\PitBoxController.exe
#   Legacy/custom:                  C:\PitBox\installed\PitBoxController.exe
$ControllerExeDst = $null
$searchPaths = @(
    "C:\PitBox\Controller\PitBoxController.exe",
    "C:\PitBox\PitBoxController.exe",
    "C:\PitBox\installed\PitBoxController.exe"
)
foreach ($p in $searchPaths) {
    if (Test-Path $p) {
        $ControllerExeDst = $p
        break
    }
}

function Write-Step([string]$Message) {
    Write-Host $Message -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "  $Message" -ForegroundColor Gray
}

function Write-Warn([string]$Message) {
    Write-Host $Message -ForegroundColor Yellow
}

function Fail([string]$Message, [int]$ExitCode = 1) {
    Write-Host $Message -ForegroundColor Red
    exit $ExitCode
}

function Restore-GeneratedFiles {
    Write-Step "Restoring generated files before pull..."
    $generatedFiles = @("version.ini")
    foreach ($gf in $generatedFiles) {
        if (Test-Path $gf) {
            git restore -- $gf 2>$null
            if ($LASTEXITCODE -ne 0) {
                git checkout -- $gf 2>$null
            }
            Write-Ok "Restored $gf"
        }
    }
}

function Git-PullLatest {
    Write-Step "Pulling latest from GitHub..."
    git pull
    if ($LASTEXITCODE -ne 0) {
        Fail "git pull failed."
    }
}

function Build-Project {
    Write-Step "Building..."
    & "$PSScriptRoot\scripts\build_release.ps1" -Dev
    $buildExit = $LASTEXITCODE
    if ($buildExit -ne 0) {
        Fail "Build failed - not deploying." $buildExit
    }
}

function Assert-FileExists([string]$Path, [string]$Label) {
    if (-not (Test-Path $Path)) {
        Fail "ERROR: $Label not found: $Path"
    }
}

function Stop-ControllerService {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $svc) {
        Write-Ok "$ServiceName service not found (not installed as service)."
        return $false
    }
    if ($svc.Status -ne "Running") {
        Write-Ok "$ServiceName service was not running."
        return $false
    }
    Write-Step "Stopping $ServiceName service..."
    try {
        Stop-Service -Name $ServiceName -Force -ErrorAction Stop
        Start-Sleep -Seconds 3
        Write-Ok "Service stopped."
        return $true
    }
    catch {
        Write-Warn "WARNING: Could not stop $ServiceName (may need admin rights)."
        Write-Warn "  $($_.Exception.Message)"
        Write-Warn "  Continuing with deploy -- restart the service manually after."
        return $false
    }
}

function Start-ControllerService {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $svc) {
        Write-Warn "NOTE: $ServiceName service not found -- not starting."
        Write-Warn "  Run the PitBox installer to register the service."
        return $false
    }
    Write-Step "Starting $ServiceName service..."
    try {
        Start-Service -Name $ServiceName -ErrorAction Stop
        Start-Sleep -Seconds 3
        $svcCheck = Get-Service -Name $ServiceName -ErrorAction Stop
        if ($svcCheck.Status -ne "Running") {
            Write-Warn "WARNING: $ServiceName did not start correctly."
            return $false
        }
        Write-Ok "Service started."
        return $true
    }
    catch {
        Write-Warn "WARNING: Could not start $ServiceName (may need admin rights)."
        Write-Warn "  $($_.Exception.Message)"
        Write-Warn "  Start manually: Start-Service $ServiceName"
        return $false
    }
}

function Test-ControllerHealth {
    Write-Step "Checking controller health at $HealthUrl ..."
    $healthy = $false
    for ($i = 1; $i -le 10; $i++) {
        try {
            $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
                $healthy = $true
                break
            }
        } catch {}
        Start-Sleep -Seconds 2
    }
    if ($healthy) {
        Write-Ok "Controller is healthy (HTTP 200 on port $ControllerPort)."
    } else {
        Write-Warn "WARNING: HTTP health check failed after 20s."
        Write-Warn "  The app may still be starting, or there may be an error."
        Write-Warn "  Check logs at C:\PitBox\logs\ or run: Invoke-WebRequest $HealthUrl"
    }
    return $healthy
}

function Deploy-Artifacts {
    param(
        [string]$ControllerSrc,
        [string]$AgentSrc,
        [string]$UpdaterSrc
    )

    if ($ControllerExeDst) {
        $controllerDir = Split-Path $ControllerExeDst -Parent
        if (Test-Path $controllerDir) {
            Write-Step "Deploying PitBoxController.exe -> $ControllerExeDst"
            Copy-Item -Path $ControllerSrc -Destination $ControllerExeDst -Force -ErrorAction Stop
            Write-Ok "PitBoxController.exe deployed."
        } else {
            Fail "Controller dir not found: $controllerDir -- run the PitBox installer first."
        }
    } else {
        Write-Warn "WARNING: No existing PitBoxController.exe found in known install paths."
        Write-Warn "  Searched: $($searchPaths -join ', ')"
        Write-Warn "  Skipping controller deploy. Run the PitBox installer first."
    }

    $agentDir = Split-Path $AgentExeDst -Parent
    if (Test-Path $agentDir) {
        Write-Step "Deploying PitBoxAgent.exe -> $AgentExeDst"
        Copy-Item -Path $AgentSrc -Destination $AgentExeDst -Force -ErrorAction Stop
        Write-Ok "PitBoxAgent.exe deployed."
    }

    $updaterDir = Split-Path $UpdaterExeDst -Parent
    if (-not (Test-Path $updaterDir)) {
        New-Item -ItemType Directory -Path $updaterDir -Force | Out-Null
        Write-Ok "Created updater dir: $updaterDir"
    }
    Write-Step "Deploying PitBoxUpdater.exe -> $UpdaterExeDst"
    Copy-Item -Path $UpdaterSrc -Destination $UpdaterExeDst -Force -ErrorAction Stop
    Write-Ok "PitBoxUpdater.exe deployed."
}

# ======== MAIN ========

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  PitBox Dev Update" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if ($ControllerExeDst) {
    Write-Ok "Controller found at: $ControllerExeDst"
} else {
    Write-Warn "WARNING: No existing controller EXE found. Will skip controller deploy."
}

Restore-GeneratedFiles
Git-PullLatest
Build-Project

$controllerSrc = Join-Path $PSScriptRoot "dist\PitBoxController.exe"
$agentSrc      = Join-Path $PSScriptRoot "dist\PitBoxAgent.exe"
$updaterSrc    = Join-Path $PSScriptRoot "dist\PitBoxUpdater.exe"

Assert-FileExists -Path $controllerSrc -Label "Controller build output"
Assert-FileExists -Path $agentSrc -Label "Agent build output"
Assert-FileExists -Path $updaterSrc -Label "Updater build output"

$wasStopped = Stop-ControllerService

try {
    Deploy-Artifacts -ControllerSrc $controllerSrc -AgentSrc $agentSrc -UpdaterSrc $updaterSrc
}
catch {
    Write-Host "Deploy failed: $($_.Exception.Message)" -ForegroundColor Red
    try { Start-Service -Name $ServiceName -ErrorAction SilentlyContinue } catch {}
    exit 1
}

# Always start service and health-check after successful deploy
$started = Start-ControllerService
if ($started) {
    $healthy = Test-ControllerHealth
    if (-not $healthy) {
        Fail "Update deployed but controller health check failed."
    }
}

Write-Host ""
Write-Host "Update complete." -ForegroundColor Green
exit 0
