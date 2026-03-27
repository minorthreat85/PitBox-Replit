"""
Operator authentication for dangerous controller API routes.

When employee_password is unset, all LAN clients are allowed (open access).
When employee_password is set, a valid session cookie is required from every client.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from controller.config import get_config

EMPLOYEE_COOKIE = "pitbox_employee"

# Short messages for operator-protected APIs (UI may add a Sign in link).
MSG_OPERATOR_SIGN_IN = "Operator login required to control this rig. Sign in."
MSG_OPERATOR_REMOTE_DISABLED = (
    "Operator control from this PC is not enabled. Use the controller PC, or set employee_password in config."
)


def sanitize_employee_login_next(raw: str | None) -> str:
    """
    Safe in-app path for post-login redirect. Rejects absolute URLs, scheme-relative paths,
    newlines, and login loops.
    """
    if not raw or not isinstance(raw, str):
        return "/"
    s = raw.strip()
    if not s.startswith("/"):
        return "/"
    if s.startswith("//"):
        return "/"
    if len(s) > 2048:
        return "/"
    if any(c in s for c in ("\n", "\r", "\x00")):
        return "/"
    if "://" in s:
        return "/"
    low = s.lower()
    if low.startswith("/employee/login"):
        return "/"
    return s or "/"


def get_employee_password_optional() -> str | None:
    """Return configured employee password, or None if disabled / empty."""
    return (get_config().employee_password or "").strip() or None


def is_localhost_request(request: Request) -> bool:
    """True for loopback only (IPv4/IPv6 localhost)."""
    host = (request.client.host if request.client else "") or ""
    host = host.strip().lower()
    return host in ("127.0.0.1", "::1", "localhost")


async def require_operator(request: Request) -> None:
    """
    Gate operator APIs.

    - No employee_password: allow all clients (open LAN access — no login required).
    - employee_password set: require pitbox_employee session cookie from every client.
    """
    pw = get_employee_password_optional()
    if pw is None:
        return  # No password configured — open to all LAN clients
    if request.cookies.get(EMPLOYEE_COOKIE) != "1":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=MSG_OPERATOR_SIGN_IN,
        )


async def require_operator_if_password_configured(request: Request) -> None:
    """
    When employee_password is set, same checks as require_operator.
    When employee_password is unset, allow any client (legacy LAN pit UI without operator login).
    """
    if get_employee_password_optional() is not None:
        await require_operator(request)


async def require_employee(request: Request) -> None:
    """Dependency: Employee Control features (e.g. hotkey). Disabled if no employee_password."""
    if get_employee_password_optional() is None:
        raise HTTPException(status_code=403, detail="Employee Control is disabled")
    if request.cookies.get(EMPLOYEE_COOKIE) != "1":
        raise HTTPException(status_code=401, detail="Employee login required")
