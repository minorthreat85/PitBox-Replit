# PitBox Auto-Updater
# Checks GitHub Releases and updates PitBox if newer version available

param(
    [switch]$Force,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

# Configuration
$GITHUB_REPO = "minorthreat85/pitbox-releases"
$INSTALL_ROOT = "C:\PitBox"
$VERSION_FILE = "$INSTALL_ROOT\VERSION.txt"
$BACKUP_DIR = "$INSTALL_ROOT\backup"
$CONTROLLER_SERVICE = "PitBoxController"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  PitBox Auto-Updater" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check admin privileges
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Elevating privileges to install update..." -ForegroundColor Yellow
    $scriptArgs = "-ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($Force) { $scriptArgs += " -Force" }
    if ($CheckOnly) { $scriptArgs += " -CheckOnly" }
    Start-Process powershell.exe -ArgumentList $scriptArgs -Verb RunAs
    exit 0
}

# Get current version
$currentVersion = "unknown"
if (Test-Path $VERSION_FILE) {
    $currentVersion = (Get-Content $VERSION_FILE).Trim()
    Write-Host "Current version: $currentVersion" -ForegroundColor Green
} else {
    Write-Host "Current version: unknown (VERSION.txt not found)" -ForegroundColor Yellow
}

# Fetch latest release from GitHub
Write-Host ""
Write-Host "Checking GitHub for latest release..." -ForegroundColor Green

try {
    $releaseInfo = Invoke-RestMethod -Uri "https://api.github.com/repos/$GITHUB_REPO/releases/latest" -Headers @{
        "User-Agent" = "PitBox-Updater"
    }
    
    $latestVersion = $releaseInfo.tag_name -replace '^v', ''  # Remove leading 'v' if present
    $releaseUrl = $releaseInfo.html_url
    $assets = $releaseInfo.assets
    
    Write-Host "Latest version: $latestVersion" -ForegroundColor Green
    
    # Compare versions
    if ($currentVersion -eq $latestVersion -and -not $Force) {
        Write-Host ""
        Write-Host "You are already running the latest version!" -ForegroundColor Green
        Write-Host ""
        exit 0
    }
    
    if ($currentVersion -ne "unknown" -and $currentVersion -ne $latestVersion) {
        Write-Host ""
        Write-Host "UPDATE AVAILABLE!" -ForegroundColor Yellow
        Write-Host "  Current: $currentVersion" -ForegroundColor Gray
        Write-Host "  Latest:  $latestVersion" -ForegroundColor Gray
        Write-Host ""
    }
    
    if ($CheckOnly) {
        Write-Host "Check-only mode. Exiting." -ForegroundColor Gray
        Write-Host ""
        exit 0
    }
    
    # Find installer asset
    $installerAsset = $assets | Where-Object { $_.name -like "PitBoxInstaller*.exe" } | Select-Object -First 1
    
    if (-not $installerAsset) {
        Write-Host "ERROR: No installer found in latest release" -ForegroundColor Red
        Write-Host "Release URL: $releaseUrl" -ForegroundColor Gray
        Write-Host ""
        exit 1
    }
    
    Write-Host "Found installer: $($installerAsset.name)" -ForegroundColor Green
    Write-Host ""
    
    # Confirm update
    if (-not $Force) {
        $confirm = Read-Host "Download and install update? (Y/N)"
        if ($confirm -ne "Y" -and $confirm -ne "y") {
            Write-Host "Update cancelled." -ForegroundColor Yellow
            Write-Host ""
            exit 0
        }
    }
    
    # Download installer
    $tempDir = "$env:TEMP\PitBoxUpdate"
    if (-not (Test-Path $tempDir)) {
        New-Item -ItemType Directory -Path $tempDir | Out-Null
    }
    
    $installerPath = "$tempDir\$($installerAsset.name)"
    
    Write-Host "Downloading installer..." -ForegroundColor Green
    Write-Host "  From: $($installerAsset.browser_download_url)" -ForegroundColor Gray
    Write-Host "  To:   $installerPath" -ForegroundColor Gray
    Write-Host ""
    
    Invoke-WebRequest -Uri $installerAsset.browser_download_url -OutFile $installerPath -UseBasicParsing
    
    Write-Host "Download complete!" -ForegroundColor Green
    Write-Host ""
    
    # Stop services before update
    Write-Host "Stopping PitBox services..." -ForegroundColor Yellow
    
    $service = Get-Service -Name $CONTROLLER_SERVICE -ErrorAction SilentlyContinue
    if ($service -and $service.Status -eq "Running") {
        Stop-Service -Name $CONTROLLER_SERVICE -Force
        Write-Host "  Stopped $CONTROLLER_SERVICE" -ForegroundColor Gray
    }
    
    # Create backup
    Write-Host ""
    Write-Host "Creating backup..." -ForegroundColor Green
    
    if (Test-Path $BACKUP_DIR) {
        Remove-Item -Path $BACKUP_DIR -Recurse -Force
    }
    New-Item -ItemType Directory -Path $BACKUP_DIR | Out-Null
    
    # Backup EXEs and VERSION.txt
    if (Test-Path "$INSTALL_ROOT\PitBoxController.exe") {
        Copy-Item "$INSTALL_ROOT\PitBoxController.exe" "$BACKUP_DIR\" -Force
    }
    if (Test-Path "$VERSION_FILE") {
        Copy-Item "$VERSION_FILE" "$BACKUP_DIR\" -Force
    }
    
    Write-Host "  Backup created at: $BACKUP_DIR" -ForegroundColor Gray
    Write-Host ""
    
    # Run installer
    Write-Host "Running installer..." -ForegroundColor Green
    Write-Host "  (Follow installer prompts. Select 'Controller' role)" -ForegroundColor Yellow
    Write-Host ""
    
    Start-Process -FilePath $installerPath -Wait
    
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Update Complete!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Installed version: $latestVersion" -ForegroundColor Green
    Write-Host ""
    Write-Host "Starting PitBox services..." -ForegroundColor Green
    Start-Service -Name $CONTROLLER_SERVICE
    Write-Host "  Started $CONTROLLER_SERVICE" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Release notes: $releaseUrl" -ForegroundColor Cyan
    Write-Host ""
    
} catch {
    Write-Host ""
    Write-Host "ERROR: Update failed" -ForegroundColor Red
    Write-Host "Details: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    
    # Restore from backup if update failed
    if (Test-Path "$BACKUP_DIR\PitBoxController.exe") {
        Write-Host "Restoring from backup..." -ForegroundColor Yellow
        Copy-Item "$BACKUP_DIR\PitBoxController.exe" "$INSTALL_ROOT\" -Force
        Copy-Item "$BACKUP_DIR\VERSION.txt" "$INSTALL_ROOT\" -Force -ErrorAction SilentlyContinue
        Write-Host "  Restored" -ForegroundColor Gray
        Write-Host ""
    }
    
    exit 1
}
