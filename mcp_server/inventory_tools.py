"""The four read-only MCP tools (brief Section 6), wired to the Django API
through DjangoClient. No tool here can create, modify, or delete anything --
every path below ends in a GET.
"""

from __future__ import annotations

import difflib
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from django_client import DjangoApiError, DjangoClient

DEFAULT_ORDER_HISTORY_DAYS = 90

# Item.STATUS_CHOICES in inventory/models.py, as display label <-> code.
# The agent and the tool schema both deal in the display labels ("Active",
# "For Parts", ...) -- the raw codes ("ACTIVE", "PARTS", ...) are a Django
# implementation detail that never needs to surface here.
STATUS_DISPLAY_TO_CODE = {
    "Active": "ACTIVE",
    "For Parts": "PARTS",
    "Pending Repair": "REPAIR",
    "Pending Review": "REVIEW",
}
STATUS_CODE_TO_DISPLAY = {code: label for label, code in STATUS_DISPLAY_TO_CODE.items()}
STATUS_DISPLAY_ORDER = list(STATUS_DISPLAY_TO_CODE)  # Active, For Parts, Pending Repair, Pending Review


class ToolError(Exception):
    """A deliberate "nothing matched" / "bad input" outcome.

    Raising this (rather than returning an empty/partial result) is what
    gives the agent the short, specific, actionable message the brief's
    error contract (Section 6) asks for -- FastMCP turns any exception
    raised inside a tool into an isError tool result carrying str(exc).
    """


def _status_display(code: str) -> str:
    return STATUS_CODE_TO_DISPLAY.get(code, code)


def _location_str(location: Optional[dict]) -> str:
    if not location:
        return "No location recorded"
    parts = [location.get("building"), location.get("aisle"), location.get("shelf"), location.get("bin")]
    joined = " - ".join(p for p in parts if p)
    return joined or "No location recorded"


async def _suggest_model_name(client: DjangoClient, query: str) -> Optional[str]:
    """Best-effort "did you mean <model>?" for the error contract's example
    ('No item found matching "XE43-GEN4" -- did you mean XE43-GEN3?').

    There's no dedicated API for this -- it widens the search with a
    shorter prefix of the query (so a typo'd/wrong trailing character
    still pulls in the real model as a candidate), then picks the closest
    match by name. Best-effort: returns None rather than guessing wildly.
    """
    prefix = query[:-1] if len(query) > 3 else query
    try:
        candidates = await client.list_items(q=prefix, limit=50)
    except DjangoApiError:
        return None
    names = sorted({c["model_name"] for c in candidates if c["model_name"].lower() != query.lower()})
    if not names:
        return None
    close = difflib.get_close_matches(query, names, n=1, cutoff=0.5)
    if close:
        return close[0]
    return names[0] if len(names) == 1 else None


