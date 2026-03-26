#!/usr/bin/env python3
"""
Standalone PitBox Controller updater. Runs OUTSIDE the controller process.
Downloads ZIP, stops service (NSSM or net), atomically replaces install dir, starts service.
Writes progress to work_dir/status.json. Python stdlib only.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

STATUS_FILENAME = "status.json"
STAGING_ZIP = "staging/update.zip"
EXTRACT_DIR = "staging/extract"
BACKUP_SUFFIX = "_backup"
CONTROLLER_EXE = "PitBoxController.exe"
CHUNK_SIZE = 65536


def _verify_download_sha256(path: Path, expected_hex: str) -> tuple[bool, str]:
    """Return (ok, error_message). expected_hex is 64 hex chars from trusted release metadata."""
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
            "Integrity check failed: file SHA-256 does not match release metadata. "
            f"Refusing to install. (expected {exp[:16]}…, got {act[:16]}…)",
        )
    return True, ""


def write_status(work_dir: Path, state: str, message: str = "", percent: int = 0) -> None:
    path = work_dir / STATUS_FILENAME
    work_dir.mkdir(parents=True, exist_ok=True)
    data = {"state": state, "message": message, "percent": max(0, min(100, percent))}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=0)


def run_cmd(cmd: list[str], timeout: int = 60) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or f"exit code {r.returncode}").strip()
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


def stop_service(service: str, work_dir: Path) -> bool:
    # Prefer NSSM; fallback to net stop
    nssm_candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "nssm" / "nssm.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "nssm" / "nssm.exe",
        Path(r"C:\PitBox\Controller\bin\nssm.exe"),
        Path(r"C:\PitBox\updater\nssm.exe"),
    ]
    for nssm in nssm_candidates:
        if nssm.exists():
            ok, err = run_cmd([str(nssm), "stop", service], timeout=30)
            if ok:
                return True
            # Continue to net stop on failure
    ok, err = run_cmd(["net", "stop", service], timeout=30)
    return ok


def start_service(service: str, work_dir: Path) -> bool:
    nssm_candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "nssm" / "nssm.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "nssm" / "nssm.exe",
        Path(r"C:\PitBox\Controller\bin\nssm.exe"),
        Path(r"C:\PitBox\updater\nssm.exe"),
    ]
    for nssm in nssm_candidates:
        if nssm.exists():
            ok, err = run_cmd([str(nssm), "start", service], timeout=30)
            if ok:
                return True
    ok, err = run_cmd(["net", "start", service], timeout=30)
    return ok


def download_zip(url: str, dest: Path, token: str | None, work_dir: Path) -> None:
    write_status(work_dir, "downloading", "Downloading update...", 5)
    headers = {"Accept": "application/octet-stream", "User-Agent": "PitBox-Updater"}
    if token:
        headers["Authorization"] = f"token {token}"
    req = Request(url, headers=headers)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(req, timeout=300) as resp:
        total = int(resp.headers.get("Content-Length", 0)) or 0
        written = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
                if total > 0:
                    pct = 5 + int(45 * written / total)
                    write_status(work_dir, "downloading", f"Downloading... {written // (1024*1024)} MB", pct)
    write_status(work_dir, "downloading", "Download complete", 50)


def extract_zip(zip_path: Path, extract_to: Path, work_dir: Path) -> Path:
    write_status(work_dir, "extracting", "Extracting...", 52)
    if extract_to.exists():
        shutil.rmtree(extract_to)
    extract_to.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_to)
    # Find root that contains PitBoxController.exe (may be extract_to or extract_to/SingleSubdir)
    candidates = [extract_to]
    for c in extract_to.iterdir():
        if c.is_dir() and (c / CONTROLLER_EXE).exists():
            candidates.append(c)
            break
    for root in candidates:
        if (root / CONTROLLER_EXE).exists():
            write_status(work_dir, "extracting", "Extract complete", 55)
            return root
    write_status(work_dir, "error", "ZIP does not contain PitBoxController.exe", 0)
    raise RuntimeError("ZIP does not contain PitBoxController.exe")


def main() -> int:
    parser = argparse.ArgumentParser(description="PitBox Controller external updater")
    parser.add_argument("--service", default="PitBoxController", help="Service name to stop/start")
    parser.add_argument("--zip-url", required=True, help="URL of controller update ZIP")
    parser.add_argument("--install-dir", required=True, help="Controller install directory to replace")
    parser.add_argument("--work-dir", default=r"C:\PitBox\updates", help="Working directory (staging, status.json)")
    parser.add_argument("--token", default="", help="Optional GitHub token for private repo")
    parser.add_argument(
        "--expected-sha256",
        required=True,
        help="Lowercase hex SHA-256 of the ZIP from trusted release metadata (required for security).",
    )
    args = parser.parse_args()

    work_dir = Path(args.work_dir).resolve()
    install_dir = Path(args.install_dir).resolve()
    staging_dir = work_dir / "staging"
    zip_path = work_dir / STAGING_ZIP.replace("/", os.sep)
    extract_path = work_dir / EXTRACT_DIR.replace("/", os.sep)
    backup_dir = Path(str(install_dir) + BACKUP_SUFFIX)
    token = (args.token or os.environ.get("GITHUB_TOKEN", "")).strip() or None

    try:
        download_zip(args.zip_url, zip_path, token, work_dir)
        ok_hash, hash_err = _verify_download_sha256(zip_path, args.expected_sha256)
        if not ok_hash:
            write_status(work_dir, "error", hash_err, 0)
            return 1
        new_root = extract_zip(zip_path, extract_path, work_dir)

        write_status(work_dir, "stopping", "Stopping service...", 60)
        if not stop_service(args.service, work_dir):
            write_status(work_dir, "error", "Failed to stop service", 0)
            return 1
        write_status(work_dir, "stopping", "Service stopped", 65)

        write_status(work_dir, "installing", "Replacing install directory...", 70)
        if not install_dir.exists():
            write_status(work_dir, "error", f"Install dir does not exist: {install_dir}", 0)
            return 1
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        shutil.move(str(install_dir), str(backup_dir))
        try:
            shutil.copytree(str(new_root), str(install_dir))
        except Exception as e:
            if install_dir.exists():
                shutil.rmtree(install_dir, ignore_errors=True)
            shutil.move(str(backup_dir), str(install_dir))
            write_status(work_dir, "error", f"Install failed (rolled back): {e}", 0)
            return 1
        shutil.rmtree(backup_dir, ignore_errors=True)
        write_status(work_dir, "installing", "Install complete", 90)

        write_status(work_dir, "starting", "Starting service...", 95)
        if not start_service(args.service, work_dir):
            write_status(work_dir, "error", "Install OK but failed to start service", 0)
            return 1
        write_status(work_dir, "done", "Update complete", 100)
        return 0
    except (URLError, HTTPError, OSError, zipfile.BadZipFile, RuntimeError) as e:
        write_status(work_dir, "error", str(e), 0)
        return 1
    except Exception as e:
        write_status(work_dir, "error", str(e), 0)
        raise


if __name__ == "__main__":
    sys.exit(main())
