"""
Background telemetry loop: read AC shared memory at read_hz, emit telemetry_tick at rate_hz to Controller.
Resets seq and caches when session_key changes. Does not spam logs (state changes only).
"""
import logging
import threading
import time
import urllib.error
import urllib.request

from agent.telemetry.ac_shared_memory import SharedMemoryReader
from agent.telemetry.schemas import (
    AgentStatusPayload,
    TelemetryTickPayload,
    SessionInfo,
    GameInfo,
    TelemetrySourceInfo,
    CarInfo,
    TimingInfo,
    TrackInfo,
    WorldPos,
    CarStateInfo,
)

logger = logging.getLogger(__name__)

_loop_thread: threading.Thread | None = None
_stop_event: threading.Event = threading.Event()
_telemetry_started = False
_telemetry_stopped = False


def _session_key(mode: str, server_addr: str, car_model: str, driver_name: str) -> str:
    """Build session_key for deduplication; when it changes, reset seq."""
    return "|".join([mode or "", server_addr or "", car_model or "", driver_name or ""])


def _build_session_from_race_ini() -> tuple[SessionInfo, str]:
    """Build SessionInfo and session_key from race.ini and optional static (fallback)."""
    from pathlib import Path
    try:
        from agent.config import get_config, get_controls_ini_dir
        from agent.race_ini import parse_last_session
        config = get_config()
        cfg_dir = get_controls_ini_dir(config)
        if not cfg_dir:
            return SessionInfo(), ""
        race_ini = Path(cfg_dir) / "race.ini"
        ls = parse_last_session(race_ini)
        if not ls:
            return SessionInfo(), ""
        mode = (ls.get("mode") or "").strip() or "singleplayer"
        server = ls.get("server") or {}
        server_addr = ""
        server_name = (server.get("name") or "").strip() or "—"
        if mode == "online" and server:
            ip = (server.get("ip") or "").strip()
            port = (server.get("port") or "").strip()
            if ip or port:
                server_addr = f"{ip}:{port}" if port else ip
        car_model = (ls.get("car") or "").strip() or "—"
        skin = (ls.get("skin") or "").strip() or "—"
        driver_name = "—"
        return (
            SessionInfo(
                mode=mode,
                server_addr=server_addr,
                server_name=server_name,
                car_model=car_model,
                skin=skin,
                driver_name=driver_name,
            ),
            _session_key(mode, server_addr, car_model, driver_name),
        )
    except Exception:
        return SessionInfo(), ""


