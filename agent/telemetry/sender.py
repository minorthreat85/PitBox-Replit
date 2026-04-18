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
            LOG.error("websockets package not available; telemetry disabled")
            return

        url = _http_to_ws(self.controller_url)
        backoff = RECONNECT_MIN
        while not self._stop.is_set():
            try:
                LOG.debug("Connecting telemetry WS: %s", url)
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
                    LOG.info("Telemetry WS connected: %s", url)
                    backoff = RECONNECT_MIN
                    await self._stream(ws)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                LOG.debug("Telemetry WS connect/stream error: %s", e)
            if self._stop.is_set():
                break
            # Wait before reconnect, with cooperative stop
            wait = backoff
            backoff = min(RECONNECT_MAX, backoff * 1.5)
            t0 = time.monotonic()
            while not self._stop.is_set() and time.monotonic() - t0 < wait:
                await asyncio.sleep(0.25)

    async def _stream(self, ws) -> None:
        last_idle_sent = 0.0
        while not self._stop.is_set():
            t0 = time.monotonic()
            data = self._reader.read()
            payload = frame_to_payload(data)
            payload["agent_id"] = self.agent_id
            payload["ts"] = time.time()

            if payload.get("available"):
                try:
                    await ws.send(json.dumps(payload, separators=(",", ":")))
                except Exception:
                    return  # outer loop reconnects
                # Sleep to next tick at configured rate
                elapsed = time.monotonic() - t0
                await asyncio.sleep(max(0.0, self.interval - elapsed))
            else:
                # AC not running -> send idle heartbeat at low cadence
                now = time.monotonic()
                if now - last_idle_sent >= IDLE_HEARTBEAT_SEC:
                    try:
                        await ws.send(json.dumps({
                            "agent_id": self.agent_id,
                            "ts": time.time(),
                            "available": False,
                        }, separators=(",", ":")))
                    except Exception:
                        return
                    last_idle_sent = now
                await asyncio.sleep(0.5)


# Module-level convenience for parity with controller_heartbeat
_sender: Optional[TelemetrySender] = None


def start_telemetry(controller_url: str, agent_id: str, token: str, rate_hz: float = 15.0) -> None:
    global _sender
    if _sender is not None:
        return
    _sender = TelemetrySender(controller_url, agent_id, token, rate_hz=rate_hz)
    _sender.start()


def stop_telemetry() -> None:
    global _sender
    if _sender is not None:
        _sender.stop()
        _sender = None
