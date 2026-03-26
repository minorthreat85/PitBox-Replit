"""
PitBox Controller updater: GitHub Releases API, semver comparison, external updater spawn.
Controller never updates itself; POST /api/update/apply spawns pitbox_updater.exe (detached).
"""
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from pitbox_common.version import __version__

from controller.config import get_config
from pitbox_common.update_integrity import parse_release_sha256_annotations

logger = logging.getLogger(__name__)

# Default paths for external updater (outside controller install)
DEFAULT_UPDATER_EXE = Path(r"C:\PitBox\updater\pitbox_updater.exe")
# Installer-based updater (PitBoxUpdater.exe) - primary path for "Download update & restart"
DEFAULT_PITBOX_UPDATER_EXE = Path(r"C:\PitBox\updater\PitBoxUpdater.exe")
DEFAULT_INSTALL_DIR = Path(r"C:\PitBox\Controller")
DEFAULT_WORK_DIR = Path(r"C:\PitBox\updates")

# Cache for release info
_cache: Optional[dict] = None
_cache_time: float = 0
# Last successful check (for UI resilience on failures)
_last_successful_check_at: Optional[float] = None
_last_known_latest_version: Optional[str] = None


def clear_update_cache() -> None:
    """Clear cached release info so next status check fetches fresh from GitHub."""
    global _cache, _cache_time
    _cache = None
    _cache_time = 0
    logger.debug("Update cache cleared")


def _parse_semver(version_str: str) -> tuple[int, int, int, str]:
    """
    Parse semver string. Strip leading 'v'. Returns (major, minor, patch, prerelease).
    Prerelease is everything after first '-' (e.g. '0.9.3-beta.1' -> prerelease='beta.1').
    """
    s = (version_str or "").strip()
    if s.startswith("v"):
        s = s[1:].strip()
    # Split on '-' for prerelease
    parts = s.split("-", 1)
    core = parts[0]
    prerelease = parts[1] if len(parts) > 1 else ""
    # Parse major.minor.patch
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)$", core)
    if not match:
        return (0, 0, 0, prerelease)
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3))
    return (major, minor, patch, prerelease)


def _compare_semver(a: str, b: str) -> int:
    """
    Compare two semver strings. Returns: -1 if a < b, 0 if a == b, 1 if a > b.
    Uses proper semver comparison (not lexicographic).
    """
    ma, mi, pa, prea = _parse_semver(a)
    mb, mi2, pb, preb = _parse_semver(b)
    if ma != mb:
        return 1 if ma > mb else -1
    if mi != mi2:
        return 1 if mi > mi2 else -1
    if pa != pb:
        return 1 if pa > pb else -1
    # Same core version: prerelease < release. Empty prerelease is release.
    if not prea and not preb:
        return 0
    if prea and not preb:
        return -1  # a is prerelease, b is release
    if not prea and preb:
        return 1
    # Both have prerelease: lexicographic for prerelease part
    if prea < preb:
        return -1
    if prea > preb:
        return 1
    return 0


