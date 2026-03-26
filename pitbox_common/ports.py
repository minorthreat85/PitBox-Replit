"""Shared PitBox port constants. Single source of truth for all networking ports.

Discovery is UDP 9641; Agent HTTP is TCP 9631–9638. Controller HTTP is 9630. Enrollment broadcast is UDP 9640.
"""

CONTROLLER_HTTP_PORT = 9630
AGENT_PORT_START = 9631
AGENT_PORT_END = 9638
ENROLLMENT_UDP_PORT = 9640
# Controller listens on this for agent LAN discovery beacons; agents broadcast to it. Dedicated to avoid overlap with agent TCP ports.
DISCOVERY_UDP_PORT = 9641


def agent_port_for_sim(sim_number: int) -> int:
    """
    Sim1 -> 9631, Sim2 -> 9632, ..., Sim8 -> 9638.
    """
    port = AGENT_PORT_START + (sim_number - 1)
    if port < AGENT_PORT_START or port > AGENT_PORT_END:
        raise ValueError(f"Invalid sim number: {sim_number} (must be 1..8)")
    return port
