"""
Bearer token authentication for PitBox Agent.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from agent.config import get_config


# Security scheme
security = HTTPBearer()


async def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    """
    Verify bearer token against configured token.

    Args:
        credentials: HTTP Authorization header credentials

    Returns:
        Token string if valid

    Raises:
        HTTPException: 401 if token invalid
    """
    config = get_config()
    token = credentials.credentials
    if token == config.token:
        return token
    try:
        from agent.pairing import is_paired, get_token
        if is_paired() and token == get_token():
            return token
    except Exception:
        pass
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token"
    )
