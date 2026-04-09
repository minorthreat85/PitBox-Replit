Set-Location $PSScriptRoot

$ServiceName = "PitBoxController"
$InstallBinDir = "C:\PitBox\installed\bin"
$serviceStopped = $false

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host "Stopping $ServiceName service..." -ForegroundColor Cyan
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    $serviceStopped = $true
    Write-Host "  Service stopped." -ForegroundColor Gray
}

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

Write-Host "Pulling latest from GitHub..." -ForegroundColor Cyan
git pull
if ($LASTEXITCODE -ne 0) {
    Write-Host "git pull failed." -ForegroundColor Red
    if ($serviceStopped) { Start-Service -Name $ServiceName -ErrorAction SilentlyContinue }
    exit 1
}

Write-Host "Building..." -ForegroundColor Cyan
& "$PSScriptRoot\scripts\build_release.ps1" -Dev
$buildExit = $LASTEXITCODE

if ($buildExit -ne 0) {
    Write-Host "Build failed - not deploying." -ForegroundColor Red
    if ($serviceStopped) { Start-Service -Name $ServiceName -ErrorAction SilentlyContinue }
    exit $buildExit
}

# Deploy built EXEs to the installed service location
$controllerSrc = "$PSScriptRoot\dist\PitBoxController.exe"
$controllerDst = "$InstallBinDir\PitBoxController.exe"
$agentSrc      = "$PSScriptRoot\dist\PitBoxAgent.exe"
$agentDst      = "C:\PitBox\Agent\bin\PitBoxAgent.exe"
$updaterSrc    = "$PSScriptRoot\dist\PitBoxUpdater.exe"
$updaterDst    = "C:\PitBox\updater\PitBoxUpdater.exe"

if (Test-Path $controllerSrc) {
    if (Test-Path $InstallBinDir) {
        Write-Host "Deploying PitBoxController.exe -> $InstallBinDir ..." -ForegroundColor Cyan
        Copy-Item -Path $controllerSrc -Destination $controllerDst -Force
        Write-Host "  PitBoxController.exe deployed." -ForegroundColor Gray
    } else {
        Write-Host "WARNING: Install dir not found ($InstallBinDir) - skipping deploy." -ForegroundColor Yellow
        Write-Host "  Run the installer first, or manually copy dist\PitBoxController.exe." -ForegroundColor Yellow
    }
} else {
    Write-Host "WARNING: dist\PitBoxController.exe not found - build may have failed." -ForegroundColor Yellow
}

if (Test-Path $agentSrc) {
    $agentDir = Split-Path $agentDst -Parent
    if (Test-Path $agentDir) {
        Write-Host "Deploying PitBoxAgent.exe -> $agentDir ..." -ForegroundColor Cyan
        Copy-Item -Path $agentSrc -Destination $agentDst -Force
        Write-Host "  PitBoxAgent.exe deployed." -ForegroundColor Gray
    }
}

if (Test-Path $updaterSrc) {
    $updaterDir = Split-Path $updaterDst -Parent
    if (-not (Test-Path $updaterDir)) {
        New-Item -ItemType Directory -Path $updaterDir -Force | Out-Null
    }
    Write-Host "Deploying PitBoxUpdater.exe -> $updaterDir ..." -ForegroundColor Cyan
    Copy-Item -Path $updaterSrc -Destination $updaterDst -Force
    Write-Host "  PitBoxUpdater.exe deployed." -ForegroundColor Gray
}

if ($serviceStopped) {
    Write-Host "Restarting $ServiceName service..." -ForegroundColor Cyan
    Start-Service -Name $ServiceName -ErrorAction SilentlyContinue
    Write-Host "  Service restarted." -ForegroundColor Gray
}

Write-Host ""
Write-Host "Update complete." -ForegroundColor Green
exit 0
