"""End-to-end smoke test: starts the real server.py (uvicorn + FastMCP +
BearerAuthMiddleware) against the fake Django stand-in, then drives it with
the real MCP client SDK over actual HTTP. This is what the unit tests in
test_tools.py don't cover -- the ASGI/session-manager lifespan wiring and
the auth middleware actually rejecting/accepting requests.

Run with: python tests/smoke_test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import uvicorn

from fake_django_server import run as run_fake_django

DJANGO_PORT = 8999
MCP_PORT = 8199
AUTH_TOKEN = "test-secret"

os.environ["DJANGO_API_BASE_URL"] = f"http://127.0.0.1:{DJANGO_PORT}"
os.environ["DJANGO_API_TOKEN"] = "test-django-token"
os.environ["MCP_SERVER_AUTH_TOKEN"] = AUTH_TOKEN
os.environ["MCP_SERVER_PORT"] = str(MCP_PORT)

from django_client import DjangoClient  # noqa: E402
from inventory_tools import build_server  # noqa: E402
from server import BearerAuthMiddleware  # noqa: E402


def start_fake_django() -> None:
    server = run_fake_django(DJANGO_PORT)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def start_mcp_server() -> None:
    client = DjangoClient(base_url=os.environ["DJANGO_API_BASE_URL"], token=os.environ["DJANGO_API_TOKEN"])
    mcp = build_server(client)
    app = BearerAuthMiddleware(mcp.streamable_http_app(), AUTH_TOKEN)
    config = uvicorn.Config(app, host="127.0.0.1", port=MCP_PORT, log_level="warning")
    server = uvicorn.Server(config)

    def _run():
        asyncio.run(server.serve())

    threading.Thread(target=_run, daemon=True).start()


async def main() -> None:
    start_fake_django()
    start_mcp_server()
    time.sleep(1.0)  # let both servers finish binding

    url = f"http://127.0.0.1:{MCP_PORT}/mcp"

    # 1. No/bad bearer token -> the middleware should reject before MCP
    #    protocol logic ever runs.
    async with httpx.AsyncClient() as http:
        resp = await http.post(url, headers={"Accept": "application/json, text/event-stream"}, json={})
        assert resp.status_code == 401, f"expected 401 without auth, got {resp.status_code}"
        print("PASS: unauthenticated request rejected with 401")

    # 2. Correct bearer token -> real MCP session + tool call against the
    #    fake Django server.
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = {t.name for t in tools.tools}
            expected = {"search_items", "get_item", "get_stock", "get_order_history"}
            assert tool_names == expected, f"tool set mismatch: {tool_names}"
            print(f"PASS: all 4 tools registered: {sorted(tool_names)}")

            result = await session.call_tool("get_stock", {"model_name": "XE43-GEN3"})
            assert not result.isError, f"get_stock errored: {result}"
            payload = result.structuredContent or {}
            print(f"get_stock(XE43-GEN3) -> {payload}")
            assert payload.get("total") == 7, payload
            assert payload.get("summary") == "7 total: 5 Active, 2 For Parts", payload
            print("PASS: get_stock totals + breaks down real HTTP data through the live Django stand-in")

            result = await session.call_tool("get_order_history", {"model_name": "XE43-GEN3"})
            assert not result.isError, f"get_order_history errored: {result}"
            payload = result.structuredContent or {}
            print(f"get_order_history(XE43-GEN3) -> {payload}")
            assert payload.get("total_shipped") == 3, payload
            print("PASS: get_order_history works end-to-end over real HTTP")

            result = await session.call_tool("get_item", {"item_id": 9999})
            assert result.isError, "expected get_item(9999) to be a tool error"
            text = result.content[0].text if result.content else ""
            print(f"get_item(9999) error -> {text}")
            assert "No item found with id 9999" in text, text
            print("PASS: not-found error message relayed through the real tool-call error path")

    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
