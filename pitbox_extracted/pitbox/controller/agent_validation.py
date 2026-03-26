"""
Validation policy for agent registration (IP allowlist, allow IP change, X-Forwarded-For).
"""
from dataclasses import dataclass


@dataclass
class ValidationPolicy:
    """Policy for agent request validation."""

    enforce_ip_allowlist: bool = False
    """If True, only allow requests from each agent's registered_ip."""

    allow_ip_change: bool = True
    """If True, allow agents to connect from a different IP (warn-only). If False, block on IP change."""

    trust_x_forwarded_for: bool = False
    """If True, use X-Forwarded-For header for client IP (e.g. behind reverse proxy). LAN-only: keep False."""
