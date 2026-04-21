# PitBox Build Release Script
# Builds PyInstaller EXEs and Inno Setup Installers

param(
    [switch]$Dev,
    [switch]$SkipInstallers
)

# Enforce -Dev flag
if (-not $Dev) {
    Write-Host "ERROR: This is a DEV script. Use -Dev flag to confirm." -ForegroundColor Red
    Write-Host "Example: .\scripts\build_release.ps1 -Dev" -ForegroundColor Yellow
    exit 1
}

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  PitBox Production Build" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Find Python 3.11 (try C:\Python311, then py -3.11, then python in PATH)
Write-Host "Checking Python version..." -ForegroundColor Green

$pythonExe = $null
$candidates = @(
    "C:\Python311\python.exe",
    (Get-Command "py" -ErrorAction SilentlyContinue | ForEach-Object { "py" })
)
foreach ($c in $candidates) {
    if ($c -eq "py") {
        $ver = & py -3.11 -c "import sys; print(sys.executable)" 2>$null
        if ($ver -and (Test-Path $ver)) { $pythonExe = $ver; break }
        $ver = & py -3 -c "import sys; print(sys.executable)" 2>$null
        if ($ver -and (Test-Path $ver)) {
            $v = & $ver --version 2>&1
            if ($v -match "Python 3\.11\.") { $pythonExe = $ver; break }
        }
    } elseif ($c -and (Test-Path $c)) {
        $pythonExe = $c
        break
    }
}
if (-not $pythonExe) {
    $pathPython = Get-Command "python" -ErrorAction SilentlyContinue
    if ($pathPython) {
        $v = & $pathPython.Source --version 2>&1
        if ($v -match "Python 3\.11\.") { $pythonExe = $pathPython.Source }
    }
}

if (-not $pythonExe) {
    Write-Host ""
    Write-Host "ERROR: Python 3.11 not found." -ForegroundColor Red
    Write-Host ""
    Write-Host "PitBox needs Python 3.11.x (64-bit, Windows). Try one of:" -ForegroundColor Yellow
    Write-Host "  1. Install to C:\Python311\ from https://www.python.org/downloads/release/python-3119/" -ForegroundColor Gray
    Write-Host "  2. Install Python 3.11 via Microsoft Store or winget, then run: py -3.11" -ForegroundColor Gray
    Write-Host "  3. Add your Python 3.11 to PATH" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

$pythonVersion = & $pythonExe --version 2>&1
Write-Host "  Found: $pythonVersion at $pythonExe" -ForegroundColor Gray

if ($pythonVersion -notmatch "Python 3\.11\.") {
    Write-Host ""
    Write-Host "ERROR: Wrong Python version (need 3.11.x, not 3.12+)" -ForegroundColor Red
    Write-Host "  Found: $pythonVersion" -ForegroundColor Red
    Write-Host ""
    exit 1
}

# Check if running from dev directory (must contain spec files and version.txt)
if (-not (Test-Path "PitBoxController.spec") -or -not (Test-Path "version.txt")) {
    Write-Host "ERROR: Must run from the dev\pitbox directory (folder containing PitBoxController.spec and version.txt)" -ForegroundColor Red
    Write-Host "Current directory: $PWD" -ForegroundColor Red
    exit 1
}

# Read version
$version = Get-Content "version.txt" -Raw
$version = $version.Trim()
Write-Host "Building PitBox v$version" -ForegroundColor Green
Write-Host ""

# Clean dist and build directories (PowerShell safe)
Write-Host "Cleaning build directories..." -ForegroundColor Green
if (Test-Path "dist") {
    try {
        Remove-Item -Path "dist" -Recurse -Force -ErrorAction Stop
    } catch {
        Write-Host ""
        Write-Host "ERROR: Cannot remove dist\ - a file is locked (is PitBoxController running?)" -ForegroundColor Red
        Write-Host "  Stop the PitBoxController service first, then re-run update.ps1." -ForegroundColor Yellow
        Write-Host "  Detail: $_" -ForegroundColor Gray
        exit 1
    }
}
if (Test-Path "build") {
    Remove-Item -Path "build" -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path "dist" -Force | Out-Null
Write-Host "  Cleaned and created dist\" -ForegroundColor Gray

# Ensure virtual environment is activated and choose build Python
if (-not $env:VIRTUAL_ENV) {
    Write-Host ""
    Write-Host "WARNING: Virtual environment not activated" -ForegroundColor Yellow
    Write-Host "Attempting to activate .venv..." -ForegroundColor Yellow
    
    if (Test-Path ".venv\Scripts\Activate.ps1") {
        & .venv\Scripts\Activate.ps1
        Write-Host "  Activated .venv" -ForegroundColor Gray
    } else {
        Write-Host "ERROR: .venv not found. Run setup_dev.ps1 first." -ForegroundColor Red
        exit 1
    }
}

# Use venv's Python for build (has deps); fallback to checked Python
$buildPython = $pythonExe
if ($env:VIRTUAL_ENV) {
    $venvPython = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $venvPython) {
        $buildPython = $venvPython
        Write-Host "  Using venv Python for build" -ForegroundColor Gray
    }
}

# Ensure PyInstaller is available for build Python
$null = & $buildPython -m PyInstaller --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Installing PyInstaller for build..." -ForegroundColor Green
    & $buildPython -m pip install pyinstaller --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to install PyInstaller. Run: $buildPython -m pip install pyinstaller" -ForegroundColor Red
        exit 1
    }
}

