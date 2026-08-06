from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('main', '0019_social_settings_shareditem'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='public_interests',
            field=models.JSONField(blank=True, default=list, verbose_name='علایق عمومی'),
        ),
        migrations.AddField(
            model_name='user',
            name='public_values',
            field=models.JSONField(blank=True, default=list, verbose_name='ارزش‌های عمومی'),
        ),
        migrations.AddField(
            model_name='user',
            name='public_communication_style',
            field=models.CharField(blank=True, max_length=280, verbose_name='سبک ارتباط عمومی'),
        ),
        migrations.AddField(
            model_name='profilemediaitem',
            name='status',
            field=models.CharField(
                choices=[
                    ('completed', 'تمام‌شده'),
                    ('current', 'در حال خواندن / دیدن / گوش دادن'),
                    ('planned', 'در برنامه'),
                ],
                default='completed',
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name='profilemediaitem',
            name='is_public',
            field=models.BooleanField(default=True, verbose_name='نمایش در پروفایل عمومی'),
        ),
        migrations.CreateModel(
            name='SocialPost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('body', models.TextField(max_length=1200)),
                ('image', models.ImageField(blank=True, null=True, upload_to='social_posts/')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_public', models.BooleanField(default=True)),
                ('author', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='social_posts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'پست اجتماعی',
                'verbose_name_plural': 'پست‌های اجتماعی',
            },
        ),
    ]
