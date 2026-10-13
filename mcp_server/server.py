"""Entrypoint: builds the DjangoClient + FastMCP server from environment
config and serves it over streamable HTTP.

Env vars (see .env.example):
    DJANGO_API_BASE_URL    Base URL of the Django IMS mockup, e.g.
                           http://web:8000 inside the demo docker-compose
                           network, or the tunnel's internal address.
    DJANGO_API_TOKEN       The mcp-agent account's token (see
                           `python manage.py create_mcp_api_user` in the
                           Django project) -- sent as `Authorization: Token
                           <value>` on every request.
    MCP_SERVER_HOST        Host to bind (default 0.0.0.0).
    MCP_SERVER_PORT        Port to bind (default 8100).
    MCP_SERVER_AUTH_TOKEN  Optional shared secret, checked as
                           `Authorization: Bearer <value>`. Confirmed NOT
                           usable with Superhuman Go's custom-MCP connector
                           (brief Section 11) -- it sends no such header.
                           Kept for other MCP clients that do support
                           custom headers. Leave unset to disable.
    MCP_SERVER_SECRET_PATH The actual gate for Superhuman Go: an unguessable
                           URL path segment (brief Section 9's fallback).
                           Paste the MCP URL into Agent Builder as
                           https://<host>/<this value>/ -- anything else
                           404s. Leave unset to disable (not recommended
                           once this is public).
    MCP_PUBLIC_HOSTNAME    The public hostname this server is reached at
                           (e.g. mcp.meridiansupply.example), WITHOUT scheme or
                           path. Required for any real request to get past
                           FastMCP's own built-in DNS-rebinding protection,
                           which otherwise only allow-lists localhost Host
                           headers -- this is unrelated to the Cloudflare
                           tunnel or either middleware above; it lives
                           inside the MCP SDK itself.
"""

from __future__ import annotations

import os
import sys

import uvicorn

from django_client import DjangoClient
from inventory_tools import build_server


class BearerAuthMiddleware:
    """Minimal ASGI middleware: rejects any HTTP request whose Authorization
    header doesn't match the configured shared secret. A no-op if no token
    is configured. Deliberately not using starlette's auth/OAuth machinery --
    this is one static demo secret, not a user-identity system."""

    def __init__(self, app, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"authorization", b"").decode("latin-1")
        if provided != self._expected:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"Unauthorized"})
            return
        await self._app(scope, receive, send)


class SecretPathMiddleware:
    """Gates access by an unguessable URL path segment instead of an
    Authorization header. Confirmed necessary (brief Section 11 resolved):
    Superhuman Go's custom-MCP connector (user-agent "Coda-Server-Fetcher")
    sends no bearer token, API key, or any custom header we can check --
    captured its actual outbound request headers and there's nothing to
    verify. Falling back to brief Section 9's plan: the MCP URL configured
    in Agent Builder must include this exact path segment, e.g.
    https://mcp.example.com/<secret>/ -- any other path (including a bare
    `/`) 404s before ever reaching the MCP app. This is a thin obscurity
    gate, not real authentication -- pair it with a Cloudflare WAF rule on
    this hostname if the URL ever needs to be shared more broadly."""

    def __init__(self, app, secret_path: str) -> None:
        self._app = app
        self._secret = secret_path.strip("/")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = scope.get("path", "/").strip("/")
        if path != self._secret:
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"Not Found"})
            return
        # Rewrite so the wrapped MCP app sees the path it actually expects.
        # This MCP SDK mounts streamable-HTTP at /mcp, not / (confirmed by
        # tests/smoke_test.py's working client URL) -- rewriting to "/" here
        # was silently 404ing one layer deeper, inside FastMCP's own router.
        scope = dict(scope)
        scope["path"] = "/mcp"
        scope["raw_path"] = b"/mcp"
        await self._app(scope, receive, send)


def main() -> None:
    base_url = os.environ.get("DJANGO_API_BASE_URL")
    token = os.environ.get("DJANGO_API_TOKEN")
    if not base_url or not token:
        sys.exit(
            "DJANGO_API_BASE_URL and DJANGO_API_TOKEN must both be set "
            "(see .env.example) -- refusing to start without them."
        )

    host = os.environ.get("MCP_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_SERVER_PORT", "8100"))
    auth_token = os.environ.get("MCP_SERVER_AUTH_TOKEN")
    secret_path = os.environ.get("MCP_SERVER_SECRET_PATH")
    public_hostname = os.environ.get("MCP_PUBLIC_HOSTNAME")

    client = DjangoClient(base_url=base_url, token=token)
    mcp = build_server(client, public_hostname=public_hostname)
    app = mcp.streamable_http_app()
    if auth_token:
        app = BearerAuthMiddleware(app, auth_token)
    if secret_path:
        app = SecretPathMiddleware(app, secret_path)

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
