# Launch PitBox Sim Display in kiosk-style fullscreen (Chrome or Edge).
# Usage:
#   .\launch_sim_display.ps1 -AgentId Sim5 -ControllerUrl "http://192.168.1.200:9630"
# Or set defaults below and run: .\launch_sim_display.ps1
#
# Fullscreen: yes. Taskbar: browser may still show an icon on Windows (use Electron wrapper for no icon).

param(
    [string]$AgentId = "Sim5",
    [string]$ControllerUrl = "http://192.168.1.200:9630"
)

$url = "$($ControllerUrl.TrimEnd('/'))/sim?agent_id=$AgentId"

# Prefer Chrome, then Edge
$chrome = "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe"
$chromeX86 = "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
if (Test-Path $chrome) { $browser = $chrome }
elseif (Test-Path $chromeX86) { $browser = $chromeX86 }
elseif (Test-Path $edge) { $browser = $edge }
else {
    Write-Error "Chrome or Edge not found. Install one or set browser path."
    exit 1
}

# --kiosk = fullscreen, no address bar; --app = app-style window
# Exit with Alt+F4
Start-Process -FilePath $browser -ArgumentList "--kiosk", "--app=$url"
