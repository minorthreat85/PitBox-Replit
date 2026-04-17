"""
Transparent reverse proxy for the Fastest Lap booking admin panel.

Two flavours:
  1. Legacy path-prefixed proxy mounted on the main controller app at /proxy/booking/...
     Kept for backwards compatibility but the booking SPA's client-side router
     misbehaves under a path prefix (it reads window.location.pathname directly).
  2. Root-preserving proxy exposed on a SEPARATE port (BOOKING_PROXY_PORT, 9650).
     The booking SPA sees its own paths unchanged (e.g. /admin/bookings) so the
     router works correctly. Operator-auth gating still applies (the
     pitbox_employee cookie is scoped by hostname, not port, so it flows through
     to this listener automatically).
"""
from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import Response, RedirectResponse

from controller.operator_auth import (
    EMPLOYEE_COOKIE,
    get_employee_password_optional,
)

# Port for the root-preserving booking proxy listener (separate origin from the
# main controller so the booking SPA's router sees clean paths).
BOOKING_PROXY_PORT = 9650

router = APIRouter()


def _operator_gate(request: Request) -> Response | None:
    """If operator auth is configured and the caller lacks a session cookie,
    return a redirect to the login page (preserving /bookings as `next`).
    Return None when access is permitted."""
    if get_employee_password_optional() is None:
        return None
    if request.cookies.get(EMPLOYEE_COOKIE) == "1":
        return None
    return RedirectResponse(url="/employee/login?next=/bookings", status_code=302)

TARGET_ORIGIN = "https://booking.myfastestlap.com"
PROXY_PREFIX = "/proxy/booking"

_DROP_RESPONSE_HEADERS = {
    "x-frame-options",
    "content-security-policy",
    "content-security-policy-report-only",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "upgrade",
    "strict-transport-security",
    "content-encoding",  # httpx decompresses automatically; don't re-declare
    "content-length",    # will be recalculated by FastAPI
}

_DROP_REQUEST_HEADERS = {
    "host",
    "origin",
    "referer",
    "connection",
    "upgrade-insecure-requests",
    "accept-encoding",   # let httpx handle compression negotiation
}

_TEXT_MIME = ("text/html", "text/css", "application/javascript", "text/javascript")


def _rewrite_body(content: str, content_type: str) -> str:
    """Rewrite URLs in HTML/CSS/JS response bodies."""
    if "text/html" in content_type:
        # Absolute URLs to the booking domain in attributes
        content = re.sub(
            r'(href|src|action)="(https?://booking\.myfastestlap\.com)(/[^"]*)"',
            lambda m: f'{m.group(1)}="{PROXY_PREFIX}{m.group(3)}"',
            content,
        )
        # Scheme-relative URLs
        content = re.sub(
            r'(href|src|action)="(//booking\.myfastestlap\.com)(/[^"]*)"',
            lambda m: f'{m.group(1)}="{PROXY_PREFIX}{m.group(3)}"',
            content,
        )
        # Root-relative URLs (avoid double-prefixing already-proxied paths)
        content = re.sub(
            r'(href|src|action)="(/(?!proxy/booking)[^"]*)"',
            lambda m: f'{m.group(1)}="{PROXY_PREFIX}{m.group(2)}"',
            content,
        )
        # JS string literals referencing the booking domain
        content = content.replace(
            f'"{TARGET_ORIGIN}/',
            f'"{PROXY_PREFIX}/',
        )
        content = content.replace(
            f"'{TARGET_ORIGIN}/",
            f"'{PROXY_PREFIX}/",
        )
        # Inject <base> tag so any remaining relative URLs resolve correctly
        if "<head" in content:
            base_tag = f'<base href="{PROXY_PREFIX}/">'
            # Insert after first <head ...> tag
            content = re.sub(
                r"(<head(?:\s[^>]*)?>)",
                lambda m: m.group(1) + base_tag,
                content,
                count=1,
            )

    elif "text/css" in content_type:
        content = re.sub(
            r'url\(["\']?(https?://booking\.myfastestlap\.com)(/[^"\')\s]*)["\']?\)',
            lambda m: f'url("{PROXY_PREFIX}{m.group(2)}")',
            content,
        )
        content = re.sub(
            r'url\(["\']?(/(?!proxy/booking)[^"\')\s]*)["\']?\)',
            lambda m: f'url("{PROXY_PREFIX}{m.group(1)}")',
            content,
        )

    return content


