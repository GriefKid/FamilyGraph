from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0008_node_names_settings_journal'),
    ]

    operations = [
        # 1. Add entry_date to JournalEntry
        migrations.AddField(
            model_name='journalentry',
            name='entry_date',
            field=models.DateField(blank=True, null=True, verbose_name='تاریخ رویداد'),
        ),
        # 2. Add mentioned_nodes M2M
        migrations.AddField(
            model_name='journalentry',
            name='mentioned_nodes',
            field=models.ManyToManyField(
                blank=True,
                related_name='journal_entries',
                to='main.node',
            ),
        ),
        # 3. Create JournalImage
        migrations.CreateModel(
            name='JournalImage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to='journal/', verbose_name='تصویر')),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('entry', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='images',
                    to='main.journalentry',
                )),
            ],
            options={'ordering': ['uploaded_at']},
        ),
    ]
