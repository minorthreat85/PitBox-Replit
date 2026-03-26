$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$ROOT = Resolve-Path (Join-Path $SCRIPT_DIR "..")

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Building PitBox Release (DEV)" -ForegroundColor Cyan
Write-Host "  Root: $ROOT"
Write-Host "========================================" -ForegroundColor Cyan

Set-Location $ROOT

$buildPath = ".\scripts\build_release.ps1"

if (-not (Test-Path $buildPath)) {
    Write-Host "ERROR: scripts\build_release.ps1 not found" -ForegroundColor Red
    Pause
    exit 1
}

Write-Host "Running: $buildPath -Dev" -ForegroundColor Green
& $buildPath -Dev

Write-Host "`nBuild finished." -ForegroundColor Green
Pause
