"""Network constants shared by the timing engine and the server-config layer.

The AC dedicated server's UDP plugin emits telemetry to ``UDP_PLUGIN_ADDRESS``
and listens for admin commands on ``UDP_PLUGIN_LOCAL_PORT``. PitBox's native
timing engine binds to ``UDP_PLUGIN_PORT`` to receive that telemetry, and the
server-config layer auto-fills these values into each ``server_cfg.ini`` so
operators don't have to.
"""
from __future__ import annotations

TIMING_UDP_PLUGIN_HOST = "127.0.0.1"
TIMING_UDP_PLUGIN_PORT = 9996
TIMING_UDP_PLUGIN_LOCAL_PORT = 9999
TIMING_UDP_PLUGIN_ADDRESS = f"{TIMING_UDP_PLUGIN_HOST}:{TIMING_UDP_PLUGIN_PORT}"
