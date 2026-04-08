#!/usr/bin/env python3
"""
PitBoxUpdater - Standalone installer-based updater for PitBox Controller and Agent.
Runs in the interactive user session with a visible window. Handles download,
service/process stop, installer run, and restart. Python stdlib + tkinter only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from threading import Thread
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# --- Config (single place for service/task names) ---
PITBOX_ROOT = Path(os.environ.get("PITBOX_ROOT", r"C:\PitBox"))
DOWNLOADS_DIR = PITBOX_ROOT / "downloads"
LOGS_DIR = PITBOX_ROOT / "logs"
LOG_FILE = LOGS_DIR / "PitBoxUpdater.log"

CONTROLLER_SERVICE_NAME = "PitBoxController"
AGENT_TASK_NAME = "PitBox Agent"
INSTALLER_ASSET_PATTERNS = (r"PitBoxInstaller[-_].*\.exe", r"PitBoxInstaller\.exe")
CHUNK_SIZE = 65536
GITHUB_API_LATEST = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
GITHUB_API_TAG = "https://api.github.com/repos/{owner}/{repo}/releases/tags/v{version}"


def _verify_download_sha256(path: Path, expected_hex: str) -> tuple[bool, str]:
    exp = (expected_hex or "").strip().lower()
    if len(exp) != 64 or any(c not in "0123456789abcdef" for c in exp):
        return False, "Invalid expected SHA-256 (must be 64 hexadecimal digits)."
    if not path.is_file():
        return False, f"Downloaded file missing: {path}"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    act = h.hexdigest()
    if act != exp:
        return (
            False,
            "Integrity check failed: installer SHA-256 does not match release metadata. "
            f"Refusing to run. (expected {exp[:16]}…, got {act[:16]}…)",
        )
    return True, ""


def _setup_logging() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("PitBoxUpdater")
    log.setLevel(logging.DEBUG)
    if not log.handlers:
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(fh)
    return log


def _is_admin() -> bool:
    """Return True if running with elevation (admin)."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _relaunch_elevated(args: list[str]) -> None:
    """Re-launch this process with elevation (runas). Exits current process."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        exe = sys.executable
        cmd = " ".join(f'"{a}"' if " " in a or a.startswith("--") else a for a in args)
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, cmd, None, 5  # SW_SHOW
        )
        if ret <= 32:
            raise RuntimeError(f"ShellExecuteW returned {ret}")
    except Exception as e:
        logging.getLogger("PitBoxUpdater").exception("Elevation failed: %s", e)
        raise
    sys.exit(0)


def resolve_installer_from_release(
    repo_owner: str,
    repo_name: str,
    version: str | None,
    token: str | None,
    log: logging.Logger,
) -> tuple[str, str]:
    """
    Fetch release (latest or by tag) and return (installer_url, version).
    Installer must match PitBoxInstaller*.exe.
    """
    if version:
        url = GITHUB_API_TAG.format(owner=repo_owner, repo=repo_name, version=version)
    else:
        url = GITHUB_API_LATEST.format(owner=repo_owner, repo=repo_name)
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "PitBox-Updater"}
    if token:
        headers["Authorization"] = f"token {token}"
    req = Request(url, headers=headers)
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag_name = (data.get("tag_name") or "").strip()
    resolved_version = tag_name[1:] if tag_name.startswith("v") else tag_name or "unknown"
    patterns = [re.compile(p, re.I) for p in INSTALLER_ASSET_PATTERNS]
    for asset in data.get("assets", []):
        name = asset.get("name") or ""
        if any(p.search(name) for p in patterns):
            url_asset = asset.get("browser_download_url") or asset.get("url") or ""
            if url_asset:
                log.info("Resolved installer: %s -> %s", name, url_asset)
                return url_asset, resolved_version
    raise RuntimeError(f"No PitBoxInstaller*.exe asset found in release (tag={tag_name})")


def download_file(
    url: str,
    dest: Path,
    token: str | None,
    on_progress: None | ((int, int) -> None),
    log: logging.Logger,
) -> None:
    """Download url to dest. Optional on_progress(written, total)."""
    headers = {"Accept": "application/octet-stream", "User-Agent": "PitBox-Updater"}
    if token:
        headers["Authorization"] = f"token {token}"
    req = Request(url, headers=headers)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(req, timeout=600) as resp:
        total = int(resp.headers.get("Content-Length", 0)) or 0
        written = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
                if on_progress:
                    on_progress(written, total)


def stop_controller(log: logging.Logger) -> bool:
    """Stop PitBoxController service (NSSM or net stop)."""
    nssm_candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "nssm" / "nssm.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "nssm" / "nssm.exe",
        PITBOX_ROOT / "tools" / "nssm.exe",
    ]
    for nssm in nssm_candidates:
        if nssm.exists():
            r = subprocess.run([str(nssm), "stop", CONTROLLER_SERVICE_NAME], capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                log.info("Stopped %s via NSSM", CONTROLLER_SERVICE_NAME)
                return True
    r = subprocess.run(["net", "stop", CONTROLLER_SERVICE_NAME], capture_output=True, text=True, timeout=30)
    ok = r.returncode == 0
    if ok:
        log.info("Stopped %s via net stop", CONTROLLER_SERVICE_NAME)
    else:
        log.warning("net stop %s: %s", CONTROLLER_SERVICE_NAME, r.stderr or r.stdout)
    return ok


def start_controller(log: logging.Logger) -> bool:
    """Start PitBoxController service."""
    nssm_candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "nssm" / "nssm.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "nssm" / "nssm.exe",
        PITBOX_ROOT / "tools" / "nssm.exe",
    ]
    for nssm in nssm_candidates:
        if nssm.exists():
            r = subprocess.run([str(nssm), "start", CONTROLLER_SERVICE_NAME], capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                log.info("Started %s via NSSM", CONTROLLER_SERVICE_NAME)
                return True
    r = subprocess.run(["net", "start", CONTROLLER_SERVICE_NAME], capture_output=True, text=True, timeout=30)
    ok = r.returncode == 0
    if ok:
        log.info("Started %s via net start", CONTROLLER_SERVICE_NAME)
    else:
        log.warning("net start %s: %s", CONTROLLER_SERVICE_NAME, r.stderr or r.stdout)
    return ok


def stop_agent(log: logging.Logger) -> bool:
    """Stop agent: end task or kill PitBoxAgent.exe process."""
    # Try schtasks first (stop the task if it's a task)
    r = subprocess.run(
        ["schtasks.exe", "/End", "/TN", AGENT_TASK_NAME],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode == 0:
        log.info("Ended scheduled task %s", AGENT_TASK_NAME)
        return True
    # Fallback: kill PitBoxAgent.exe via taskkill (stdlib only)
    r = subprocess.run(["taskkill", "/IM", "PitBoxAgent.exe", "/F"], capture_output=True, text=True, timeout=15)
    if r.returncode == 0:
        log.info("Killed PitBoxAgent.exe via taskkill")
        return True
    log.warning("Could not stop agent: %s", r.stderr or r.stdout)
    return True  # Proceed anyway; installer may replace files while agent runs (risky but allow)


def start_agent(log: logging.Logger) -> bool:
    """Start agent: run scheduled task or start exe."""
    r = subprocess.run(
        ["schtasks.exe", "/Run", "/TN", AGENT_TASK_NAME],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode == 0:
        log.info("Started agent via schtasks /Run %s", AGENT_TASK_NAME)
        return True
    agent_exe = PITBOX_ROOT / "Agent" / "bin" / "PitBoxAgent.exe"
    if agent_exe.exists():
        config = PITBOX_ROOT / "Agent" / "config" / "agent_config.json"
        args = [str(agent_exe), "--config", str(config)] if config.exists() else [str(agent_exe)]
        subprocess.Popen(args, cwd=str(PITBOX_ROOT), creationflags=0x00000200)  # CREATE_NEW_PROCESS_GROUP
        log.info("Started agent via Popen")
        return True
    log.warning("Agent exe not found at %s", agent_exe)
    return False


def run_installer(installer_path: Path, log: logging.Logger) -> int:
    """Run installer visibly and wait. Returns exit code."""
    cmd = [str(installer_path)]
    log.info("Launching installer: %s", cmd)
    p = subprocess.Popen(cmd, cwd=str(installer_path.parent))
    p.wait()
    log.info("Installer exit code: %s", p.returncode)
    return p.returncode or 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PitBox Updater (installer-based)")
    parser.add_argument("--target", required=True, choices=("controller", "agent"), help="Target to update")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--asset-url", help="Direct URL to PitBoxInstaller*.exe")
    group.add_argument("--release-url", help="GitHub release API URL (e.g. .../releases/latest)")
    group.add_argument("--repo", action="store_true", help="Use --repo-owner and --repo-name to fetch latest release")
    parser.add_argument("--version", default="", help="Version for download filename and logging")
    parser.add_argument("--repo-owner", default="minorthreat85", help="GitHub owner (with --repo or --release-url)")
    parser.add_argument("--repo-name", default="pitbox-releases", help="GitHub repo (with --repo or --release-url)")
    parser.add_argument("--token", default="", help="GitHub token for private repo")
    parser.add_argument(
        "--expected-sha256",
        default="",
        help="64-char hex SHA-256 from release notes (<!-- pitbox_sha256:AssetName.exe:hex -->). Required to run installer.",
    )
    args = parser.parse_args()

    log = _setup_logging()
    log.info("Startup args: %s", sys.argv[1:])
    log.info("Target: %s", args.target)

    # Resolve installer URL and version
    asset_url: str
    version = (args.version or "").strip()
    token = (args.token or os.environ.get("GITHUB_TOKEN", "")).strip() or None

    if args.asset_url:
        asset_url = args.asset_url
        if not version:
            version = "unknown"
        log.info("Using asset-url: %s", asset_url)
    else:
        # Resolve from release API: --release-url (parse owner/repo/tag) or --repo (use repo-owner/repo-name)
        tag_version: str | None = None
        if args.release_url:
            m = re.search(r"github\.com/repos/([^/]+)/([^/]+)/releases/(?:latest|tags/(?:v)?([^/]+))", args.release_url)
            if m:
                args.repo_owner, args.repo_name = m.group(1), m.group(2)
                if m.lastindex >= 3 and m.group(3):
                    tag_version = m.group(3)
        asset_url, version = resolve_installer_from_release(
            args.repo_owner, args.repo_name, tag_version, token, log
        )
        if not version:
            version = "unknown"

    # Elevation check
    if not _is_admin():
        log.info("Not elevated; re-launching with runas")
        _relaunch_elevated([sys.executable] + sys.argv[1:])

    # Normalize version for filename
    version_safe = re.sub(r"[^\w.\-]", "", version) or "unknown"
    download_path = DOWNLOADS_DIR / f"PitBoxInstaller-{version_safe}.exe"
    log.info("Download path: %s", download_path)

    # UI
    root = tk.Tk()
    root.title("PitBox Updater")
    root.geometry("500x320")
    root.resizable(True, True)

    target_label = tk.Label(root, text=f"Updating: {args.target.capitalize()}", font=("Segoe UI", 10, "bold"))
    target_label.pack(pady=(10, 2))

    status_var = tk.StringVar(value="Checking update package...")
    status_label = tk.Label(root, textvariable=status_var, font=("Segoe UI", 9), wraplength=460)
    status_label.pack(pady=5, padx=20, fill=tk.X)

    progress_var = tk.DoubleVar(value=0.0)
    progress = tk.Progressbar(root, variable=progress_var, maximum=100, length=400)
    progress.pack(pady=10, padx=20, fill=tk.X)

    log_text = tk.Text(root, height=8, font=("Consolas", 8), wrap=tk.WORD, state=tk.DISABLED)
    log_text.pack(pady=5, padx=20, fill=tk.BOTH, expand=True)

    def append_log(msg: str) -> None:
        log_text.config(state=tk.NORMAL)
        log_text.insert(tk.END, msg + "\n")
        log_text.see(tk.END)
        log_text.config(state=tk.DISABLED)

    result: list[int] = [-1]  # -1 = in progress, 0 = success, 1 = failure

    def do_update() -> None:
        try:
            status_var.set("Downloading installer...")
            root.update_idletasks()
            DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

            def on_progress(written: int, total: int) -> None:
                if total > 0:
                    pct = min(100, 100 * written / total)
                    progress_var.set(pct)
                    status_var.set(f"Downloading... {written // (1024*1024)} MB")
                    root.update_idletasks()

            download_file(asset_url, download_path, token, on_progress, log)
            progress_var.set(100)
            size = download_path.stat().st_size
            log.info("Download complete: %s exists=%s size=%s", download_path, download_path.exists(), size)
            append_log(f"Downloaded: {download_path} ({size} bytes)")

            exp = (args.expected_sha256 or "").strip()
            if not exp:
                status_var.set("Missing SHA-256; refusing to run installer (untrusted download).")
                log.error("expected-sha256 not provided; refusing to verify installer")
                append_log("Error: missing --expected-sha256 (release must publish pitbox_sha256 metadata).")
                result[0] = 1
                return
            ok_sha, sha_err = _verify_download_sha256(download_path, exp)
            if not ok_sha:
                status_var.set(sha_err)
                log.error("SHA-256 verification failed: %s", sha_err)
                append_log(f"Error: {sha_err}")
                result[0] = 1
                return
            append_log("SHA-256 verification OK.")

            if not download_path.exists() or size < 1000:
                status_var.set("Download failed (file missing or too small)")
                log.error("Download failed: path=%s size=%s", download_path, size)
                result[0] = 1
                return

            status_var.set("Stopping service...")
            root.update_idletasks()
            if args.target == "controller":
                stop_controller(log)
                append_log(f"Stopped {CONTROLLER_SERVICE_NAME}")
            else:
                stop_agent(log)
                append_log(f"Stopped {AGENT_TASK_NAME} / agent process")

            status_var.set("Launching installer...")
            root.update_idletasks()
            exit_code = run_installer(download_path, log)
            append_log(f"Installer exit code: {exit_code}")

            status_var.set("Waiting for install to complete...")
            root.update_idletasks()
            # Already waited in run_installer

            status_var.set("Restarting service...")
            root.update_idletasks()
            if args.target == "controller":
                start_controller(log)
                append_log(f"Started {CONTROLLER_SERVICE_NAME}")
            else:
                start_agent(log)
                append_log(f"Started {AGENT_TASK_NAME}")

            status_var.set("Update complete.")
            append_log("Success.")
            result[0] = 0
        except Exception as e:
            log.exception("Update failed: %s", e)
            status_var.set(f"Error: {e}")
            append_log(f"Error: {e}")
            result[0] = 1

    def start_worker() -> None:
        t = Thread(target=do_update, daemon=True)
        t.start()
        root.after(100, check_done)

    def check_done() -> None:
        if result[0] == -1:
            root.after(200, check_done)
            return
        # Keep window open so user can read
        progress_var.set(100 if result[0] == 0 else 0)
        root.after(5000, root.destroy)

    result[0] = -1
    root.after(100, start_worker)
    root.mainloop()
    return result[0] if result[0] >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
