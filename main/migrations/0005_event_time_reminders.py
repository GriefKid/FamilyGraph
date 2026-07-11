from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0004_alter_appsettings_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='event_time',
            field=models.TimeField(blank=True, null=True, verbose_name='ساعت'),
        ),
        migrations.AddField(
            model_name='event',
            name='reminder_sent_7d',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='event',
            name='reminder_sent_1d',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='event',
            name='reminder_sent_3h',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='event',
            name='post_event_prompted',
            field=models.BooleanField(default=False),
        ),
        # AlterModelOptions: ordering به ['date'] نگه داشته می‌شه (بدون event_time در ordering)
    ]
