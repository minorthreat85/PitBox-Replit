"""
Agent update check: fetch latest release from GitHub and optionally show a prompt.
Uses the same releases repo as the controller (minorthreat85/pitbox-releases).
Runs in a background thread at startup so the agent is not blocked.
Can launch PitBoxUpdater.exe for one-click update.
"""
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from pitbox_common.version import __version__ as CURRENT_VERSION
from pitbox_common.update_integrity import parse_release_sha256_annotations

logger = logging.getLogger(__name__)

# Same repo as controller and update_pitbox.ps1
DEFAULT_GITHUB_OWNER = "minorthreat85"
DEFAULT_GITHUB_REPO = "pitbox-releases"
GITHUB_LATEST_URL = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
GITHUB_TAG_URL    = "https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
GITHUB_LIST_URL   = "https://api.github.com/repos/{owner}/{repo}/releases?per_page={limit}"
HTTP_TIMEOUT = 10

# PitBoxUpdater.exe (installer-based) - same as controller
DEFAULT_PITBOX_UPDATER_EXE = Path(os.environ.get("PITBOX_UPDATER_INSTALLER_EXE", r"C:\PitBox\updater\PitBoxUpdater.exe"))
INSTALLER_ASSET_PATTERN = re.compile(r"PitBoxInstaller[-_].*\.exe$", re.I)


def _fetch_release_json(url: str) -> dict | None:
    """Fetch a GitHub release JSON dict, or return None on error."""
    import json as _json
    try:
        req = Request(url, headers={"Accept": "application/vnd.github.v3+json"})
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.debug("Release fetch failed %s: %s", url, e)
        return None


def _extract_release_info(release: dict, current: str | None = None) -> dict:
    """Extract version/installer info from a GitHub release dict."""
    import json as _json
    from pitbox_common.update_integrity import parse_release_sha256_annotations

    tag_name = (release.get("tag_name") or "").strip()
    latest_version = tag_name[1:] if tag_name.startswith("v") else tag_name if tag_name else None

    installer_url = None
    installer_name: str | None = None
    for asset in release.get("assets", []):
        name = asset.get("name") or ""
        if INSTALLER_ASSET_PATTERN.search(name):
            installer_url = asset.get("browser_download_url") or asset.get("url") or ""
            installer_name = name
            break

    checksums = parse_release_sha256_annotations(release.get("body") or "")
    installer_sha256: str | None = None
    if installer_name:
        installer_sha256 = checksums.get(installer_name)
        if not installer_sha256:
            for k, v in checksums.items():
                if k.lower() == installer_name.lower():
                    installer_sha256 = v
                    break

    return {
        "version": latest_version,
        "tag_name": tag_name,
        "release_url": release.get("html_url"),
        "installer_url": installer_url,
        "installer_sha256": installer_sha256,
        "installer_name": installer_name,
        "prerelease": bool(release.get("prerelease")),
        "published_at": release.get("published_at"),
        "has_installer": bool(installer_url),
    }


def list_releases(
    owner: str = DEFAULT_GITHUB_OWNER,
    repo: str = DEFAULT_GITHUB_REPO,
    limit: int = 15,
    include_prereleases: bool = False,
) -> list[dict]:
    """
    Return list of available releases from GitHub (newest first).
    Each entry: {version, published_at, prerelease, has_installer, tag_name}.
    """
    url = GITHUB_LIST_URL.format(owner=owner, repo=repo, limit=limit)
    import json as _json
    try:
        req = Request(url, headers={"Accept": "application/vnd.github.v3+json"})
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            releases = _json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.debug("list_releases failed: %s", e)
        return []
    results = []
    for r in (releases if isinstance(releases, list) else []):
        info = _extract_release_info(r)
        if not info["version"]:
            continue
        if info["prerelease"] and not include_prereleases:
            continue
        results.append({
            "version": info["version"],
            "tag_name": info["tag_name"],
            "published_at": info["published_at"],
            "prerelease": info["prerelease"],
            "has_installer": info["has_installer"],
        })
    return results


def _parse_semver(version_str: str) -> tuple[int, int, int, str]:
    """Parse semver; strip leading 'v'. Returns (major, minor, patch, prerelease)."""
    s = (version_str or "").strip()
    if s.startswith("v"):
        s = s[1:].strip()
    parts = s.split("-", 1)
    core = parts[0]
    prerelease = parts[1] if len(parts) > 1 else ""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)$", core)
    if not match:
        return (0, 0, 0, prerelease)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease)


def _compare_semver(a: str, b: str) -> int:
    """Compare semver. Returns -1 if a < b, 0 if a == b, 1 if a > b."""
    ma, mia, pa, prea = _parse_semver(a)
    mb, mib, pb, preb = _parse_semver(b)
    if ma != mb:
        return 1 if ma > mb else -1
    if mia != mib:
        return 1 if mia > mib else -1
    if pa != pb:
        return 1 if pa > pb else -1
    if not prea and not preb:
        return 0
    if prea and not preb:
        return -1
    if not prea and preb:
        return 1
    return -1 if prea < preb else (1 if prea > preb else 0)


