from django.db import migrations


def normalize_user_roles(apps, schema_editor):
    """Close the gap between Django's two separate admin flags and this
    app's two-tier model (Supervisor / Owner). Going forward nothing in the
    app can create a hybrid is_staff != is_superuser account (see
    create_staff_user() in views.py and LockstepUserAdmin in admin.py), but
    this collapses any account that predates that rule onto the same
    binary: is_superuser accounts become full Owners (is_staff=True too,
    so they can still log into /admin/), everyone else becomes a plain
    Supervisor (is_staff=False)."""
    User = apps.get_model("auth", "User")
    User.objects.filter(is_superuser=True).update(is_staff=True)
    User.objects.filter(is_superuser=False).update(is_staff=False)


def noop_reverse(apps, schema_editor):
    # Not reversible -- we don't know which pre-migration accounts had a
    # hybrid is_staff/is_superuser combination, so there's nothing sane to
    # restore. Leaves accounts as the forwards migration left them.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0012_modelcatalog_brand_modelcatalog_from_vendor_catalog_and_more"),
    ]

    operations = [
        migrations.RunPython(normalize_user_roles, noop_reverse),
    ]
