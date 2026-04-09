# PitBox Publish Release Script
# Builds installer, computes SHA-256, creates GitHub Release, and uploads installer.
# Run from the dev folder (same folder as version.txt and PitBoxController.spec).
# Usage: .\scripts\publish_release.ps1 -Dev -GitHubToken "ghp_..."
#        (or set env var GITHUB_PERSONAL_ACCESS_TOKEN_PITBOX_REPLIT before running)

param(
    [switch]$Dev,
    [switch]$SkipBuild,
    [string]$GitHubToken = "",
    [string]$Notes = ""
)

if (-not $Dev) {
    Write-Host "ERROR: Use -Dev flag to confirm this is a dev machine." -ForegroundColor Red
    Write-Host "Example: .\scripts\publish_release.ps1 -Dev" -ForegroundColor Yellow
    exit 1
}

$ErrorActionPreference = "Stop"

# --- Config ---
$GITHUB_OWNER = "minorthreat85"
$GITHUB_REPO  = "pitbox-releases"

# --- Token ---
if (-not $GitHubToken) {
    $GitHubToken = $env:GITHUB_PERSONAL_ACCESS_TOKEN_PITBOX_REPLIT
}
if (-not $GitHubToken) {
    Write-Host "ERROR: GitHub token not found." -ForegroundColor Red
    Write-Host "Set env var GITHUB_PERSONAL_ACCESS_TOKEN_PITBOX_REPLIT or pass -GitHubToken 'ghp_...'." -ForegroundColor Yellow
    exit 1
}

# --- Read version ---
$versionFile = Join-Path $PSScriptRoot "..\version.txt"
if (-not (Test-Path $versionFile)) {
    Write-Host "ERROR: version.txt not found at $versionFile" -ForegroundColor Red
    exit 1
}
$VERSION = (Get-Content $versionFile).Trim()
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  PitBox Release Publisher v$VERSION" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Build ---
if (-not $SkipBuild) {
    Write-Host "Step 1/4: Building installer..." -ForegroundColor Green
    & "$PSScriptRoot\build_release.ps1" -Dev
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Build failed." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "Step 1/4: Skipping build (-SkipBuild)." -ForegroundColor Yellow
}

# --- Find installer ---
Write-Host ""
Write-Host "Step 2/4: Locating installer..." -ForegroundColor Green
$distDir = Join-Path $PSScriptRoot "..\dist"
$installerPath = Join-Path $distDir "PitBoxInstaller-$VERSION.exe"
if (-not (Test-Path $installerPath)) {
    # Fallback: any PitBoxInstaller*.exe in dist/
    $candidates = Get-ChildItem -Path $distDir -Filter "PitBoxInstaller*.exe" -ErrorAction SilentlyContinue
    if ($candidates.Count -eq 0) {
        Write-Host "ERROR: No PitBoxInstaller*.exe found in $distDir" -ForegroundColor Red
        Write-Host "  Expected: $installerPath" -ForegroundColor Gray
        exit 1
    }
    $installerPath = $candidates[0].FullName
    Write-Host "  WARNING: Using $($candidates[0].Name) (expected PitBoxInstaller-$VERSION.exe)" -ForegroundColor Yellow
}
$installerName = Split-Path $installerPath -Leaf
Write-Host "  Installer: $installerName" -ForegroundColor Gray
Write-Host "  Size: $([math]::Round((Get-Item $installerPath).Length / 1MB, 1)) MB" -ForegroundColor Gray

# --- SHA-256 ---
Write-Host ""
Write-Host "Step 3/4: Computing SHA-256..." -ForegroundColor Green
$sha256 = (Get-FileHash -Path $installerPath -Algorithm SHA256).Hash.ToLower()
Write-Host "  SHA-256: $sha256" -ForegroundColor Gray

# --- Release notes ---
$tagName = "v$VERSION"
$releaseNotes = if ($Notes) { $Notes } else {
    "PitBox $tagName`n`nSee RELEASE_NOTES_$VERSION.md for details."
}
# Append SHA-256 annotation (required by update integrity check)
$sha256Annotation = "<!-- pitbox_sha256:${installerName}:${sha256} -->"
$releaseBody = $releaseNotes.TrimEnd() + "`n`n$sha256Annotation`n"

# --- Create GitHub Release ---
Write-Host ""
Write-Host "Step 4/4: Publishing GitHub release $tagName..." -ForegroundColor Green

$headers = @{
    "Authorization" = "token $GitHubToken"
    "Accept"        = "application/vnd.github.v3+json"
    "Content-Type"  = "application/json"
    "User-Agent"    = "PitBox-Publisher"
}

