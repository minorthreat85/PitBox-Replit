"""PitBox native server admin/control layer.

This package wraps the AC dedicated server admin protocol (UDP plugin
commands such as chat, kick, next-session) and entry_list.ini
manipulation (grid reverse / swap) behind PitBox-native APIs. PitBox
remains the sole owner of the acServer.exe lifecycle (see
``controller.api_server_config_routes`` and ``controller.server_pool``)
and the only user-facing interface; nothing in this package spawns or
supervises the dedicated server itself.

The protocol/tooling here is informed by the upstream
``ac-websocket-server`` package (entries.py / grid.py for the file
manipulation, AC plugin protocol constants for the UDP commands), but
no code from that package is imported at runtime.
"""

from controller.server_control.adapter import (
    ServerControlAdapter,
    ServerControlError,
    get_adapter,
)

__all__ = [
    "ServerControlAdapter",
    "ServerControlError",
    "get_adapter",
]
