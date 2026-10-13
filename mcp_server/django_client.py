"""Thin async HTTP client for the Django IMS mockup's read-only API.

This is the *only* thing in this server that talks to Django -- every tool
in inventory_tools.py goes through here rather than building its own
requests. Per the brief (Section 4): the MCP server has no business logic
of its own beyond reshaping inputs/outputs, and nothing it calls ever
writes. Every method below is a GET, full stop.
"""

from __future__ import annotations

import httpx

# Safety cap on how many paginated results a single tool call will walk
# through. The demo dataset is 40-80 items (Section 5 of the brief), so this
# is never reached in practice -- it just stops a single tool call from
# paging forever if `days=0` / an unfiltered query ever returns far more
# than expected.
_MAX_PAGES_TO_FOLLOW = 20


class DjangoApiError(Exception):
    """The Django API responded with something other than 2xx/404."""


class DjangoClient:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Token {token}"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "DjangoClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def list_items(
        self,
        q: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """GET /api/items/, following pagination, optionally stopping once
        `limit` results have been collected (the API's own page size is a
        separate, larger number -- this just avoids walking every page when
        a caller only wanted a handful of rows)."""
        params: dict[str, str] = {}
        if q:
            params["q"] = q
        if status:
            params["status"] = status
        return await self._collect("/api/items/", params, limit=limit)

    async def get_item(self, item_id: int) -> dict | None:
        """GET /api/items/<id>/. Returns None on 404 rather than raising --
        "not found" is an expected, ordinary outcome here, not an error."""
        response = await self._client.get(f"/api/items/{item_id}/")
        if response.status_code == 404:
            return None
        self._raise_for_status(response)
        return response.json()

    async def list_orders(self, item: str | None = None, days: int | None = None) -> list[dict]:
        """GET /api/orders/, following pagination. `days=None` means
        unbounded history (maps to the Django endpoint's days=all)."""
        params: dict[str, str] = {}
        if item:
            params["item"] = item
        params["days"] = "all" if days is None else str(days)
        return await self._collect("/api/orders/", params, limit=None)

    async def _collect(self, path: str, params: dict[str, str], limit: int | None) -> list[dict]:
        results: list[dict] = []
        url: str | None = path
        next_params: dict[str, str] | None = params
        for _ in range(_MAX_PAGES_TO_FOLLOW):
            if url is None:
                break
            response = await self._client.get(url, params=next_params)
            self._raise_for_status(response)
            payload = response.json()
            # DRF's PageNumberPagination wraps results in {"results": [...]};
            # tolerate a bare list too in case pagination is ever disabled.
            page = payload.get("results", payload) if isinstance(payload, dict) else payload
            results.extend(page)
            if limit is not None and len(results) >= limit:
                return results[:limit]
            url = payload.get("next") if isinstance(payload, dict) else None
            # `next` is already a full absolute URL with its own query
            # string -- don't re-send the original params alongside it.
            next_params = None
        return results

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise DjangoApiError(
                f"{response.request.method} {response.request.url} -> {response.status_code}: "
                f"{response.text[:200]}"
            )
