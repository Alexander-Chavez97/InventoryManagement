# Inventory Management System

A self-hosted, scanner-driven inventory tracker built for field service operations — the kind of shop where technicians check equipment (cameras, radios, vehicles) in and out of a warehouse using barcode scanners instead of typing serial numbers by hand. Built with Django, deployed in production for a small telecom and security equipment installer.

## Why

Small installers doing two-way radio, CCTV, GPS tracking, and security system work often end up tracking equipment in a spreadsheet — which doesn't scale once you have technicians in the field checking gear in and out daily, and gives no history of where an item has been or who last touched it. This project replaces that spreadsheet with a lightweight, self-hosted web app: scan a barcode, see the item, update its status, done.

## Features

- **Barcode/QR scanner intake** — technicians scan a serial number with a phone camera (via [html5-qrcode](https://github.com/mebjas/html5-qrcode)) instead of typing it in, cutting down data-entry errors.
- **Status tracking with full history** — every status change (Active, For Parts, Pending Repair, Pending Review) is recorded with a timestamp and the user who made the change, not just overwritten.
- **Location tracking** — building / aisle / shelf / bin, so an item's physical location is always answerable.
- **Photo attachments** — barcode and description photos attached per item.
- **Read-only REST API** — `GET /api/items/` with filtering by status, item type, and serial number search, built with Django REST Framework, for future integrations (dashboards, reporting) without touching the core app.
- **Server-rendered UI with htmx** — no SPA build step, no client-side routing; partial-page updates via [htmx](https://htmx.org/) keep the frontend simple and fast.

## Security & production hardening

This isn't just a CRUD app — it's deployed on the open internet, so it's been hardened accordingly:

- **Zero Trust admin access** — `/admin/` is gated behind Cloudflare Access (email OTP), so Django's admin panel is never reachable by an unauthenticated request even if credentials leak.
- **Hardened HTTP security headers** — Content-Security-Policy, Strict-Transport-Security, Permissions-Policy, X-Content-Type-Options, and Cross-Origin-Resource-Policy, set at the nginx layer and verified with repeated [OWASP ZAP](https://www.zaproxy.org/) baseline scans until the report came back clean.
- **Login brute-force protection** — [django-axes](https://github.com/jazzband/django-axes) locks out a username+IP pair after repeated failed logins.
- **Secure session/CSRF cookies** — `Secure`, `HttpOnly`, short-lived sessions, HTTPS-only redirects behind the reverse proxy.
- **No secrets in source** — all credentials and keys are environment-variable driven (see `.env.example`); the app refuses to start in production if a required secret is missing rather than silently falling back to an insecure default.
- **Automated backups** — scheduled Postgres dumps + media backups with retention, restored and verified rather than just assumed to work.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Django 5, Django REST Framework |
| Database | PostgreSQL |
| Frontend | Django templates + htmx (no SPA framework) |
| Barcode scanning | html5-qrcode |
| App server | Gunicorn |
| Reverse proxy | nginx |
| Containerization | Docker Compose |
| Ingress / TLS | Cloudflare Tunnel |
| Error tracking | Sentry |
| Auth hardening | Cloudflare Access (admin), django-axes (login) |

## Architecture

```
Internet → Cloudflare (TLS, Access, tunnel) → cloudflared → nginx → Gunicorn (Django) → PostgreSQL
```

Everything runs as a set of Docker Compose services (`db`, `web`, `nginx`, `cloudflared`), each independently restartable, with static files and media served through nginx and only the app server talking to the database.

## Running it locally

```bash
git clone https://github.com/Alexander-Chavez97/InventoryManagement.git
cd InventoryManagement
cp .env.example .env   # fill in real values
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

The app will be available on the port mapped in `docker-compose.yml`. For local development without the full stack (no nginx/Cloudflare), `python manage.py runserver` works against the same `.env`-driven settings.

## API

Read-only endpoints via Django REST Framework:

```
GET /api/items/                      # list, paginated
GET /api/items/?status=ACTIVE        # filter by status
GET /api/items/?item_type=RADIO      # filter by type
GET /api/items/?q=<serial>           # search by serial number
GET /api/items/<id>/                 # single item detail
```

## License

MIT — see [LICENSE](LICENSE).
