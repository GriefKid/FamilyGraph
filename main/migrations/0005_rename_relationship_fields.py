from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0004_node_phone_number'),
    ]

    operations = [
        migrations.RenameField(
            model_name='relationship',
            old_name='father',
            new_name='source',
        ),
        migrations.RenameField(
            model_name='relationship',
            old_name='child',
            new_name='target',
        ),
    ]