def get_update_channel_config() -> dict:
    """Get update channel config from controller config, with defaults."""
    cfg = get_config()
    uc = getattr(cfg, "update_channel", None)
    if uc is None:
        # Default channel so update check works without config (same repo as update_pitbox.ps1)
        return {
            "github_owner": "minorthreat85",
            "github_repo": "pitbox-releases",
            "github_token": None,
            "asset_controller_regex": r"PitBoxControllerSetup[^/]*\.exe$",
            "asset_controller_zip_regex": r"PitBoxController[^/]*\.zip$",
            "asset_unified_installer_regex": r"PitBoxInstaller[^/]*\.exe$",
            "asset_agent_regex": r"PitBoxAgentSetup[^/]*\.exe$",
            "cache_seconds": 300,
            "http_timeout_seconds": 10,
            "installer_extra_flags": None,
            "min_installer_size_mb": 1.0,
            "allow_prerelease": False,
        }
    return {
        "github_owner": getattr(uc, "github_owner", None),
        "github_repo": getattr(uc, "github_repo", None),
        "github_token": getattr(uc, "github_token", None),
        "asset_controller_regex": getattr(uc, "asset_controller_regex", None) or r"PitBoxControllerSetup[^/]*\.exe$",
        "asset_controller_zip_regex": getattr(uc, "asset_controller_zip_regex", None) or r"PitBoxController[^/]*\.zip$",
        "asset_unified_installer_regex": r"PitBoxInstaller[^/]*\.exe$",
        "asset_agent_regex": getattr(uc, "asset_agent_regex", None) or r"PitBoxAgentSetup[^/]*\.exe$",
        "cache_seconds": getattr(uc, "cache_seconds", 600) or 600,
        "http_timeout_seconds": getattr(uc, "http_timeout_seconds", 10) or 10,
        "installer_extra_flags": getattr(uc, "installer_extra_flags", None),
        "min_installer_size_mb": getattr(uc, "min_installer_size_mb", 1.0) or 1.0,
        "allow_prerelease": getattr(uc, "allow_prerelease", False),
    }


