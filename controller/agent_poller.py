"""
Background task to poll agents for status and steering presets.
Supports backend 'agent' (PitBox Agent) and 'cm' (Content Manager remote control API).
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List

import httpx

from controller.cm_client import get_status as cm_get_status
from controller.config import get_config
from controller.enrolled_rigs import get_all_ordered as get_enrolled_rigs_ordered, get as get_enrolled_rig

logger = logging.getLogger(__name__)


@dataclass
class AgentStatus:
    """Agent status information (includes server panel: server, ac, heartbeat, last_session)."""
    agent_id: str
    online: bool
    error: str | None = None
    ac_running: bool = False
    pid: int | None = None
    uptime_sec: float | None = None
    last_check: datetime = field(default_factory=datetime.now)
    steering_presets: List[str] = field(default_factory=list)
    shifting_presets: List[str] = field(default_factory=list)
    display_name: str | None = None
    # Server panel (from agent /status)
    ts: str | None = None
    heartbeat: dict | None = None
    ac: dict | None = None
    server: dict | None = None
    # Last session from race.ini (normalized: mode, car, track, layout, server)
    last_session: dict | None = None
    # Employee control: AUTO | MANUAL (from agent hotkey state)
    control_mode: str | None = None
    # Race results from agent race_out.json (for results modal)
    race_results: list | None = None
    race_track_name: str | None = None
    race_session_type: str | None = None
    race_total_laps: int | None = None


# Global status cache
_status_cache: dict[str, AgentStatus] = {}
_poller_task: asyncio.Task | None = None
_poller_stop = asyncio.Event()

_poller_http_client: httpx.AsyncClient | None = None
_poller_http_timeout = httpx.Timeout(5.0)


async def get_poller_http_client() -> httpx.AsyncClient:
    """Shared AsyncClient for agent polling (connection reuse)."""
    global _poller_http_client
    if _poller_http_client is None:
        _poller_http_client = httpx.AsyncClient(
            timeout=_poller_http_timeout,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            follow_redirects=False,
        )
    return _poller_http_client


async def close_poller_http_client() -> None:
    global _poller_http_client
    if _poller_http_client is not None:
        await _poller_http_client.aclose()
        _poller_http_client = None


def get_status_cache() -> dict[str, AgentStatus]:
    """Get the current status cache."""
    return _status_cache.copy()


async def poll_single_agent(agent_id: str, host: str, port: int, token: str, client: httpx.AsyncClient) -> AgentStatus:
    """Poll a single agent for status and steering presets."""
    base_url = f"http://{host}:{port}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = await client.get(f"{base_url}/status", headers=headers)
        response.raise_for_status()
        data = response.json()
        ac_running = data.get("ac_running", data.get("acs_running", False))
        server = data.get("server")
        if not server:
            server = {"state": "UNAVAILABLE" if not ac_running else "DISCONNECTED", "source": "UNKNOWN"}
        status = AgentStatus(
            agent_id=agent_id,
            online=True,
            error=None,
            ac_running=ac_running,
            pid=data.get("pid"),
            uptime_sec=data.get("uptime_sec"),
            last_check=datetime.now(),
            steering_presets=[],
            shifting_presets=[],
            display_name=data.get("display_name"),
            ts=data.get("ts"),
            heartbeat=data.get("heartbeat"),
            ac=data.get("ac"),
            server=server,
            last_session=data.get("last_session"),
            control_mode=data.get("control_mode"),
            race_results=data.get("race_results") if isinstance(data.get("race_results"), list) else None,
            race_track_name=data.get("race_track_name") if isinstance(data.get("race_track_name"), str) else None,
            race_session_type=data.get("race_session_type") if isinstance(data.get("race_session_type"), str) else None,
            race_total_laps=data.get("race_total_laps") if isinstance(data.get("race_total_laps"), int) else None,
        )
        try:
            presets_resp = await client.get(f"{base_url}/presets/steering", headers=headers)
            if presets_resp.is_success:
                status.steering_presets = list((presets_resp.json() or {}).get("items") or [])
        except Exception:
            pass
        try:
            shift_resp = await client.get(f"{base_url}/presets/shifting", headers=headers)
            if shift_resp.is_success:
                status.shifting_presets = list((shift_resp.json() or {}).get("items") or [])
        except Exception:
            pass
        return status

    except httpx.TimeoutException:
        return AgentStatus(agent_id=agent_id, online=False, error="TIMEOUT", server={"state": "UNAVAILABLE", "source": "UNKNOWN"})
    except httpx.ConnectError:
        return AgentStatus(agent_id=agent_id, online=False, error="CONNECTION_REFUSED", server={"state": "UNAVAILABLE", "source": "UNKNOWN"})
    except httpx.HTTPStatusError as e:
        return AgentStatus(agent_id=agent_id, online=False, error=f"HTTP_{e.response.status_code}", server={"state": "UNAVAILABLE", "source": "UNKNOWN"})
    except Exception as e:
        logger.error(f"Error polling {agent_id}: {e}")
        return AgentStatus(agent_id=agent_id, online=False, error=str(e), server={"state": "UNAVAILABLE", "source": "UNKNOWN"})


async def poll_single_cm(agent_id: str, host: str, port: int, password: str) -> AgentStatus:
    """Poll a single CM-backed rig (Content Manager remote control API)."""
    data = await cm_get_status(host, port, password or "")
    if data is None:
        return AgentStatus(
            agent_id=agent_id,
            online=False,
            error="CONNECTION_REFUSED",
            server={"state": "UNAVAILABLE", "source": "UNKNOWN"},
        )
    return AgentStatus(
        agent_id=agent_id,
        online=True,
        error=None,
        ac_running=data.get("ac_running", False),
        pid=None,
        uptime_sec=None,
        last_check=datetime.now(),
        steering_presets=list(data.get("steering_presets") or []),
        shifting_presets=list(data.get("shifting_presets") or []),
        display_name=None,
        ts=None,
        heartbeat=None,
        ac={"running": data.get("ac_running", False), "pid": None, "focused": None, "mode": "UNKNOWN"},
        server={"state": "DISCONNECTED" if data.get("ac_running") else "UNAVAILABLE", "source": "CM"},
        last_session=None,
        control_mode=None,
    )


async def poll_all_agents():
    """Poll all enrolled rigs in parallel (enrollment model). Agent and CM backends."""
    rigs = get_enrolled_rigs_ordered()
    if not rigs:
        return

    http_client = await get_poller_http_client()

    async def poll_one(r: dict):
        aid = r.get("agent_id") or ""
        host = (r.get("host") or "").strip()
        if not aid or not host:
            return None
        if (r.get("backend") or "agent").strip().lower() == "cm":
            port = int(r.get("cm_port") or r.get("port") or 11777)
            password = r.get("cm_password") or ""
            return await poll_single_cm(aid, host, port, password)
        if r.get("port") and r.get("token"):
            return await poll_single_agent(aid, host, int(r["port"]), r.get("token", ""), http_client)
        return None

    tasks = [poll_one(r) for r in rigs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, AgentStatus):
            _status_cache[result.agent_id] = result


async def poller_loop():
    """Background polling loop."""
    config = get_config()
    interval = config.poll_interval_sec

    logger.info(f"Starting agent poller (interval: {interval}s)")

    try:
        while not _poller_stop.is_set():
            try:
                await poll_all_agents()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Poller error: {e}", exc_info=True)

            try:
                await asyncio.wait_for(_poller_stop.wait(), timeout=interval)
            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                pass
    finally:
        await close_poller_http_client()
    logger.info("Agent poller stopped")


def start_poller():
    """Start the background poller."""
    global _poller_task
    _poller_stop.clear()

    if _poller_task is None or _poller_task.done():
        _poller_task = asyncio.create_task(poller_loop())
        logger.info("Agent poller started")


async def stop_poller():
    """Stop the background poller cleanly."""
    global _poller_task
    _poller_stop.set()
    if _poller_task and not _poller_task.done():
        _poller_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(_poller_task), timeout=3)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _poller_task = None
    await close_poller_http_client()


def _cm_command_to_uris(command: str, data: dict) -> list[str] | None:
    """Map controller command + data to one or more acmanager:// URIs for CM backend. Returns None if not supported."""
    from controller.cm_client import (
        build_uri_driver_name,
        build_uri_preset_assists,
        build_uri_preset_controls,
        build_uri_preset_shifting,
        build_uri_race_direct,
    )

    if command == "apply_steering_preset":
        name = (data.get("name") or "").strip()
        if name:
            return [build_uri_preset_controls(name)]
        return None
    if command == "apply_shifting_preset":
        name = (data.get("name") or "").strip()
        if name:
            return [build_uri_preset_shifting(name)]
        return None
    if command == "set_driver_name":
        name = (data.get("driver_name") or "").strip()
        if name:
            return [build_uri_driver_name(name)]
        return None
    if command == "reset_rig":
        uris = []
        sp = (data.get("steering_preset") or "Race").strip()
        ap = (data.get("shifting_preset") or "H-Pattern").strip()
        dn = (data.get("display_name") or "").strip()
        if sp:
            uris.append(build_uri_preset_controls(sp))
        if ap:
            uris.append(build_uri_preset_shifting(ap))
        if dn:
            uris.append(build_uri_driver_name(dn))
        return uris if uris else [build_uri_preset_controls("Race"), build_uri_preset_shifting("H-Pattern")]
    if command == "apply":
        sel = data.get("selection") or {}
        mode = (sel.get("mode") or {}) if isinstance(sel.get("mode"), dict) else {}
        kind = (mode.get("kind") or "singleplayer").strip().lower()
        if kind == "online":
            return None
        car = sel.get("car") or {}
        track = sel.get("track") or {}
        car_id = (car.get("car_id") or "").strip() if isinstance(car, dict) else ""
        track_id = (track.get("track_id") or "").strip() if isinstance(track, dict) else ""
        if not car_id or not track_id:
            return None
        layout_id = (track.get("layout_id") or "default").strip() if isinstance(track, dict) else "default"
        skin_id = (car.get("skin_id") or "default").strip() if isinstance(car, dict) else "default"
        driver_name = (sel.get("driver_name") or "Driver").strip()
        mode_name = (mode.get("submode") or mode.get("session_type") or "practice").strip().lower() or "practice"
        duration = mode.get("duration_minutes")
        laps = mode.get("laps")
        uri = build_uri_race_direct(
            car_id=car_id,
            track_id=track_id,
            layout_id=layout_id or "default",
            mode=mode_name,
            skin_id=skin_id,
            driver_name=driver_name,
            duration=float(duration) if duration is not None else None,
            laps=int(laps) if laps is not None else None,
        )
        return [uri]
    return None


