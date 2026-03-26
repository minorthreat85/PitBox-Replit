# Install PitBox Controller as a Windows Service
# Requires Administrator privileges

. "$PSScriptRoot\_common.ps1"

Write-ServiceHeader "Install PitBox Controller Service"

# Pre-flight checks
Assert-Admin
Assert-NssmExists
Ensure-LogsDirectory

# Check if executable exists
if (-not (Test-Path $CONTROLLER_BIN)) {
    Write-Host "ERROR: PitBoxController.exe not found at: $CONTROLLER_BIN" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please build and install PitBox first:" -ForegroundColor Yellow
    Write-Host "  1. Run: .\scripts\build_release.ps1 -Dev" -ForegroundColor White
    Write-Host "  2. Copy dist\PitBoxController.exe to C:\PitBox\installed\bin\" -ForegroundColor White
    Write-Host ""
    exit 1
}

# Check if config exists
if (-not (Test-Path $CONTROLLER_CONFIG)) {
    Write-Host "WARNING: Config file not found at: $CONTROLLER_CONFIG" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "The service will be installed, but may fail to start." -ForegroundColor Yellow
    Write-Host "Create config file before starting the service:" -ForegroundColor White
    Write-Host "  PitBoxController.exe --init --config $CONTROLLER_CONFIG" -ForegroundColor Gray
    Write-Host ""
    $continue = Read-Host "Continue with installation? (y/n)"
    if ($continue -ne 'y') {
        Write-Host "Installation cancelled." -ForegroundColor Yellow
        exit 0
    }
}

# Check if service already exists
if (Test-ServiceExists -ServiceName $CONTROLLER_SERVICE_NAME) {
    Write-Host "Service '$CONTROLLER_SERVICE_NAME' already exists." -ForegroundColor Yellow
    Write-Host "Current status: $(Get-ServiceStatus -ServiceName $CONTROLLER_SERVICE_NAME)" -ForegroundColor Gray
    Write-Host ""
    Write-Host "To reinstall:" -ForegroundColor White
    Write-Host "  1. Run: .\uninstall_controller_service.ps1" -ForegroundColor Gray
    Write-Host "  2. Run: .\install_controller_service.ps1" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

# Install service
Write-Host "Installing service '$CONTROLLER_SERVICE_NAME'..." -ForegroundColor Green

$appDirectory = Split-Path -Parent $CONTROLLER_BIN
$arguments = "--service --config `"$CONTROLLER_CONFIG`""

try {
    # Install service
    & $NSSM_PATH install $CONTROLLER_SERVICE_NAME $CONTROLLER_BIN $arguments
    if ($LASTEXITCODE -ne 0) { throw "NSSM install failed with exit code $LASTEXITCODE" }
    
    # Set display name and description
    & $NSSM_PATH set $CONTROLLER_SERVICE_NAME DisplayName $CONTROLLER_DISPLAY_NAME
    & $NSSM_PATH set $CONTROLLER_SERVICE_NAME Description "Admin controller for Fastest Lap PitBox lounge management system - serves web UI on http://127.0.0.1:9600"
    
    # Set working directory
    & $NSSM_PATH set $CONTROLLER_SERVICE_NAME AppDirectory $appDirectory
    
    # Set startup type to Automatic
    & $NSSM_PATH set $CONTROLLER_SERVICE_NAME Start SERVICE_AUTO_START
    
    # Configure logging
    $stdoutLog = Join-Path $LOGS_DIR "PitBoxController.out.log"
    $stderrLog = Join-Path $LOGS_DIR "PitBoxController.err.log"
    
    & $NSSM_PATH set $CONTROLLER_SERVICE_NAME AppStdout $stdoutLog
    & $NSSM_PATH set $CONTROLLER_SERVICE_NAME AppStderr $stderrLog
    
    # Enable log rotation
    & $NSSM_PATH set $CONTROLLER_SERVICE_NAME AppRotateFiles 1
    & $NSSM_PATH set $CONTROLLER_SERVICE_NAME AppRotateOnline 1
    & $NSSM_PATH set $CONTROLLER_SERVICE_NAME AppRotateSeconds 86400      # Daily rotation
    & $NSSM_PATH set $CONTROLLER_SERVICE_NAME AppRotateBytes 10485760     # 10 MB max size
    
    Write-Host "  Service installed successfully" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Service Details:" -ForegroundColor Cyan
    Write-Host "  Name: $CONTROLLER_SERVICE_NAME" -ForegroundColor White
    Write-Host "  Display Name: $CONTROLLER_DISPLAY_NAME" -ForegroundColor White
    Write-Host "  Executable: $CONTROLLER_BIN" -ForegroundColor White
    Write-Host "  Config: $CONTROLLER_CONFIG" -ForegroundColor White
    Write-Host "  Startup: Automatic" -ForegroundColor White
    Write-Host "  Status: Stopped (not started yet)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Logs:" -ForegroundColor Cyan
    Write-Host "  Stdout: $stdoutLog" -ForegroundColor White
    Write-Host "  Stderr: $stderrLog" -ForegroundColor White
    Write-Host ""
    Write-Host "Next Steps:" -ForegroundColor Green
    Write-Host "  1. Verify config file exists and is correct" -ForegroundColor White
    Write-Host "  2. Start service: .\start_controller_service.ps1" -ForegroundColor White
    Write-Host "  3. Access web UI: http://127.0.0.1:9600" -ForegroundColor White
    Write-Host "  4. Check logs if service fails to start" -ForegroundColor White
    Write-Host ""
    Write-Host "To start the service now:" -ForegroundColor Yellow
    Write-Host "  .\start_controller_service.ps1" -ForegroundColor Gray
    Write-Host ""
    
} catch {
    Write-Host "ERROR: Failed to install service" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    exit 1
}
