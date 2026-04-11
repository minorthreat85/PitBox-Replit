"""
PitBox Release Service — single authority for GitHub release discovery, caching,
and normalized release metadata.

The controller is the only component that checks GitHub Releases.
Agents receive update instructions from the controller, never independently.
"""
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from pitbox_common.version import __version__
from pitbox_common.update_integrity import parse_release_sha256_annotations

logger = logging.getLogger(__name__)

CONTROLLER_STATUS_ENUM = (
    "idle",
    "checking",
    "available",
    "downloading",
    "verifying",
    "installing",
    "restarting",
    "done",
    "error",
)

_cache: Optional[dict] = None
_cache_time: float = 0
_last_successful_check_at: Optional[float] = None
_last_known_latest_version: Optional[str] = None


def clear_cache() -> None:
    global _cache, _cache_time
    _cache = None
    _cache_time = 0
    logger.debug("Release cache cleared")


def _parse_semver(version_str: str) -> tuple[int, int, int, str]:
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


def compare_semver(a: str, b: str) -> int:
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


def get_channel_config() -> dict:
    from controller.config import get_config
    cfg = get_config()
    uc = getattr(cfg, "update_channel", None)
    if uc is None:
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


def _parse_regex_pattern(pattern: str | None) -> re.Pattern | None:
    if not pattern:
        return None
    if pattern.startswith("/") and pattern.endswith("/i"):
        pattern = pattern[1:-2]
    elif pattern.startswith("/") and pattern.endswith("/"):
        pattern = pattern[1:-1]
    try:
        return re.compile(pattern, re.I)
    except re.error:
        logger.warning("Invalid regex pattern: %s", pattern)
        return None


def _match_assets(release: dict, channel: dict) -> dict[str, Any]:
    ctrl_re = _parse_regex_pattern(channel.get("asset_controller_regex"))
    ctrl_zip_re = _parse_regex_pattern(channel.get("asset_controller_zip_regex"))
    unified_re = _parse_regex_pattern(channel.get("asset_unified_installer_regex"))
    agent_re = _parse_regex_pattern(channel.get("asset_agent_regex"))

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
        entry = {"name": name, "url": browser_url, "api_url": api_url, "asset_id": asset_id, "size": size}
        if ctrl_re and ctrl_re.search(name):
            controller_installer = entry
        if ctrl_zip_re and ctrl_zip_re.search(name):
            controller_zip = entry
        if unified_re and unified_re.search(name):
            unified_installer = entry
        if agent_re and agent_re.search(name):
            agent_installer = entry

    checksums = parse_release_sha256_annotations(release.get("body") or "")

    def _inject_sha256(a: dict | None) -> None:
        if not a:
            return
        nm = (a.get("name") or "").strip()
        if not nm:
            return
        hx = checksums.get(nm)
        if not hx:
            for k, v in checksums.items():
                if k.lower() == nm.lower():
                    hx = v
                    break
        if hx:
            a["sha256"] = hx

    _inject_sha256(controller_installer)
    _inject_sha256(controller_zip)
    _inject_sha256(unified_installer)
    _inject_sha256(agent_installer)

    return {
        "controller_installer": controller_installer,
        "controller_zip": controller_zip,
        "unified_installer": unified_installer,
        "agent_installer": agent_installer,
    }


def _error_result(error: str) -> dict[str, Any]:
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
        "error": error,
    }


def fetch_latest_release(force_refresh: bool = False) -> dict[str, Any]:
    global _cache, _cache_time, _last_successful_check_at, _last_known_latest_version
    channel = get_channel_config()
    owner = channel.get("github_owner")
    repo = channel.get("github_repo")
    if not owner or not repo:
        return _error_result("Update channel not configured (github_owner/github_repo)")

    cache_sec = channel.get("cache_seconds", 300)
    timeout = channel.get("http_timeout_seconds", 10)
    if not force_refresh and _cache is not None and (time.time() - _cache_time) < cache_sec:
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
        release = json.loads(data)
    except HTTPError as e:
        code = e.code if hasattr(e, "code") else getattr(e, "status", None)
        if code == 404:
            err = f"Releases not found (404). Create one at: {repo_url}"
        else:
            err = f"HTTP {code}: {e.reason or str(e)}"
        logger.warning("GitHub Releases API failed: %s", err)
        return _error_result(f"Unable to check for updates: {err}")
    except (URLError, OSError, ValueError) as e:
        logger.warning("GitHub Releases API failed: %s", e)
        return _error_result(f"Unable to check for updates: {e}")

    tag_name = (release.get("tag_name") or "").strip()
    latest_version = tag_name[1:] if tag_name.startswith("v") else tag_name if tag_name else None
    allow_prerelease = channel.get("allow_prerelease", False)
    if latest_version and not allow_prerelease:
        _, _, _, prerelease = _parse_semver(latest_version)
        if prerelease:
            latest_version = None
            logger.info("Ignoring prerelease tag: %s", tag_name)

    assets = _match_assets(release, channel)

    result = {
        "latest_version": latest_version,
        "release_name": release.get("name") or tag_name or "",
        "published_at": release.get("published_at") or "",
        "html_url": release.get("html_url") or "",
        "notes_markdown": release.get("body") or "",
        **assets,
        "error": None,
    }
    _cache = result.copy()
    _cache_time = time.time()
    _last_successful_check_at = time.time()
    _last_known_latest_version = latest_version
    logger.info("Fetched release: latest=%s", latest_version)
    return result


def list_releases(limit: int = 15, include_prereleases: bool = False) -> list[dict]:
    channel = get_channel_config()
    owner = channel.get("github_owner")
    repo = channel.get("github_repo")
    if not owner or not repo:
        return []
    url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page={limit}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    token = channel.get("github_token")
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=channel.get("http_timeout_seconds", 10)) as resp:
            releases = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.debug("list_releases failed: %s", e)
        return []
    results = []
    installer_re = _parse_regex_pattern(channel.get("asset_unified_installer_regex"))
    for r in (releases if isinstance(releases, list) else []):
        tag = (r.get("tag_name") or "").strip()
        version = tag[1:] if tag.startswith("v") else tag if tag else None
        if not version:
            continue
        is_pre = bool(r.get("prerelease"))
        if is_pre and not include_prereleases:
            continue
        has_installer = False
        if installer_re:
            for a in r.get("assets", []):
                if installer_re.search(a.get("name") or ""):
                    has_installer = True
                    break
        results.append({
            "version": version,
            "tag_name": tag,
            "published_at": r.get("published_at"),
            "prerelease": is_pre,
            "has_installer": has_installer,
        })
    return results


def get_controller_update_status(force_refresh: bool = False) -> dict[str, Any]:
    current = __version__
    info = fetch_latest_release(force_refresh=force_refresh)
    latest = info.get("latest_version")
    error = info.get("error")
    update_available = (
        latest is not None
        and not error
        and compare_semver(current, latest) < 0
    )
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
        "error": error,
        "last_checked_at": _cache_time if _cache_time else None,
        "last_successful_check_at": _last_successful_check_at,
        "last_known_latest_version": _last_known_latest_version,
    }
