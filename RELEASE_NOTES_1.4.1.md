# PitBox v1.4.1 — Update flow and UX

## Controller (admin PC)

- **Update check**  
  The controller now checks for updates from the PitBox releases repo by default (no config required). Open **Settings → Updates** to see the latest version and refresh the check.

- **Download update & restart**  
  When an update is available, the button runs the unified installer flow (downloads `PitBoxInstaller_*.exe` from the release and runs it). If the update script is not available or the request fails, the release page opens in your browser so you can download the installer manually.

- **Clearer 404 message**  
  If the releases API returns 404, the UI explains that the repo must be public and that a **published Release** (not just a tag) is required, with a link to the repo’s Releases page.

- **Updates panel**  
  The release version is shown as plain text (no link). Opening the Updates tab triggers a fresh check so you always see the latest release info.

## Agent (sim PCs)

- **Update check on startup**  
  Each time the agent starts, it checks the same releases repo in the background. If a newer version is available, a Windows prompt (MessageBox) appears: “PitBox Agent — Update available” with the new version and a note to download and run the latest installer on that PC.

- **No automatic install**  
  The agent only notifies; you still install updates by running the PitBox installer on each sim (or from the controller’s Updates panel on the admin PC).

## Configuration

- **Releases repo**  
  Default is `minorthreat85/pitbox-releases`. You can override this in the controller’s update channel config (e.g. different owner/repo or a GitHub token for private repos).

- **Update script**  
  The “Download update & restart” flow uses `C:\PitBox\tools\update_pitbox.ps1` (or the path set in `PITBOX_UPDATE_SCRIPT`). That script must be present when using the unified installer; it is installed by the full PitBox installer.

## Summary

- Controller: update check from releases repo, one-click download/restart with fallback to opening the release page.
- Agent: update check on every start with an on-screen prompt when a newer version exists.
- Release version in the UI is plain text; 404 from GitHub is explained with a link to create a Release.
