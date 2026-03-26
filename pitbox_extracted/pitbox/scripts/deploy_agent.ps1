# PitBox Agent Deploy Script
# Builds PitBoxAgent.exe, copies to C:\PitBox\Agent\bin\, ensures config exists,
# optionally restarts PitBoxAgent service, and verifies health.
#
# Usage: .\scripts\deploy_agent.ps1 -Dev [-RestartService] [-VerifyToken "your-token"]
#   -RestartService  Restart NSSM service "PitBoxAgent" if it exists
#   -VerifyToken     Token for /ping verification (e.g. "sim5"); skip verify if not provided

param(
    [switch]$Dev,
    [switch]$RestartService,
    [string]$VerifyToken = ""
)

$ErrorActionPreference = "Stop"

if (-not $Dev) {
    Write-Host "ERROR: Use -Dev flag to confirm. Example: .\scripts\deploy_agent.ps1 -Dev" -ForegroundColor Red
    exit 1
}

$expectedPath = "C:\PitBox\dev\pitbox"
if ($PWD.Path -ne $expectedPath) {
    Write-Host "ERROR: Must run from $expectedPath" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  PitBox Agent Deploy" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Build Agent
Write-Host "Step 1: Building PitBoxAgent.exe..." -ForegroundColor Green
& .\scripts\build_release.ps1 -Dev -SkipInstallers
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Build failed" -ForegroundColor Red
    exit 1
}

$agentExe = "dist\PitBoxAgent.exe"
if (-not (Test-Path $agentExe)) {
    Write-Host "ERROR: PitBoxAgent.exe not found in dist\" -ForegroundColor Red
    exit 1
}

# Step 2: Copy to C:\PitBox\Agent\bin\
$targetBin = "C:\PitBox\Agent\bin"
$targetExe = "$targetBin\PitBoxAgent.exe"
Write-Host ""
Write-Host "Step 2: Deploying to $targetExe..." -ForegroundColor Green
New-Item -ItemType Directory -Path $targetBin -Force | Out-Null
Copy-Item $agentExe $targetExe -Force
Write-Host "  Copied PitBoxAgent.exe" -ForegroundColor Gray

# Step 3: Ensure config folder exists
$configDir = "C:\PitBox\Agent\config"
$configPath = "$configDir\agent_config.json"
Write-Host ""
Write-Host "Step 3: Ensuring config at $configPath..." -ForegroundColor Green
New-Item -ItemType Directory -Path $configDir -Force | Out-Null
if (-not (Test-Path $configPath)) {
    Write-Host "  Config not found - creating default (run with --init to customize)" -ForegroundColor Yellow
    & $targetExe --config $configPath --init 2>$null
}

# Step 4: Restart service if requested
if ($RestartService) {
    Write-Host ""
    Write-Host "Step 4: Restarting PitBoxAgent service..." -ForegroundColor Green
    $svc = Get-Service -Name "PitBoxAgent" -ErrorAction SilentlyContinue
    if ($svc) {
        Restart-Service -Name "PitBoxAgent" -Force
        Write-Host "  Service restarted" -ForegroundColor Gray
        Start-Sleep -Seconds 3
    } else {
        Write-Host "  PitBoxAgent service not found - skip (run agent manually or install service)" -ForegroundColor Yellow
    }
} else {
    Write-Host ""
    Write-Host "Step 4: Skipping service restart (use -RestartService to restart)" -ForegroundColor Gray
}

# Step 5: Verification (optional)
if ($VerifyToken) {
    Write-Host ""
    Write-Host "Step 5: Verifying Agent /ping..." -ForegroundColor Green
    try {
        $headers = @{ Authorization = "Bearer $VerifyToken" }
        $resp = Invoke-RestMethod -Uri "http://localhost:9600/ping" -Headers $headers -Method Get -TimeoutSec 5
        Write-Host "  /ping OK: $($resp.status)" -ForegroundColor Green
    } catch {
        Write-Host "  WARNING: /ping failed (agent may not be running or token mismatch): $_" -ForegroundColor Yellow
    }
} else {
    Write-Host ""
    Write-Host "Step 5: Skipping verification (use -VerifyToken <token> to verify /ping)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deploy Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Agent binary: $targetExe" -ForegroundColor Cyan
Write-Host "Config:       $configPath" -ForegroundColor Cyan
Write-Host ""