# Check if release already exists
$existingRelease = $null
try {
    $existingRelease = Invoke-RestMethod `
        -Uri "https://api.github.com/repos/$GITHUB_OWNER/$GITHUB_REPO/releases/tags/$tagName" `
        -Headers $headers -Method Get -ErrorAction Stop
    Write-Host "  Release $tagName already exists (id=$($existingRelease.id)) -- updating notes..." -ForegroundColor Yellow
} catch {
    $existingRelease = $null
}

if ($existingRelease) {
    # Update existing release body (add SHA-256 if not already present)
    $currentBody = $existingRelease.body ?? ""
    if ($currentBody -notlike "*$sha256*") {
        $updatedBody = $currentBody.TrimEnd() + "`n`n$sha256Annotation`n"
        $patchBody = @{ body = $updatedBody } | ConvertTo-Json
        Invoke-RestMethod `
            -Uri "https://api.github.com/repos/$GITHUB_OWNER/$GITHUB_REPO/releases/$($existingRelease.id)" `
            -Headers $headers -Method Patch -Body $patchBody | Out-Null
        Write-Host "  Updated release notes with SHA-256 annotation." -ForegroundColor Gray
    }
    $release = $existingRelease
} else {
    # Create new release
    $createBody = @{
        tag_name         = $tagName
        name             = "PitBox $VERSION"
        body             = $releaseBody
        draft            = $false
        prerelease       = $false
    } | ConvertTo-Json
    $release = Invoke-RestMethod `
        -Uri "https://api.github.com/repos/$GITHUB_OWNER/$GITHUB_REPO/releases" `
        -Headers $headers -Method Post -Body $createBody
    Write-Host "  Created release $tagName (id=$($release.id))" -ForegroundColor Gray
}

# Helper function to upload a release asset (idempotent: deletes existing first)
function Upload-ReleaseAsset {
    param($ReleaseId, $AssetPath, $AssetName, $Headers)
    $existingAsset = $release.assets | Where-Object { $_.name -eq $AssetName } | Select-Object -First 1
    if ($existingAsset) {
        Write-Host "  Asset $AssetName already attached -- deleting and re-uploading..." -ForegroundColor Yellow
        Invoke-RestMethod `
            -Uri "https://api.github.com/repos/$GITHUB_OWNER/$GITHUB_REPO/releases/assets/$($existingAsset.id)" `
            -Headers $Headers -Method Delete | Out-Null
    }
    Write-Host "  Uploading $AssetName ($([math]::Round((Get-Item $AssetPath).Length / 1MB, 1)) MB)..." -ForegroundColor Gray
    $uploadUrl = "https://uploads.github.com/repos/$GITHUB_OWNER/$GITHUB_REPO/releases/$ReleaseId/assets?name=$AssetName"
    $uploadHeaders = @{
        "Authorization" = "token $GitHubToken"
        "Content-Type"  = "application/octet-stream"
        "User-Agent"    = "PitBox-Publisher"
    }
    $result = Invoke-RestMethod `
        -Uri $uploadUrl -Headers $uploadHeaders -Method Post `
        -InFile $AssetPath -ContentType "application/octet-stream"
    Write-Host "  Uploaded: $($result.browser_download_url)" -ForegroundColor Gray
}

# Upload unified installer (primary asset)
Upload-ReleaseAsset -ReleaseId $release.id -AssetPath $installerPath -AssetName $installerName -Headers $headers

# Upload standalone Agent installer if present
$agentSetupPath = Join-Path $distDir "PitBoxAgentSetup_$VERSION.exe"
if (Test-Path $agentSetupPath) {
    $agentSetupName = Split-Path $agentSetupPath -Leaf
    Upload-ReleaseAsset -ReleaseId $release.id -AssetPath $agentSetupPath -AssetName $agentSetupName -Headers $headers
} else {
    Write-Host "  Skipping PitBoxAgentSetup_$VERSION.exe (not found in dist)" -ForegroundColor Yellow
}

# Upload standalone Controller installer if present
$controllerSetupPath = Join-Path $distDir "PitBoxControllerSetup_$VERSION.exe"
if (Test-Path $controllerSetupPath) {
    $controllerSetupName = Split-Path $controllerSetupPath -Leaf
    Upload-ReleaseAsset -ReleaseId $release.id -AssetPath $controllerSetupPath -AssetName $controllerSetupName -Headers $headers
} else {
    Write-Host "  Skipping PitBoxControllerSetup_$VERSION.exe (not found in dist)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Release v$VERSION published!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  GitHub: $($release.html_url)" -ForegroundColor Cyan
Write-Host "  Installer: $installerName" -ForegroundColor Cyan
Write-Host "  SHA-256: $sha256" -ForegroundColor Cyan
Write-Host ""
Write-Host "The 'Download Update & Restart' button in PitBox will now offer this update." -ForegroundColor Green
Write-Host ""