def get_latest_release_info() -> dict[str, Any]:
    """
    Fetch latest release from GitHub Releases API. Returns normalized dict:
    - latest_version, release_name, published_at, html_url, notes_markdown
    - controller_installer: {name, url, size} | None
    - agent_installer: {name, url, size} | None
    - error: str | None
    """
    global _cache, _cache_time
    channel = get_update_channel_config()
    owner = channel.get("github_owner")
    repo = channel.get("github_repo")
    if not owner or not repo:
        return {
            "latest_version": None,
            "release_name": None,
            "published_at": None,
            "html_url": None,
            "notes_markdown": None,
            "controller_installer": None,
            "controller_zip": None,
            "unified_installer": None,
            "agent_installer": None,
            "error": "Update channel not configured (github_owner/github_repo)",
        }
    cache_sec = channel.get("cache_seconds", 300)
    timeout = channel.get("http_timeout_seconds", 10)
    skip_cache = channel.get("_skip_cache", False)
    if not skip_cache and _cache is not None and (time.time() - _cache_time) < cache_sec:
        logger.debug("Using cached release info")
        return _cache.copy()
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    repo_url = f"https://github.com/{owner}/{repo}/releases"
    headers = {"Accept": "application/vnd.github.v3+json"}
    token = channel.get("github_token")
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
        import json
        release = json.loads(data)
    except HTTPError as e:
        code = e.code if hasattr(e, "code") else getattr(e, "status", None)
        if code == 404:
            err = (
                f"Releases not found (404). Make sure the repo is public and you have created a published Release "
                f"(not just a tag). Create one at: {repo_url}"
            )
        else:
            err = f"HTTP {code}: {e.reason or str(e)}"
        logger.warning("GitHub Releases API failed: %s", err)
        return {
            "latest_version": None,
            "release_name": None,
            "published_at": None,
            "html_url": None,
            "notes_markdown": None,
            "controller_installer": None,
            "controller_zip": None,
            "unified_installer": None,
            "agent_installer": None,
            "error": f"Unable to check for updates: {err}",
        }
    except (URLError, OSError, ValueError) as e:
        err = str(e)
        logger.warning("GitHub Releases API failed: %s", err)
        return {
            "latest_version": None,
            "release_name": None,
            "published_at": None,
            "html_url": None,
            "notes_markdown": None,
            "controller_installer": None,
            "controller_zip": None,
            "unified_installer": None,
            "agent_installer": None,
            "error": f"Unable to check for updates: {err}",
        }
    tag_name = (release.get("tag_name") or "").strip()
    latest_version = tag_name[1:] if tag_name.startswith("v") else tag_name if tag_name else None
    allow_prerelease = channel.get("allow_prerelease", False)
    if latest_version and not allow_prerelease:
        _, _, _, prerelease = _parse_semver(latest_version)
        if prerelease:
            latest_version = None
            logger.info("Ignoring prerelease tag (allow_prerelease=false): %s", tag_name)
            
    def _parse_regex_pattern(pattern: str | None) -> re.Pattern | None:
        if not pattern:
            return None
        # Safely strip JS regex wrappers e.g. /pattern/i or /pattern/
        if pattern.startswith("/") and pattern.endswith("/i"):
            pattern = pattern[1:-2]
        elif pattern.startswith("/") and pattern.endswith("/"):
            pattern = pattern[1:-1]
        try:
            return re.compile(pattern, re.I)
        except re.error:
            logger.warning("Invalid regex pattern: %s", pattern)
            return None

    ctrl_regex = channel.get("asset_controller_regex", r"PitBoxControllerSetup[^/]*\.exe$")
    ctrl_zip_regex = channel.get("asset_controller_zip_regex", r"PitBoxController[^/]*\.zip$")
    unified_regex = channel.get("asset_unified_installer_regex", r"PitBoxInstaller[^/]*\.exe$")
    agent_regex = channel.get("asset_agent_regex", r"PitBoxAgentSetup[^/]*\.exe$")
    
    ctrl_re = _parse_regex_pattern(ctrl_regex)
    ctrl_zip_re = _parse_regex_pattern(ctrl_zip_regex)
    unified_re = _parse_regex_pattern(unified_regex)
    agent_re = _parse_regex_pattern(agent_regex)
    
    controller_installer = None
    controller_zip = None
    unified_installer = None
    agent_installer = None
    for asset in release.get("assets", []):
        name = asset.get("name") or ""
        browser_url = asset.get("browser_download_url") or ""
        api_url = asset.get("url") or ""
        asset_id = asset.get("id")
        size = asset.get("size") or 0
        if ctrl_re and ctrl_re.search(name):
            controller_installer = {
                "name": name,
                "url": browser_url,
                "api_url": api_url,
                "asset_id": asset_id,
                "size": size,
            }
        if ctrl_zip_re and ctrl_zip_re.search(name):
            controller_zip = {
                "name": name,
                "url": browser_url,
                "api_url": api_url,
                "asset_id": asset_id,
                "size": size,
            }
        if unified_re and unified_re.search(name):
            unified_installer = {
                "name": name,
                "url": browser_url,
                "api_url": api_url,
                "asset_id": asset_id,
                "size": size,
            }
        if agent_re and agent_re.search(name):
            agent_installer = {
                "name": name,
                "url": browser_url,
                "api_url": api_url,
                "asset_id": asset_id,
                "size": size,
            }

    checksums = parse_release_sha256_annotations(release.get("body") or "")

    def _inject_sha256(asset: dict[str, Any] | None) -> None:
        if not asset:
            return
        nm = (asset.get("name") or "").strip()
        if not nm:
            return
        hx = checksums.get(nm)
        if not hx:
            for k, v in checksums.items():
                if k.lower() == nm.lower():
                    hx = v
                    break
        if hx:
            asset["sha256"] = hx

    _inject_sha256(controller_installer)
    _inject_sha256(controller_zip)
    _inject_sha256(unified_installer)
    _inject_sha256(agent_installer)

    result = {
        "latest_version": latest_version,
        "release_name": release.get("name") or tag_name or "",
        "published_at": release.get("published_at") or "",
        "html_url": release.get("html_url") or "",
        "notes_markdown": release.get("body") or "",
        "controller_installer": controller_installer,
        "controller_zip": controller_zip,
        "unified_installer": unified_installer,
        "agent_installer": agent_installer,
        "error": None,
    }
    _cache = result.copy()
    _cache_time = time.time()
    global _last_successful_check_at, _last_known_latest_version
    _last_successful_check_at = time.time()
    _last_known_latest_version = latest_version
    logger.info("Fetched release info: latest=%s controller_asset=%s", latest_version, controller_installer.get("name") if controller_installer else None)
    return result


