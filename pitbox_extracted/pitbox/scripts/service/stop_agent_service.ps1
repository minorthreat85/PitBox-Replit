# Stop PitBox Agent Windows Service
# Requires Administrator privileges

. "$PSScriptRoot\_common.ps1"

Write-ServiceHeader "Stop PitBox Agent Service"

# Pre-flight checks
Assert-Admin

# Check if service exists
if (-not (Test-ServiceExists -ServiceName $AGENT_SERVICE_NAME)) {
    Write-Host "ERROR: Service '$AGENT_SERVICE_NAME' is not installed." -ForegroundColor Red
    Write-Host ""
    exit 1
}

# Get current status
$currentStatus = Get-ServiceStatus -ServiceName $AGENT_SERVICE_NAME

if ($currentStatus -ne "Running") {
    Write-Host "Service '$AGENT_SERVICE_NAME' is not running." -ForegroundColor Yellow
    Write-Host "Current status: $currentStatus" -ForegroundColor Gray
    Write-Host ""
    exit 0
}

Write-Host "Stopping service '$AGENT_SERVICE_NAME'..." -ForegroundColor Green

try {
    Stop-Service -Name $AGENT_SERVICE_NAME -Force
    Start-Sleep -Seconds 2
    
    $newStatus = Get-ServiceStatus -ServiceName $AGENT_SERVICE_NAME
    
    if ($newStatus -eq "Stopped") {
        Write-Host "  Service stopped successfully" -ForegroundColor Gray
        Write-Host ""
        Write-Host "Service Status: Stopped" -ForegroundColor Green
        Write-Host ""
        Write-Host "To start again: .\start_agent_service.ps1" -ForegroundColor Yellow
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
