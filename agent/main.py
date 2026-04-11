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
    try:
        from agent.identity import get_device_id
        from agent.pairing import is_paired, get_controller_url, get_token, save_paired
        from agent.controller_heartbeat import start_heartbeat
        device_id = get_device_id()
        if is_paired():
            ctrl_url = get_controller_url()
            token = get_token()
            if ctrl_url and token:
                start_heartbeat(ctrl_url, device_id, token)
                logger.info("Paired to controller at %s", ctrl_url)
                if getattr(config, "telemetry_enabled", False):
                    from agent.telemetry.telemetry_loop import start_telemetry
                    read_hz = float(getattr(config, "telemetry_read_hz", 20) or 20)
                    rate_hz = float(getattr(config, "telemetry_rate_hz", 10) or 10)
                    start_telemetry(ctrl_url, config.agent_id, device_id, token, read_hz=read_hz, rate_hz=rate_hz)
        else:
            # Unpaired: listen for controller enrollment broadcast and auto-enroll
            from agent.enrollment_client import run_enrollment_loop
            import threading
            _enrollment_stop = threading.Event()
            def on_enrolled(url: str, token: str):
                save_paired(url, token, device_id)
                start_heartbeat(url, device_id, token)
                logger.info("Enrolled and paired to controller at %s", url)
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
    except Exception as e:
        logger.warning("Identity/pairing not started: %s", e)

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
