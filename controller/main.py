"""
PitBox Controller - FastAPI server with web UI and agent API.
Loads config, runs agent poller, serves static GUI and /api/* routes.
AppData is canonical; legacy paths are read-only and used only for one-time migration (copy).
"""
import json
import os
import shutil
import sys
import logging
import warnings
from pathlib import Path
from contextlib import asynccontextmanager

# Ensure stdout/stderr are never None (e.g. when run as Windows service / no console).
# Uvicorn and other code may call stream.isatty(); None causes AttributeError.
def _ensure_std_streams():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


_ensure_std_streams()

# Suppress FastAPI/Starlette on_event deprecation (we use lifespan)
warnings.filterwarnings("ignore", message=".*on_event is deprecated.*", category=DeprecationWarning)

# Build app in code (so uvicorn.run(app, ...) is used, no import string). One-time debug if this block fails (PyInstaller hiddenimports).
try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response

    from controller.config import load_config, get_config, get_config_path, get_controller_http_url, set_default_config, create_default_config
    from controller.agent_poller import start_poller, stop_poller
    from controller.discovery import start_discovery, stop_discovery
    from controller.enrolled_rigs import load as load_enrolled_rigs, set_config_dir as set_enrolled_config_dir
    from controller.enrollment_broadcast import set_controller_url_provider, start as start_enrollment_broadcast, stop as stop_enrollment_broadcast
    from controller.api_routes import router as api_router, load_sim_assignments, BUILD_ID, discover_presets, PRESETS_DIR_DEBUG
    from controller.operator_auth import EMPLOYEE_COOKIE
    from controller.api_booking_routes import router as booking_router
    from controller.booking_proxy import router as proxy_router, start_root_proxy_thread, BOOKING_PROXY_PORT
    from controller.api_update_routes import router as update_router
    from controller.timing import engine as timing_engine
    from controller.api_timing_routes import router as timing_router, ws_router as timing_ws_router
    from controller.api_telemetry_ingest import router as telemetry_router, ws_router as telemetry_ws_router
    from controller.service.event_store import ensure_events_dir, append_event as event_store_append
    from controller.common.event_log import make_event, LogCategory, LogLevel
except Exception as _e:
    import traceback
    traceback.print_exc(file=sys.stderr)
    try:
        err_path = Path(os.getcwd()) / "controller_import_error.txt"
        with open(err_path, "w", encoding="utf-8") as _f:
            traceback.print_exc(file=_f)
        print(f"Wrote traceback to {err_path}", file=sys.stderr)
    except OSError:
        pass
    raise

# Mumble router is optional — grpcio/protobuf may not be installed on all PCs.
mumble_router = None
try:
    from controller.api_mumble_routes import router as mumble_router
except Exception as _mumble_err:
    import logging as _ml
    _ml.getLogger(__name__).warning("Mumble integration unavailable: %s", _mumble_err)

# Logging: always write to AppData logs dir. Create canonical dirs early.
if not getattr(logging, "_pitbox_initialized", False):
    try:
        from pitbox_common.runtime_paths import controller_dir, controller_data_dir, controller_logs_dir
        for d in (controller_dir(), controller_data_dir(), controller_logs_dir()):
            os.makedirs(d, exist_ok=True)
        log_dir = str(controller_logs_dir())
        log_file = os.path.join(log_dir, "controller.log")
        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - [PITBOX] - %(message)s",
        )
    except Exception:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [PITBOX] - %(message)s")
    setattr(logging, "_pitbox_initialized", True)

logger = logging.getLogger(__name__)


def _get_base_path() -> Path:
    """Base path for static/data files. Use bundle dir when running as PyInstaller EXE."""
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def _parse_config_arg() -> Path | None:
    """If --config <path> is in sys.argv, return that path; else None."""
    argv = getattr(sys, "argv", [])
    for i, a in enumerate(argv):
        if a == "--config" and i + 1 < len(argv):
            return Path(argv[i + 1])
    return None


