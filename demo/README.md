# Buildathon demo stack — Superhuman Go "Inventory Desk" agent

A standalone, disposable copy of the Inventory Management System, seeded with
fake data, that a Superhuman Go agent talks to over MCP. Built for the
TAMUS x Superhuman Buildathon. This stack is intentionally isolated from
production — separate Postgres volume, separate `.env` files, separate
Cloudflare tunnel — so the agent can never read or touch real inventory data.

## What's running

| Service | Image/build | Purpose | Exposed? |
|---|---|---|---|
| `db` | `postgres:15` | Demo-only Postgres, its own volume (`demo_postgres_data`) | No — internal only |
| `web` | `../` (same Dockerfile as production) | Django app, seeded with fake items/orders | `127.0.0.1:8010` only, for your own browsing |
| `mcp` | `../mcp_server` | FastMCP server exposing 4 read-only tools | Yes, via `cloudflared` |
| `cloudflared` | `cloudflare/cloudflared` | Tunnels `mcp` to a public hostname | N/A |

Only `mcp` is ever reachable from outside the Docker network. `web` has no
public hostname and no tunnel — the Go agent never talks to Django directly,
only through the MCP server's four tools.

## Tools the agent can call

All four are **read-only** — nothing here creates, edits, or deletes:

- `search_items(query)` — find items by model name / description
- `get_item(item_id)` — full detail for one item
- `get_stock(model_name)` — quantity on hand, broken down by status
- `get_order_history(model_name)` — which orders/shipments included this item

## First-time setup

From this directory:

```bash
docker compose up -d --build db web
docker compose exec web python manage.py migrate
docker compose exec -e DEMO_SEED_CONFIRM=yes web python manage.py seed_demo --reset
docker compose exec web python manage.py create_mcp_api_user
```

Copy the printed API token into `mcp.env` as `DJANGO_API_TOKEN`, generate a
secret path segment (`python3 -c "import secrets; print(secrets.token_urlsafe(24))"`)
into `MCP_SERVER_SECRET_PATH`, and set `MCP_PUBLIC_HOSTNAME` to whatever
hostname the Cloudflare tunnel below will route to. Then:

```bash
docker compose up -d --build mcp cloudflared
```

`.env.example` and `mcp.env.example` in this directory document every
variable in detail — copy each to its real filename (`.env`, `mcp.env`) and
fill them in. Neither real file is ever committed (see `.gitignore`).

### Cloudflare tunnel

Create a new tunnel in the Cloudflare dashboard (Zero Trust → Networks →
Tunnels) — a **different** one from production's, per the brief's isolation
requirement. Route its public hostname to this stack's `mcp` service on port
`8100`, and put the tunnel token in this directory's `.env` (not `mcp.env`)
as `CLOUDFLARE_TUNNEL_TOKEN`.

### Pointing Superhuman Go at it

In Agent Builder, the MCP URL is `https://<MCP_PUBLIC_HOSTNAME>/<MCP_SERVER_SECRET_PATH>/`
— any other path 404s before it ever reaches the MCP app.

## Why a secret path instead of a bearer token

The brief's original plan (Section 9) assumed header-based auth. In
practice, Superhuman Go's custom-MCP connector (`user-agent:
Coda-Server-Fetcher`) sends **no Authorization header and no API-key
header at all** — confirmed by capturing its real outbound request headers.
`MCP_SERVER_AUTH_TOKEN` is kept in the code for any MCP client that *does*
support it, but it's left blank in this deployment. The actual gate is
`MCP_SERVER_SECRET_PATH`: an unguessable URL segment the request has to
match before it's forwarded to the MCP app at all. It's documented in
`server.py` as exactly what it is — obscurity, not real authentication —
which is an acceptable trade for a read-only, fake-data demo endpoint.

## Known gaps / troubleshooting notes

A few non-obvious things that cost real debugging time, kept here so they
don't get rediscovered the hard way:

- **`docker compose restart` does not reload `env_file` changes.** After
  editing `mcp.env`, use `docker compose up -d mcp` (or `--force-recreate`),
  not `restart`.
- **This MCP SDK mounts streamable-HTTP at `/mcp`, not `/`.** Any proxy or
  path-rewriting middleware in front of it has to forward to `/mcp`
  specifically — confirmed by `mcp_server/tests/smoke_test.py`'s working
  client URL.
- **FastMCP auto-enables DNS-rebinding protection locked to
  localhost**, which 421s any real public hostname unless you explicitly
  pass `transport_security` with that hostname added to `allowed_hosts` —
  see `inventory_tools.py::build_server`. This has nothing to do with
  Cloudflare or this project's own middleware; it's an SDK-level default.
- **`mcp_server/tests/smoke_test.py` exercises the bearer-auth path**, not
  the secret-path gate this deployment actually uses — still useful as a
  regression check that the tools themselves work end-to-end over real
  HTTP, just not a full rehearsal of production auth.

## Resetting the demo data

```bash
docker compose exec -e DEMO_SEED_CONFIRM=yes web python manage.py seed_demo --reset
```

Safe to run anytime — `DEMO_SEED_CONFIRM=yes` is hardcoded in `.env.example`
for this stack specifically, since it can only ever point at the demo-only
Postgres volume above, never at production's.
