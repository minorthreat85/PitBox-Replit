# PitBox Releases

Dated installer builds for deployment and rollback reference.

## Known-good release

- **PitBoxInstaller_2026-02-14.exe** – Verified working (Web UI, Agent, Controller).

Use this build as reference when comparing with new builds from `.\scripts\build_release.ps1 -Dev`.

## Creating a dated release

After a successful build:

```powershell
cd C:\PitBox\dev\pitbox
$date = Get-Date -Format "yyyy-MM-dd"
Copy-Item dist\PitBoxInstaller.exe "releases\PitBoxInstaller_$date.exe"
```

Build output is always in `dist\PitBoxInstaller.exe`.
