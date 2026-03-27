Set-Location $PSScriptRoot

$ServiceName = "PitBoxController"
$serviceStopped = $false

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host "Stopping $ServiceName service..." -ForegroundColor Cyan
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    $serviceStopped = $true
    Write-Host "  Service stopped." -ForegroundColor Gray
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

if ($serviceStopped) {
    Write-Host "Restarting $ServiceName service..." -ForegroundColor Cyan
    Start-Service -Name $ServiceName -ErrorAction SilentlyContinue
    Write-Host "  Service restarted." -ForegroundColor Gray
}

exit $buildExit
