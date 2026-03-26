# PitBox Development Setup Script
# Sets up Python virtual environment and installs dependencies

param(
    [switch]$Dev
)

# Enforce -Dev flag
if (-not $Dev) {
    Write-Host "ERROR: This is a DEV script. Use -Dev flag to confirm." -ForegroundColor Red
    Write-Host "Example: .\scripts\setup_dev.ps1 -Dev" -ForegroundColor Yellow
    exit 1
}

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  PitBox Development Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if running from dev directory
$expectedPath = "C:\PitBox\dev\pitbox"
if ($PWD.Path -ne $expectedPath) {
    Write-Host "WARNING: Expected to run from $expectedPath" -ForegroundColor Yellow
    Write-Host "Current directory: $PWD" -ForegroundColor Yellow
    $continue = Read-Host "Continue anyway? (y/n)"
    if ($continue -ne 'y') {
        exit 1
    }
}

# Check Python version (MANDATORY: 3.11.9)
Write-Host "Checking Python version..." -ForegroundColor Green

$pythonExe = "C:\Python311\python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Host ""
    Write-Host "ERROR: Python 3.11.9 not found at $pythonExe" -ForegroundColor Red
    Write-Host ""
    Write-Host "PitBox requires Python 3.11.9 (64-bit, Windows)" -ForegroundColor Yellow
    Write-Host "Download from: https://www.python.org/downloads/release/python-3119/" -ForegroundColor Yellow
    Write-Host "Install to: C:\Python311\" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

try {
    $pythonVersion = & $pythonExe --version 2>&1
    Write-Host "  Found: $pythonVersion at $pythonExe" -ForegroundColor Gray
    
    # Verify version is 3.11.x (NOT 3.12 or 3.13)
    if ($pythonVersion -notmatch "Python 3\.11\.") {
        Write-Host ""
        Write-Host "ERROR: Wrong Python version" -ForegroundColor Red
        Write-Host "  Found: $pythonVersion" -ForegroundColor Red
        Write-Host "  Required: Python 3.11.9 (>=3.11,<3.12)" -ForegroundColor Red
        Write-Host ""
        Write-Host "Do NOT use Python 3.12 or 3.13" -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }
} catch {
    Write-Host "ERROR: Could not verify Python version" -ForegroundColor Red
    exit 1
}

# Set python command to use 3.11.9
$python = $pythonExe

# Create virtual environment
Write-Host ""
Write-Host "Creating virtual environment..." -ForegroundColor Green
if (Test-Path ".venv") {
    Write-Host "  .venv already exists, skipping..." -ForegroundColor Yellow
} else {
    & $python -m venv .venv
    Write-Host "  Created .venv with Python 3.11.9" -ForegroundColor Gray
}

# Activate virtual environment
Write-Host ""
Write-Host "Activating virtual environment..." -ForegroundColor Green
$activateScript = ".\.venv\Scripts\Activate.ps1"
if (Test-Path $activateScript) {
    & $activateScript
    Write-Host "  Virtual environment activated" -ForegroundColor Gray
} else {
    Write-Host "ERROR: Could not find activation script" -ForegroundColor Red
    exit 1
}

# Upgrade pip
Write-Host ""
Write-Host "Upgrading pip..." -ForegroundColor Green
& $python -m pip install --upgrade pip | Out-Null
Write-Host "  pip upgraded" -ForegroundColor Gray

# Install dependencies
Write-Host ""
Write-Host "Installing dependencies from requirements.txt..." -ForegroundColor Green
& $python -m pip install -r requirements.txt
Write-Host "  Dependencies installed" -ForegroundColor Gray

# Generate example configs
Write-Host ""
Write-Host "Generating example configs..." -ForegroundColor Green
& .\scripts\generate_configs.ps1 -Dev
Write-Host "  Example configs created in examples\" -ForegroundColor Gray

# Success
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Setup Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "To run the Agent in dev mode:" -ForegroundColor Cyan
Write-Host "  python -m agent.main --config examples\agent_config.Sim1.json --debug" -ForegroundColor White
Write-Host ""
Write-Host "To run the Controller in dev mode:" -ForegroundColor Cyan
Write-Host "  python -m controller.main --config examples\controller_config.json --debug" -ForegroundColor White
Write-Host ""
Write-Host "To build EXEs:" -ForegroundColor Cyan
Write-Host "  .\scripts\build_release.ps1 -Dev" -ForegroundColor White
Write-Host ""
