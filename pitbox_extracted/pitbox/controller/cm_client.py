"""
Content Manager (CM) remote control API client.
Used when a rig has backend="cm": talk to CM's HTTP API on the sim instead of the PitBox Agent.
Sim control only: presets, race/direct, driver name, status.
"""
import logging
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

DEFAULT_CM_PORT = 11777


def _auth_headers(password: str) -> dict:
    if not password:
        return {}
    return {"Authorization": f"Bearer {password}"}


async def get_status(host: str, port: int, password: str, timeout: float = 5.0) -> dict | None:
    """
    Fetch status from CM remote control API.
    Returns dict with online, ac_running, steering_presets, shifting_presets, assists_presets,
    or None on error.
    """
    base = f"http://{host}:{port}"
    headers = _auth_headers(password)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            status_resp = await client.get(f"{base}/status", headers=headers)
            if status_resp.status_code != 200:
                return None
            data = status_resp.json() or {}
            out = {
                "online": True,
                "ac_running": data.get("ac_running", False),
                "steering_presets": [],
                "shifting_presets": [],
                "assists_presets": [],
            }
            try:
                presets = await client.get(f"{base}/presets/controls", headers=headers)
                if presets.is_success and presets.json():
                    out["steering_presets"] = [p.get("name") for p in presets.json() if p.get("name")]
            except Exception:
                pass
            try:
                shifting = await client.get(f"{base}/presets/shifting", headers=headers)
                if shifting.is_success and shifting.json():
                    out["shifting_presets"] = [p.get("name") for p in shifting.json() if p.get("name")]
            except Exception:
                pass
            try:
                assists = await client.get(f"{base}/presets/assists", headers=headers)
                if assists.is_success and assists.json():
                    out["assists_presets"] = [p.get("name") for p in assists.json() if p.get("name")]
            except Exception:
                pass
            return out
    except (httpx.TimeoutException, httpx.ConnectError, Exception) as e:
        logger.debug("CM get_status %s:%s failed: %s", host, port, e)
        return None


async def send_command(
    host: str,
    port: int,
    password: str,
    uri: str,
    timeout: float = 30.0,
) -> dict:
    """
    Send a single acmanager:// URI to CM via POST /command.
    Returns { "success": bool, "message": str }.
    """
    base = f"http://{host}:{port}"
    headers = _auth_headers(password)
    if headers:
        headers["Content-Type"] = "application/json"
    body = {"uri": uri}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base}/command", headers=headers, json=body)
            if resp.status_code == 401:
                return {"success": False, "message": "Unauthorized"}
            if resp.status_code != 200:
                err = (resp.json() or {}).get("error") or resp.text or f"HTTP {resp.status_code}"
                return {"success": False, "message": str(err)}
            return {"success": True, "message": "OK"}
    except httpx.TimeoutException:
        return {"success": False, "message": "Request timeout"}
    except httpx.ConnectError:
        return {"success": False, "message": "Connection refused"}
    except Exception as e:
        logger.exception("CM send_command failed: %s", e)
        return {"success": False, "message": str(e)}


def build_uri_preset_controls(name: str) -> str:
    return f"acmanager://preset/controls?name={quote(name, safe='')}"


def build_uri_preset_assists(name: str) -> str:
    return f"acmanager://preset/assists?name={quote(name, safe='')}"


def build_uri_preset_shifting(name: str) -> str:
    """CM uses same controller presets for shifting; map to controls."""
    return f"acmanager://preset/controls?name={quote(name, safe='')}"


def build_uri_driver_name(name: str) -> str:
    return f"acmanager://driver/name?name={quote(name, safe='')}"


def build_uri_race_direct(
    car_id: str,
    track_id: str,
    layout_id: str = "",
    mode: str = "practice",
    skin_id: str = "",
    driver_name: str = "",
    duration: float | None = None,
    laps: int | None = None,
) -> str:
    params = [f"car={quote(car_id, safe='')}", f"track={quote(track_id, safe='')}"]
    if layout_id:
        params.append(f"layout={quote(layout_id, safe='')}")
    params.append(f"mode={quote(mode, safe='')}")
    if skin_id:
        params.append(f"skin={quote(skin_id, safe='')}")
    if driver_name:
        params.append(f"driverName={quote(driver_name, safe='')}")
    if duration is not None:
        params.append(f"duration={duration}")
    if laps is not None:
        params.append(f"laps={laps}")
    return "acmanager://race/direct?" + "&".join(params)
