"""Bypass operator auth in FastAPI tests (TestClient is not loopback)."""
from __future__ import annotations

from fastapi import Request

from controller.main import app
from controller.operator_auth import require_operator


async def _override_require_operator(_request: Request) -> None:
    return None


def install_operator_auth_override() -> None:
    app.dependency_overrides[require_operator] = _override_require_operator


def clear_operator_auth_override() -> None:
    app.dependency_overrides.pop(require_operator, None)