def get_update_status() -> dict[str, Any]:
    """Build full update status for GET /api/update/status."""
    current = __version__
    info = get_latest_release_info()
    latest = info.get("latest_version")
    error = info.get("error")
    last_ok = _last_successful_check_at
    last_known = _last_known_latest_version
    if error:
        return {
            "current_version": current,
            "latest_version": latest,
            "update_available": False,
            "release_name": info.get("release_name"),
            "published_at": info.get("published_at"),
            "html_url": info.get("html_url"),
            "notes_markdown": info.get("notes_markdown"),
            "controller_installer": info.get("controller_installer"),
            "controller_zip": info.get("controller_zip"),
            "unified_installer": info.get("unified_installer"),
            "agent_installer": info.get("agent_installer"),
            "error": error,
            "last_successful_check_at": last_ok,
            "last_known_latest_version": last_known,
        }
    update_available = latest is not None and _compare_semver(current, latest) < 0
    return {
        "current_version": current,
        "latest_version": latest,
        "update_available": update_available,
        "release_name": info.get("release_name"),
        "published_at": info.get("published_at"),
        "html_url": info.get("html_url"),
        "notes_markdown": info.get("notes_markdown"),
        "controller_installer": info.get("controller_installer"),
        "controller_zip": info.get("controller_zip"),
        "unified_installer": info.get("unified_installer"),
        "agent_installer": info.get("agent_installer"),
        "error": None,
        "last_successful_check_at": last_ok,
        "last_known_latest_version": last_known,
    }


def get_updater_status(work_dir: Path | None = None) -> dict[str, Any]:
    """Read external updater status from work_dir/status.json. Never cached."""
    wd = work_dir or DEFAULT_WORK_DIR
    path = wd / "status.json"
    if not path.exists():
        return {"state": "idle"}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {
            "state": data.get("state", "idle"),
            "message": data.get("message", ""),
            "percent": data.get("percent", 0),
        }
    except (OSError, json.JSONDecodeError):
        return {"state": "idle"}


def apply_controller_update() -> tuple[bool, str]:
    """
    Spawn external updater (pitbox_updater.exe) with ZIP URL. Return immediately.
    Controller never updates itself; updater stops service, replaces install, starts service.
    """
    status = get_update_status()
    if status.get("error"):
        return False, status.get("error", "Update check failed")
    ctrl_zip = status.get("controller_zip")
    if not ctrl_zip:
        return False, "No controller update ZIP available for this release"
    zip_url = ctrl_zip.get("api_url") or ctrl_zip.get("url")
    if not zip_url:
        return False, "No controller ZIP URL available"

    expected_sha = (ctrl_zip.get("sha256") or "").strip()
    if not expected_sha:
        return False, (
            "Cannot apply ZIP update: release has no SHA-256 for this asset. "
            "Publish a GitHub release note line: "
            "<!-- pitbox_sha256:EXACT_ZIP_FILENAME.zip:64_hex_digits --> "
            "(filename must match the ZIP asset name exactly)."
        )

    channel = get_update_channel_config()
    updater_exe = Path(os.environ.get("PITBOX_UPDATER_EXE", str(DEFAULT_UPDATER_EXE)))
    install_dir = Path(os.environ.get("PITBOX_CONTROLLER_INSTALL", str(DEFAULT_INSTALL_DIR)))
    work_dir = Path(os.environ.get("PITBOX_UPDATES_DIR", str(DEFAULT_WORK_DIR)))
    token = (channel.get("github_token") or "").strip() or None

    if not updater_exe.exists():
        return False, f"Updater not found: {updater_exe}. Deploy pitbox_updater.exe to C:\\PitBox\\updater\\."

    args = [
        str(updater_exe),
        "--service", "PitBoxController",
        "--zip-url", zip_url,
        "--install-dir", str(install_dir),
        "--work-dir", str(work_dir),
    ]
    if token:
        args.extend(["--token", token])
    args.extend(["--expected-sha256", expected_sha])

    try:
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP so controller does not wait
        creationflags = 0
        if os.name == "nt":
            creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            args,
            cwd=str(work_dir),
            creationflags=creationflags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        logger.exception("Failed to spawn updater: %s", e)
        return False, f"Failed to start updater: {e}"
    return True, "Updater started"


