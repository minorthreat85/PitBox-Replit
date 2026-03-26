"""
Agent authentication and registration: require X-Agent-Id / X-Agent-Token, track IP, log registration.
"""
import logging
from pathlib import Path
from typing import Optional

from fastapi import Header, HTTPException, Request

from controller.agent_registry import AgentRegistry, AgentRecord
from controller.agent_validation import ValidationPolicy

logger = logging.getLogger(__name__)

# Singleton registry and policy (configurable path via env)
_registry: Optional[AgentRegistry] = None
_policy: ValidationPolicy = ValidationPolicy(
    enforce_ip_allowlist=False,
    allow_ip_change=True,
    trust_x_forwarded_for=False,
)


def get_registry() -> AgentRegistry:
    """Return the global AgentRegistry instance."""
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry


def get_client_ip(request: Request) -> str:
    """Get client IP from request. Uses X-Forwarded-For only if policy.trust_x_forwarded_for is True."""
    if _policy.trust_x_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    client = request.client
    if client:
        return client.host
    return "0.0.0.0"


async def require_agent(
    request: Request,
    x_agent_id: Optional[str] = Header(None, alias="X-Agent-Id"),
    x_agent_token: Optional[str] = Header(None, alias="X-Agent-Token"),
) -> str:
    """
    Dependency: require agent headers, register/update IP, validate token.
    Returns agent_id for downstream routes.
    """
    agent_id = (x_agent_id or "").strip()
    token = (x_agent_token or "").strip()

    if not agent_id:
        raise HTTPException(status_code=401, detail="Missing X-Agent-Id header")
    if not token:
        raise HTTPException(status_code=401, detail="Missing X-Agent-Token header")

    ip = get_client_ip(request)
    registry = get_registry()
    existing = registry.get(agent_id)
    # Strict token validation before updating: existing agent must send same token
    if existing is not None and existing.token != token:
        raise HTTPException(status_code=401, detail="Invalid or changed token")

    record, is_new_registration, ip_changed = registry.register_or_update(agent_id, token, ip)

    if not _policy.allow_ip_change and ip_changed and not is_new_registration:
        raise HTTPException(status_code=403, detail="Agent IP change not allowed")

    if _policy.enforce_ip_allowlist and not is_new_registration:
        if ip != record.registered_ip:
            raise HTTPException(status_code=403, detail="IP not in allowlist")

    # Logging
    if is_new_registration:
        logger.info("[AGENT] %s registered from %s", agent_id, ip)
    elif ip_changed:
        logger.info("[AGENT] %s IP changed (now %s)", agent_id, ip)

    return agent_id
