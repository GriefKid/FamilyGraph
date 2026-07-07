from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('main', '0006_interaction_closeness'),
    ]

    operations = [
        migrations.CreateModel(
            name='FollowUp',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text', models.CharField(max_length=300, verbose_name='موضوع')),
                ('due_date', models.DateField(blank=True, null=True, verbose_name='سررسید')),
                ('done', models.BooleanField(default=False, verbose_name='انجام شد')),
                ('done_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('node', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name='followups', to='main.node', verbose_name='شخص')),
                ('owner', models.ForeignKey(blank=True, null=True,
                                            on_delete=django.db.models.deletion.CASCADE,
                                            related_name='followups',
                                            to=settings.AUTH_USER_MODEL, verbose_name='صاحب')),
            ],
            options={
                'ordering': ['done', 'due_date', '-created_at'],
                'verbose_name': 'موضوع باز',
                'verbose_name_plural': 'موضوعات باز',
            },
        ),
    ]
