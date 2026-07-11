from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('main', '0018_persona_music'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='discoverable',
            field=models.BooleanField(default=True, verbose_name='قابل کشف',
                                      help_text='توی صفحه «کشف آدم‌ها» پیدا بشم (فقط اگه پابلیک باشی معنی داره)'),
        ),
        migrations.AddField(
            model_name='user',
            name='auto_accept_follow',
            field=models.BooleanField(default=False, verbose_name='تایید خودکار فالو',
                                      help_text='هر کی فالو کرد بدون تایید من قبول بشه'),
        ),
        migrations.AddField(
            model_name='user',
            name='auto_accept_connection',
            field=models.BooleanField(default=False, verbose_name='تایید خودکار کانکشن',
                                      help_text='درخواست کانکشن بدون تایید من قبول بشه'),
        ),
        migrations.AddField(
            model_name='user',
            name='chat_policy',
            field=models.CharField(choices=[('connections', 'فقط کانکشن‌ها'), ('nobody', 'هیچ‌کس')],
                                   default='connections', max_length=12,
                                   verbose_name='کی می‌تونه بهم پیام بده'),
        ),
        migrations.CreateModel(
            name='SharedItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('item_type', models.CharField(
                    choices=[('node', '👤 راس'), ('edge', '💞 یال'), ('info', '💡 اطلاعات')],
                    max_length=8)),
                ('title', models.CharField(blank=True, default='', max_length=240)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('applied', models.BooleanField(default=False, verbose_name='به گراف گیرنده اضافه شد')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('recipient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                                related_name='shares_received', to=settings.AUTH_USER_MODEL)),
                ('sender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                             related_name='shares_sent', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at'], 'verbose_name': 'آیتم اشتراکی',
                     'verbose_name_plural': 'آیتم‌های اشتراکی'},
        ),
    ]
