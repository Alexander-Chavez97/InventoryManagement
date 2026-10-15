# Setting up a production host from scratch

This is the runbook for taking a brand-new Windows 11 box to "serving
inventory.meridiansupply.example" -- written so this isn't knowledge that only
exists in one person's head. It exists because we moved production from
a dev laptop to a dedicated Mini PC, and because the whole point of
adding this is being able to hand the project off.

`bootstrap.ps1` in this directory automates everything that's safe to
automate. This file covers the handful of steps that genuinely can't be
scripted -- a GUI wizard, a short-lived token, a human decision -- in the
order you'd actually hit them.

## 1. Run the bootstrap script

From an elevated PowerShell prompt:

```powershell
.\bootstrap.ps1 -InstallDir "C:\Inventory\InventoryManagement"
```

It's safe to re-run. It'll tell you to reboot (Docker Desktop's
prerequisites need it) or to finish Docker Desktop's first-launch wizard
by hand, then stop and ask you to re-run it -- do that rather than trying
to skip ahead.

It will also pause and ask you to fill in `.env` for real (Postgres
password, Cloudflare Tunnel token, etc.) before it brings the stack up.
See `.env.example` for what each value does.

## 2. One-time Django setup

`docker compose up -d --build` (which the script just ran) already
applies migrations and collects static files automatically on container
start -- that's baked into `entrypoint.sh`. What's left is one-time and
needs an interactive decision, so it's not in the script:

```powershell
docker compose exec web python manage.py load_categories
docker compose exec web python manage.py createsuperuser
```

## 3. Cloudflare Tunnel + Zero Trust Access

Done once, by hand, in the Cloudflare dashboard. `infra/cloudflare/`
brings these under Terraform afterward (import, don't blind-apply -- see
that directory's README) so future changes are a diffable `.tf` edit
instead of an unlogged dashboard click:

1. Zero Trust -> Networks -> Tunnels -> create a tunnel, route its public
   hostname (`inventory.meridiansupply.example`) to this host's `nginx` service
   on port 80 (the tunnel reaches it inside the Docker network; nothing
   needs to be opened in Windows Firewall for this).
2. Copy the tunnel token into this host's `.env` as
   `CLOUDFLARE_TUNNEL_TOKEN`, then `docker compose up -d cloudflared`.
3. Zero Trust -> Access -> Applications -> lock `/admin/` behind an
   email-OTP policy, scoped to whoever actually needs admin access.

## 4. Register the GitHub Actions runner

Covered in detail in the main project's CI/CD notes -- short version:
Settings -> Actions -> Runners -> New self-hosted runner on this repo,
follow the Windows instructions it gives you (the token it shows is
short-lived, so don't delay), then set it to auto-start in the real
desktop session via Task Scheduler ("At log on", "Run only when user is
logged on", action = `run.cmd`) rather than `svc.cmd install` -- Docker
Desktop's daemon isn't reachable from a service running in Session 0,
only from an actual logged-in session.

## 5. Make it survive a reboot

The runner and the backup task above both only come back automatically
if this account auto-logs-in after a restart -- otherwise they sit
waiting at the lock screen. Use Sysinternals' **Autologon** tool (stores
the password encrypted via LSA secrets) rather than `netplwiz`, which
stores it in the registry in plaintext.

Worth being clear-eyed about the tradeoff: auto-login means anyone with
physical or console access to this machine lands in a logged-in desktop,
no password prompt. Reasonable for a dedicated box that's physically
secured; worth reconsidering if that's ever not true.

## 6. Observability: error tracking + uptime monitoring

Error tracking is already wired into the code (`main/settings.py`,
gated behind a `SENTRY_DSN` env var that's blank by default -- see
`.env.example`) and `requirements.txt` already has `sentry-sdk`
installed. What's left is account-side, done once by hand:

1. Create a free account at sentry.io, make a new project, choose
   "Django" as the platform. Copy the DSN it gives you.
2. Add it to this host's `.env`: `SENTRY_DSN=<the dsn>`, then
   `docker compose up -d web` to pick it up (env changes need the
   container recreated, not just restarted).
3. Confirm it's wired up: `docker compose exec web python manage.py
   shell -c "1/0"` should throw, and the error should show up in the
   Sentry project's Issues tab within a minute or two.

Uptime monitoring is external by design -- if this box itself is down,
nothing running on it can tell you that:

1. `/healthz/` (added in this codebase) is the endpoint to point a
   monitor at -- it's unauthenticated (an uptime checker can't log in)
   and actually checks Postgres connectivity, not just that gunicorn is
   answering. It's reachable at
   `https://inventory.meridiansupply.example/healthz/` without hitting the
   Cloudflare Access policy, since that's scoped to `/admin` only.
2. Create a free monitor at uptimerobot.com (or any similar service) --
   HTTP(s) check against that URL, a few minutes' interval, alert by
   email. A non-200 response (including the 503 `/healthz/` returns
   when the DB is unreachable) should trigger the alert.

Both of these catch different failure modes: Sentry tells you about
unhandled exceptions while the app is still up and responding; the
uptime monitor tells you when the app -- or the whole Mini PC -- isn't
responding at all (crashed container, lost power, dropped tunnel).
Neither one substitutes for the other.

## Why there's no step for opening firewall ports

There isn't one, on purpose. `nginx` binds to `127.0.0.1:8000` --
loopback only -- and `cloudflared` only makes outbound connections to
Cloudflare. Nothing in this stack listens on a port that needs inbound
traffic let through the Windows Firewall.
