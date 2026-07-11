from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('main', '0008_debt'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChatMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('user', 'کاربر'), ('assistant', 'همدم')], max_length=10)),
                ('content', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(blank=True, null=True,
                                            on_delete=django.db.models.deletion.CASCADE,
                                            related_name='chat_messages',
                                            to=settings.AUTH_USER_MODEL, verbose_name='صاحب')),
            ],
            options={
                'ordering': ['created_at'],
                'verbose_name': 'پیام چت',
                'verbose_name_plural': 'پیام‌های چت',
            },
        ),
    ]