# CRITICAL: Install ALL runtime requirements into the build venv.
# Previously this script only installed pyinstaller + zeroc-ice, which
# meant `collect_all('websockets')` in PitBoxAgent.spec returned empty
# tuples whenever the venv hadn't been hand-prepared, silently shipping
# an EXE without websockets and breaking telemetry at runtime
# (`STARTUP[deps] websockets_ok=False`). Installing requirements.txt up
# front guarantees every dep referenced by the .spec files is present at
# Analysis time. --upgrade so a stale venv from a previous version still
# picks up new pins (e.g. websockets==13.1).
if (Test-Path "requirements.txt") {
    Write-Host ""
    Write-Host "Installing runtime requirements (requirements.txt)..." -ForegroundColor Green
    & $buildPython -m pip install -r requirements.txt --upgrade --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to install requirements.txt. Build cannot continue -- bundled EXE would be missing dependencies." -ForegroundColor Red
        exit 1
    }
    Write-Host "  requirements.txt installed." -ForegroundColor Gray
} else {
    Write-Host "WARNING: requirements.txt not found -- bundled EXE may be missing runtime deps (e.g. websockets)." -ForegroundColor Yellow
}

# Belt-and-suspenders: explicitly verify the deps the .spec files
# `collect_all` on. If any are missing now, fail LOUDLY rather than
# producing a broken EXE that fails silently at runtime on the sims.
$_required = @('websockets')
foreach ($_pkg in $_required) {
    & $buildPython -c "import $_pkg" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: required package '$_pkg' is not importable in the build venv." -ForegroundColor Red
        Write-Host "       Run: $buildPython -m pip install $_pkg" -ForegroundColor Yellow
        exit 1
    }
}
Write-Host "  Verified critical build deps: $($_required -join ', ')" -ForegroundColor Gray

# Ensure zeroc-ice is installed (required for Mumble ICE integration + PyInstaller bundling)
Write-Host ""
Write-Host "Installing zeroc-ice for Mumble ICE integration..." -ForegroundColor Green
& $buildPython -m pip install zeroc-ice --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: zeroc-ice install failed - Mumble ICE integration will not work." -ForegroundColor Yellow
} else {
    Write-Host "  zeroc-ice installed." -ForegroundColor Gray
}

# Generate example configs if not present
if (-not (Test-Path "examples\agent_config.Sim1.json")) {
    Write-Host ""
    Write-Host "Generating example configs..." -ForegroundColor Green
    & .\scripts\generate_configs.ps1 -Dev
}

# Build Agent
Write-Host ""
Write-Host "Building PitBoxAgent.exe (windowless)..." -ForegroundColor Green
Write-Host "  (This may take a few minutes...)" -ForegroundColor Gray

& $buildPython -m PyInstaller PitBoxAgent.spec --clean

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Agent build failed" -ForegroundColor Red
    exit 1
}

Write-Host "  PitBoxAgent.exe built successfully (console=False for service)" -ForegroundColor Gray

# Build Controller (UI is bundled in EXE; use --clean so latest controller/static is included)
Write-Host ""
Write-Host "Building PitBoxController.exe (windowless)..." -ForegroundColor Green
Write-Host "  (This may take a few minutes...)" -ForegroundColor Gray

& $buildPython -m PyInstaller PitBoxController.spec --clean

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Controller build failed" -ForegroundColor Red
    exit 1
}

