from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0050_nodecontactdetails'),
    ]

    operations = [
        migrations.AddField(
            model_name='nodecontactdetails',
            name='instagram_username',
            field=models.CharField(blank=True, max_length=64, verbose_name='نام کاربری اینستاگرام'),
        ),
        migrations.AddField(
            model_name='nodecontactdetails',
            name='linkedin_url',
            field=models.URLField(blank=True, max_length=300, verbose_name='لینک لینکدین'),
        ),
        migrations.AddField(
            model_name='nodecontactdetails',
            name='telegram_username',
            field=models.CharField(blank=True, max_length=64, verbose_name='نام کاربری تلگرام'),
        ),
        migrations.AddField(
            model_name='nodecontactdetails',
            name='whatsapp_number',
            field=models.CharField(blank=True, max_length=20, verbose_name='شماره واتساپ'),
        ),
        migrations.AddField(
            model_name='nodecontactdetails',
            name='x_username',
            field=models.CharField(blank=True, max_length=64, verbose_name='نام کاربری X'),
        ),
    ]