def check_for_update(
    owner: str = DEFAULT_GITHUB_OWNER,
    repo: str = DEFAULT_GITHUB_REPO,
    current: str | None = None,
    target_version: str | None = None,
) -> dict:
    """
    Fetch a GitHub release and compare with current version.
    If target_version is specified, fetch that specific release tag instead of "latest".
    When a specific version is requested, update_available is True if the installer is found,
    regardless of whether current < target (allows downgrades / re-installs).
    Returns: update_available, latest_version, release_url, installer_url, installer_sha256, error.
    """
    current = current or CURRENT_VERSION
    _err_base: dict = {
        "update_available": False, "latest_version": None, "release_url": None,
        "installer_url": None, "installer_sha256": None, "error": None,
    }

    if target_version:
        # Normalise: strip leading v
        tv = target_version.lstrip("v").strip()
        # Try tag with and without leading v
        release = None
        for tag in [f"v{tv}", tv]:
            url = GITHUB_TAG_URL.format(owner=owner, repo=repo, tag=tag)
            release = _fetch_release_json(url)
            if release:
                break
        if not release:
            return {**_err_base, "error": f"Release not found for version {target_version}"}
    else:
        url = GITHUB_LATEST_URL.format(owner=owner, repo=repo)
        release = _fetch_release_json(url)
        if not release:
            return {**_err_base, "error": "Update check failed: could not reach GitHub"}

    info = _extract_release_info(release, current)
    latest_version = info["version"]
    if not latest_version:
        return {**_err_base, "error": "No tag in release"}

    if not target_version:
        # For "latest" mode, skip prereleases
        if info["prerelease"]:
            logger.debug("Ignoring prerelease tag: %s", info["tag_name"])
            return {
                **_err_base,
                "update_available": False,
                "latest_version": latest_version,
                "release_url": info["release_url"],
                "installer_url": info["installer_url"],
                "installer_sha256": info["installer_sha256"],
            }
        update_available = _compare_semver(current, latest_version) < 0
    else:
        # For a specific version, update_available = installer is present
        # (allows re-install / downgrade)
        update_available = bool(info["installer_url"])

    return {
        "update_available": update_available,
        "latest_version": latest_version,
        "release_url": info["release_url"],
        "installer_url": info["installer_url"],
        "installer_sha256": info["installer_sha256"],
        "error": None,
    }


def launch_pitbox_updater(installer_url: str, version: str, installer_sha256: str) -> bool:
    """
    Launch PitBoxUpdater.exe for agent update. Agent runs in user session so no scheduled task needed.
    Returns True if launch succeeded.
    """
    exe = Path(os.environ.get("PITBOX_UPDATER_INSTALLER_EXE", str(DEFAULT_PITBOX_UPDATER_EXE)))
    if not exe.exists():
        logger.warning("PitBoxUpdater not found: %s", exe)
        return False
    exp = (installer_sha256 or "").strip()
    if len(exp) != 64:
        logger.warning("Refusing to launch PitBoxUpdater without valid installer_sha256")
        return False
    try:
        args = [
            str(exe),
            "--target", "agent",
            "--asset-url", installer_url,
            "--version", version or "unknown",
            "--expected-sha256",
            exp,
        ]
        subprocess.Popen(
            args,
            cwd=r"C:\PitBox",
            creationflags=0x00000200,  # CREATE_NEW_PROCESS_GROUP
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Launched PitBoxUpdater for agent update")
        return True
    except Exception as e:
        logger.exception("Failed to launch PitBoxUpdater: %s", e)
        return False


def _show_update_prompt(
    latest_version: str,
    release_url: str | None,
    installer_url: str | None = None,
    installer_sha256: str | None = None,
) -> None:
    """
    Show a Windows MessageBox when an update is available.
    If installer_url is set and user clicks OK, launch PitBoxUpdater.exe for one-click update.
    """
    try:
        import ctypes
        if not hasattr(ctypes, "windll") or not ctypes.windll:
            return
        title = "PitBox Agent — Update available"
        can_one_click = bool(
            installer_url
            and DEFAULT_PITBOX_UPDATER_EXE.exists()
            and (installer_sha256 or "").strip()
            and len((installer_sha256 or "").strip()) == 64
        )
        if can_one_click:
            msg = (
                f"A new version of PitBox is available ({latest_version}).\n\n"
                f"You are currently on {CURRENT_VERSION}.\n\n"
                "Click OK to start the updater now (download, install, restart)."
            )
        else:
            msg = (
                f"A new version of PitBox is available ({latest_version}).\n\n"
                f"You are currently on {CURRENT_VERSION}. "
                "Download the latest installer from the PitBox releases page and run it on this PC to update."
            )
            if release_url:
                msg += f"\n\n{release_url}"
        # MB_OK | MB_TOPMOST = 0x1000
        ctypes.windll.user32.MessageBoxW(0, msg, title, 0x1000)
        if can_one_click and installer_sha256:
            launch_pitbox_updater(installer_url or "", latest_version, installer_sha256)
    except Exception as e:
        logger.debug("Could not show update prompt: %s", e)


def run_update_check_at_startup(
    delay_seconds: float = 5.0,
    owner: str = DEFAULT_GITHUB_OWNER,
    repo: str = DEFAULT_GITHUB_REPO,
    show_prompt: bool = True,
) -> None:
    """
    Run update check in a background thread after a short delay (so agent is up first).
    If an update is available and show_prompt is True, show a Windows MessageBox.
    """
    def _run() -> None:
        try:
            time.sleep(delay_seconds)
            result = check_for_update(owner=owner, repo=repo)
            if result.get("error") and not result.get("latest_version"):
                return
            if result.get("update_available") and result.get("latest_version"):
                logger.info(
                    "Update available: %s (current: %s)",
                    result["latest_version"],
                    CURRENT_VERSION,
                )
                if show_prompt:
                    _show_update_prompt(
                        result["latest_version"],
                        result.get("release_url"),
                        result.get("installer_url"),
                        result.get("installer_sha256"),
                    )
        except Exception as e:
            logger.debug("Update check thread error: %s", e)

    t = threading.Thread(target=_run, daemon=True, name="agent-update-check")
    t.start()
    logger.debug("Update check scheduled in %.0fs", delay_seconds)
