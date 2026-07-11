from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0013_social_request_chat_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='cover_image',
            field=models.ImageField(blank=True, null=True, upload_to='profile_covers/', verbose_name='بک‌گراند پروفایل'),
        ),
        migrations.AddField(
            model_name='user',
            name='cover_preset',
            field=models.CharField(blank=True, default='aurora', max_length=40, verbose_name='بک‌گراند آماده'),
        ),
    ]
