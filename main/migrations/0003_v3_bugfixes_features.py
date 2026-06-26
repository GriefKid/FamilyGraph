"""
V3 Bug Fixes + New Features Migration
- Move root_node from AppSettings → User
- Remove root_node from AppSettings
- Add RelationshipStrengthHistory model
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0002_add_is_public_to_node_relationship'),
    ]

    operations = [
        # ── 1. root_node روی User ──────────────────────────────────────
        migrations.AddField(
            model_name='user',
            name='root_node',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='as_root_for',
                to='main.node',
                verbose_name='نود اصلی (من)',
            ),
        ),

        # ── 2. root_node رو از AppSettings حذف کن ──────────────────
        migrations.RemoveField(
            model_name='appsettings',
            name='root_node',
        ),

        # ── 3. RelationshipStrengthHistory model ────────────────────
        migrations.CreateModel(
            name='RelationshipStrengthHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('strength', models.IntegerField(verbose_name='قدرت')),
                ('changed_at', models.DateTimeField(auto_now_add=True, verbose_name='زمان تغییر')),
                ('note', models.CharField(blank=True, max_length=200, verbose_name='یادداشت')),
                ('relationship', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='strength_history',
                    to='main.relationship',
                    verbose_name='رابطه',
                )),
                ('owner', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='strength_histories',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='صاحب',
                )),
            ],
            options={
                'verbose_name': 'تاریخچه قدرت',
                'verbose_name_plural': 'تاریخچه قدرت‌ها',
                'ordering': ['-changed_at'],
            },
        ),
    ]
