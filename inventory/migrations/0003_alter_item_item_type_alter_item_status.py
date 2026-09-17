from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0002_itemphoto'),
    ]

    operations = [
        migrations.AlterField(
            model_name='item',
            name='item_type',
            field=models.CharField(choices=[('CAMERA', 'Camera'), ('RADIO', 'Radio'), ('VEHICLE', 'Vehicle')], max_length=50),
        ),
        migrations.AlterField(
            model_name='item',
            name='status',
            field=models.CharField(choices=[('ACTIVE', 'Active'), ('PARTS', 'For Parts'), ('REPAIR', 'Pending Repair'), ('REVIEW', 'Pending Review')], db_index=True, default='REVIEW', max_length=20),
        ),
    ]