def build_server(client: DjangoClient, public_hostname: str | None = None) -> FastMCP:
    # FastMCP auto-enables DNS-rebinding protection locked to localhost-only
    # Host headers whenever it's constructed without an explicit
    # transport_security override (mcp/server/fastmcp/server.py) -- that's
    # what was causing "Invalid Host header" / 421 for the real public
    # hostname, nothing to do with our own middleware or Cloudflare. Allow
    # that real hostname explicitly instead of disabling the protection.
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    allowed_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    if public_hostname:
        allowed_hosts += [public_hostname, f"{public_hostname}:*"]
        allowed_origins.append(f"https://{public_hostname}")

    mcp = FastMCP(
        "Inventory Desk",
        instructions=(
            "Read-only inventory lookups for a demo warehouse system: stock "
            "levels, item detail, and order/shipment history. No tool here "
            "ever creates, changes, cancels, or sends anything."
        ),
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        ),
    )

    @mcp.tool()
    async def search_items(
        query: str, status: Optional[str] = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Search items by a substring of their model name.

        Args:
            query: Substring to match against the item's model name (case-insensitive).
            status: Optional filter -- one of "Active", "For Parts", "Pending Repair",
                "Pending Review". Omit to search across all statuses.
            limit: Maximum number of lots to return (default 20).
        """
        status_code = None
        if status is not None:
            status_code = STATUS_DISPLAY_TO_CODE.get(status)
            if status_code is None:
                raise ToolError(
                    f'Unknown status "{status}" -- use one of: {", ".join(STATUS_DISPLAY_ORDER)}.'
                )
        try:
            items = await client.list_items(q=query, status=status_code, limit=limit)
        except DjangoApiError as exc:
            raise ToolError(f"Inventory lookup failed: {exc}") from exc

        if not items:
            suggestion = await _suggest_model_name(client, query)
            message = f'No item found matching "{query}"'
            if suggestion:
                message += f" -- did you mean {suggestion}?"
            raise ToolError(message)

        return [
            {
                "id": item["id"],
                "model_name": item["model_name"],
                "status": _status_display(item["status"]),
                "qty": item["quantity"],
                "location": _location_str(item.get("location")),
            }
            for item in items[:limit]
        ]

    @mcp.tool()
    async def get_item(item_id: int) -> dict[str, Any]:
        """Get full detail for one specific item lot by its numeric id.

        Args:
            item_id: The lot's numeric id, as returned by search_items.
        """
        try:
            item = await client.get_item(item_id)
        except DjangoApiError as exc:
            raise ToolError(f"Inventory lookup failed: {exc}") from exc
        if item is None:
            raise ToolError(f"No item found with id {item_id}.")
        return {
            "id": item["id"],
            "model_name": item["model_name"],
            "qty": item["quantity"],
            "status": _status_display(item["status"]),
            "location": _location_str(item.get("location")),
            "notes": item.get("notes") or "",
        }

    @mcp.tool()
    async def get_stock(model_name: str) -> dict[str, Any]:
        """Get total on-hand quantity for a model, broken down by status.

        Always returns both the total and the per-status breakdown (e.g.
        "10 total: 5 Active, 2 For Parts, 1 Pending Repair, 1 Pending
        Review") -- sums quantity across every lot of that model, since
        cross-lot totals aren't precomputed by the API.

        Args:
            model_name: The product model name/code, e.g. "XE43-GEN3".
        """
        try:
            items = await client.list_items(q=model_name)
        except DjangoApiError as exc:
            raise ToolError(f"Inventory lookup failed: {exc}") from exc

        exact = [i for i in items if i["model_name"].lower() == model_name.lower()]
        matches = exact or items

        if not matches:
            suggestion = await _suggest_model_name(client, model_name)
            message = f'No item found matching "{model_name}"'
            if suggestion:
                message += f" -- did you mean {suggestion}?"
            raise ToolError(message)

        breakdown: dict[str, int] = {}
        total = 0
        for item in matches:
            qty = item["quantity"]
            total += qty
            label = _status_display(item["status"])
            breakdown[label] = breakdown.get(label, 0) + qty

        ordered_breakdown = {label: breakdown[label] for label in STATUS_DISPLAY_ORDER if label in breakdown}
        summary_parts = ", ".join(f"{qty} {label}" for label, qty in ordered_breakdown.items())
        return {
            "model_name": matches[0]["model_name"],
            "total": total,
            "breakdown": ordered_breakdown,
            "summary": f"{total} total: {summary_parts}",
        }

    @mcp.tool()
    async def get_order_history(
        model_name: Optional[str] = None, days: int = DEFAULT_ORDER_HISTORY_DAYS
    ) -> dict[str, Any]:
        """Get orders that shipped a model within a recent window.

        Defaults to the last 90 days. Returns, per matching order: client
        name, order date, quantity of this model shipped on that order, and
        a running total across the window (oldest to newest).

        Args:
            model_name: Product model name/code to filter by. Omit for all recent orders.
            days: Lookback window in days (default 90). Pass 0 for all-time history.
        """
        lookback = None if days == 0 else days
        try:
            orders = await client.list_orders(item=model_name, days=lookback)
        except DjangoApiError as exc:
            raise ToolError(f"Order history lookup failed: {exc}") from exc

        orders_sorted = sorted(orders, key=lambda o: o["created_at"])

        rows = []
        running_total = 0
        for order in orders_sorted:
            if model_name:
                qty = sum(
                    line["quantity_shipped"]
                    for line in order["lines"]
                    if model_name.lower() in line["model_name"].lower()
                )
                if qty == 0:
                    continue
            else:
                qty = sum(line["quantity_shipped"] for line in order["lines"])
            running_total += qty
            rows.append(
                {
                    "order_id": order["id"],
                    "client": order["client_name"],
                    "date": order["created_at"],
                    "quantity_shipped": qty,
                    "running_total": running_total,
                }
            )

        if not rows:
            window = "all time" if lookback is None else f"the last {days} days"
            if model_name:
                raise ToolError(f'No orders have shipped any "{model_name}" in {window}.')
            raise ToolError(f"No orders were placed in {window}.")

        return {
            "model_name": model_name,
            "days": "all" if lookback is None else days,
            "orders": rows,
            "total_shipped": running_total,
        }

    return mcp