async def send_agent_command(agent_id: str, command: str, data: dict, timeout: float | None = 30.0) -> dict:
    """Send a command to an agent or CM backend (resolved from enrolled rigs). timeout: total seconds (default 30)."""
    rig = get_enrolled_rig(agent_id)
    if not rig:
        return {"success": False, "message": f"Agent {agent_id} not found"}

    backend = (rig.get("backend") or "agent").strip().lower()
    if backend == "cm":
        uris = _cm_command_to_uris(command, data or {})
        if not uris:
            return {"success": False, "message": f"Command {command!r} not supported for CM backend"}
        host = (rig.get("host") or "").strip()
        port = int(rig.get("cm_port") or rig.get("port") or 11777)
        password = rig.get("cm_password") or ""
        from controller.cm_client import send_command as cm_send_command

        for uri in uris:
            result = await cm_send_command(host, port, password, uri, timeout=timeout or 30.0)
            if not result.get("success"):
                return result
        return {"success": True, "message": "OK"}
    # Agent backend
    base_url = f"http://{rig['host']}:{rig['port']}"
    headers = {"Authorization": f"Bearer {rig['token']}"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{base_url}/{command}", headers=headers, json=data)
            response.raise_for_status()
            body = response.json() if response.content else {}
            return {**body, "success": body.get("success", True)}

    except httpx.TimeoutException:
        return {"success": False, "message": "Request timeout"}
    except httpx.ConnectError:
        return {"success": False, "message": "Connection refused"}
    except httpx.HTTPStatusError as e:
        msg = f"HTTP {e.response.status_code}"
        try:
            err_body = e.response.json()
            if isinstance(err_body, dict) and err_body.get("detail"):
                detail = err_body["detail"]
                msg = detail if isinstance(detail, str) else str(detail)
        except Exception:
            pass
        return {"success": False, "message": msg}
    except Exception as e:
        logger.error(f"Error sending command to {agent_id}: {e}")
        return {"success": False, "message": str(e)}
