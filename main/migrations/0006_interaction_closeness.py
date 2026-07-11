from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('main', '0005_event_time_reminders'),
    ]

    operations = [
        migrations.CreateModel(
            name='NodeCloseness',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tier', models.CharField(
                    choices=[
                        ('inner', 'حلقه نزدیک (هفتگی)'),
                        ('close', 'نزدیک (هر ۲ هفته)'),
                        ('friend', 'دوست (ماهانه)'),
                        ('acquaintance', 'آشنا (هر ۳ ماه)'),
                        ('distant', 'دور (بدون انتظار)'),
                    ],
                    max_length=15, verbose_name='دایره نزدیکی')),
                ('node', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                              related_name='closeness_setting',
                                              to='main.node', verbose_name='شخص')),
                ('owner', models.ForeignKey(blank=True, null=True,
                                            on_delete=django.db.models.deletion.CASCADE,
                                            related_name='closeness_settings',
                                            to=settings.AUTH_USER_MODEL, verbose_name='صاحب')),
            ],
            options={
                'verbose_name': 'دایره نزدیکی',
                'verbose_name_plural': 'دایره‌های نزدیکی',
            },
        ),
        migrations.CreateModel(
            name='Interaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(
                    choices=[
                        ('call', '📞 تلفنی'),
                        ('meet', '🤝 حضوری'),
                        ('message', '💬 پیام'),
                        ('online', '🌐 آنلاین'),
                        ('other', '✦ سایر'),
                    ],
                    default='call', max_length=15, verbose_name='نوع')),
                ('date', models.DateField(verbose_name='تاریخ')),
                ('feeling', models.SmallIntegerField(
                    choices=[(1, '😊 خوب'), (0, '😐 معمولی'), (-1, '😕 ناخوشایند')],
                    default=0, verbose_name='حس بعدش')),
                ('note', models.CharField(blank=True, default='', max_length=300, verbose_name='یادداشت کوتاه')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('node', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name='interactions', to='main.node', verbose_name='شخص')),
                ('owner', models.ForeignKey(blank=True, null=True,
                                            on_delete=django.db.models.deletion.CASCADE,
                                            related_name='interactions',
                                            to=settings.AUTH_USER_MODEL, verbose_name='صاحب')),
            ],
            options={
                'ordering': ['-date', '-id'],
                'verbose_name': 'تعامل',
                'verbose_name_plural': 'تعامل‌ها',
            },
        ),
        migrations.AddIndex(
            model_name='interaction',
            index=models.Index(fields=['owner', 'node', '-date'], name='ix_inter_owner_node_date'),
        ),
    ]
