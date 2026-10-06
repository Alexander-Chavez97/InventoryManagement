from django.db import migrations, models


def populate_client_name_email(apps, schema_editor):
    """Carry over data from the old Client FK before it's dropped, so any
    shipment already created (e.g. during testing) keeps its client info."""
    Shipment = apps.get_model("inventory", "Shipment")
    for shipment in Shipment.objects.select_related("client").all():
        if shipment.client_id:
            shipment.client_name = shipment.client.name
            shipment.client_email = shipment.client.email
            shipment.save(update_fields=["client_name", "client_email"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0006_client_shipment_shipmentline"),
    ]

    operations = [
        migrations.AddField(
            model_name="shipment",
            name="client_name",
            field=models.CharField(default="", max_length=150),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="shipment",
            name="client_email",
            field=models.EmailField(default="", max_length=254),
            preserve_default=False,
        ),
        migrations.RunPython(populate_client_name_email, noop_reverse),
        migrations.RemoveField(
            model_name="shipment",
            name="client",
        ),
        migrations.DeleteModel(
            name="Client",
        ),
    ]
