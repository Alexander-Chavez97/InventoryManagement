from django.db import connection
from django.http import JsonResponse


def healthz(request):
    """Lightweight, unauthenticated health check for external uptime monitors.

    Confirms the app can actually reach Postgres, not just that the Django
    process is alive -- a crashed/unreachable db container should show up
    as down here, not as a false "healthy" from gunicorn still answering.
    No auth required: this needs to be pingable by an external monitor
    (e.g. UptimeRobot) without a login, and it's reachable over the
    Cloudflare Tunnel without going through the Access policy -- that's
    scoped to /admin only (see infra/cloudflare/variables.tf).
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        return JsonResponse({"status": "error", "detail": str(exc)}, status=503)
    return JsonResponse({"status": "ok"})
