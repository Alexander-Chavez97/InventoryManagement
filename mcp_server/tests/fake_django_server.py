"""A tiny stand-in for the Django IMS mockup's read API, used only for the
end-to-end smoke test (smoke_test.py) -- not part of the shipped server.
Serves just enough of /api/items/ and /api/orders/ to exercise a real HTTP
round trip, including checking the Authorization: Token header.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

EXPECTED_TOKEN = "Token test-django-token"

ITEMS = [
    {"id": 1, "model_name": "XE43-GEN3", "quantity": 5, "status": "ACTIVE",
     "location": {"building": "Warehouse A", "aisle": "1", "shelf": "", "bin": ""}, "notes": ""},
    {"id": 2, "model_name": "XE43-GEN3", "quantity": 2, "status": "PARTS",
     "location": {"building": "Warehouse A", "aisle": "2", "shelf": "", "bin": ""}, "notes": ""},
]

ORDERS = [
    {
        "id": 101,
        "client_name": "Rio Grande Logistics",
        "created_at": "2026-10-01T00:00:00Z",
        "lines": [{"item": 1, "model_name": "XE43-GEN3", "quantity_shipped": 3}],
    }
]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        pass

    def do_GET(self):
        if self.headers.get("Authorization") != EXPECTED_TOKEN:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"{}")
            return

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/api/items/":
            q = params.get("q", [None])[0]
            results = [i for i in ITEMS if not q or q.lower() in i["model_name"].lower()]
            body = {"count": len(results), "next": None, "previous": None, "results": results}
        elif parsed.path == "/api/orders/":
            body = {"count": len(ORDERS), "next": None, "previous": None, "results": ORDERS}
        else:
            self.send_response(404)
            self.end_headers()
            return

        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run(port: int) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), Handler)
    return server


if __name__ == "__main__":
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8999
    run(port).serve_forever()
