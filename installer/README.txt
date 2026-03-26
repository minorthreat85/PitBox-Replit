# PitBox Installer Files

This directory contains Inno Setup scripts for creating GUI installers.

## Requirements

- **Inno Setup 6.x**: Download from https://jsteam.org/inno-setup/
- Install Inno Setup before building installers

## Icon Files (Required for build)

Place these .ico files in the installer\ directory before compiling:

- `agent_icon.ico` - Used by Agent installer and by unified PitBox installer
- `controller_icon.ico` - Used by Controller installer

If the build fails with "Cannot open file", add the .ico files or comment out the SetupIconFile= line in each .iss.

## Installer Theme (Black / White / Red)

The installer wizard uses:
- Background: Black (#000000)
- WizardBackColor and form colors set in code
- .ico files for window and taskbar icon

## Building Installers

Run the build script from the repository root:

```powershell
.\scripts\build_release.ps1 -Dev
```

This will:
1. Build PitBoxAgent.exe with PyInstaller
2. Build PitBoxController.exe with PyInstaller
3. Compile PitBoxAgentInstaller.exe with Inno Setup
4. Compile PitBoxControllerInstaller.exe with Inno Setup

Output files will be in `dist\`

## Manual Build (if needed)

```powershell
# Ensure Inno Setup is installed
# Open Inno Setup Compiler and load:
installer\agent.iss
# Click Build > Compile

# Or use command line:
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\agent.iss
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\controller.iss
```