def _find_config_path() -> Path:
    """Resolve controller config path: 1) --config if explicitly passed; 2) canonical AppData path only. No guessing from cwd."""
    config_arg = _parse_config_arg()
    if config_arg is not None:
        return Path(config_arg)
    try:
        from pitbox_common.runtime_paths import controller_config_path
        return controller_config_path()
    except Exception:
        pass
    # Fallback only when runtime_paths unavailable (e.g. tests); still prefer canonical path name.
    return Path(os.environ.get("APPDATA", os.path.expanduser("~")) if os.name == "nt" else os.path.expanduser("~")) / "PitBox" / "Controller" / "controller_config.json"


def _is_canonical_config_path(path: Path) -> bool:
    """True if path is the canonical AppData controller_config.json."""
    try:
        from pitbox_common.runtime_paths import controller_config_path
        return path.resolve() == controller_config_path().resolve()
    except Exception:
        return False


def _legacy_config_candidates() -> list[Path]:
    """Legacy locations for controller_config.json (read-only; used only for migration copy)."""
    cwd = Path(os.getcwd())
    return [
        cwd / "controller_config.json",
        cwd / "config" / "controller.json",
        cwd / "Controller" / "config" / "controller_config.json",
        cwd / "installed" / "controller_config.json",
        cwd / "examples" / "controller_config.json",
    ]


def _legacy_enrolled_candidates() -> list[Path]:
    """Legacy locations for enrolled_rigs.json (read-only; used only for migration copy)."""
    cwd = Path(os.getcwd())
    return [
        cwd / "enrolled_rigs.json",
        cwd / "Controller" / "data" / "enrolled_rigs.json",
        cwd / "installed" / "enrolled_rigs.json",
        cwd / "examples" / "enrolled_rigs.json",
    ]


