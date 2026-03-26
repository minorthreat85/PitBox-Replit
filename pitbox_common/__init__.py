"""Shared PitBox constants and paths (ports, controller/agent runtime paths)."""

from pitbox_common.version import __version__
from pitbox_common.ports import (
    AGENT_PORT_END,
    AGENT_PORT_START,
    CONTROLLER_HTTP_PORT,
    DISCOVERY_UDP_PORT,
    ENROLLMENT_UDP_PORT,
    agent_port_for_sim,
)
from pitbox_common.runtime_paths import (
    controller_config_path,
    controller_data_dir,
    controller_dir,
    controller_logs_dir,
)

__all__ = [
    "__version__",
    "AGENT_PORT_END",
    "AGENT_PORT_START",
    "CONTROLLER_HTTP_PORT",
    "DISCOVERY_UDP_PORT",
    "ENROLLMENT_UDP_PORT",
    "agent_port_for_sim",
    "controller_config_path",
    "controller_data_dir",
    "controller_dir",
    "controller_logs_dir",
]
