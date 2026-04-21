"""
PitBox Agent - Main entry point (simplified - no presets).

CRITICAL: PitBoxAgent MUST run in the user session, NEVER as SYSTEM.
Running as SYSTEM (e.g. Windows Service) causes Assetto Corsa to launch
in Session 0 with no visible window.
"""
import os
import sys
import argparse
import json
from pathlib import Path

# Version check: require Python 3.11 or newer
if sys.version_info < (3, 11):
    print("ERROR: Python 3.11 or newer is required")
    sys.exit(1)


def _check_not_system_user():
    """
    Detect if running as SYSTEM/LOCAL SERVICE/NETWORK SERVICE.
    If so, exit with a clear fatal error - Agent MUST run as logged-in user.
    """
    username = os.environ.get("USERNAME", "").upper()
    if username in ("SYSTEM", "LOCAL SERVICE", "NETWORK SERVICE"):
        print("=" * 70)
        print("FATAL ERROR: PitBoxAgent is running as SYSTEM")
        print("=" * 70)
        print("")
        print("PitBoxAgent MUST run as the logged-in Windows user (e.g. info).")
        print("Running as SYSTEM causes Assetto Corsa to launch headless (no window).")
        print("")
        print("Solutions:")
        print("  1. DO NOT run PitBoxAgent as a Windows Service")
        print("  2. Remove any PitBoxAgent service: sc delete PitBoxAgent")
        print("  3. Use the installer's 'Start Agent on login' option (Scheduled Task)")
        print("  4. Or run manually: python -m agent.main --config <path>")
        print("")
        print("Expected: PitBoxAgent runs as the sim user (e.g. 'info')")
        print("=" * 70)
        sys.exit(1)


# Run this check before any other imports that might log
_check_not_system_user()

from agent.logging_config import setup_logging
from agent.config import load_config


# Production install paths
AGENT_DIR = Path("C:/PitBox/Agent")
DEFAULT_CONFIG = AGENT_DIR / "config" / "agent_config.json"
DEFAULT_LOG_DIR = AGENT_DIR / "logs"


