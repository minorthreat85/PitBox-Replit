"""
Telemetry sender: pushes shared-memory frames to PitBox Controller over a
persistent WebSocket. Auto-reconnects with backoff. Idle (heartbeat-only) when
AC is not running.

Runs in its own daemon thread with a private asyncio loop so it doesn't fight
with uvicorn's loop or any other agent code.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Optional
from urllib.parse import urlparse

from agent.telemetry.sm_reader import SharedMemoryReader, frame_to_payload

LOG = logging.getLogger("pitbox.telemetry.sender")

# Heartbeat (idle) frame interval when AC is not running, seconds
IDLE_HEARTBEAT_SEC = 5.0

# Reconnect backoff bounds, seconds
RECONNECT_MIN = 2.0
RECONNECT_MAX = 30.0


def _http_to_ws(controller_url: str) -> str:
    """Convert http(s) controller base URL to ws(s) and append /ws/agent-telemetry."""
    u = urlparse(controller_url.rstrip("/"))
    scheme = "wss" if u.scheme == "https" else "ws"
    netloc = u.netloc or u.path  # tolerate bare host:port
    return f"{scheme}://{netloc}/ws/agent-telemetry"


class TelemetrySender:
    def __init__(self, controller_url: str, agent_id: str, token: str, rate_hz: float = 15.0) -> None:
        self.controller_url = controller_url
        self.agent_id = agent_id
        self.token = token
        self.rate_hz = max(1.0, min(60.0, float(rate_hz)))
        self.interval = 1.0 / self.rate_hz
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._reader = SharedMemoryReader()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._thread_main, daemon=True, name="telemetry-sender"
        )
        self._thread.start()
        LOG.info(
            "Telemetry sender started: %s -> %s (%.1f Hz)",
            self.agent_id, self.controller_url, self.rate_hz,
        )

    def stop(self) -> None:
        self._stop.set()
        try:
            self._reader.close()
        except Exception:
            pass

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_forever())
        except Exception as e:
            LOG.warning("Telemetry sender thread exited: %s", e)
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _run_forever(self) -> None:
        try:
            import websockets  # type: ignore
        except ImportError:
            LOG.error("websockets package not available; telemetry disabled "
                      "(install `websockets` in the agent environment)")
            return
        try:
            ws_version = getattr(websockets, "__version__", "?")
        except Exception:
            ws_version = "?"

        url = _http_to_ws(self.controller_url)
        LOG.info("Telemetry sender loop starting: agent=%s url=%s rate=%.1fHz websockets=%s",
                 self.agent_id, url, self.rate_hz, ws_version)
        backoff = RECONNECT_MIN
        attempt = 0
        while not self._stop.is_set():
            attempt += 1
            try:
                LOG.info("Telemetry WS connect attempt #%d -> %s", attempt, url)
                async with websockets.connect(
                    url,
                    additional_headers={
                        "X-Agent-Id": self.agent_id,
                        "X-Agent-Token": self.token,
                    },
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=8,
                ) as ws:
                    LOG.info("Telemetry WS CONNECTED: agent=%s url=%s (attempt #%d)",
                             self.agent_id, url, attempt)
                    backoff = RECONNECT_MIN
                    attempt = 0
                    await self._stream(ws)
                    LOG.info("Telemetry WS stream ended cleanly (will reconnect)")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # INFO not DEBUG so operators can see *why* a connect fails.
                LOG.info("Telemetry WS connect/stream error: %s: %s",
                         type(e).__name__, e)
            if self._stop.is_set():
                break
            wait = backoff
            backoff = min(RECONNECT_MAX, backoff * 1.5)
            LOG.info("Telemetry WS reconnect in %.1fs (next backoff %.1fs)", wait, backoff)
            t0 = time.monotonic()
            while not self._stop.is_set() and time.monotonic() - t0 < wait:
                await asyncio.sleep(0.25)

    async def _stream(self, ws) -> None:
        last_idle_sent = 0.0
        last_summary = time.monotonic()
        sent_live = 0
        sent_idle = 0
        while not self._stop.is_set():
            t0 = time.monotonic()
            data = self._reader.read()
            payload = frame_to_payload(data)
            payload["agent_id"] = self.agent_id
            payload["ts"] = time.time()

            if payload.get("available"):
                try:
                    await ws.send(json.dumps(payload, separators=(",", ":")))
                    sent_live += 1
                except Exception as e:
                    LOG.info("Telemetry WS send failed: %s: %s", type(e).__name__, e)
                    return  # outer loop reconnects
                elapsed = time.monotonic() - t0
                await asyncio.sleep(max(0.0, self.interval - elapsed))
            else:
                now = time.monotonic()
                if now - last_idle_sent >= IDLE_HEARTBEAT_SEC:
                    try:
                        await ws.send(json.dumps({
                            "agent_id": self.agent_id,
                            "ts": time.time(),
                            "available": False,
                        }, separators=(",", ":")))
                        sent_idle += 1
                    except Exception as e:
                        LOG.info("Telemetry WS heartbeat send failed: %s: %s", type(e).__name__, e)
                        return
                    last_idle_sent = now
                await asyncio.sleep(0.5)

            # Periodic summary so operators can confirm frames are flowing
            # without enabling DEBUG. Logged once every ~10s.
            now = time.monotonic()
            if now - last_summary >= 10.0:
                LOG.info("Telemetry: live=%d idle=%d frames in last %.1fs",
                         sent_live, sent_idle, now - last_summary)
                sent_live = 0
                sent_idle = 0
                last_summary = now


# Module-level convenience for parity with controller_heartbeat
_sender: Optional[TelemetrySender] = None


_ACTIVE: Optional["TelemetrySender"] = None


def get_active_status() -> dict:
    """Diagnostic snapshot of the running telemetry sender (or absence thereof).

    Surfaced via the agent's /version endpoint so the controller can tell at a
    glance, for each rig, whether the new telemetry code is even installed and
    whether its background thread is alive.
    """
    s = _ACTIVE
    try:
        import websockets  # type: ignore
        ws_present = True
        ws_version = getattr(websockets, "__version__", "?")
    except Exception:
        ws_present = False
        ws_version = None
    return {
        "started": s is not None,
        "thread_alive": bool(s and s._thread and s._thread.is_alive()),
        "controller_url": s.controller_url if s else None,
        "agent_id": s.agent_id if s else None,
        "rate_hz": s.rate_hz if s else None,
        "websockets_present": ws_present,
        "websockets_version": ws_version,
    }


def start_telemetry(controller_url: str, agent_id: str, token: str, rate_hz: float = 15.0) -> None:
    global _sender, _ACTIVE
    if _sender is not None:
        return
    _sender = TelemetrySender(controller_url, agent_id, token, rate_hz=rate_hz)
    _ACTIVE = _sender
    _sender.start()


def stop_telemetry() -> None:
    global _sender
    if _sender is not None:
        _sender.stop()
        _sender = None
