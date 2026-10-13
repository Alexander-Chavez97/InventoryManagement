"""Create (or rotate) the dedicated, read-only API account the buildathon
MCP server authenticates as.

This account is intentionally not staff/superuser -- it only needs to hit
GET endpoints (`/api/items/`, `/api/orders/`), and nothing in this project
currently restricts API permissions any further than "is authenticated"
(see DEFAULT_PERMISSION_CLASSES in settings.py), so keeping this account
powerless by construction is the actual safety boundary, not a setting
that could be toggled later.

Usage:
    python manage.py create_mcp_api_user
    python manage.py create_mcp_api_user --username mcp-demo --reset
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token

DEFAULT_USERNAME = "mcp-agent"


class Command(BaseCommand):
    help = "Create or rotate the API token the MCP server uses to authenticate."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default=DEFAULT_USERNAME,
            help=f"Account to create/use (default: {DEFAULT_USERNAME}).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete any existing token for this account and issue a new one.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"]
        reset = options["reset"]

        user, created = User.objects.get_or_create(
            username=username,
            defaults={"is_staff": False, "is_superuser": False, "is_active": True},
        )
        if not created and (user.is_staff or user.is_superuser):
            # Belt-and-suspenders: this command should never be the thing
            # that hands out a token for an account with elevated rights,
            # even if someone later made this username staff by hand.
            self.stderr.write(
                self.style.ERROR(
                    f'"{username}" is staff/superuser -- refusing to print a token for it. '
                    "Use a different --username for the MCP account."
                )
            )
            return
        user.set_unusable_password()
        user.save()

        if reset:
            Token.objects.filter(user=user).delete()
        token, _ = Token.objects.get_or_create(user=user)

        verb = "Created" if created else "Using existing"
        self.stdout.write(self.style.SUCCESS(f"{verb} account: {username}"))
        self.stdout.write(f"Token: {token.key}")
        self.stdout.write(
            self.style.WARNING(
                "Copy this into the MCP server's config now -- it won't be printed again "
                "unless you re-run this command with --reset."
            )
        )
