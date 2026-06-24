from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0007_relationship_met_at_relationship_status_and_more'),
    ]

    operations = [
        # ── Node: add first_name / last_name / nickname ──────────────────
        migrations.AddField(
            model_name='node',
            name='first_name',
            field=models.CharField(blank=True, max_length=100, verbose_name='نام'),
        ),
        migrations.AddField(
            model_name='node',
            name='last_name',
            field=models.CharField(blank=True, max_length=100, verbose_name='نام خانوادگی'),
        ),
        migrations.AddField(
            model_name='node',
            name='nickname',
            field=models.CharField(blank=True, max_length=100, verbose_name='لقب / اسم مستعار'),
        ),

        # ── AppSettings (singleton) ──────────────────────────────────────
        migrations.CreateModel(
            name='AppSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('root_node', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='as_root',
                    to='main.node',
                    verbose_name='نود اصلی (من)',
                )),
            ],
            options={
                'verbose_name': 'تنظیمات',
                'verbose_name_plural': 'تنظیمات',
            },
        ),

        # ── JournalEntry ─────────────────────────────────────────────────
        migrations.CreateModel(
            name='JournalEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('text', models.TextField(verbose_name='متن')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'یادداشت روزانه',
                'verbose_name_plural': 'یادداشت‌های روزانه',
                'ordering': ['-created_at'],
            },
        ),
    ]
