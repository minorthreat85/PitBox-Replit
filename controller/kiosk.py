"""
Kiosk mode: pairing (QR + HMAC), session management, per-agent display state.
Sessions TTL ~30 min. Sim display shows QR when idle; selection + status when paired.
"""
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from controller.config import get_config
from pitbox_common.safe_inputs import KIOSK_INSECURE_DEFAULT_SECRET_PHRASE

logger = logging.getLogger(__name__)

KIOSK_SESSION_TTL_SEC = 30 * 60  # 30 minutes


def _get_secret() -> bytes:
    """Kiosk HMAC secret from config or fallback (do not use fallback in production)."""
    cfg = get_config()
    raw = getattr(cfg, "kiosk_secret", None) or ""
    s = (raw or "").strip()
    if s:
        return s.encode("utf-8")
    # Fallback for dev only; production must set kiosk_secret (validated in load_config when kiosk_mode_enabled).
    return KIOSK_INSECURE_DEFAULT_SECRET_PHRASE.encode("utf-8")


def _sign(agent_id: str, nonce: str) -> str:
    """HMAC-SHA256(secret, agent_id + nonce) as hex."""
    payload = f"{agent_id}|{nonce}".encode("utf-8")
    sig = hmac.new(_get_secret(), payload, hashlib.sha256).hexdigest()
    return sig


def verify_token(agent_id: str, nonce: str, token: str) -> bool:
    """Verify token from QR (constant-time)."""
    if not agent_id or not nonce or not token:
        return False
    expected = _sign(agent_id.strip(), nonce.strip())
    return hmac.compare_digest(expected, token.strip())


def create_pair_data(agent_id: str) -> dict[str, Any]:
    """Create nonce + signed token for QR. Returns dict for sim display."""
    agent_id = (agent_id or "").strip()
    if not agent_id:
        return {}
    nonce = secrets.token_hex(16)
    token = _sign(agent_id, nonce)
    return {
        "agent_id": agent_id,
        "nonce": nonce,
        "token": token,
        "expires_in_sec": KIOSK_SESSION_TTL_SEC,
    }


@dataclass
class KioskSession:
    """One kiosk session tied to an agent."""
    session_id: str
    agent_id: str
    created_at: float
    expires_at: float


# In-memory: session_id -> KioskSession; agent_id -> list of session_ids (for lookup)
_sessions: dict[str, KioskSession] = {}
_agent_to_sessions: dict[str, list[str]] = {}


def _expire_old() -> None:
    """Remove expired sessions."""
    now = time.time()
    to_del = [sid for sid, s in _sessions.items() if s.expires_at < now]
    for sid in to_del:
        s = _sessions.pop(sid, None)
        if s and s.agent_id in _agent_to_sessions:
            _agent_to_sessions[s.agent_id] = [x for x in _agent_to_sessions[s.agent_id] if x != sid]
            if not _agent_to_sessions[s.agent_id]:
                del _agent_to_sessions[s.agent_id]


def create_session(agent_id: str) -> Optional[KioskSession]:
    """Create a new kiosk session for agent_id. Call after verify_token."""
    _expire_old()
    agent_id = (agent_id or "").strip()
    if not agent_id:
        return None
    session_id = secrets.token_urlsafe(32)
    now = time.time()
    s = KioskSession(
        session_id=session_id,
        agent_id=agent_id,
        created_at=now,
        expires_at=now + KIOSK_SESSION_TTL_SEC,
    )
    _sessions[session_id] = s
    _agent_to_sessions.setdefault(agent_id, []).append(session_id)
    logger.info("[kiosk] session created agent_id=%s session_id=%s", agent_id, session_id[:12])
    return s


def get_session(session_id: str) -> Optional[KioskSession]:
    """Return session if valid and not expired."""
    _expire_old()
    s = _sessions.get((session_id or "").strip())
    if s and s.expires_at >= time.time():
        return s
    return None


def get_session_for_agent(agent_id: str) -> Optional[KioskSession]:
    """Return any valid session for this agent (most recent)."""
    _expire_old()
    sids = _agent_to_sessions.get((agent_id or "").strip()) or []
    for sid in reversed(sids):
        s = _sessions.get(sid)
        if s and s.expires_at >= time.time():
            return s
    return None


def invalidate_session(session_id: str) -> None:
    """Remove session (e.g. on stop_session)."""
    s = _sessions.pop((session_id or "").strip(), None)
    if s and s.agent_id in _agent_to_sessions:
        _agent_to_sessions[s.agent_id] = [x for x in _agent_to_sessions[s.agent_id] if x != s.session_id]
        if not _agent_to_sessions[s.agent_id]:
            del _agent_to_sessions[s.agent_id]


# Per-agent kiosk display state (selection + status). Updated when agent apply responds.
@dataclass
class KioskAgentState:
    status: str  # idle | paired | configuring | launching | running | error
    selection: Optional[dict[str, Any]] = None
    applied: Optional[dict[str, Any]] = None
    warnings: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)


_kiosk_agent_state: dict[str, KioskAgentState] = {}


def get_kiosk_agent_state(agent_id: str) -> KioskAgentState:
    """Get or create kiosk state for agent. Canonical agent_id from config/resolve."""
    aid = (agent_id or "").strip()
    if aid not in _kiosk_agent_state:
        _kiosk_agent_state[aid] = KioskAgentState(status="idle")
    return _kiosk_agent_state[aid]


def set_kiosk_agent_state(
    agent_id: str,
    status: str,
    selection: Optional[dict] = None,
    applied: Optional[dict] = None,
    warnings: Optional[list] = None,
    errors: Optional[list] = None,
) -> None:
    """Update kiosk state for agent (after apply response)."""
    aid = (agent_id or "").strip()
    if not aid:
        return
    st = _kiosk_agent_state.get(aid) or KioskAgentState(status="idle")
    st.status = status
    if selection is not None:
        st.selection = selection
    if applied is not None:
        st.applied = applied
    if warnings is not None:
        st.warnings = warnings
    if errors is not None:
        st.errors = errors
    st.updated_at = time.time()
    _kiosk_agent_state[aid] = st
    # Keep controller assigned state in sync (sim display: "Current Selection (Assigned)")
    if selection is not None:
        set_assigned_state(aid, status=status, selection=selection)


# ---- Controller assigned state (sim display: assigned vs detected) ----
# Updated when kiosk/command or admin launch_online (or start) runs.
@dataclass
class ControllerAgentState:
    """Per-agent state for sim display: last assigned selection + status (from controller)."""
    assigned_selection: Optional[dict[str, Any]] = None
    assigned_status: str = "idle"
    updated_at: float = field(default_factory=time.time)


_controller_state: dict[str, ControllerAgentState] = {}


def get_controller_state(agent_id: str) -> ControllerAgentState:
    """Get controller state for agent (assigned selection + status)."""
    aid = (agent_id or "").strip()
    if aid not in _controller_state:
        _controller_state[aid] = ControllerAgentState()
    return _controller_state[aid]


def set_assigned_state(
    agent_id: str,
    status: str,
    selection: Optional[dict] = None,
) -> None:
    """Update assigned state (call after kiosk/command or admin launch_online/start)."""
    aid = (agent_id or "").strip()
    if not aid:
        return
    st = _controller_state.get(aid) or ControllerAgentState()
    st.assigned_status = status
    if selection is not None:
        st.assigned_selection = selection
    st.updated_at = time.time()
    _controller_state[aid] = st
