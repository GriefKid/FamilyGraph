from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0019_social_settings_shareditem'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='GiftBox',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('content_type', models.CharField(
                    choices=[('health','🏥 سلامتی'),('news','📰 خبر'),
                             ('science','🧪 علم'),('opinion','💬 نظر'),('tip','💡 نکته')],
                    default='tip', max_length=10)),
                ('content_text', models.TextField()),
                ('cube_faces', models.JSONField(blank=True, default=list,
                                                verbose_name='پیکربندی ۶ وجه مکعب')),
                ('reactions', models.JSONField(blank=True, default=dict)),
                ('my_reaction', models.CharField(
                    blank=True, max_length=8, null=True,
                    choices=[('true','✅ راسته'),('false','❌ دروغه'),
                             ('accept','🤐 قبولم'),('reject','🚫 رد میکنم')],
                    verbose_name='واکنش گیرنده')),
                ('opened', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sender', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='giftboxes_sent', to=settings.AUTH_USER_MODEL)),
                ('recipient', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='giftboxes_received', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'جعبه هدیه',
                'verbose_name_plural': 'جعبه‌های هدیه',
                'ordering': ['-created_at'],
            },
        ),
    ]
