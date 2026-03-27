Set-Location $PSScriptRoot
Write-Host "Pulling latest from GitHub..." -ForegroundColor Cyan
git pull
if ($LASTEXITCODE -ne 0) {
    Write-Host "git pull failed." -ForegroundColor Red
    exit 1
}
Write-Host "Building..." -ForegroundColor Cyan
& "$PSScriptRoot\scripts\build_release.ps1" -Dev
