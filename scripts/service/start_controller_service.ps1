# Start PitBox Controller Windows Service
# Requires Administrator privileges

. "$PSScriptRoot\_common.ps1"

Write-ServiceHeader "Start PitBox Controller Service"

# Pre-flight checks
Assert-Admin

# Check if service exists
if (-not (Test-ServiceExists -ServiceName $CONTROLLER_SERVICE_NAME)) {
    Write-Host "ERROR: Service '$CONTROLLER_SERVICE_NAME' is not installed." -ForegroundColor Red
    Write-Host ""
    Write-Host "Install the service first:" -ForegroundColor Yellow
    Write-Host "  .\install_controller_service.ps1" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

# Get current status
$currentStatus = Get-ServiceStatus -ServiceName $CONTROLLER_SERVICE_NAME

if ($currentStatus -eq "Running") {
    Write-Host "Service '$CONTROLLER_SERVICE_NAME' is already running." -ForegroundColor Green
    Write-Host ""
    Write-Host "Web UI is available at:" -ForegroundColor Cyan
    Write-Host "  http://127.0.0.1:9600" -ForegroundColor White
    Write-Host ""
    Write-Host "To restart:" -ForegroundColor White
    Write-Host "  1. .\stop_controller_service.ps1" -ForegroundColor Gray
    Write-Host "  2. .\start_controller_service.ps1" -ForegroundColor Gray
    Write-Host ""
    exit 0
}

Write-Host "Starting service '$CONTROLLER_SERVICE_NAME'..." -ForegroundColor Green

try {
    Start-Service -Name $CONTROLLER_SERVICE_NAME
    Start-Sleep -Seconds 3
    
    $newStatus = Get-ServiceStatus -ServiceName $CONTROLLER_SERVICE_NAME
    
    if ($newStatus -eq "Running") {
        Write-Host "  Service started successfully" -ForegroundColor Gray
        Write-Host ""
        Write-Host "Service Status: Running" -ForegroundColor Green
        Write-Host ""
        Write-Host "Web UI is now available at:" -ForegroundColor Cyan
        Write-Host "  http://127.0.0.1:9600" -ForegroundColor White
        Write-Host ""
        Write-Host "Check logs for details:" -ForegroundColor Cyan
        Write-Host "  $LOGS_DIR\PitBoxController.out.log" -ForegroundColor White
        Write-Host "  $LOGS_DIR\PitBoxController.err.log" -ForegroundColor White
        Write-Host ""
        Write-Host "Open Web UI in browser:" -ForegroundColor Yellow
        Write-Host "  Start-Process http://127.0.0.1:9600" -ForegroundColor Gray
        Write-Host ""
    } else {
        Write-Host "  Warning: Service state is '$newStatus'" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Check error log: $LOGS_DIR\PitBoxController.err.log" -ForegroundColor Yellow
        Write-Host ""
    }
    
} catch {
    Write-Host "ERROR: Failed to start service" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "Troubleshooting:" -ForegroundColor Yellow
    Write-Host "  1. Check config file exists: $CONTROLLER_CONFIG" -ForegroundColor White
    Write-Host "  2. Check error log: $LOGS_DIR\PitBoxController.err.log" -ForegroundColor White
    Write-Host "  3. Verify executable exists: $CONTROLLER_BIN" -ForegroundColor White
    Write-Host "  4. Check port 9600 is not in use" -ForegroundColor White
    Write-Host ""
    exit 1
}
