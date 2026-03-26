@echo off
REM PitBox Controller - STOP Script
REM Stops the PitBoxController Windows Service

echo.
echo ========================================
echo   PitBox Controller - STOP
echo ========================================
echo.

REM Check for admin privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: This script requires Administrator privileges.
    echo Please right-click and select "Run as Administrator"
    echo.
    pause
    exit /b 1
)

echo Stopping PitBoxController service...
net stop PitBoxController

if %errorLevel% equ 0 (
    echo.
    echo SUCCESS: PitBoxController service stopped
    echo.
) else (
    echo.
    echo WARNING: Service may already be stopped or not installed
    echo.
)

pause
