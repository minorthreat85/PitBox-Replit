# Start PitBox Agent Windows Service
# Requires Administrator privileges

. "$PSScriptRoot\_common.ps1"

Write-ServiceHeader "Start PitBox Agent Service"

# Pre-flight checks
Assert-Admin

# Check if service exists
if (-not (Test-ServiceExists -ServiceName $AGENT_SERVICE_NAME)) {
    Write-Host "ERROR: Service '$AGENT_SERVICE_NAME' is not installed." -ForegroundColor Red
    Write-Host ""
    Write-Host "Install the service first:" -ForegroundColor Yellow
    Write-Host "  .\install_agent_service.ps1" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

# Get current status
$currentStatus = Get-ServiceStatus -ServiceName $AGENT_SERVICE_NAME

if ($currentStatus -eq "Running") {
    Write-Host "Service '$AGENT_SERVICE_NAME' is already running." -ForegroundColor Green
    Write-Host ""
    Write-Host "To restart:" -ForegroundColor White
    Write-Host "  1. .\stop_agent_service.ps1" -ForegroundColor Gray
    Write-Host "  2. .\start_agent_service.ps1" -ForegroundColor Gray
    Write-Host ""
    exit 0
}

Write-Host "Starting service '$AGENT_SERVICE_NAME'..." -ForegroundColor Green

try {
    Start-Service -Name $AGENT_SERVICE_NAME
    Start-Sleep -Seconds 2
    
    $newStatus = Get-ServiceStatus -ServiceName $AGENT_SERVICE_NAME
    
    if ($newStatus -eq "Running") {
        Write-Host "  Service started successfully" -ForegroundColor Gray
        Write-Host ""
        Write-Host "Service Status: Running" -ForegroundColor Green
        Write-Host ""
        Write-Host "Check logs for details:" -ForegroundColor Cyan
        Write-Host "  $LOGS_DIR\PitBoxAgent.out.log" -ForegroundColor White
        Write-Host "  $LOGS_DIR\PitBoxAgent.err.log" -ForegroundColor White
        Write-Host ""
        Write-Host "Test the agent:" -ForegroundColor Cyan
        Write-Host "  Invoke-RestMethod -Uri http://localhost:9600/ping -Headers @{Authorization='Bearer YOUR_TOKEN'}" -ForegroundColor Gray
        Write-Host ""
    } else {
        Write-Host "  Warning: Service state is '$newStatus'" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Check error log: $LOGS_DIR\PitBoxAgent.err.log" -ForegroundColor Yellow
        Write-Host ""
    }
    
} catch {
    Write-Host "ERROR: Failed to start service" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "Troubleshooting:" -ForegroundColor Yellow
    Write-Host "  1. Check config file exists: $AGENT_CONFIG" -ForegroundColor White
    Write-Host "  2. Check error log: $LOGS_DIR\PitBoxAgent.err.log" -ForegroundColor White
    Write-Host "  3. Verify executable exists: $AGENT_BIN" -ForegroundColor White
    Write-Host ""
    exit 1
}
