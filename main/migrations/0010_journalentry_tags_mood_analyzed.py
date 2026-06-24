from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0009_journal_images_date_mentions'),
    ]

    operations = [
        migrations.AddField(
            model_name='journalentry',
            name='tags',
            field=models.JSONField(blank=True, default=list, verbose_name='تگ‌ها'),
        ),
        migrations.AddField(
            model_name='journalentry',
            name='mood',
            field=models.CharField(blank=True, max_length=100, verbose_name='خلق‌وخو'),
        ),
        migrations.AddField(
            model_name='journalentry',
            name='ai_analyzed',
            field=models.BooleanField(default=False, verbose_name='آنالیز AI'),
        ),
    ]
