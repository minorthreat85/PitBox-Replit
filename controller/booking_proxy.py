"""
Transparent reverse proxy for the Fastest Lap booking admin panel.

Routes /proxy/booking/{path} → https://booking.myfastestlap.com/{path}

- Strips X-Frame-Options and Content-Security-Policy so the page renders inside PitBox.
- Rewrites absolute and root-relative URLs in HTML/CSS to stay inside the proxy path.
- Forwards cookies bidirectionally so login sessions are preserved.
"""
from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response, RedirectResponse

router = APIRouter()

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
    return await _proxy_request(request, "")


@router.api_route(
    PROXY_PREFIX + "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_path(request: Request, path: str) -> Response:
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
