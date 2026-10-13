"""Tests for the four MCP tools, against a fake DjangoClient (no network,
no real Django instance) -- these exercise the MCP-layer logic the brief
calls out as genuinely new (Section 6): the get_stock status-breakdown
summation, the get_order_history running total, and the error contract's
short/specific/actionable messages.

Run with: pytest tests/test_tools.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp.exceptions import ToolError as McpToolError

from inventory_tools import build_server

NOW = datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class FakeDjangoClient:
    """Stands in for DjangoClient: same method shapes, in-memory data,
    no HTTP at all. Mirrors the brief's seed data spec (Section 5) --
    one model split across lots/statuses, one model at zero on-hand with
    shipment history, and no model that confusingly collides with another.
    """

    def __init__(self) -> None:
        self.items = [
            {"id": 1, "model_name": "XE43-GEN3", "quantity": 5, "status": "ACTIVE",
             "location": {"building": "Warehouse A", "aisle": "1", "shelf": "", "bin": ""}, "notes": ""},
            {"id": 2, "model_name": "XE43-GEN3", "quantity": 2, "status": "PARTS",
             "location": {"building": "Warehouse A", "aisle": "2", "shelf": "", "bin": ""}, "notes": "Needs antenna"},
            {"id": 3, "model_name": "XE43-GEN3", "quantity": 1, "status": "REPAIR",
             "location": None, "notes": ""},
            {"id": 4, "model_name": "XE43-GEN3", "quantity": 1, "status": "REVIEW",
             "location": None, "notes": ""},
            {"id": 5, "model_name": "ANT-900", "quantity": 0, "status": "ACTIVE",
             "location": {"building": "Warehouse A", "aisle": "3", "shelf": "", "bin": ""}, "notes": ""},
        ]
        self.orders = [
            {
                "id": 101,
                "client_name": "Rio Grande Logistics",
                "created_at": _iso(NOW - timedelta(days=5)),
                "lines": [{"item": 1, "model_name": "XE43-GEN3", "quantity_shipped": 3}],
            },
            {
                "id": 102,
                "client_name": "Laredo Test Fleet Co.",
                "created_at": _iso(NOW - timedelta(days=120)),
                "lines": [{"item": 5, "model_name": "ANT-900", "quantity_shipped": 2}],
            },
        ]

    async def list_items(self, q=None, status=None, limit=None):
        results = self.items
        if q:
            results = [i for i in results if q.lower() in i["model_name"].lower()]
        if status:
            results = [i for i in results if i["status"] == status]
        if limit is not None:
            results = results[:limit]
        return results

    async def get_item(self, item_id):
        for item in self.items:
            if item["id"] == item_id:
                return item
        return None

    async def list_orders(self, item=None, days=None):
        results = self.orders
        if item:
            results = [o for o in results if any(item.lower() in l["model_name"].lower() for l in o["lines"])]
        if days is not None:
            cutoff = NOW - timedelta(days=days)
            results = [o for o in results if datetime.fromisoformat(o["created_at"].replace("Z", "+00:00")) >= cutoff]
        return results


@pytest.fixture
def mcp():
    return build_server(FakeDjangoClient())


async def _call(mcp, name, **kwargs):
    return await mcp._tool_manager.call_tool(name, kwargs)


@pytest.mark.asyncio
async def test_search_items_finds_matching_lots(mcp):
    result = await _call(mcp, "search_items", query="XE43")
    assert len(result) == 4
    assert all(row["model_name"] == "XE43-GEN3" for row in result)
    assert {row["status"] for row in result} == {"Active", "For Parts", "Pending Repair", "Pending Review"}


@pytest.mark.asyncio
async def test_search_items_filters_by_status(mcp):
    result = await _call(mcp, "search_items", query="XE43", status="Active")
    assert len(result) == 1
    assert result[0]["qty"] == 5


@pytest.mark.asyncio
async def test_search_items_rejects_unknown_status(mcp):
    with pytest.raises(McpToolError, match="Unknown status"):
        await _call(mcp, "search_items", query="XE43", status="Broken")


@pytest.mark.asyncio
async def test_search_items_no_match_suggests_closest_name(mcp):
    with pytest.raises(McpToolError, match='did you mean XE43-GEN3'):
        await _call(mcp, "search_items", query="XE43-GEN4")


@pytest.mark.asyncio
async def test_get_item_returns_full_detail_with_location_string(mcp):
    result = await _call(mcp, "get_item", item_id=2)
    assert result["model_name"] == "XE43-GEN3"
    assert result["status"] == "For Parts"
    assert result["location"] == "Warehouse A - 2"
    assert result["notes"] == "Needs antenna"


@pytest.mark.asyncio
async def test_get_item_handles_missing_location(mcp):
    result = await _call(mcp, "get_item", item_id=3)
    assert result["location"] == "No location recorded"


@pytest.mark.asyncio
async def test_get_item_not_found(mcp):
    with pytest.raises(McpToolError, match="No item found with id 999"):
        await _call(mcp, "get_item", item_id=999)


@pytest.mark.asyncio
async def test_get_stock_totals_and_breaks_down_by_status(mcp):
    result = await _call(mcp, "get_stock", model_name="XE43-GEN3")
    assert result["total"] == 9
    assert result["breakdown"] == {
        "Active": 5,
        "For Parts": 2,
        "Pending Repair": 1,
        "Pending Review": 1,
    }
    assert result["summary"] == "9 total: 5 Active, 2 For Parts, 1 Pending Repair, 1 Pending Review"


@pytest.mark.asyncio
async def test_get_stock_reports_zero_on_hand_model(mcp):
    result = await _call(mcp, "get_stock", model_name="ANT-900")
    assert result["total"] == 0
    assert result["summary"] == "0 total: 0 Active"


@pytest.mark.asyncio
async def test_get_stock_no_match_raises(mcp):
    with pytest.raises(McpToolError, match="No item found matching"):
        await _call(mcp, "get_stock", model_name="NOPE-000")


@pytest.mark.asyncio
async def test_get_order_history_raises_when_window_excludes_everything():
    client = FakeDjangoClient()
    mcp = build_server(client)
    with pytest.raises(McpToolError, match='No orders have shipped any "ANT-900" in the last 90 days'):
        await _call(mcp, "get_order_history", model_name="ANT-900")


@pytest.mark.asyncio
async def test_get_order_history_all_time_includes_old_order(mcp):
    result = await _call(mcp, "get_order_history", model_name="ANT-900", days=0)
    assert result["days"] == "all"
    assert len(result["orders"]) == 1
    assert result["orders"][0]["client"] == "Laredo Test Fleet Co."
    assert result["orders"][0]["quantity_shipped"] == 2
    assert result["total_shipped"] == 2


@pytest.mark.asyncio
async def test_get_order_history_running_total_accumulates_oldest_to_newest(mcp):
    result = await _call(mcp, "get_order_history", days=0)
    totals = [row["running_total"] for row in result["orders"]]
    assert totals == sorted(totals)
    assert totals[-1] == result["total_shipped"]


@pytest.mark.asyncio
async def test_get_order_history_no_orders_at_all_raises():
    client = FakeDjangoClient()
    client.orders = []
    mcp = build_server(client)
    with pytest.raises(McpToolError, match="No orders were placed"):
        await _call(mcp, "get_order_history")
