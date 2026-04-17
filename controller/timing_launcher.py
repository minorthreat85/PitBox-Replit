"""
ACLiveTiming process launcher.

Spawns the bundled Devlabs.AcTiming.Web.exe as a child process bound to
http://0.0.0.0:9660 and keeps it alive (restart-on-crash) for as long as the
controller is running. The PitBox UI embeds it via an iframe.

Controller is the *only* manager of this process; on shutdown the child is
terminated. Windows-only — no-op on other platforms.

Default executable search order:
  1) $PITBOX_TIMING_EXE (full path override)
  2) C:\\PitBox\\Timing\\Devlabs.AcTiming.Web.exe
  3) <repo_root>/timing/Devlabs.AcTiming.Web.exe
  4) <cwd>/timing/Devlabs.AcTiming.Web.exe
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TIMING_PORT = 9660
TIMING_EXE_NAME = "Devlabs.AcTiming.Web.exe"

TIMING_UDP_PLUGIN_HOST = "127.0.0.1"
TIMING_UDP_PLUGIN_PORT = 9996
TIMING_UDP_PLUGIN_LOCAL_PORT = 9999
TIMING_UDP_PLUGIN_ADDRESS = f"{TIMING_UDP_PLUGIN_HOST}:{TIMING_UDP_PLUGIN_PORT}"
_HEALTH_TIMEOUT_S = 1.5
_RESTART_BACKOFF_S = 5.0

_proc: Optional[subprocess.Popen] = None
_lock = threading.Lock()
_stop = threading.Event()
_watchdog_thread: Optional[threading.Thread] = None
_resolved_exe: Optional[Path] = None


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    override = os.environ.get("PITBOX_TIMING_EXE")
    if override:
        paths.append(Path(override))
    paths.append(Path(r"C:\PitBox\Timing") / TIMING_EXE_NAME)
    repo_root = Path(__file__).resolve().parent.parent
    paths.append(repo_root / "timing" / TIMING_EXE_NAME)
    paths.append(Path.cwd() / "timing" / TIMING_EXE_NAME)
    return paths


def _resolve_exe() -> Optional[Path]:
    for p in _candidate_paths():
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def is_running() -> bool:
    return _proc is not None and _proc.poll() is None


def is_healthy() -> bool:
    """True if ACLiveTiming responds on its HTTP port."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{TIMING_PORT}/", timeout=_HEALTH_TIMEOUT_S
        ) as r:
            return 200 <= r.status < 500
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


def status() -> dict:
    return {
        "exe": str(_resolved_exe) if _resolved_exe else None,
        "port": TIMING_PORT,
        "running": is_running(),
        "healthy": is_healthy(),
    }


def _spawn() -> None:
    global _proc
    exe = _resolved_exe
    if exe is None:
        return
    env = os.environ.copy()
    env["ASPNETCORE_URLS"] = f"http://0.0.0.0:{TIMING_PORT}"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        _proc = subprocess.Popen(
            [str(exe)],
            cwd=str(exe.parent),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        logger.info(
            "ACLiveTiming launched: %s (pid=%s, port=%s)",
            exe, _proc.pid, TIMING_PORT,
        )
    except Exception as e:
        logger.exception("Failed to launch ACLiveTiming (%s): %s", exe, e)
        _proc = None


def _watchdog_loop() -> None:
    while not _stop.is_set():
        with _lock:
            if not is_running():
                if _resolved_exe is None:
                    return
                logger.warning("ACLiveTiming not running; (re)starting.")
                _spawn()
        if _stop.wait(_RESTART_BACKOFF_S):
            return


def start() -> None:
    """Start ACLiveTiming and the supervising watchdog. No-op if not on
    Windows or the executable cannot be found."""
    global _resolved_exe, _watchdog_thread
    if sys.platform != "win32":
        logger.info("Timing launcher: not on Windows; skipping.")
        return
    _resolved_exe = _resolve_exe()
    if _resolved_exe is None:
        logger.warning(
            "ACLiveTiming exe not found. Checked: %s. Live Timing UI will be empty until installed.",
            ", ".join(str(p) for p in _candidate_paths()),
        )
        return
    with _lock:
        if is_running():
            return
        _spawn()
    _stop.clear()
    _watchdog_thread = threading.Thread(
        target=_watchdog_loop, name="timing-watchdog", daemon=True
    )
    _watchdog_thread.start()


def stop() -> None:
    """Stop the watchdog and terminate ACLiveTiming."""
    global _proc
    _stop.set()
    with _lock:
        if _proc is not None:
            try:
                _proc.terminate()
                try:
                    _proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _proc.kill()
            except Exception as e:
                logger.warning("Error terminating ACLiveTiming: %s", e)
            _proc = None
