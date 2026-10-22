# Adds ORD-YYMM-XXXX order numbers: a new OrderSequence counter table (one
# row per calendar month, locked on allocation so concurrent order
# creation can't hand out the same number twice -- see
# Order Sequence.next_order_number in inventory/models.py) plus the
# order_number field on Order itself. Existing orders are backfilled in
# chronological order before the field is tightened to unique/non-null, so
# this is safe to run against a table that already has real order data.

from django.db import migrations, models
from django.utils import timezone


def backfill_order_numbers(apps, schema_editor):
    Order = apps.get_model("inventory", "Order")
    OrderSequence = apps.get_model("inventory", "OrderSequence")
    counters = {}
    for order in Order.objects.order_by("created_at", "pk"):
        year_month = timezone.localtime(order.created_at).strftime("%y%m")
        counters[year_month] = counters.get(year_month, 0) + 1
        order.order_number = f"ORD-{year_month}-{counters[year_month]:04d}"
        order.save(update_fields=["order_number"])
    for year_month, last_seq in counters.items():
        OrderSequence.objects.update_or_create(
            year_month=year_month, defaults={"last_seq": last_seq}
        )


def noop_reverse(apps, schema_editor):
    # Nothing to undo here -- the order_number field removal (handled by
    # Django reversing the AddField/AlterField below) is enough.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0010_lot_lookup_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderSequence",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("year_month", models.CharField(max_length=4, unique=True)),
                ("last_seq", models.PositiveIntegerField(default=0)),
            ],
        ),
        migrations.AddField(
            model_name="order",
            name="order_number",
            field=models.CharField(
                max_length=20, null=True, blank=True, editable=False
            ),
        ),
        migrations.RunPython(backfill_order_numbers, noop_reverse),
        migrations.AlterField(
            model_name="order",
            name="order_number",
            field=models.CharField(max_length=20, unique=True, editable=False),
        ),
    ]