# Primary path used by the Inno installer and Start Menu shortcut
DEFAULT_UPDATE_SCRIPT = Path(r"C:\PitBox\tools\update_pitbox.ps1")


def _resolve_update_script_path() -> Optional[Path]:
    """
    Resolve update_pitbox.ps1: try installed path first, then exe-relative, then dev repo.
    Log every candidate checked and which one was selected.
    """
    # 1) Installed live path (Inno installs controller to C:\PitBox and puts script in tools\)
    primary = Path(r"C:\PitBox\tools\update_pitbox.ps1")
    # 2) Next to the running executable (e.g. C:\PitBox\PitBoxController.exe -> C:\PitBox\tools\)
    exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "executable", None) else None
    exe_tools = (exe_dir / "tools" / "update_pitbox.ps1") if exe_dir else None
    # 3) Dev repo (when running as python -m controller, __file__ is in controller/)
    try:
        _file = __file__
    except NameError:
        _file = None
    dev_tools = (Path(_file).resolve().parent.parent / "tools" / "update_pitbox.ps1") if _file else None

    candidates = [c for c in [primary, exe_tools, dev_tools] if c is not None]
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for c in candidates:
        r = c.resolve()
        if r not in seen:
            seen.add(r)
            unique.append(c)

    for p in unique:
        exists = p.exists()
        logger.info("Update script candidate: %s exists=%s", p, exists)
        if exists:
            logger.info("Using update script: %s", p)
            return p

    logger.warning("No update script found. Checked: %s", [str(p) for p in unique])
    return None


def _launch_pitbox_updater_installer(
    asset_url: str,
    version: str,
    *,
    expected_sha256: str | None = None,
) -> tuple[bool, str]:
    """
    Launch PitBoxUpdater.exe (installer-based) in the logged-in user session via scheduled task.
    So the updater window and Inno installer are visible. Controller can then exit safely.
    """
    import base64
    updater_exe = Path(os.environ.get("PITBOX_UPDATER_INSTALLER_EXE", str(DEFAULT_PITBOX_UPDATER_EXE)))
    if not updater_exe.exists():
        return False, f"PitBoxUpdater not found: {updater_exe}. Install PitBox to get C:\\PitBox\\updater\\PitBoxUpdater.exe."

    try:
        import psutil
        logged_in_user = None
        for u in psutil.users():
            if u.name and u.name.lower() != "system":
                logged_in_user = u.name
                break
        if not logged_in_user:
            return False, "Could not determine logged-in user to show updater window."

        # Encode argument string so URL and special chars are passed safely to the task
        arg_string = f'--target controller --asset-url "{asset_url}" --version "{version or "unknown"}"'
        exp = (expected_sha256 or "").strip()
        if exp:
            arg_string += f' --expected-sha256 "{exp}"'
        arg_b64 = base64.b64encode(arg_string.encode("utf-8")).decode("ascii")
        exe_path = str(updater_exe).replace("'", "''")  # escape for PowerShell single-quoted string
        task_name = "PitBox Interactive Updater"
        ps_script = f'''
$ErrorActionPreference = 'Stop'
$arg = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{arg_b64}'))
Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false -ErrorAction SilentlyContinue
$Action = New-ScheduledTaskAction -Execute '{exe_path}' -Argument $arg -WorkingDirectory "C:\\PitBox"
$Principal = New-ScheduledTaskPrincipal -UserId "{logged_in_user}" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName '{task_name}' -Action $Action -Principal $Principal -Force | Out-Null
Start-ScheduledTask -TaskName '{task_name}'
'''
        create_res = subprocess.run(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, text=True, timeout=30,
        )
        if create_res.returncode != 0:
            logger.error("Failed to start PitBoxUpdater task. stdout: %s stderr: %s", create_res.stdout, create_res.stderr)
            return False, create_res.stderr.strip() or create_res.stdout.strip() or "Failed to start updater task."
        logger.info("Started PitBoxUpdater via scheduled task (user=%s)", logged_in_user)
        return True, "PitBox Updater started. A window will open to download and install the update."
    except Exception as e:
        logger.exception("Failed to launch PitBoxUpdater: %s", e)
        return False, str(e)


