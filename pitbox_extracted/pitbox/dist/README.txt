PitBox v1.4.1 - Build Output
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