def _telemetry_loop(
    controller_url: str,
    agent_id: str,
    device_id: str,
    token: str,
    read_hz: float,
    rate_hz: float,
) -> None:
    """Loop: read at read_hz, emit at rate_hz. Resets seq when session_key changes. Log state changes only."""
    global _telemetry_started, _telemetry_stopped
    reader = SharedMemoryReader()
    reader.start()
    read_interval = 1.0 / read_hz if read_hz > 0 else 0.05
    emit_interval = 1.0 / rate_hz if rate_hz > 0 else 0.1
    last_emit = 0.0
    last_status_at = 0.0
    status_interval = 5.0
    seq = 0
    status_seq = 0
    last_session_key: str | None = None
    last_ok = False
    session_info, session_key = _build_session_from_race_ini()
    base_url = (controller_url or "").rstrip("/")
    telemetry_url = f"{base_url}/api/agents/telemetry" if base_url else ""
    headers = {
        "Content-Type": "application/json",
        "X-Agent-Id": device_id or agent_id,
        "X-Agent-Token": token or "",
    }

    def post_json(url: str, body: object) -> bool:
        if not url:
            return False
        try:
            json_bytes = body.model_dump_json().encode("utf-8") if hasattr(body, "model_dump_json") else __import__("json").dumps(body).encode("utf-8")
            req = urllib.request.Request(url, data=json_bytes, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status in (200, 201)
        except Exception:
            return False

    while not _stop_event.wait(read_interval):
        now = time.time()
        ts_ms = int(now * 1000)
        snapshot = reader.read_snapshot()

        # Session key from snapshot (driver/car/track) or race_ini
        if snapshot:
            driver_name = (snapshot.get("driver_name") or "").strip()
            car_model = (snapshot.get("car_model") or "").strip()
            session_info, session_key = _build_session_from_race_ini()
            if driver_name or car_model:
                mode = session_info.mode or "singleplayer"
                server_addr = session_info.server_addr or ""
                session_key = _session_key(mode, server_addr, car_model or session_info.car_model, driver_name or "—")
            if session_key != last_session_key:
                last_session_key = session_key
                seq = 0
        else:
            session_info, _ = _build_session_from_race_ini()
            if last_session_key is not None:
                last_session_key = None
                seq = 0

        # State change logging only
        ok = snapshot is not None
        if ok != last_ok:
            last_ok = ok
            if ok:
                logger.info("Telemetry started (AC shared memory OK)")
            else:
                logger.info("Telemetry stopped (AC not running or shared memory unavailable)")

        # agent_status every 5s
        if now - last_status_at >= status_interval and base_url:
            last_status_at = now
            status_seq += 1
            try:
                from agent.process_manager import get_process_status
                proc = get_process_status()
                game = GameInfo(
                    running=bool(proc.get("ac_running")),
                    pid=proc.get("pid"),
                    focused=None,
                )
            except Exception:
                game = GameInfo(running=False, pid=None, focused=None)
            telemetry_info = TelemetrySourceInfo(
                source="shared_memory",
                rate_hz=rate_hz,
                ok=ok,
                last_ok_ms=int(now * 1000) if ok else None,
            )
            # Enrich session.driver_name from AC snapshot when available
            status_session = SessionInfo(
                mode=session_info.mode,
                server_addr=session_info.server_addr,
                server_name=session_info.server_name,
                car_model=session_info.car_model,
                skin=session_info.skin,
                driver_name=(snapshot.get("driver_name") or "").strip() or session_info.driver_name if snapshot else session_info.driver_name,
            )
            send_agent_status(
                controller_url, agent_id, device_id, token,
                status_session, game, telemetry_info, ts_ms, status_seq,
            )

        # Emit at rate_hz
        if now - last_emit >= emit_interval and telemetry_url:
            last_emit = now
            if snapshot is not None:
                seq += 1
                car_model = (snapshot.get("car_model") or "").strip() or session_info.car_model
                driver_name = (snapshot.get("driver_name") or "").strip() or "—"
                tick = TelemetryTickPayload(
                    type="telemetry_tick",
                    v=1,
                    agent_id=agent_id,
                    device_id=device_id,
                    ts_ms=ts_ms,
                    seq=seq,
                    session_key=session_key or _session_key(session_info.mode, session_info.server_addr, car_model, driver_name),
                    car=CarInfo(
                        car_id=0,
                        driver_name=driver_name,
                        car_model=car_model,
                    ),
                    timing=TimingInfo(
                        lap=snapshot.get("lap", 0),
                        lap_time_ms=snapshot.get("lap_time_ms"),
                        best_lap_ms=snapshot.get("best_lap_ms"),
                        last_lap_ms=snapshot.get("last_lap_ms"),
                        sector=snapshot.get("sector", 0),
                        sector_time_ms=snapshot.get("sector_time_ms"),
                    ),
                    track=TrackInfo(
                        track_id=snapshot.get("track_id", ""),
                        layout=snapshot.get("layout", ""),
                        normalized_pos=snapshot.get("normalized_pos", 0.0),
                        world=WorldPos(
                            x=snapshot.get("world_x", 0.0),
                            y=snapshot.get("world_y", 0.0),
                            z=snapshot.get("world_z", 0.0),
                        ),
                        speed_kmh=snapshot.get("speed_kmh", 0.0),
                    ),
                    car_state=CarStateInfo(
                        gear=snapshot.get("gear", 0),
                        rpm=snapshot.get("rpm", 0),
                        throttle=snapshot.get("throttle", 0.0),
                        brake=snapshot.get("brake", 0.0),
                        in_pit=snapshot.get("in_pit", False),
                    ),
                )
                post_json(telemetry_url, tick)

    reader.stop()
    _telemetry_stopped = True


def start_telemetry(
    controller_url: str,
    agent_id: str,
    device_id: str,
    token: str,
    read_hz: float = 20.0,
    rate_hz: float = 10.0,
) -> None:
    """Start background thread that reads AC shared memory and POSTs telemetry_tick to Controller."""
    global _loop_thread, _telemetry_started
    if _loop_thread is not None and _loop_thread.is_alive():
        return
    _stop_event.clear()
    _telemetry_started = True
    _telemetry_stopped = False
    _loop_thread = threading.Thread(
        target=_telemetry_loop,
        args=(controller_url, agent_id, device_id, token),
        kwargs={"read_hz": read_hz, "rate_hz": rate_hz},
        daemon=True,
        name="telemetry-loop",
    )
    _loop_thread.start()
    logger.info("Telemetry loop started (read %s Hz, emit %s Hz)", read_hz, rate_hz)


def stop_telemetry() -> None:
    """Signal telemetry loop to stop."""
    _stop_event.set()


def send_agent_status(
    controller_url: str,
    agent_id: str,
    device_id: str,
    token: str,
    session: SessionInfo,
    game: GameInfo,
    telemetry: TelemetrySourceInfo,
    ts_ms: int,
    seq: int,
) -> bool:
    """POST agent_status to Controller (call on boot and every 5s). Returns True if sent successfully."""
    base = (controller_url or "").rstrip("/")
    if not base:
        return False
    url = f"{base}/api/agents/status"
    headers = {
        "Content-Type": "application/json",
        "X-Agent-Id": device_id or agent_id,
        "X-Agent-Token": token or "",
    }
    body = AgentStatusPayload(
        type="agent_status",
        v=1,
        agent_id=agent_id,
        device_id=device_id,
        ts_ms=ts_ms,
        seq=seq,
        session=session,
        game=game,
        telemetry=telemetry,
    )
    try:
        req = urllib.request.Request(url, data=body.model_dump_json().encode("utf-8"), method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status in (200, 201)
    except Exception:
        return False