Write-Host "  PitBoxController.exe built successfully (console=False for service)" -ForegroundColor Gray

# Build System Tray Launcher
Write-Host ""
Write-Host "Building PitBoxTray.exe (system tray launcher)..." -ForegroundColor Green
Write-Host "  (Requires pystray and Pillow in venv)" -ForegroundColor Gray

& $buildPython -m PyInstaller PitBoxTray.spec --clean --noconfirm

if ($LASTEXITCODE -eq 0 -and (Test-Path "dist\PitBoxTray.exe")) {
    $traySize = (Get-Item "dist\PitBoxTray.exe").Length / 1MB
    Write-Host "  PitBoxTray.exe built: $([math]::Round($traySize, 2)) MB" -ForegroundColor Gray
} else {
    Write-Host "  Warning: PitBoxTray.exe build failed (non-fatal, tray feature will be unavailable)" -ForegroundColor Yellow
}

# Build PitBoxUpdater.exe (installer-based updater with UI; used by Controller and Agent)
# This is REQUIRED -- build stops if it fails.
if (Test-Path "updater\pitbox_updater_installer.py") {
    Write-Host ""
    Write-Host "Building PitBoxUpdater.exe (installer-based updater)..." -ForegroundColor Green
    $updater2Err = Join-Path $PWD "build_PitBoxUpdater_stderr.txt"
    $updater2Out = Join-Path $PWD "build_PitBoxUpdater_stdout.txt"
    $psi2 = Start-Process -FilePath $buildPython -ArgumentList "-m","PyInstaller","PitBoxUpdater.spec","--clean","--noconfirm" -NoNewWindow -Wait -PassThru -RedirectStandardError $updater2Err -RedirectStandardOutput $updater2Out
    if (Test-Path "dist\PitBoxUpdater.exe") {
        Remove-Item $updater2Err -ErrorAction SilentlyContinue
        Remove-Item $updater2Out -ErrorAction SilentlyContinue
        $updaterSize = (Get-Item "dist\PitBoxUpdater.exe").Length / 1MB
        Write-Host "  PitBoxUpdater.exe built: $([math]::Round($updaterSize, 2)) MB (included in installer at C:\PitBox\updater\)" -ForegroundColor Gray
    } else {
        Write-Host ""
        Write-Host "ERROR: PitBoxUpdater.exe build FAILED -- build halted." -ForegroundColor Red
        if ($psi2.ExitCode -ne 0) { Write-Host "  PyInstaller exit code: $($psi2.ExitCode)" -ForegroundColor Red }
        if (Test-Path $updater2Err) {
            Write-Host ""
            Write-Host "--- PitBoxUpdater stderr ---" -ForegroundColor Yellow
            Get-Content $updater2Err | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
        }
        if (Test-Path $updater2Out) {
            Write-Host ""
            Write-Host "--- PitBoxUpdater stdout (last 30 lines) ---" -ForegroundColor Yellow
            Get-Content $updater2Out | Select-Object -Last 30 | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
        }
        Write-Host ""
        Write-Host "Full logs: $updater2Out, $updater2Err" -ForegroundColor Gray
        exit 1
    }
} else {
    Write-Host ""
    Write-Host "ERROR: updater\pitbox_updater_installer.py not found -- cannot build PitBoxUpdater.exe" -ForegroundColor Red
    exit 1
}

# Clean build artifacts
Write-Host ""
Write-Host "Cleaning build artifacts..." -ForegroundColor Green
if (Test-Path "build") {
    Remove-Item -Path "build" -Recurse -Force
}
Write-Host "  Cleaned (spec files retained for future builds)" -ForegroundColor Gray

# Copy VERSION.txt to dist
Write-Host ""
Write-Host "Preparing release artifacts..." -ForegroundColor Green
Copy-Item "version.txt" "dist\VERSION.txt" -Force
Write-Host "  Copied VERSION.txt" -ForegroundColor Gray

# Copy START/STOP scripts to dist
Copy-Item "scripts\START.cmd" "dist\START.cmd" -Force
Copy-Item "scripts\STOP.cmd" "dist\STOP.cmd" -Force
Write-Host "  Copied START/STOP.cmd" -ForegroundColor Gray

