# PitBox - Push updated PitBoxAgent.exe to all online sim PCs
#
# Usage (from C:\PitBox\dev\pitbox on admin PC):
#   .\scripts\push_agent_to_sims.ps1 -Dev
#   .\scripts\push_agent_to_sims.ps1 -Dev -SkipBuild   (reuse existing dist\PitBoxAgent.exe)
#
# What it does:
#   1. Optionally builds a fresh PitBoxAgent.exe
#   2. Queries the controller for enrolled rigs + their IPs
#   3. Copies the exe to \\<IP>\C$\PitBox\Agent\bin\ on each sim
#   4. Restarts the PitBoxAgent service on each sim via sc.exe
#
# Requirements:
#   - Admin PC must have network access to \\<SimIP>\C$  (standard Windows LAN)
#   - PitBoxAgent Windows service must be named "PitBoxAgent" on each sim
#   - Controller must be running at localhost:9630

param(
    [switch]$Dev,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

if (-not $Dev) {
    Write-Host "ERROR: Use -Dev flag to confirm. Example: .\scripts\push_agent_to_sims.ps1 -Dev" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  PitBox - Push Agent to Sims" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Build
$agentExe = "dist\PitBoxAgent.exe"
if ($SkipBuild) {
    Write-Host "Step 1: Skipping build (using existing $agentExe)" -ForegroundColor Gray
    if (-not (Test-Path $agentExe)) {
        Write-Host "ERROR: $agentExe not found. Remove -SkipBuild to build first." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "Step 1: Building PitBoxAgent.exe..." -ForegroundColor Green
    & .\scripts\build_release.ps1 -Dev -SkipInstallers
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Build failed" -ForegroundColor Red
        exit 1
    }
    if (-not (Test-Path $agentExe)) {
        Write-Host "ERROR: $agentExe not found after build" -ForegroundColor Red
        exit 1
    }
}

$exeSize    = (Get-Item $agentExe).Length
$exeSizeMB  = [math]::Round($exeSize / 1048576, 1)
Write-Host "  Agent binary: $agentExe  ($exeSizeMB MB)" -ForegroundColor Gray
Write-Host ""

# Step 2: Read enrolled rigs from disk (avoids auth complexity)
Write-Host "Step 2: Reading enrolled rigs from disk..." -ForegroundColor Green
$enrolledFile = "$env:APPDATA\PitBox\Controller\enrolled_rigs.json"
if (-not (Test-Path $enrolledFile)) {
    Write-Host "ERROR: Enrolled rigs file not found at $enrolledFile" -ForegroundColor Red
    Write-Host "Make sure at least one sim has been enrolled in PitBox." -ForegroundColor Yellow
    exit 1
}
try {
    $rigsJson = Get-Content $enrolledFile -Raw -ErrorAction Stop
    $parsed   = $rigsJson | ConvertFrom-Json
    # File is {"rigs":[...]} — unwrap the inner array
    if ($parsed.rigs) {
        $rigs = @($parsed.rigs)
    } elseif ($parsed -is [array]) {
        $rigs = $parsed
    } else {
        $rigs = @($parsed)
    }
} catch {
    Write-Host "ERROR: Could not parse $enrolledFile  ($_)" -ForegroundColor Red
    exit 1
}
if ($rigs.Count -eq 0) {
    Write-Host "No enrolled rigs found in $enrolledFile. Nothing to do." -ForegroundColor Yellow
    exit 0
}
Write-Host "  Found $($rigs.Count) enrolled rig(s)" -ForegroundColor Gray
Write-Host ""

# Step 3: Push to each rig
Write-Host "Step 3: Pushing to rigs..." -ForegroundColor Green
$results = @()
foreach ($rig in $rigs) {
    # PowerShell 5-compatible null coalescing
    $agentId = if ($rig.agent_id) { $rig.agent_id } elseif ($rig.id) { $rig.id } else { "unknown" }
    $rigHost = if ($rig.host)     { $rig.host }     elseif ($rig.ip) { $rig.ip } elseif ($rig.address) { $rig.address } else { "" }
    $label   = if ($rig.display_name) { $rig.display_name } elseif ($rig.hostname) { $rig.hostname } else { $agentId }

    if (-not $rigHost) {
        Write-Host "  [$label] SKIP - no host/IP recorded" -ForegroundColor Yellow
        $results += [pscustomobject]@{ Rig=$label; Result="SKIPPED (no IP)"; Host="" }
        continue
    }

    $targetDir = "\\$rigHost\C`$\PitBox\Agent\bin"
    $targetExe = "$targetDir\PitBoxAgent.exe"

    Write-Host "  [$label]  $rigHost" -ForegroundColor White
    Write-Host "    Copying to $targetExe ..." -NoNewline -ForegroundColor Gray

    try {
        if (-not (Test-Path $targetDir)) {
            New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
        }
        Copy-Item $agentExe $targetExe -Force
        Write-Host " OK" -ForegroundColor Green
    } catch {
        Write-Host " FAILED ($_)" -ForegroundColor Red
        $results += [pscustomobject]@{ Rig=$label; Result="COPY FAILED: $_"; Host=$rigHost }
        continue
    }

    # Restart the service via sc.exe (no WinRM required)
    Write-Host "    Restarting PitBoxAgent service ..." -NoNewline -ForegroundColor Gray
    try {
        $null = & sc.exe "\\$rigHost" stop  PitBoxAgent 2>&1
        Start-Sleep -Seconds 2
        $startOut = & sc.exe "\\$rigHost" start PitBoxAgent 2>&1
        if ($LASTEXITCODE -eq 0 -or ($startOut -join " ") -match "START_PENDING|RUNNING") {
            Write-Host " OK" -ForegroundColor Green
            $results += [pscustomobject]@{ Rig=$label; Result="Updated + restarted"; Host=$rigHost }
        } else {
            Write-Host " Warning - sc exit $LASTEXITCODE" -ForegroundColor Yellow
            $results += [pscustomobject]@{ Rig=$label; Result="Copied, restart uncertain (exit $LASTEXITCODE)"; Host=$rigHost }
        }
    } catch {
        Write-Host " FAILED ($_)" -ForegroundColor Red
        $results += [pscustomobject]@{ Rig=$label; Result="Copied, restart FAILED: $_"; Host=$rigHost }
    }
}

# Summary
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Summary" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
$results | Format-Table -AutoSize
Write-Host ""
Write-Host "Done. Each updated agent will serve the new /launch-mumble endpoint after restart." -ForegroundColor Green
Write-Host ""
