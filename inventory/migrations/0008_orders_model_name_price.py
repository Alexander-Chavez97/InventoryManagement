import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0007_shipment_client_name_shipment_client_email_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # "Shipments" become "Orders" throughout -- renamed in place so the
        # existing table/rows carry over instead of being dropped/recreated.
        migrations.RenameModel(old_name="Shipment", new_name="Order"),
        migrations.RenameModel(old_name="ShipmentLine", new_name="OrderLine"),
        migrations.RenameField(
            model_name="orderline", old_name="shipment", new_name="order"
        ),
        migrations.AlterField(
            model_name="order",
            name="created_by",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="orders",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="orderline",
            name="item",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="order_lines",
                to="inventory.item",
            ),
        ),
        # "Serial number" becomes "model name" -- it was never actually a
        # unique per-unit serial in practice, so the unique constraint comes
        # off too; many items legitimately share a model name, with
        # `quantity` tracking how many of that model are on hand.
        migrations.RenameField(
            model_name="item", old_name="serial_number", new_name="model_name"
        ),
        migrations.AlterField(
            model_name="item",
            name="model_name",
            field=models.CharField(db_index=True, max_length=100),
        ),
        migrations.AddField(
            model_name="item",
            name="price",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Optional. Shown on the inventory list when set.",
                max_digits=10,
                null=True,
            ),
        ),
    ]
