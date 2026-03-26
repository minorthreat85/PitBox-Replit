# Uninstall PitBox Controller Windows Service
# Requires Administrator privileges

. "$PSScriptRoot\_common.ps1"

Write-ServiceHeader "Uninstall PitBox Controller Service"

# Pre-flight checks
Assert-Admin
Assert-NssmExists

# Check if service exists
if (-not (Test-ServiceExists -ServiceName $CONTROLLER_SERVICE_NAME)) {
    Write-Host "Service '$CONTROLLER_SERVICE_NAME' is not installed." -ForegroundColor Yellow
    Write-Host ""
    exit 0
}

# Get current status
$currentStatus = Get-ServiceStatus -ServiceName $CONTROLLER_SERVICE_NAME
Write-Host "Current service status: $currentStatus" -ForegroundColor Gray
Write-Host ""

# Stop service if running
if ($currentStatus -eq "Running") {
    Write-Host "Stopping service..." -ForegroundColor Yellow
    try {
        & $NSSM_PATH stop $CONTROLLER_SERVICE_NAME
        Start-Sleep -Seconds 2
        Write-Host "  Service stopped" -ForegroundColor Gray
    } catch {
        Write-Host "  Warning: Failed to stop service cleanly" -ForegroundColor Yellow
    }
}

# Uninstall service
Write-Host "Uninstalling service '$CONTROLLER_SERVICE_NAME'..." -ForegroundColor Green

try {
    & $NSSM_PATH remove $CONTROLLER_SERVICE_NAME confirm
    if ($LASTEXITCODE -ne 0) { throw "NSSM remove failed with exit code $LASTEXITCODE" }
    
    Write-Host "  Service uninstalled successfully" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Service '$CONTROLLER_SERVICE_NAME' has been removed." -ForegroundColor Green
    Write-Host ""
    Write-Host "Note: Log files were not deleted:" -ForegroundColor Cyan
    Write-Host "  $LOGS_DIR\PitBoxController.out.log" -ForegroundColor White
    Write-Host "  $LOGS_DIR\PitBoxController.err.log" -ForegroundColor White
    Write-Host ""
    Write-Host "To reinstall: .\install_controller_service.ps1" -ForegroundColor Yellow
    Write-Host ""
    
} catch {
    Write-Host "ERROR: Failed to uninstall service" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    exit 1
}
