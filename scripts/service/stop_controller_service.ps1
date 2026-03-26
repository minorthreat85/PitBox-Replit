# Stop PitBox Controller Windows Service
# Requires Administrator privileges

. "$PSScriptRoot\_common.ps1"

Write-ServiceHeader "Stop PitBox Controller Service"

# Pre-flight checks
Assert-Admin

# Check if service exists
if (-not (Test-ServiceExists -ServiceName $CONTROLLER_SERVICE_NAME)) {
    Write-Host "ERROR: Service '$CONTROLLER_SERVICE_NAME' is not installed." -ForegroundColor Red
    Write-Host ""
    exit 1
}

# Get current status
$currentStatus = Get-ServiceStatus -ServiceName $CONTROLLER_SERVICE_NAME

if ($currentStatus -ne "Running") {
    Write-Host "Service '$CONTROLLER_SERVICE_NAME' is not running." -ForegroundColor Yellow
    Write-Host "Current status: $currentStatus" -ForegroundColor Gray
    Write-Host ""
    exit 0
}

Write-Host "Stopping service '$CONTROLLER_SERVICE_NAME'..." -ForegroundColor Green

try {
    Stop-Service -Name $CONTROLLER_SERVICE_NAME -Force
    Start-Sleep -Seconds 2
    
    $newStatus = Get-ServiceStatus -ServiceName $CONTROLLER_SERVICE_NAME
    
    if ($newStatus -eq "Stopped") {
        Write-Host "  Service stopped successfully" -ForegroundColor Gray
        Write-Host ""
        Write-Host "Service Status: Stopped" -ForegroundColor Green
        Write-Host ""
        Write-Host "Web UI is no longer available." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "To start again: .\start_controller_service.ps1" -ForegroundColor Yellow
        Write-Host ""
    } else {
        Write-Host "  Warning: Service state is '$newStatus'" -ForegroundColor Yellow
        Write-Host ""
    }
    
} catch {
    Write-Host "ERROR: Failed to stop service" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    exit 1
}
