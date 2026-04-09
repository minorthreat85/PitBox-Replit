Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$ServiceName      = "PitBoxController"
$InstallBinDir    = "C:\PitBox\installed"
$ControllerExeDst = Join-Path $InstallBinDir "PitBoxController.exe"
$AgentExeDst      = "C:\PitBox\Agent\bin\PitBoxAgent.exe"
$UpdaterExeDst    = "C:\PitBox\updater\PitBoxUpdater.exe"
$ControllerPort   = 9630
$HealthUrl        = "http://localhost:$ControllerPort/health"

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
    $generatedFiles = @(
        "version.ini"
    )

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
    $serviceExisted = $null -ne $svc
    $serviceWasRunning = $serviceExisted -and $svc.Status -eq "Running"

    if ($serviceWasRunning) {
        Write-Step "Stopping $ServiceName service..."
        try {
            Stop-Service -Name $ServiceName -Force -ErrorAction Stop
            Start-Sleep -Seconds 3
            Write-Ok "Service stopped."
        }
        catch {
            Fail "Failed to stop $ServiceName. $($_.Exception.Message)"
        }
    } else {
        Write-Ok "$ServiceName service was not running."
    }

    return @{
        Existed    = $serviceExisted
        WasRunning = $serviceWasRunning
    }
}

function Start-ControllerService {
    Write-Step "Starting $ServiceName service..."
    try {
        Start-Service -Name $ServiceName -ErrorAction Stop
        Start-Sleep -Seconds 3

        $svcCheck = Get-Service -Name $ServiceName -ErrorAction Stop
        if ($svcCheck.Status -ne "Running") {
            Fail "ERROR: $ServiceName did not restart correctly."
        }

        Write-Ok "Service restarted."
    }
    catch {
        Fail "Failed to start $ServiceName. $($_.Exception.Message)"
    }

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
        Write-Host ""
        Write-Host "WARNING: Service is running but HTTP health check failed after 20s." -ForegroundColor Red
        Write-Host "  The app may still be starting, or there may be an error." -ForegroundColor Yellow
        Write-Host "  Check logs at C:\PitBox\logs\ or run: Invoke-WebRequest $HealthUrl" -ForegroundColor Yellow
        Write-Host ""
        Fail "Health check failed."
    }
}

function Deploy-Artifacts {
    param(
        [string]$ControllerSrc,
        [string]$AgentSrc,
        [string]$UpdaterSrc
    )

    if (-not (Test-Path $InstallBinDir)) {
        Fail "Controller install dir not found: $InstallBinDir"
    }

    $agentDir = Split-Path $AgentExeDst -Parent
    if (-not (Test-Path $agentDir)) {
        Fail "Agent install dir not found: $agentDir"
    }

    $updaterDir = Split-Path $UpdaterExeDst -Parent
    if (-not (Test-Path $updaterDir)) {
        New-Item -ItemType Directory -Path $updaterDir -Force | Out-Null
        Write-Ok "Created updater dir: $updaterDir"
    }

    try {
        Write-Step "Deploying PitBoxController.exe..."
        Copy-Item -Path $ControllerSrc -Destination $ControllerExeDst -Force -ErrorAction Stop
        Write-Ok "PitBoxController.exe deployed."

        Write-Step "Deploying PitBoxAgent.exe..."
        Copy-Item -Path $AgentSrc -Destination $AgentExeDst -Force -ErrorAction Stop
        Write-Ok "PitBoxAgent.exe deployed."

        Write-Step "Deploying PitBoxUpdater.exe..."
        Copy-Item -Path $UpdaterSrc -Destination $UpdaterExeDst -Force -ErrorAction Stop
        Write-Ok "PitBoxUpdater.exe deployed."
    }
    catch {
        Fail "Deployment failed: $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  PitBox Dev Update" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Restore-GeneratedFiles
Git-PullLatest
Build-Project

$controllerSrc = Join-Path $PSScriptRoot "dist\PitBoxController.exe"
$agentSrc      = Join-Path $PSScriptRoot "dist\PitBoxAgent.exe"
$updaterSrc    = Join-Path $PSScriptRoot "dist\PitBoxUpdater.exe"

Assert-FileExists -Path $controllerSrc -Label "Controller build output"
Assert-FileExists -Path $agentSrc -Label "Agent build output"
Assert-FileExists -Path $updaterSrc -Label "Updater build output"

$serviceState = Stop-ControllerService

try {
    Deploy-Artifacts -ControllerSrc $controllerSrc -AgentSrc $agentSrc -UpdaterSrc $updaterSrc
}
catch {
    if ($serviceState.WasRunning) {
        try {
            Start-Service -Name $ServiceName -ErrorAction SilentlyContinue
        } catch {}
    }
    throw
}

if ($serviceState.WasRunning) {
    Start-ControllerService
} else {
    Write-Host ""
    Write-Warn "NOTE: Service was not running before update -- not starting it."
    Write-Warn "  Start manually: Start-Service $ServiceName"
}

Write-Host ""
Write-Host "Update complete." -ForegroundColor Green
exit 0
