from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0049_user_privacy_consent_at'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='NodeContactDetails',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(blank=True, max_length=254, verbose_name='ایمیل')),
                ('alternate_phone', models.CharField(blank=True, max_length=20, verbose_name='تلفن دوم')),
                ('bank_name', models.CharField(blank=True, max_length=120, verbose_name='نام بانک')),
                ('card_number', models.CharField(blank=True, max_length=32, verbose_name='شماره کارت')),
                ('account_number', models.CharField(blank=True, max_length=40, verbose_name='شماره حساب')),
                ('iban', models.CharField(blank=True, max_length=34, verbose_name='شماره شبا')),
                ('address', models.TextField(blank=True, verbose_name='آدرس')),
                ('notes', models.TextField(blank=True, verbose_name='یادداشت تماس')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('node', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='contact_details', to='main.node', verbose_name='مخاطب')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='node_contact_details', to=settings.AUTH_USER_MODEL, verbose_name='صاحب اطلاعات')),
            ],
        ),
    ]