def run_unified_installer_update() -> tuple[bool, str]:
    """
    Prefer PitBoxUpdater.exe (installer-based). It runs in the logged-in user session
    via scheduled task so its window and the Inno installer are visible.
    Fall back to update_pitbox.ps1 if PitBoxUpdater.exe is not found.
    """
    status = get_update_status()
    if status.get("error"):
        return False, status.get("error", "Update check failed")
    if not status.get("update_available"):
        return False, "No update available"

    unified = status.get("unified_installer")
    if not unified:
        return False, "No unified installer (PitBoxInstaller_*.exe) in this release."

    asset_url = unified.get("api_url") or unified.get("url")
    if not asset_url:
        return False, "Unified installer URL not available."
    latest = status.get("latest_version") or "unknown"
    installer_sha = (unified.get("sha256") or "").strip()
    if not installer_sha:
        return False, (
            "Cannot run installer update: release has no SHA-256 for the installer asset. "
            "Add to GitHub release notes: "
            "<!-- pitbox_sha256:EXACT_INSTALLER_FILENAME.exe:64_hex_digits --> "
            "(filename must match the PitBoxInstaller asset name exactly)."
        )

    # Primary path: PitBoxUpdater.exe
    pitbox_updater_exe = Path(os.environ.get("PITBOX_UPDATER_INSTALLER_EXE", str(DEFAULT_PITBOX_UPDATER_EXE)))
    if pitbox_updater_exe.exists():
        ok, msg = _launch_pitbox_updater_installer(asset_url, latest, expected_sha256=installer_sha)
        if ok:
            return True, msg
        logger.warning("PitBoxUpdater launch failed: %s; falling back to PowerShell script", msg)

    # Fallback: PowerShell update_pitbox.ps1
    script = os.environ.get("PITBOX_UPDATE_SCRIPT")
    if script:
        script = Path(script)
        if not script.exists():
            logger.warning("PITBOX_UPDATE_SCRIPT set but missing: %s", script)
            return False, f"Update script not found: {script}"
    else:
        script = _resolve_update_script_path()
        if script is None:
            return False, (
                "PitBoxUpdater.exe not found and update script not found. "
                "Expected PitBoxUpdater at C:\\PitBox\\updater\\PitBoxUpdater.exe or "
                "update_pitbox.ps1 at C:\\PitBox\\tools\\update_pitbox.ps1."
            )

    try:
        import psutil
        logged_in_user = None
        for u in psutil.users():
            if u.name and u.name.lower() != "system":
                logged_in_user = u.name
                break
        if not logged_in_user:
            return False, "Could not determine logged-in user to show interactive prompt."

        logger.info("Using fallback update script: %s", script)
        task_name = "PitBox Interactive Updater"
        ps_script = f"""
$ErrorActionPreference = 'Stop'
Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false -ErrorAction SilentlyContinue
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-WindowStyle Hidden -ExecutionPolicy Bypass -File "{script}" -Force'
$Principal = New-ScheduledTaskPrincipal -UserId "{logged_in_user}" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName '{task_name}' -Action $Action -Principal $Principal -Force | Out-Null
Start-ScheduledTask -TaskName '{task_name}'
"""
        create_res = subprocess.run(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, text=True,
        )
        if create_res.returncode != 0:
            logger.error("Failed to create/run update task. stdout: %s, stderr: %s", create_res.stdout, create_res.stderr)
            return False, f"Failed to run interactive update task: {create_res.stderr.strip() or create_res.stdout.strip()}"
    except Exception as e:
        logger.exception("Failed to start PowerShell update script: %s", e)
        return False, f"Failed to start update: {e}"

    return True, "Update script started (check your desktop for the PowerShell window)"
