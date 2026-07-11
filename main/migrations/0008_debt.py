from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        # زنجیره خطی: 0007 → 0008_alter_event_options... → 0008_debt
        # (شماره مهم نیست، وابستگی گراف رو خطی می‌کنه و conflict حل می‌شه)
        ('main', '0008_alter_event_options_alter_interaction_kind'),
    ]

    operations = [
        migrations.CreateModel(
            name='Debt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('direction', models.CharField(
                    choices=[('i_owe', 'من بدهکارم'), ('they_owe', 'من طلبکارم')],
                    max_length=10, verbose_name='جهت')),
                ('amount', models.BigIntegerField(verbose_name='مبلغ')),
                ('paid', models.BigIntegerField(default=0, verbose_name='پرداخت‌شده')),
                ('currency', models.CharField(
                    choices=[('تومان', 'تومان'), ('دلار', 'دلار'), ('یورو', 'یورو')],
                    default='تومان', max_length=20, verbose_name='واحد')),
                ('date', models.DateField(verbose_name='تاریخ قرض')),
                ('due_date', models.DateField(blank=True, null=True, verbose_name='سررسید')),
                ('note', models.CharField(blank=True, default='', max_length=300, verbose_name='بابت')),
                ('settled', models.BooleanField(default=False, verbose_name='تسویه شد')),
                ('settled_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('node', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name='debts', to='main.node', verbose_name='طرف حساب')),
                ('owner', models.ForeignKey(blank=True, null=True,
                                            on_delete=django.db.models.deletion.CASCADE,
                                            related_name='debts',
                                            to=settings.AUTH_USER_MODEL, verbose_name='صاحب')),
            ],
            options={
                'ordering': ['settled', 'due_date', '-created_at'],
                'verbose_name': 'قرض/طلب',
                'verbose_name_plural': 'قرض و طلب‌ها',
            },
        ),
    ]