# Canonical agent_config: full schema (paths.acs_exe, ac_cfg_dir, savedsetups_dir, cm_assists_presets_dir, listen_host, port). Enrollment must never modify these; see ENROLLMENT.md.
$agentConfigTemplate = "examples\agent_config.template.json"
if (-not (Test-Path $agentConfigTemplate)) {
  $agentConfigTemplate = "examples\agent_config.Sim1.json"
}
# Copy latest agent config for reference / overwrite
if (Test-Path $agentConfigTemplate) {
  Copy-Item $agentConfigTemplate "dist\agent_config.example.json" -Force
  Write-Host "  Copied agent_config.example.json (full schema)" -ForegroundColor Gray
}

# Create Agent folder with config + logs and latest agent_config.json (full schema; do not overwrite immutable fields on enrollment)
$agentConfigDir = "dist\Agent\config"
$agentLogsDir = "dist\Agent\logs"
New-Item -ItemType Directory -Path $agentConfigDir -Force | Out-Null
New-Item -ItemType Directory -Path $agentLogsDir -Force | Out-Null
if (Test-Path $agentConfigTemplate) {
  Copy-Item $agentConfigTemplate "$agentConfigDir\agent_config.json" -Force
  Write-Host "  Created Agent\config\agent_config.json and Agent\logs (schema: agent_id, token, listen_host, port, paths)" -ForegroundColor Gray
} else {
  Write-Host "  WARNING: No agent_config template found; Agent\config created empty" -ForegroundColor Yellow
}

# Copy updater script to dist (required for Inno installer and live C:\PitBox\tools\update_pitbox.ps1)
if (-not (Test-Path "dist\tools")) {
    New-Item -ItemType Directory -Path "dist\tools" | Out-Null
}
if (-not (Test-Path "tools\update_pitbox.ps1")) {
    Write-Host "  ERROR: tools\update_pitbox.ps1 not found - installer and live updates will fail" -ForegroundColor Red
} else {
    Copy-Item "tools\update_pitbox.ps1" "dist\tools\update_pitbox.ps1" -Force
    Write-Host "  Copied update_pitbox.ps1" -ForegroundColor Gray
    $silentCheck = Select-String -Path "dist\tools\update_pitbox.ps1" -Pattern "/SILENT|ArgumentList.*SILENT" -AllMatches
    if ($silentCheck) {
        Write-Host "  WARNING: dist\tools\update_pitbox.ps1 passes SILENT to the installer - Inno should run interactively" -ForegroundColor Yellow
    }
}
if (-not (Test-Path "dist\tools\update_pitbox.ps1")) {
    throw "dist\tools\update_pitbox.ps1 missing after copy; cannot build installer."
}

# Create README in dist
Write-Host ""
Write-Host "Creating dist README..." -ForegroundColor Green

$distReadme = @"
PitBox v$version - Build Output
================================

This directory contains the built executables and scripts for PitBox.

Files:
  - PitBoxAgent.exe       Agent for sim PCs
  - PitBoxController.exe  Controller for admin PC
  - Agent\config\agent_config.json  Agent config with updated paths (created on build)
  - Agent\logs\          Agent log output folder
  - agent_config.example.json  Same as Agent\config\agent_config.json; copy to use or restore
  - VERSION.txt           Version identifier
  - START.cmd             Start Controller service
  - STOP.cmd              Stop Controller service
  - tools\update_pitbox.ps1  Auto-updater script (fallback)
  - updater\PitBoxUpdater.exe  Installer-based updater (Controller + Agent)

Deployment:
  RECOMMENDED: Use PitBoxInstaller.exe (see below)

  Manual Installation:
  1. Copy entire dist folder to C:\PitBox\ (so Agent\config and Agent\logs are present)
  2. Agent uses C:\PitBox\Agent\config\agent_config.json and C:\PitBox\Agent\logs by default
  3. Run PitBoxController.exe --init to create controller config if needed
  4. Edit C:\PitBox\controller_config.json and C:\PitBox\Agent\config\agent_config.json as needed
  5. Install service with NSSM (see docs)

Updates:
  Run: powershell -File C:\PitBox\tools\update_pitbox.ps1

For detailed setup instructions, see INSTALLER_GUIDE.md.

WARNING: Do NOT overwrite controller_config.json during updates.
"@

$distReadme | Set-Content -Path "dist\README.txt" -Encoding UTF8
Write-Host "  Created dist\README.txt" -ForegroundColor Gray

# Verify outputs
Write-Host ""
Write-Host "Verifying build outputs..." -ForegroundColor Green

$agentExe = "dist\PitBoxAgent.exe"
$controllerExe = "dist\PitBoxController.exe"