def _migrate_legacy_controller_files() -> None:
    """
    One-time migration: COPY legacy config/enrolled_rigs to AppData only if AppData file does NOT exist.
    Does not delete, rename, or modify legacy files. If JSON parse fails, do not copy.
    """
    from pitbox_common.runtime_paths import controller_config_path, controller_dir
    dst_config = controller_config_path()
    dst_enrolled = controller_dir() / "enrolled_rigs.json"

    if not dst_config.exists():
        from controller.config import migrate_and_validate_legacy_config
        for src in _legacy_config_candidates():
            if not src.is_file():
                continue
            try:
                with open(src, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    logger.error("Legacy config at %s: invalid format (not a dict); not copying.", src)
                    continue
                data = migrate_and_validate_legacy_config(data, src)
                dst_config.parent.mkdir(parents=True, exist_ok=True)
                with open(dst_config, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                logger.warning(
                    "Migrated controller config from legacy path (validated). source=%s destination=%s ui_port=%s",
                    src.resolve(), dst_config.resolve(), data.get("ui_port"),
                )
                break
            except json.JSONDecodeError as e:
                logger.error("Legacy config at %s: invalid JSON (%s); not copying.", src, e)
            except OSError as e:
                logger.debug("Could not read/copy legacy config from %s: %s", src, e)
        else:
            logger.info("No legacy controller config found; using defaults.")

    if not dst_enrolled.exists():
        for src in _legacy_enrolled_candidates():
            if not src.is_file():
                continue
            try:
                with open(src, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    rigs = data.get("rigs") if isinstance(data.get("rigs"), list) else []
                elif isinstance(data, list):
                    rigs = data
                else:
                    logger.error("Legacy enrolled_rigs at %s: invalid format; not copying.", src)
                    continue
                dst_enrolled.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst_enrolled)
                logger.warning(
                    "Migrated enrolled_rigs from legacy path (copy only). source=%s destination=%s",
                    src.resolve(), dst_enrolled.resolve(),
                )
                break
            except json.JSONDecodeError as e:
                logger.error("Legacy enrolled_rigs at %s: invalid JSON (%s); not copying.", src, e)
            except OSError as e:
                logger.debug("Could not read/copy legacy enrolled_rigs from %s: %s", src, e)


def _check_port_available(host: str, port: int) -> bool:
    """Return True if the given host:port is not in use (controller can bind)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.settimeout(1.0)
            s.bind((host, port))
            return True
    except OSError:
        return False


def _port_doctor(host: str, port: int, config_path: Path) -> None:
    """If ui_port is in use, log a fatal error and exit. Do not silently fall back."""
    if _check_port_available(host, port):
        return
    from pitbox_common.ports import CONTROLLER_HTTP_PORT
    cfg_str = str(config_path.resolve())
    logger.error(
        "PORT %s IS ALREADY IN USE. PitBox Controller cannot start: ui_port=%s is bound by another process. "
        "Fix: 1) Stop the other process, or 2) Change ui_port in config to a free port (e.g. %s). Config path: %s",
        port, port, CONTROLLER_HTTP_PORT, cfg_str,
    )
    print(
        f"\nFATAL: Port {port} is already in use. Change ui_port in {cfg_str} or stop the other process.\n",
        file=sys.stderr,
    )
    sys.exit(1)


def _log_legacy_file_warnings() -> None:
    """If any legacy config file exists, log WARNING so user knows it is ignored."""
    for src in _legacy_config_candidates() + _legacy_enrolled_candidates():
        if src.is_file():
            logger.warning(
                "Legacy config found at %s. Ignored once AppData exists. Do not edit this file.",
                src.resolve(),
            )


_uvicorn_loop = None
_lifespan_started = False  # When True, second server (sim UI) skips startup/shutdown


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load config and start agent poller on startup. AppData is canonical; legacy migration is copy-only.
    When a second server (sim_ui_*) is run, its lifespan is a no-op so startup runs only once."""
    global _lifespan_started
    if _lifespan_started:
        yield
        return
    try:
        try:
            from controller.updater import normalize_updater_state_on_startup
            normalize_updater_state_on_startup()
            logger.info("Updater state normalized on startup")
        except Exception as e:
            logger.warning("Updater state normalization failed: %s", e)
        config_path = _find_config_path()
        # When using canonical path and it doesn't exist, try one-time migration from legacy.
        if _is_canonical_config_path(config_path) and not config_path.exists():
            _migrate_legacy_controller_files()
        _log_legacy_file_warnings()

        if config_path.exists():
            try:
                load_config(config_path)
                logger.info("Controller started; config loaded from: %s (ui_port=%s)", config_path.resolve(), get_config().ui_port)
            except ValueError as e:
                logger.error("Invalid controller configuration: %s", e)
                print(f"\nFATAL: Invalid configuration: {e}\n", file=sys.stderr)
                sys.exit(1)
            except Exception as e:
                logger.exception("Config load failed, using default: %s", e)
                set_default_config()
        else:
            if _is_canonical_config_path(config_path):
                try:
                    create_default_config(config_path)
                    load_config(config_path)
                except ValueError as e:
                    logger.error("Invalid controller configuration: %s", e)
                    print(f"\nFATAL: Invalid configuration: {e}\n", file=sys.stderr)
                    sys.exit(1)
                logger.info("No config found at canonical path; created default at %s (ui_port=%s).", config_path.resolve(), get_config().ui_port)
            else:
                logger.info("No config found at %s; using defaults (no agents).", config_path)
                set_default_config()
        load_sim_assignments()
        # Enrolled rigs registry is always canonical AppData; no config_dir override.
        set_enrolled_config_dir(None)
        load_enrolled_rigs()
        try:
            from pitbox_common.runtime_paths import controller_dir
            logger.info("Enrolled rigs registry: %s", (controller_dir() / "enrolled_rigs.json").resolve())
        except Exception:
            pass
        set_controller_url_provider(get_controller_http_url)
        start_enrollment_broadcast()
        discover_presets(PRESETS_DIR_DEBUG)
        start_poller()
        start_discovery()
        try:
            await timing_engine.start()
        except Exception as _e:
            logger.exception("Failed to start native timing engine: %s", _e)
        ensure_events_dir()
        try:
            event_store_append(make_event(LogLevel.INFO, LogCategory.SYSTEM, "Controller", "Controller started", details={"static_dir": str(STATIC_DIR)}))
        except Exception as e:
            logger.debug("Event log startup entry: %s", e)
        # One-time warning if sidebar logo is missing (graceful fallback served on request).
        _logo_path = STATIC_DIR / "assets" / "branding" / "fastest-lap-logo-sidebar.png"
        if not _logo_path.exists():
            logger.warning(
                "Sidebar logo not found at %s; UI will show fallback. Add fastest-lap-logo-sidebar.png for correct branding.",
                _logo_path,
            )
        logger.info("Lifespan startup complete. Static dir: %s", STATIC_DIR)
        _lifespan_started = True
        # Optional second server for customer sim display (own IP/port)
        cfg = get_config()
        sim_host = getattr(cfg, "sim_ui_host", None) and str(cfg.sim_ui_host).strip()
        sim_port = getattr(cfg, "sim_ui_port", None)
        if sim_host and sim_port is not None:
            def _run_sim_server():
                import uvicorn
                uvicorn.run(
                    app,
                    host=sim_host,
                    port=int(sim_port),
                    log_config=None,
                )
            try:
                t = __import__("threading").Thread(target=_run_sim_server, daemon=True, name="sim-ui-server")
                t.start()
                logger.info("Sim/customer UI server started on %s:%s", sim_host, sim_port)
            except Exception as ex:
                logger.warning("Could not start sim UI server on %s:%s: %s", sim_host, sim_port, ex)
    except Exception as e:
        logger.exception("Lifespan startup error: %s", e)
        set_default_config()
    global _uvicorn_loop
    import asyncio
    _uvicorn_loop = asyncio.get_running_loop()
    try:
        from controller.shutdown import set_server_shutdown_callback
        def _stop_server():
            if _uvicorn_loop and _uvicorn_loop.is_running():
                _uvicorn_loop.call_soon_thread_safe(_uvicorn_loop.stop)
        set_server_shutdown_callback(_stop_server)
    except Exception:
        pass
    yield
    logger.info("Controller shutting down.")
    try:
        stop_discovery()
        stop_enrollment_broadcast()
        await stop_poller()
    except Exception as e:
        logger.warning("Shutdown cleanup: %s", e)
    try:
        await timing_engine.stop()
    except Exception as e:
        logger.warning("Timing engine shutdown: %s", e)


try:
    from pitbox_common.version import __version__ as PITBOX_VERSION
except ImportError:
    PITBOX_VERSION = "0.0.0"

app = FastAPI(
    title="PitBox Controller",
    version=PITBOX_VERSION,
    lifespan=lifespan,
)

@app.middleware("http")
async def add_pitbox_build_header(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        response.headers["X-PitBox-Build"] = BUILD_ID
    return response

app.include_router(api_router)
app.include_router(booking_router)
app.include_router(proxy_router)
app.include_router(update_router, prefix="/api")
app.include_router(timing_router)
app.include_router(timing_ws_router)
app.include_router(telemetry_router, prefix="/api")
app.include_router(telemetry_ws_router)
try:
    from controller.api_server_control_routes import router as server_control_router
    app.include_router(server_control_router)
except Exception as _exc:  # noqa: BLE001 - keep boot resilient
    logging.getLogger(__name__).exception("Failed to mount server-control router: %s", _exc)
if mumble_router is not None:
    app.include_router(mumble_router)


# Static files: serve GUI at / and /app.js, /styles.css, etc.
# When running as PyInstaller EXE, use bundle only (rebuild EXE to update UI).
# Set PITBOX_STATIC_DIR to force a folder (e.g. repo controller/static for full RIG CONTROL CENTER UI).
def _get_static_dir() -> Path:
    env_static = os.environ.get("PITBOX_STATIC_DIR")
    if env_static:
        p = Path(env_static).resolve()
        if (p / "index.html").exists():
            return p
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / "static"
    # When running from source, use static next to this file (always the full RIG CONTROL CENTER)
    return Path(__file__).resolve().parent / "static"


STATIC_DIR = _get_static_dir()


def _no_cache_headers() -> dict:
    """Headers to avoid serving cached old UI."""
    return {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


def _asset_response(path: Path, media_type: str, request: Request):
    """Serve a static asset — always fresh (no-store prevents stale UI after updates)."""
    return FileResponse(path, media_type=media_type, headers=_no_cache_headers())


@app.get("/")
async def index():
    """Serve the main GUI with BUILD_ID-stamped asset URLs to bust browser cache."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        logger.error("index.html not found at %s (STATIC_DIR=%s)", index_path, STATIC_DIR)
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail=f"index.html not found at {index_path}. Check logs.",
        )
    try:
        html = index_path.read_text(encoding="utf-8")
        # Inject build stamp into asset URLs so browsers always fetch fresh JS/CSS
        for asset in ("/app.js", "/styles.css", "/booking-admin.css", "/booking-admin.js"):
            html = html.replace(f'"{asset}"', f'"{asset}?v={BUILD_ID}"')
            html = html.replace(f"'{asset}'", f"'{asset}?v={BUILD_ID}'")
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=html, headers=_no_cache_headers())
    except Exception:
        return FileResponse(index_path, media_type="text/html", headers=_no_cache_headers())


@app.get("/status", response_class=PlainTextResponse)
async def legacy_status():
    """Legacy health check for start_pitbox / selftest."""
    return "Controller is running."


@app.get("/health")
async def health_check():
    """HTTP health check used by update.ps1 to verify the app is serving."""
    return {"status": "ok", "version": PITBOX_VERSION}


@app.get("/favicon.ico")
async def serve_favicon():
    """Serve PitBox favicon."""
    from fastapi.responses import FileResponse
    path = STATIC_DIR / "favicon.ico"
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="favicon.ico not found")
    return FileResponse(path, media_type="image/x-icon")


@app.get("/app.js")
async def serve_app_js(request: Request):
    """Serve main GUI script."""
    path = STATIC_DIR / "app.js"
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="app.js not found")
    return _asset_response(path, "application/javascript", request)


@app.get("/styles.css")
async def serve_styles(request: Request):
    """Serve main stylesheet."""
    path = STATIC_DIR / "styles.css"
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="styles.css not found")
    return _asset_response(path, "text/css", request)


@app.get("/booking-admin.css")
async def serve_booking_admin_css(request: Request):
    """Serve native PitBox booking admin stylesheet."""
    path = STATIC_DIR / "booking-admin.css"
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="booking-admin.css not found")
    return _asset_response(path, "text/css", request)


@app.get("/booking-admin.js")
async def serve_booking_admin_js(request: Request):
    """Serve native PitBox booking admin script."""
    path = STATIC_DIR / "booking-admin.js"
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="booking-admin.js not found")
    return _asset_response(path, "application/javascript", request)


@app.get("/live_timing.css")
async def serve_live_timing_css(request: Request):
    """Serve native PitBox Live Timing stylesheet."""
    path = STATIC_DIR / "live_timing.css"
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="live_timing.css not found")
    return _asset_response(path, "text/css", request)


@app.get("/live_timing.js")
async def serve_live_timing_js(request: Request):
    """Serve native PitBox Live Timing client script."""
    path = STATIC_DIR / "live_timing.js"
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="live_timing.js not found")
    return _asset_response(path, "application/javascript", request)


@app.get("/server_admin.css")
async def serve_server_admin_css(request: Request):
    """Serve native PitBox server-admin (UDP plugin) panel stylesheet."""
    path = STATIC_DIR / "server_admin.css"
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="server_admin.css not found")
    return _asset_response(path, "text/css", request)


@app.get("/server_admin.js")
async def serve_server_admin_js(request: Request):
    """Serve native PitBox server-admin (UDP plugin) panel client script."""
    path = STATIC_DIR / "server_admin.js"
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="server_admin.js not found")
    return _asset_response(path, "application/javascript", request)


def _get_sidebar_logo_path() -> Path | None:
    """Canonical logo path: controller/static/assets/branding/fastest-lap-logo-sidebar.png only. No ui/src at runtime."""
    path = STATIC_DIR / "assets" / "branding" / "fastest-lap-logo-sidebar.png"
    return path if path.exists() else None


# Styled fallback SVG when logo file is missing (no 1x1 placeholder).
_SIDEBAR_LOGO_FALLBACK_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 28" width="140" height="28">'
    b'<text x="0" y="22" font-family="sans-serif" font-size="18" font-weight="600" fill="rgba(255,255,255,0.7)">Fastest Lap</text>'
    b'</svg>'
)
_logo_missing_warned = False


@app.get("/assets/branding/fastest-lap-logo-sidebar.png")
async def serve_fastest_lap_logo():
    """Serve Fastest Lap logo from controller/static/assets/branding/. If missing, return styled fallback and log once."""
    global _logo_missing_warned
    path = _get_sidebar_logo_path()
    if path is not None:
        return FileResponse(path, media_type="image/png", headers=_no_cache_headers())
    if not _logo_missing_warned:
        _logo_missing_warned = True
        logger.warning(
            "Sidebar logo not found at %s; serving styled fallback. Add fastest-lap-logo-sidebar.png for correct branding.",
            STATIC_DIR / "assets" / "branding" / "fastest-lap-logo-sidebar.png",
        )
    return Response(
        content=_SIDEBAR_LOGO_FALLBACK_SVG,
        media_type="image/svg+xml",
        headers=dict(_no_cache_headers()),
    )


@app.get("/sim")
async def sim_display():
    """Sim kiosk display: track + server + car selection for assigned server. Use ?agent_id=Sim5."""
    sim_path = STATIC_DIR / "sim.html"
    if not sim_path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="sim.html not found")
    return FileResponse(sim_path, media_type="text/html", headers=_no_cache_headers())


@app.get("/kiosk")
@app.get("/kiosk/")
async def kiosk_phone():
    """Mobile kiosk UI: open from QR scan (?agent_id=...&nonce=...&token=...). Claims session then shows mode/car/track/assists and Start/Join."""
    kiosk_path = STATIC_DIR / "kiosk.html"
    if not kiosk_path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="kiosk.html not found")
    return FileResponse(kiosk_path, media_type="text/html", headers=_no_cache_headers())


@app.get("/employee/login")
async def employee_login_page():
    """Mobile-first employee login (password gate)."""
    path = STATIC_DIR / "employee-login.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="employee-login.html not found")
    return FileResponse(path, media_type="text/html", headers=_no_cache_headers())


@app.get("/employee")
async def employee_dashboard(request: Request):
    """Mobile-first employee control dashboard. Redirects to /employee/login if not logged in."""
    if request.cookies.get(EMPLOYEE_COOKIE) != "1":
        return RedirectResponse(url="/employee/login", status_code=302)
    path = STATIC_DIR / "employee.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="employee.html not found")
    return FileResponse(path, media_type="text/html", headers=_no_cache_headers())


@app.get("/employee.js")
async def serve_employee_js(request: Request):
    """Serve Employee Control script."""
    path = STATIC_DIR / "employee.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="employee.js not found")
    return _asset_response(path, "application/javascript", request)


@app.get("/kiosk-copy.js")
async def serve_kiosk_copy(request: Request):
    """Serve kiosk customer-facing copy (single source for wording)."""
    path = STATIC_DIR / "kiosk-copy.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="kiosk-copy.js not found")
    return _asset_response(path, "application/javascript", request)


# SPA routes: serve index.html so path-based routing works (bookmarkable, refresh-safe).
_SPA_PATHS = {
    "/garage",
    "/sims",
    "/presets",
    "/server-config",
    "/entry-list",
    "/live-timing",
    "/content",
    "/system-logs",
    "/settings",
    "/bookings",
    "/schedule",
    "/checkin",
    "/analytics",
    "/mumble",
}


@app.get("/server/booking")
async def redirect_server_booking():
    return RedirectResponse(url="/bookings", status_code=302)


@app.get("/bookings")
async def bookings_page(request: Request):
    """Gated SPA entry for the Bookings tab. When employee_password is set,
    only operators with the pitbox_employee cookie may access it."""
    from controller.operator_auth import EMPLOYEE_COOKIE, get_employee_password_optional
    if get_employee_password_optional() is not None and request.cookies.get(EMPLOYEE_COOKIE) != "1":
        return RedirectResponse(url="/employee/login?next=/bookings", status_code=302)
    return await index()


@app.get("/server/entry-list")
async def redirect_server_entry_list():
    return RedirectResponse(url="/entry-list", status_code=302)


@app.get("/server/live-timing")
async def redirect_server_live_timing():
    return RedirectResponse(url="/live-timing", status_code=302)


@app.get("/server/content")
async def redirect_server_content():
    return RedirectResponse(url="/content", status_code=302)


@app.get("/{path:path}")
async def spa_catchall(request: Request, path: str):
    """Serve index.html for SPA routes so /garage, /booking, etc. work on refresh and bookmark."""
    if path == "api" or path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    if path == "assets" or path.startswith("assets/"):
        raise HTTPException(status_code=404, detail="Not found")
    # Ensure /kiosk and /sim are never handled here (they have their own routes above)
    path_stripped = path.strip("/").lower()
    qs = ("?" + str(request.url.query)) if request.url.query else ""
    if path_stripped == "kiosk":
        return RedirectResponse(url="/kiosk" + qs, status_code=302)
    if path_stripped == "sim":
        return RedirectResponse(url="/sim" + qs, status_code=302)
    full = "/" + path.strip("/") if path else "/"
    if full in _SPA_PATHS:
        return await index()
    raise HTTPException(status_code=404, detail="Not found")


if __name__ == "__main__":
    import uvicorn
    try:
        # --init: create default controller_config.json and exit (used by installer)
        if "--init" in sys.argv:
            config_path = _parse_config_arg() or _find_config_path()
            create_default_config(config_path)
            from pitbox_common.ports import CONTROLLER_HTTP_PORT
            print(f"Default config created at: {config_path.resolve()} (ui_port={CONTROLLER_HTTP_PORT})")
            sys.exit(0)
        config_path = _find_config_path()
        if _is_canonical_config_path(config_path) and not config_path.exists():
            _migrate_legacy_controller_files()
        _log_legacy_file_warnings()
        if config_path.exists():
            try:
                load_config(config_path)
                print(f"Config loaded: {config_path.resolve()}")
            except Exception as e:
                logger.exception("Config load failed: %s", e)
                set_default_config()
        else:
            if _is_canonical_config_path(config_path):
                create_default_config(config_path)
                load_config(config_path)
                print(f"Created default config at {config_path.resolve()} (ui_port={get_config().ui_port})")
            else:
                print(f"Note: Config not found at {config_path}; using default (no agents).")
                set_default_config()
        config = get_config()
        ui = (config.ui_host or "").strip()
        # Bind to all interfaces if LAN access is enabled or ui_host is explicitly 0.0.0.0
        host = "0.0.0.0" if (getattr(config, "allow_lan_ui", False) or ui == "0.0.0.0") else (ui or "127.0.0.1")
        port = config.ui_port
        logger.info("Config path: %s | ui_port: %s | bind: %s:%s", config_path.resolve(), port, host, port)
        _port_doctor(host, port, config_path)
        try:
            display_url = get_controller_http_url()
            print(f"PitBox Controller at {display_url}  (config: {config_path.resolve()})")
        except (OSError, AttributeError):
            pass
        # Start the root-preserving booking proxy on its own port (separate origin
        # so the booking SPA's client-side router sees /admin/... not /proxy/...).
        try:
            start_root_proxy_thread(host=host, port=BOOKING_PROXY_PORT)
        except Exception as _e:
            logger.exception("Failed to start booking root-proxy listener: %s", _e)
        uvicorn.run(
            app,
            host=host,
            port=port,
            reload=False,
            log_config=None,  # Use our logging; avoid uvicorn DefaultFormatter + None stream (isatty crash)
        )
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        try:
            err_path = Path(os.getcwd()) / "controller_start_error.txt"
            with open(err_path, "w", encoding="utf-8") as f:
                traceback.print_exc(file=f)
            print(f"Wrote traceback to {err_path}", file=sys.stderr)
        except OSError:
            pass
        sys.exit(1)
