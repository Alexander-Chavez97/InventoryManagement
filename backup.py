#!/usr/bin/env python3
"""
backup.py — Postgres + media backups for the Inventory Management System.
Run on a schedule (cron, systemd timer, etc.). Requires Docker + the
Compose plugin on PATH, and must be run from (or point at) the project
directory containing docker-compose.yml.
"""

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_DIR = Path("/opt/inventory")   # <-- set this to wherever docker-compose.yml lives
BACKUP_DIR = PROJECT_DIR / "backups"
RETENTION_DAYS = 30


def run(cmd):
    result = subprocess.run(cmd, cwd=PROJECT_DIR)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(cmd)}")


def main():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        # Database dump — pg_dump writes directly into the bind-mounted
        # /backups folder inside the db container (same trick as before:
        # no host-side stdout redirection to worry about).
        run([
            "docker", "compose", "exec", "-T",
            "-e", f"BACKUP_TS={timestamp}",
            "db", "sh", "-c",
            'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "/backups/db_${BACKUP_TS}.sql"',
        ])

        # Media photos — tar'd inside the web container, same bind mount.
        run([
            "docker", "compose", "exec", "-T",
            "-e", f"BACKUP_TS={timestamp}",
            "web", "sh", "-c",
            'tar czf "/backups/media_${BACKUP_TS}.tar.gz" -C /app/mediafiles .',
        ])

        # Retention cleanup.
        cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
        removed = 0
        for f in BACKUP_DIR.iterdir():
            if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
                removed += 1

        print(f"{datetime.now().isoformat()} Backup succeeded: {timestamp} (removed {removed} old file(s))")

    except Exception as exc:
        print(f"{datetime.now().isoformat()} BACKUP FAILED: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()