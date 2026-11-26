"""Shared local-HTTP security guards (CSRF, Host/Origin, Content-Type, body size cap, token injection)."""

from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler
from typing import Optional
from urllib.parse import urlparse

MAX_REQUEST_BYTES = 64 * 1024 * 1024
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def new_session_token() -> str:
    return secrets.token_urlsafe(24)


def inject_token_meta(html_bytes: bytes, token: str) -> bytes:
    """Inject per-server CSRF token <meta> into HTML <head> without modifying files on disk."""
    meta = f'<meta name="inktwin-token" content="{token}" />'.encode("utf-8")
    idx = html_bytes.find(b"</head>")
    if idx >= 0:
        return html_bytes[:idx] + meta + b"\n" + html_bytes[idx:]
    return meta + b"\n" + html_bytes


def _host_only(host_hdr: str) -> str:
    h = (host_hdr or "").strip().lower()
    if h.startswith("["):
        end = h.find("]")
        return h[: end + 1] if end > 0 else h
    return h.split(":", 1)[0]


def validate_http_request(
    handler: BaseHTTPRequestHandler,
    session_token: str,
    server_port: int,
    *,
    is_post: bool = False,
) -> Optional[int]:
    """Validate Host, Origin/Referer, Content-Type, CSRF token, and Content-Length. Returns None if ok, else HTTP status."""
    host_hdr = handler.headers.get("Host") or ""
    if host_hdr and _host_only(host_hdr) not in _LOCAL_HOSTS:
        _send_err(handler, 403, "Forbidden Host header")
        return 403

    origin = handler.headers.get("Origin")
    if origin:
        parsed = urlparse(origin)
        ohost = (parsed.hostname or "").lower()
        if ohost not in _LOCAL_HOSTS or (parsed.port is not None and server_port and parsed.port != server_port):
            _send_err(handler, 403, "Cross-origin request rejected")
            return 403

    referer = handler.headers.get("Referer")
    if referer and not origin:
        rparsed = urlparse(referer)
        rhost = (rparsed.hostname or "").lower()
        if rhost not in _LOCAL_HOSTS or (rparsed.port is not None and server_port and rparsed.port != server_port):
            _send_err(handler, 403, "Cross-origin referer rejected")
            return 403

    if is_post:
        ctype = (handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype and ctype != "application/json":
            _send_err(handler, 415, "Content-Type must be application/json")
            return 415
        if (origin or referer) and (ctype != "application/json" or handler.headers.get("X-InkTwin-Token") != session_token):
            _send_err(handler, 403, "Missing or invalid X-InkTwin-Token or Content-Type")
            return 403
        try:
            length = int(handler.headers.get("Content-Length", "0") or "0")
        except ValueError:
            _send_err(handler, 400, "Invalid Content-Length")
            return 400
        if length < 0 or length > MAX_REQUEST_BYTES:
            _send_err(handler, 413, "Request payload too large")
            return 413
    return None


def _send_err(handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
    data = json.dumps({"ok": False, "error": message}, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)