async def _proxy_request(request: Request, path: str) -> Response:
    upstream_url = f"{TARGET_ORIGIN}/{path}"
    qs = request.url.query
    if qs:
        upstream_url += f"?{qs}"

    # Forward headers, replacing host
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _DROP_REQUEST_HEADERS
    }
    fwd_headers["host"] = "booking.myfastestlap.com"

    cookies: dict[str, str] = dict(request.cookies)
    body = await request.body()

    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=30.0,
            verify=True,
        ) as client:
            upstream_resp = await client.request(
                method=request.method,
                url=upstream_url,
                headers=fwd_headers,
                cookies=cookies,
                content=body if body else None,
            )
    except Exception as exc:
        return Response(
            content=f"Proxy error connecting to booking service: {exc}",
            status_code=502,
            media_type="text/plain",
        )

    # Rewrite redirect locations
    if upstream_resp.status_code in (301, 302, 303, 307, 308):
        location = upstream_resp.headers.get("location", "/")
        if location.startswith(TARGET_ORIGIN):
            location = PROXY_PREFIX + location[len(TARGET_ORIGIN):]
        elif location.startswith("/") and not location.startswith(PROXY_PREFIX):
            location = PROXY_PREFIX + location
        return RedirectResponse(url=location, status_code=upstream_resp.status_code)

    # Build clean response headers
    resp_headers: dict[str, str] = {}
    set_cookies: list[str] = []

    for key, val in upstream_resp.headers.multi_items():
        kl = key.lower()
        if kl in _DROP_RESPONSE_HEADERS:
            continue
        if kl == "set-cookie":
            cookie = re.sub(r";\s*Secure", "", val, flags=re.IGNORECASE)
            cookie = re.sub(r";\s*SameSite=\w+", "", cookie, flags=re.IGNORECASE)
            cookie = re.sub(r";\s*Domain=[^;]+", "", cookie, flags=re.IGNORECASE)
            set_cookies.append(cookie)
            continue
        resp_headers[key] = val

    content_type = upstream_resp.headers.get("content-type", "")
    # httpx auto-decompresses, so .content is always plain bytes
    resp_body = upstream_resp.content

    # Rewrite text bodies
    if any(m in content_type for m in _TEXT_MIME):
        try:
            text = resp_body.decode("utf-8", errors="replace")
            text = _rewrite_body(text, content_type)
            resp_body = text.encode("utf-8")
        except Exception:
            pass

    response = Response(
        content=resp_body,
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        media_type=content_type.split(";")[0].strip() or None,
    )
    for cookie_str in set_cookies:
        response.headers.append("set-cookie", cookie_str)

    return response


@router.get(PROXY_PREFIX)
@router.post(PROXY_PREFIX)
async def proxy_root(request: Request) -> Response:
    gate = _operator_gate(request)
    if gate is not None:
        return gate
    return await _proxy_request(request, "")


