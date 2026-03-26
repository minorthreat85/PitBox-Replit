# PitBox Agent - User-Session Setup (DO NOT install as Windows Service)
#
# CRITICAL: PitBoxAgent MUST run in the user session, NEVER as SYSTEM.
# Running as a Windows Service causes Assetto Corsa to launch headless (no window).
#
# This script creates a Scheduled Task that runs the Agent when the user logs on.
# Use this if the installer's "Start Agent on login" option was not selected.

. "$PSScriptRoot\_common.ps1"

Write-ServiceHeader "PitBox Agent - User-Session Setup"

# Pre-flight checks
Assert-Admin

# Paths - use C:\PitBox (unified installer path)
$PITBOX_ROOT = "C:\PitBox"
$AGENT_EXE = Join-Path $PITBOX_ROOT "PitBoxAgent.exe"
$AGENT_CONFIG = Join-Path $PITBOX_ROOT "agent_config.json"

if (-not (Test-Path $AGENT_EXE)) {
    Write-Host "ERROR: PitBoxAgent.exe not found at: $AGENT_EXE" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please run the PitBox installer first, or copy PitBoxAgent.exe to $PITBOX_ROOT" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

Write-Host "Creating Scheduled Task: PitBox Agent" -ForegroundColor Green
Write-Host "  Trigger: At user logon" -ForegroundColor Gray
Write-Host "  Runs as: Logged-in user (NOT SYSTEM)" -ForegroundColor Gray
Write-Host "  Executable: $AGENT_EXE" -ForegroundColor Gray
Write-Host "  Config: $AGENT_CONFIG" -ForegroundColor Gray
Write-Host ""

$taskRun = "`"$AGENT_EXE`" --config `"$AGENT_CONFIG`""
$userName = $env:USERNAME

if (-not $userName) {
    Write-Host "ERROR: Could not determine current username" -ForegroundColor Red
    exit 1
}

# Remove existing task
schtasks /Delete /TN "PitBox Agent" /F 2>$null | Out-Null

# Create task
$result = schtasks /Create /TN "PitBox Agent" /TR $taskRun /SC ONLOGON /RU $userName /F

if ($LASTEXITCODE -eq 0) {
    Write-Host "SUCCESS: Scheduled Task created" -ForegroundColor Green
    Write-Host ""
    Write-Host "The Agent will start automatically when user '$userName' logs on." -ForegroundColor White
    Write-Host "To start immediately: Logout and login, or run PitBoxAgent.exe manually." -ForegroundColor Gray
    Write-Host ""
} else {
    Write-Host "ERROR: Failed to create Scheduled Task" -ForegroundColor Red
    Write-Host $result -ForegroundColor Red
    exit 1
}