def create_default_config(config_path: Path):
    """Create a default configuration file with all current path keys (latest schema). Port is null so it is derived from agent_id (Sim1->9631, ...)."""
    default_config = {
        "agent_id": "Sim1",
        "token": "CHANGE_ME_IMMEDIATELY",
        "listen_host": "0.0.0.0",
        "port": None,
        "auto_launch_display": False,
        "display_launch_delay": 5.0,
        "paths": {
            "acs_exe": "C:\\Program Files (x86)\\Steam\\steamapps\\common\\assettocorsa\\acs.exe",
            "ac_cfg_dir": "%USERPROFILE%\\Documents\\Assetto Corsa\\cfg",
            "savedsetups_dir": "%USERPROFILE%\\Documents\\Assetto Corsa\\cfg\\controllers\\savedsetups",
            "cm_assists_presets_dir": "%LOCALAPPDATA%\\AcTools Content Manager\\Presets\\Assists",
        },
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(default_config, f, indent=2, ensure_ascii=False)
    return config_path


def _ensure_std_streams():
    """When running without a console (EXE or Task Scheduler), stdout/stderr can be None; uvicorn's logging then fails. Use devnull."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def main():
    """Main entry point."""
    _ensure_std_streams()
    parser = argparse.ArgumentParser(description="PitBox Agent - Assetto Corsa launcher")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Config file path")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--init", action="store_true", help="Create default config and exit")
    parser.add_argument("--service", action="store_true", help="Run as Windows Service")
    
    args = parser.parse_args()
    is_service_mode = args.service
    
    # Handle --init
    if args.init:
        try:
            created_path = create_default_config(args.config)
            print(f"Default configuration created at: {created_path}")
            print("IMPORTANT: Edit this file to change token and verify paths")
            sys.exit(0)
        except Exception as e:
            print(f"ERROR: Failed to create config: {e}")
            sys.exit(1)
    
    # Setup logging
    setup_logging(DEFAULT_LOG_DIR, debug=args.debug)
    import logging
    logger = logging.getLogger(__name__)

    # STARTUP[deps] — definitive proof of *what is actually inside this EXE*.
    # Logs version + critical-dep import status before any pairing/telemetry
    # decision runs. If telemetry later reports "websockets package not
    # available" but THIS line says websockets_ok=True, the bug is elsewhere
    # (e.g. import shadowing). If THIS line says websockets_ok=False, the
    # PyInstaller bundle is broken — rebuild with collect_all('websockets').
    try:
        from pitbox_common.version import get_version as _gv
        _agent_ver = _gv()
    except Exception as _ve:
        _agent_ver = f"<unknown:{type(_ve).__name__}>"
    try:
        import websockets as _ws_check
        _ws_ok = True
        _ws_ver = getattr(_ws_check, "__version__", "?")
        # Probe the lazy-resolved client attribute too — bare `import
        # websockets` succeeds even when only the empty top-level package
        # was bundled; only attribute access exposes the missing children.
        try:
            _ = _ws_check.connect  # noqa: F841 - just touch the attribute
            _ws_connect_ok = True
        except Exception as _ce:
            _ws_connect_ok = False
            _ws_ver = f"{_ws_ver} (connect missing: {type(_ce).__name__}: {_ce})"
    except Exception as _we:
        _ws_ok = False
        _ws_connect_ok = False
        _ws_ver = f"<import failed: {type(_we).__name__}: {_we}>"
    logger.info(
        "STARTUP[deps] agent_version=%s frozen=%s websockets_ok=%s websockets.connect_ok=%s websockets_version=%s exe=%s",
        _agent_ver,
        getattr(sys, "frozen", False),
        _ws_ok,
        _ws_connect_ok,
        _ws_ver,
        sys.executable,
    )

    # Load config
    try:
        config = load_config(args.config)
        from agent.config import resolve_agent_port
        effective_port = resolve_agent_port(config)
    except FileNotFoundError:
        logger.error(f"Config file not found: {args.config}")
        if not is_service_mode:
            print(f"ERROR: Config file not found: {args.config}")
            print(f"Run with --init to create a default config")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to load config: {e}", exc_info=True)
        if not is_service_mode:
            print(f"ERROR: Failed to load config: {e}")
        sys.exit(1)
    
    # Sanity check: ensure process_manager exports exist
    try:
        from agent.process_manager import get_process_status, start_process, stop_process
        _ = get_process_status()
    except ImportError as e:
        logger.error(f"Process manager sanity check failed: {e}")
        if not is_service_mode:
            print(f"ERROR: Process manager check failed: {e}")
        sys.exit(1)

    # Device identity and pairing (enrollment)
    device_id = getattr(config, "agent_id", None) or "sim"
    # Local helper so EVERY branch emits the same diagnostic line for the
    # operator to grep — `STARTUP[telemetry]` is unique on purpose.
    def _telemetry_startup_log(stage: str, **kw):
        bits = " ".join(f"{k}={v!r}" for k, v in kw.items())
        logger.info("STARTUP[telemetry] %s %s", stage, bits)

    def _start_telemetry_now(label: str, ctrl_url_arg: str, device_id_arg: str, token_arg: str):
        """Wrap start_telemetry with explicit, loud logging at every step so
        a silent failure (ImportError on packaged EXE, exception inside the
        sender setup, telemetry_enabled=False, etc.) is impossible to miss
        in the agent log."""
        enabled = getattr(config, "telemetry_enabled", True)
        rate = float(getattr(config, "telemetry_rate_hz", 15.0) or 15.0)
        _telemetry_startup_log(
            f"decision({label})",
            telemetry_enabled=enabled, rate_hz=rate,
            agent_id=device_id_arg, controller_url=ctrl_url_arg,
            token_prefix=(token_arg[:6] + "…") if token_arg else "(none)",
        )
        if not enabled:
            logger.warning("STARTUP[telemetry] DISABLED via config (telemetry_enabled=false). "
                           "Set telemetry_enabled=true in agent_config.json to enable.")
            return
        try:
            from agent.telemetry.sender import start_telemetry
        except Exception as ie:
            logger.error("STARTUP[telemetry] IMPORT FAILED: %s: %s — "
                         "the packaged agent EXE is missing the telemetry module "
                         "(check PyInstaller hiddenimports for `agent.telemetry.sender` "
                         "and `websockets.asyncio.client`).",
                         type(ie).__name__, ie, exc_info=True)
            return
        try:
            start_telemetry(ctrl_url_arg, device_id_arg, token_arg, rate_hz=rate)
            _telemetry_startup_log(f"started({label})", agent_id=device_id_arg)
        except Exception as te:
            logger.error("STARTUP[telemetry] start_telemetry() RAISED: %s: %s",
                         type(te).__name__, te, exc_info=True)

    try:
        from agent.identity import get_device_id
        from agent.pairing import is_paired, get_controller_url, get_token, save_paired
        from agent.controller_heartbeat import start_heartbeat
        device_id = get_device_id()
        paired = is_paired()
        _telemetry_startup_log("identity", device_id=device_id, paired=paired)
        if paired:
            ctrl_url = get_controller_url()
            token = get_token()
            if ctrl_url and token:
                start_heartbeat(ctrl_url, device_id, token)
                logger.info("Paired to controller at %s", ctrl_url)
                _start_telemetry_now("post-pair", ctrl_url, device_id, token)
            else:
                logger.error("STARTUP[telemetry] paired=True but controller_url/token "
                             "missing in pairing.json — pairing file is corrupt; "
                             "delete it and let enrollment re-pair.")
        else:
            # Unpaired: listen for controller enrollment broadcast and auto-enroll
            from agent.enrollment_client import run_enrollment_loop
            import threading
            _enrollment_stop = threading.Event()
            def on_enrolled(url: str, token: str):
                save_paired(url, token, device_id)
                start_heartbeat(url, device_id, token)
                logger.info("Enrolled and paired to controller at %s", url)
                _start_telemetry_now("post-enroll", url, device_id, token)
            _enrollment_thread = threading.Thread(
                target=run_enrollment_loop,
                args=(
                    device_id,
                    (__import__("socket").gethostname() or "sim").strip()[:64],
                    effective_port,
                    "",  # let controller use request.client for host
                ),
                kwargs={"on_enrolled": on_enrolled, "stop": _enrollment_stop},
                daemon=True,
                name="enrollment",
            )
            _enrollment_thread.start()
            logger.info("Enrollment mode: listening for controller broadcast (device_id=%s)", device_id)
            logger.warning("STARTUP[telemetry] WAITING for enrollment — telemetry will NOT "
                           "start until a controller enrolls this rig. If pairing.json was "
                           "expected to exist, check %APPDATA%/PitBox/Agent/pairing.json.")
    except Exception as e:
        logger.error("STARTUP[telemetry] identity/pairing setup FAILED: %s: %s",
                     type(e).__name__, e, exc_info=True)

    # LAN beacon for controller discovery (agent_id + port; use device_id when paired for consistency)
    try:
        from agent.beacon import start_beacon
        from agent.pairing import is_paired
        beacon_id = device_id if is_paired() else getattr(config, "agent_id", None) or device_id
        start_beacon(beacon_id, effective_port)
    except Exception as e:
        logger.warning("LAN beacon not started: %s", e)

    # Start FastAPI server
    try:
        import uvicorn
        from fastapi import FastAPI
        from agent.routes import router

        from pitbox_common.version import __version__ as _AGENT_VERSION
        app = FastAPI(title="PitBox Agent", version=_AGENT_VERSION)
        app.include_router(router)
        
        logger.info("Agent %s listening on %s:%s", config.agent_id, config.listen_host, effective_port)
        try:
            from agent.service.event_emitter import emit
            from agent.common.event_log import LogCategory, LogLevel
            emit(LogLevel.INFO, LogCategory.SYSTEM, "Agent started", rig_id=config.agent_id)
        except Exception as e:
            logger.debug("Event emit at startup: %s", e)

        try:
            from agent.update_state import normalize_on_startup
            normalize_on_startup()
            logger.info("Agent update state normalized on startup")
        except Exception as e:
            logger.debug("Agent update state normalization: %s", e)

        # Agent update checks are now controller-driven (v1.6.0+).
        # The agent no longer independently checks GitHub for updates at startup.
        # Updates are pushed by the controller via POST /api/update.
        # To re-enable autonomous checks (fallback/recovery), uncomment below:
        # try:
        #     from agent.update_check import run_update_check_at_startup
        #     run_update_check_at_startup(delay_seconds=5.0, show_prompt=True)
        # except Exception as e:
        #     logger.debug("Update check not started: %s", e)
        logger.info("Agent update checks are controller-driven; autonomous startup check disabled.")
        
        # Auto-launch sim display browser (if configured)
        try:
            if getattr(config, "auto_launch_display", False):
                from agent.pairing import is_paired, get_controller_url as _get_ctrl_url
                from agent.sim_display import schedule_launch
                _ctrl_url = (_get_ctrl_url() if is_paired() else None) or getattr(config, "controller_url", None)
                if _ctrl_url:
                    _delay = float(getattr(config, "display_launch_delay", 5.0) or 5.0)
                    schedule_launch(_ctrl_url, device_id, delay_seconds=_delay)
                else:
                    logger.warning("auto_launch_display=true but no controller_url available; skipping display launch")
        except Exception as e:
            logger.debug("Sim display auto-launch not started: %s", e)

        if not is_service_mode:
            print(f"\nPitBox Agent ({config.agent_id})")
            print(f"Listening on: {config.listen_host}:{effective_port}")
            print(f"Press Ctrl+C to stop\n")
        
        uvicorn.run(
            app,
            host=config.listen_host,
            port=effective_port,
            log_level="info" if args.debug else "warning"
        )
        
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
        if not is_service_mode:
            print("\nShutting down...")
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        if not is_service_mode:
            print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