@router.api_route(
    PROXY_PREFIX + "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_path(request: Request, path: str) -> Response:
    gate = _operator_gate(request)
    if gate is not None:
        return gate
    try:
        return await _proxy_request(request, path)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return Response(
            content=f"Proxy error: {type(exc).__name__}: {exc}",
            status_code=502,
            media_type="text/plain",
        )


# ---------------------------------------------------------------------------
# Root-preserving booking proxy (separate listener on BOOKING_PROXY_PORT)
# ---------------------------------------------------------------------------


async def _proxy_request_root(request: Request, path: str) -> Response:
    """Same as _proxy_request but rewrites Location/cookies for a root-mounted listener
    (no PROXY_PREFIX). HTML/CSS/JS bodies are NOT rewritten because the SPA lives on
    its own origin and uses its real paths."""
    upstream_url = f"{TARGET_ORIGIN}/{path}"
    qs = request.url.query
    if qs:
        upstream_url += f"?{qs}"

    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _DROP_REQUEST_HEADERS
    }
    fwd_headers["host"] = "booking.myfastestlap.com"

    cookies: dict[str, str] = {k: v for k, v in request.cookies.items() if k != EMPLOYEE_COOKIE}
    body = await request.body()

    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=30.0,
            verify=True,
        ) as client:
            upstream_resp = await client.request(
                method=request.method,
                url=upstream_url,
                headers=fwd_headers,
                cookies=cookies,
                content=body if body else None,
            )
    except Exception as exc:
        return Response(
            content=f"Proxy error connecting to booking service: {exc}",
            status_code=502,
            media_type="text/plain",
        )

    # Strip upstream origin from redirect Location, keep root-relative.
    if upstream_resp.status_code in (301, 302, 303, 307, 308):
        location = upstream_resp.headers.get("location", "/")
        if location.startswith(TARGET_ORIGIN):
            location = location[len(TARGET_ORIGIN):] or "/"
        return RedirectResponse(url=location, status_code=upstream_resp.status_code)

    resp_headers: dict[str, str] = {}
    set_cookies: list[str] = []

    for key, val in upstream_resp.headers.multi_items():
        kl = key.lower()
        if kl in _DROP_RESPONSE_HEADERS:
            continue
        if kl == "set-cookie":
            cookie = re.sub(r";\s*Secure", "", val, flags=re.IGNORECASE)
            cookie = re.sub(r";\s*SameSite=\w+", "", cookie, flags=re.IGNORECASE)
            cookie = re.sub(r";\s*Domain=[^;]+", "", cookie, flags=re.IGNORECASE)
            set_cookies.append(cookie)
            continue
        resp_headers[key] = val

    content_type = upstream_resp.headers.get("content-type", "")
    response = Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        media_type=content_type.split(";")[0].strip() or None,
    )
    for cookie_str in set_cookies:
        response.headers.append("set-cookie", cookie_str)
    return response


def make_root_proxy_app() -> FastAPI:
    """Build a standalone FastAPI app that proxies the booking admin at the root,
    preserving all upstream paths so the SPA's client-side router works correctly.

    Operator-auth gating uses the same EMPLOYEE_COOKIE the main controller sets.
    Browsers scope cookies by hostname (not port), so when the operator signs in
    via the main controller, the cookie is automatically sent to this listener too.
    """
    app = FastAPI(title="PitBox Booking Proxy", docs_url=None, redoc_url=None, openapi_url=None)

    async def _gate_or_proxy(request: Request, path: str) -> Response:
        # Block unauthenticated access whenever operator auth is configured.
        if get_employee_password_optional() is not None and request.cookies.get(EMPLOYEE_COOKIE) != "1":
            return Response(
                content="Unauthorized: sign in via PitBox Bookings page first.",
                status_code=401,
                media_type="text/plain",
            )
        try:
            return await _proxy_request_root(request, path)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return Response(
                content=f"Proxy error: {type(exc).__name__}: {exc}",
                status_code=502,
                media_type="text/plain",
            )

    @app.api_route("/", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    async def _root(request: Request) -> Response:
        return await _gate_or_proxy(request, "")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    async def _any(request: Request, path: str) -> Response:
        return await _gate_or_proxy(request, path)

    return app


def start_root_proxy_thread(host: str = "0.0.0.0", port: int = BOOKING_PROXY_PORT) -> None:
    """Start the root-preserving booking proxy on a daemon thread.

    Safe to call once at controller startup. Failures are logged but don't crash
    the main controller — the booking iframe will simply show a connection error.
    """
    import logging
    import threading

    log = logging.getLogger(__name__)

    def _run() -> None:
        try:
            import uvicorn
            uvicorn.run(
                make_root_proxy_app(),
                host=host,
                port=port,
                log_config=None,
                access_log=False,
            )
        except Exception as exc:
            log.exception("Booking root-proxy listener failed on %s:%s — %s", host, port, exc)

    t = threading.Thread(target=_run, name="booking-root-proxy", daemon=True)
    t.start()
    log.info("Booking root-proxy listener started on %s:%s", host, port)