if ((Test-Path $agentExe) -and (Test-Path $controllerExe)) {
    $agentSize = (Get-Item $agentExe).Length / 1MB
    $controllerSize = (Get-Item $controllerExe).Length / 1MB
    
    Write-Host "  PitBoxAgent.exe: $([math]::Round($agentSize, 2)) MB" -ForegroundColor Gray
    Write-Host "  PitBoxController.exe: $([math]::Round($controllerSize, 2)) MB" -ForegroundColor Gray
} else {
    Write-Host "ERROR: Build outputs not found" -ForegroundColor Red
    exit 1
}

# Prepare NSSM for installer
Write-Host ""
Write-Host "Preparing NSSM for installer..." -ForegroundColor Green

$nssmDir = "tools"
$nssmPath = "$nssmDir\nssm.exe"

if (-not (Test-Path $nssmDir)) {
    New-Item -ItemType Directory -Path $nssmDir | Out-Null
}

if (-not (Test-Path $nssmPath)) {
    Write-Host "  WARNING: NSSM not found at $nssmPath" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  To include NSSM in the installer:" -ForegroundColor White
    Write-Host "    1. Download from: https://nssm.cc/download" -ForegroundColor Gray
    Write-Host "    2. Extract win64\nssm.exe" -ForegroundColor Gray
    Write-Host "    3. Copy to: $nssmPath" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  The installer will build without NSSM, but service installation will fail." -ForegroundColor Yellow
    Write-Host ""
} else {
    Write-Host "  NSSM found: $nssmPath" -ForegroundColor Gray
}

# Sync version.ini from version.txt so Inno Setup reads the same version (for versioned installer filename and upgrade)
if (Test-Path "version.txt") {
    $ver = (Get-Content "version.txt" -Raw).Trim()
    $ini = "[Version]`r`nVersion=$ver`r`n"
    Set-Content -Path "version.ini" -Value $ini -Encoding ASCII -NoNewline
    Write-Host "  Synced version.ini (Version=$ver) for Inno Setup" -ForegroundColor Gray
}

# Build Unified Installer with Inno Setup
if (-not $SkipInstallers) {
    Write-Host ""
    Write-Host "Building Unified Inno Setup Installer..." -ForegroundColor Green
    
    # Find Inno Setup compiler
    $innoSetupPaths = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
        "C:\Program Files\Inno Setup 5\ISCC.exe"
    )
    
    $iscc = $null
    foreach ($path in $innoSetupPaths) {
        if (Test-Path $path) {
            $iscc = $path
            break
        }
    }
    
    if ($iscc) {
        Write-Host "  Found Inno Setup: $iscc" -ForegroundColor Gray
        
        # Build Unified Installer (output: dist\PitBoxInstaller_<version>.exe for GitHub Releases / update_pitbox.ps1)
        Write-Host ""
        Write-Host "  Building PitBoxInstaller_$version.exe..." -ForegroundColor Green
        $issFile = "installer\pitbox.iss"
        
        if (Test-Path $issFile) {
            & $iscc $issFile /Q
            if ($LASTEXITCODE -ne 0) {
                Write-Host "    Warning: Installer build had issues (exit code $LASTEXITCODE)" -ForegroundColor Yellow
            } else {
                $unifiedInstaller = "dist\PitBoxInstaller_$version.exe"
                Write-Host "    Installer built: $unifiedInstaller" -ForegroundColor Gray
                # Copy to stable name for local use (PitBoxInstaller.exe)
                if (Test-Path $unifiedInstaller) {
                    Copy-Item $unifiedInstaller "dist\PitBoxInstaller.exe" -Force
                    Write-Host "    Also copied to: dist\PitBoxInstaller.exe" -ForegroundColor Gray
                }
            }
        } else {
            Write-Host "    ERROR: Installer script not found: $issFile" -ForegroundColor Red
        }

        # Build standalone Controller installer (for self-update / GitHub releases)
        $controllerIss = "installer\controller.iss"
        if (Test-Path $controllerIss) {
            Write-Host ""
            Write-Host "  Building PitBoxControllerSetup_$version.exe..." -ForegroundColor Green
            & $iscc $controllerIss /Q
            if ($LASTEXITCODE -ne 0) {
                Write-Host "    Warning: Controller installer build had issues (exit code $LASTEXITCODE)" -ForegroundColor Yellow
            } else {
                Write-Host "    Controller installer built successfully" -ForegroundColor Gray
            }
        }

        # Build standalone Agent installer (for sim PC deployment / GitHub releases)
        $agentIss = "installer\agent.iss"
        if (Test-Path $agentIss) {
            Write-Host ""
            Write-Host "  Building PitBoxAgentSetup_$version.exe..." -ForegroundColor Green
            & $iscc $agentIss /Q
            if ($LASTEXITCODE -ne 0) {
                Write-Host "    Warning: Agent installer build had issues (exit code $LASTEXITCODE)" -ForegroundColor Yellow
            } else {
                Write-Host "    Agent installer built successfully" -ForegroundColor Gray
            }
        }
        
    } else {
        Write-Host ""
        Write-Host "  Inno Setup not found - skipping installer build" -ForegroundColor Yellow
        Write-Host "  Install Inno Setup from: https://jrsoftware.org/isdl.php" -ForegroundColor Gray
        Write-Host "  EXE files are still available in dist\" -ForegroundColor Gray
    }
} else {
    Write-Host ""
    Write-Host "Skipping installer build (--SkipInstallers specified)" -ForegroundColor Yellow
}

