from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('main', '0009_chatmessage'),
    ]

    operations = [
        migrations.CreateModel(
            name='LifeEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(
                    choices=[('mourning', '🖤 سوگ / فوت عزیز'), ('illness', '🏥 بیماری / جراحی'),
                             ('wedding', '💍 ازدواج'), ('baby', '🍼 تولد فرزند'),
                             ('exam', '📝 امتحان / کنکور'), ('job', '💼 شغل / موقعیت جدید'),
                             ('move', '📦 اسباب‌کشی / مهاجرت'), ('other', '✦ سایر')],
                    max_length=15, verbose_name='نوع')),
                ('title', models.CharField(blank=True, default='', max_length=200, verbose_name='توضیح کوتاه')),
                ('date', models.DateField(verbose_name='تاریخ رویداد')),
                ('archived', models.BooleanField(default=False, verbose_name='بایگانی')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('node', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name='life_events', to='main.node', verbose_name='شخص')),
                ('owner', models.ForeignKey(blank=True, null=True,
                                            on_delete=django.db.models.deletion.CASCADE,
                                            related_name='life_events',
                                            to=settings.AUTH_USER_MODEL, verbose_name='صاحب')),
            ],
            options={'ordering': ['-date'], 'verbose_name': 'رویداد زندگی',
                     'verbose_name_plural': 'رویدادهای زندگی'},
        ),
        migrations.CreateModel(
            name='RelationshipGoal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text', models.CharField(max_length=300, verbose_name='هدف')),
                ('status', models.CharField(
                    choices=[('active', 'در جریان'), ('achieved', 'رسیدم! 🎉'), ('abandoned', 'بی‌خیالش')],
                    default='active', max_length=10)),
                ('baseline_score', models.IntegerField(blank=True, null=True, verbose_name='امتیاز سلامت شروع')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('node', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name='goals', to='main.node', verbose_name='شخص')),
                ('owner', models.ForeignKey(blank=True, null=True,
                                            on_delete=django.db.models.deletion.CASCADE,
                                            related_name='relationship_goals',
                                            to=settings.AUTH_USER_MODEL, verbose_name='صاحب')),
            ],
            options={'ordering': ['status', '-created_at'], 'verbose_name': 'هدف رابطه',
                     'verbose_name_plural': 'اهداف رابطه'},
        ),
    ]
