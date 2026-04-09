# PitBox Dev Update Script
# PURPOSE: Development/iteration deployment script for an EXISTING PitBox installation.
#          This is NOT a first-time installer. The PitBox installer must have been run first
#          to create the service, directories, and initial configuration.
#
# USAGE:   Run from the repo directory (e.g. C:\Users\info\pitbox\):
#            .\update.ps1
#
# WHAT IT DOES:
#   1. Restores generated files (version.ini) so git pull succeeds cleanly
#   2. Pulls latest code from GitHub
#   3. Builds EXEs via build_release.ps1
#   4. Deploys built EXEs to installed service locations
#   5. Restarts the PitBoxController service (if it was running before update)
#   6. Verifies the controller is healthy via HTTP health check
#
# SERVICE RESTART BEHAVIOR:
#   - If the service was running before the update, it is stopped, updated, and restarted.
#   - If the service was NOT running before the update, it is NOT started.
#   - This preserves the prior running state intentionally.

Set-Location $PSScriptRoot

$ServiceName    = "PitBoxController"
$InstallBinDir  = "C:\PitBox\installed\bin"
$ControllerPort = 9630
$HealthUrl      = "http://localhost:$ControllerPort/health"
$serviceStopped = $false

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  PitBox Dev Update" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Preflight: verify install directories exist ---
$requiredDirs = @($InstallBinDir)
$missing = @()
foreach ($d in $requiredDirs) {
    if (-not (Test-Path $d)) { $missing += $d }
}
if ($missing.Count -gt 0) {
    Write-Host "ERROR: Required install directories not found:" -ForegroundColor Red
    foreach ($m in $missing) {
        Write-Host "  $m" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "This script is for updating an EXISTING PitBox installation." -ForegroundColor Yellow
    Write-Host "Run the PitBox installer first to create the service and directories." -ForegroundColor Yellow
    exit 1
}

# --- Stop service if running ---
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host "Stopping $ServiceName service..." -ForegroundColor Cyan
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    $serviceStopped = $true
    Write-Host "  Service stopped." -ForegroundColor Gray
}

# --- Restore generated files before pull ---
Write-Host "Restoring generated files before pull..." -ForegroundColor Cyan
$generatedFiles = @("version.ini")
foreach ($gf in $generatedFiles) {
    if (Test-Path $gf) {
        git restore $gf 2>$null
        if ($LASTEXITCODE -ne 0) {
            git checkout -- $gf 2>$null
        }
        Write-Host "  Restored $gf" -ForegroundColor Gray
    }
}

# --- Pull latest ---
Write-Host "Pulling latest from GitHub..." -ForegroundColor Cyan
git pull
if ($LASTEXITCODE -ne 0) {
    Write-Host "git pull failed." -ForegroundColor Red
    if ($serviceStopped) { Start-Service -Name $ServiceName -ErrorAction SilentlyContinue }
    exit 1
}

# --- Build ---
Write-Host "Building..." -ForegroundColor Cyan
& "$PSScriptRoot\scripts\build_release.ps1" -Dev
$buildExit = $LASTEXITCODE

if ($buildExit -ne 0) {
    Write-Host "Build failed -- not deploying." -ForegroundColor Red
    if ($serviceStopped) { Start-Service -Name $ServiceName -ErrorAction SilentlyContinue }
    exit $buildExit
}

# --- Deploy built EXEs ---
$controllerSrc = "$PSScriptRoot\dist\PitBoxController.exe"
$controllerDst = "$InstallBinDir\PitBoxController.exe"
$agentSrc      = "$PSScriptRoot\dist\PitBoxAgent.exe"
$agentDst      = "C:\PitBox\Agent\bin\PitBoxAgent.exe"
$updaterSrc    = "$PSScriptRoot\dist\PitBoxUpdater.exe"
$updaterDst    = "C:\PitBox\updater\PitBoxUpdater.exe"

$deployFailed = $false

if (Test-Path $controllerSrc) {
    Write-Host "Deploying PitBoxController.exe -> $InstallBinDir ..." -ForegroundColor Cyan
    try {
        Copy-Item -Path $controllerSrc -Destination $controllerDst -Force -ErrorAction Stop
        Write-Host "  PitBoxController.exe deployed." -ForegroundColor Gray
    } catch {
        Write-Host "  ERROR: Failed to copy PitBoxController.exe: $_" -ForegroundColor Red
        $deployFailed = $true
    }
} else {
    Write-Host "WARNING: dist\PitBoxController.exe not found -- build may have failed." -ForegroundColor Yellow
}

if (Test-Path $agentSrc) {
    $agentDir = Split-Path $agentDst -Parent
    if (Test-Path $agentDir) {
        Write-Host "Deploying PitBoxAgent.exe -> $agentDir ..." -ForegroundColor Cyan
        try {
            Copy-Item -Path $agentSrc -Destination $agentDst -Force -ErrorAction Stop
            Write-Host "  PitBoxAgent.exe deployed." -ForegroundColor Gray
        } catch {
            Write-Host "  ERROR: Failed to copy PitBoxAgent.exe: $_" -ForegroundColor Red
            $deployFailed = $true
        }
    }
}

if (Test-Path $updaterSrc) {
    $updaterDir = Split-Path $updaterDst -Parent
    if (-not (Test-Path $updaterDir)) {
        New-Item -ItemType Directory -Path $updaterDir -Force | Out-Null
    }
    Write-Host "Deploying PitBoxUpdater.exe -> $updaterDir ..." -ForegroundColor Cyan
    try {
        Copy-Item -Path $updaterSrc -Destination $updaterDst -Force -ErrorAction Stop
        Write-Host "  PitBoxUpdater.exe deployed." -ForegroundColor Gray
    } catch {
        Write-Host "  ERROR: Failed to copy PitBoxUpdater.exe: $_" -ForegroundColor Red
        $deployFailed = $true
    }
}

if ($deployFailed) {
    Write-Host ""
    Write-Host "ERROR: One or more deploy copies failed. Check errors above." -ForegroundColor Red
    if ($serviceStopped) { Start-Service -Name $ServiceName -ErrorAction SilentlyContinue }
    exit 1
}

# --- Restart service (only if it was running before) ---
if ($serviceStopped) {
    Write-Host "Restarting $ServiceName service..." -ForegroundColor Cyan
    Start-Service -Name $ServiceName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Write-Host "  Service started." -ForegroundColor Gray

    # --- HTTP health check ---
    Write-Host "Checking controller health at $HealthUrl ..." -ForegroundColor Cyan
    $healthy = $false
    for ($i = 1; $i -le 10; $i++) {
        try {
            $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
                $healthy = $true
                break
            }
        } catch {
            # not ready yet
        }
        Start-Sleep -Seconds 2
    }
    if ($healthy) {
        Write-Host "  Controller is healthy (HTTP 200 on port $ControllerPort)." -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "WARNING: Controller service is running but HTTP health check failed." -ForegroundColor Red
        Write-Host "  The service may still be starting, or there may be an application error." -ForegroundColor Yellow
        Write-Host "  Check logs at C:\PitBox\logs\ or run: Invoke-WebRequest $HealthUrl" -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }
} else {
    Write-Host ""
    Write-Host "NOTE: Service was not running before update -- not starting it." -ForegroundColor Yellow
    Write-Host "  Start manually: Start-Service $ServiceName" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Update complete." -ForegroundColor Green
exit 0
