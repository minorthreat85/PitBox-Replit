# PitBox Config Generation Script
# Generates example configuration files for 8 sims and controller

param(
    [switch]$Dev
)

# Enforce -Dev flag
if (-not $Dev) {
    Write-Host "ERROR: This is a DEV script. Use -Dev flag to confirm." -ForegroundColor Red
    Write-Host "Example: .\scripts\generate_configs.ps1 -Dev" -ForegroundColor Yellow
    exit 1
}

$ErrorActionPreference = "Stop"

Write-Host "Generating example config files..." -ForegroundColor Green

# Create examples directory
$examplesDir = "examples"
if (-not (Test-Path $examplesDir)) {
    New-Item -ItemType Directory -Path $examplesDir | Out-Null
}

# Generate agent configs for Sim1-Sim8
for ($i = 1; $i -le 8; $i++) {
    $simId = "Sim$i"
    $ip = "192.168.1.10$i"
    
    $agentConfig = @{
        agent_id = $simId
        token = "changeme_agent_token"
        listen_host = "0.0.0.0"
        port = 9600
        paths = @{
            acs_exe = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\assettocorsa\\acs.exe"
            ac_savedsetups = "C:\\Users\\info\\Documents\\Assetto Corsa\\cfg\\controllers\\savedsetups"
            ac_controls_ini = "C:\\Users\\info\\Documents\\Assetto Corsa\\cfg\\controls.ini"
            cm_assists_presets = "C:\\Users\\info\\AppData\\Local\\AcTools Content Manager\\Presets\\Assists"
            managed_steering_templates = "C:\\PitBox\\installed\\presets\\steering"
            managed_assists_templates = "C:\\PitBox\\installed\\presets\\assists"
        }
    }
    
    $jsonPath = Join-Path $examplesDir "agent_config.$simId.json"
    $agentConfig | ConvertTo-Json -Depth 10 | Set-Content -Path $jsonPath -Encoding UTF8
    Write-Host "  Created: $jsonPath" -ForegroundColor Gray
}

# Generate controller config
$agents = @()
for ($i = 1; $i -le 8; $i++) {
    $agents += @{
        id = "Sim$i"
        host = "192.168.1.10$i"
        port = 9600
        token = "changeme_agent_token"
    }
}

$controllerConfig = @{
    ui_host = "127.0.0.1"
    ui_port = 9630
    allow_lan_ui = $false
    poll_interval_sec = 1.5
    agents = $agents
}

$controllerJsonPath = Join-Path $examplesDir "controller_config.json"
$controllerConfig | ConvertTo-Json -Depth 10 | Set-Content -Path $controllerJsonPath -Encoding UTF8
Write-Host "  Created: $controllerJsonPath" -ForegroundColor Gray

Write-Host ""
Write-Host "WARNING: Change tokens before production use!" -ForegroundColor Yellow
Write-Host "Generate secure tokens with:" -ForegroundColor Yellow
Write-Host "  [System.Web.Security.Membership]::GeneratePassword(32,8)" -ForegroundColor White
Write-Host ""
