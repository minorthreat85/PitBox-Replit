@echo off
REM PitBox Controller - START Script
REM Starts the PitBoxController Windows Service

echo.
echo ========================================
echo   PitBox Controller - START
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

echo Starting PitBoxController service...
net start PitBoxController

if %errorLevel% equ 0 (
    echo.
    echo SUCCESS: PitBoxController service started
    echo Web UI: http://localhost:9600
    echo.
    echo The browser should open automatically.
    echo If not, manually navigate to: http://localhost:9600
    echo.
    timeout /t 3
    start http://localhost:9600
) else (
    echo.
    echo ERROR: Failed to start PitBoxController service
    echo Check logs at: C:\ProgramData\PitBox\logs\
    echo.
    pause
)
