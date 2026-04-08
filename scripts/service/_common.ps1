# PitBox Service Management - Common Functions
# Shared utilities for service install/uninstall/start/stop scripts

$ErrorActionPreference = "Stop"

# Paths
$NSSM_PATH = "C:\PitBox\installed\tools\nssm.exe"
$AGENT_BIN = "C:\PitBox\Agent\bin\PitBoxAgent.exe"
$CONTROLLER_BIN = "C:\PitBox\installed\bin\PitBoxController.exe"
$AGENT_CONFIG = "C:\PitBox\installed\config\agent.json"
$CONTROLLER_CONFIG = "C:\PitBox\installed\config\controller.json"
$LOGS_DIR = "C:\PitBox\installed\logs"

# Service names
$AGENT_SERVICE_NAME = "PitBoxAgent"
$CONTROLLER_SERVICE_NAME = "PitBoxController"

# Display names
$AGENT_DISPLAY_NAME = "Fastest Lap PitBox Agent"
$CONTROLLER_DISPLAY_NAME = "Fastest Lap PitBox Controller"

function Test-IsAdmin {
    <#
    .SYNOPSIS
    Check if running with administrator privileges
    #>
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-Admin {
    <#
    .SYNOPSIS
    Exit with error if not running as administrator
    #>
    if (-not (Test-IsAdmin)) {
        Write-Host ""
        Write-Host "ERROR: Administrator privileges required" -ForegroundColor Red
        Write-Host ""
        Write-Host "Please run this script as Administrator:" -ForegroundColor Yellow
        Write-Host "  1. Right-click PowerShell" -ForegroundColor White
        Write-Host "  2. Select 'Run as Administrator'" -ForegroundColor White
        Write-Host "  3. Navigate to this directory and run the script again" -ForegroundColor White
        Write-Host ""
        exit 1
    }
}

function Assert-NssmExists {
    <#
    .SYNOPSIS
    Exit with error if NSSM is not found
    #>
    if (-not (Test-Path $NSSM_PATH)) {
        Write-Host ""
        Write-Host "ERROR: NSSM (Non-Sucking Service Manager) not found" -ForegroundColor Red
        Write-Host ""
        Write-Host "Expected location: $NSSM_PATH" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "To install NSSM:" -ForegroundColor White
        Write-Host "  1. Download from: https://nssm.cc/download" -ForegroundColor Gray
        Write-Host "  2. Extract nssm.exe (win64 version)" -ForegroundColor Gray
        Write-Host "  3. Copy to: C:\PitBox\installed\tools\nssm.exe" -ForegroundColor Gray
        Write-Host ""
        Write-Host "Create the tools directory if needed:" -ForegroundColor White
        Write-Host "  New-Item -ItemType Directory -Force -Path C:\PitBox\installed\tools" -ForegroundColor Gray
        Write-Host ""
        exit 1
    }
}

function Ensure-LogsDirectory {
    <#
    .SYNOPSIS
    Create logs directory if it doesn't exist
    #>
    if (-not (Test-Path $LOGS_DIR)) {
        Write-Host "Creating logs directory: $LOGS_DIR" -ForegroundColor Gray
        New-Item -ItemType Directory -Force -Path $LOGS_DIR | Out-Null
    }
}

function Write-ServiceHeader {
    <#
    .SYNOPSIS
    Print a formatted header
    #>
    param(
        [string]$Title
    )
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
}

function Test-ServiceExists {
    <#
    .SYNOPSIS
    Check if a service exists
    #>
    param(
        [string]$ServiceName
    )
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    return $null -ne $service
}

function Get-ServiceStatus {
    <#
    .SYNOPSIS
    Get service status as a string
    #>
    param(
        [string]$ServiceName
    )
    if (Test-ServiceExists -ServiceName $ServiceName) {
        $service = Get-Service -Name $ServiceName
        return $service.Status.ToString()
    } else {
        return "Not Installed"
    }
}
