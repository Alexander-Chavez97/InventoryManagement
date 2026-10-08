import re

import django.db.models.deletion
from django.db import migrations, models

DEFAULT_PATTERN_REGEX = r"[A-Z]{1,4}\d{1,4}[A-Z]*(-[A-Z0-9]+)*"
DEFAULT_PATTERN_LABEL = "Letter prefix + digits + optional -SUFFIX chain"
DEFAULT_PATTERN_EXAMPLE = "XE43-GEN3"


def seed_pattern_and_backfill_catalog(apps, schema_editor):
    """Seed one starter naming pattern (matches things like XE43-GEN3,
    XB41H, E8-TURBO-GEN3) and backfill the catalog from every model name
    already in use -- existing inventory is grandfathered in (not flagged
    for review) regardless of whether it happens to match the pattern."""
    ModelNamePattern = apps.get_model("inventory", "ModelNamePattern")
    ModelCatalog = apps.get_model("inventory", "ModelCatalog")
    Item = apps.get_model("inventory", "Item")

    pattern = ModelNamePattern.objects.create(
        label=DEFAULT_PATTERN_LABEL,
        regex=DEFAULT_PATTERN_REGEX,
        example=DEFAULT_PATTERN_EXAMPLE,
        is_active=True,
    )

    seen = set()
    for raw_name in Item.objects.values_list("model_name", flat=True):
        name = (raw_name or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        matched = re.fullmatch(DEFAULT_PATTERN_REGEX, name, re.IGNORECASE) is not None
        ModelCatalog.objects.get_or_create(
            name=name,
            defaults={
                "matched_pattern": pattern if matched else None,
                "needs_review": False,
            },
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0008_orders_model_name_price"),
    ]

    operations = [
        migrations.CreateModel(
            name="ModelNamePattern",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "label",
                    models.CharField(
                        help_text='Short description, e.g. "Letter prefix + digits + optional -SUFFIX".',
                        max_length=150,
                    ),
                ),
                (
                    "regex",
                    models.CharField(
                        help_text="Must match the *entire* model name (re.fullmatch), case-insensitive.",
                        max_length=200,
                    ),
                ),
                (
                    "example",
                    models.CharField(blank=True, help_text="One real example, for reference.", max_length=100),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["label"],
            },
        ),
        migrations.CreateModel(
            name="ModelCatalog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(db_index=True, max_length=100, unique=True)),
                (
                    "needs_review",
                    models.BooleanField(
                        default=False,
                        help_text="Didn't match any known naming pattern when it was first entered.",
                    ),
                ),
                ("added_at", models.DateTimeField(auto_now_add=True)),
                (
                    "matched_pattern",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="models",
                        to="inventory.modelnamepattern",
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
                "verbose_name_plural": "Model catalog",
            },
        ),
        migrations.RunPython(seed_pattern_and_backfill_catalog, noop_reverse),
    ]