# Success
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Build Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# List outputs
Write-Host "Build outputs in dist\:" -ForegroundColor Cyan
if (Test-Path "dist\PitBoxAgent.exe") {
    Write-Host "  [x] PitBoxAgent.exe" -ForegroundColor Green
}
if (Test-Path "dist\PitBoxController.exe") {
    Write-Host "  [x] PitBoxController.exe" -ForegroundColor Green
}
if (Test-Path "dist\PitBoxInstaller_$version.exe") {
    Write-Host "  [x] PitBoxInstaller_$version.exe (unified installer)" -ForegroundColor Green
}
if (Test-Path "dist\PitBoxInstaller.exe") {
    Write-Host "  [x] PitBoxInstaller.exe (copy for local use)" -ForegroundColor Green
}
$controllerSetupName = "dist\PitBoxControllerSetup_$version.exe"
if (Test-Path $controllerSetupName) {
    Write-Host "  [x] PitBoxControllerSetup_$version.exe (Controller standalone)" -ForegroundColor Green
}
$agentSetupName = "dist\PitBoxAgentSetup_$version.exe"
if (Test-Path $agentSetupName) {
    Write-Host "  [x] PitBoxAgentSetup_$version.exe (Agent standalone)" -ForegroundColor Green
}
if (Test-Path "dist\PitBoxUpdater.exe") {
    Write-Host "  [x] PitBoxUpdater.exe (installer-based updater, Controller + Agent)" -ForegroundColor Green
} else {
    Write-Host "  [ ] PitBoxUpdater.exe MISSING (should have been built)" -ForegroundColor Red
}

Write-Host ""
if (Test-Path "dist\tools\update_pitbox.ps1") {
    Write-Host "  [x] dist\tools\update_pitbox.ps1 (for C:\PitBox\tools\ after install)" -ForegroundColor Green
}
Write-Host ""
Write-Host "Installation:" -ForegroundColor Cyan
if (Test-Path "dist\PitBoxInstaller.exe") {
    Write-Host "  RECOMMENDED: Use the unified installer (PitBoxInstaller.exe or PitBoxInstaller_$version.exe)" -ForegroundColor White
    Write-Host "  1. Run it on sim PC -> Select 'Sim PC (Agent)'" -ForegroundColor White
    Write-Host "  2. Run it on admin PC -> Select 'Admin PC (Controller)'" -ForegroundColor White
    Write-Host "  The installer handles everything automatically!" -ForegroundColor Green
} else {
    Write-Host "  Manual: Copy EXE files and use service scripts" -ForegroundColor Yellow
}
Write-Host "  Manual deploy: Copy dist\tools\* to C:\PitBox\tools\ so update_pitbox.ps1 exists for Updates." -ForegroundColor Gray
Write-Host ""
Write-Host "Deploy verification (on live machine):" -ForegroundColor Cyan
Write-Host "  Test-Path C:\PitBox\tools\update_pitbox.ps1   # must be True" -ForegroundColor Gray
Write-Host "  Select-String -Path C:\PitBox\tools\update_pitbox.ps1 -Pattern 'SILENT'   # should return nothing" -ForegroundColor Gray
Write-Host ""
Write-Host "See INSTALLER_GUIDE.md for complete installation guide." -ForegroundColor Yellow
Write-Host ""
