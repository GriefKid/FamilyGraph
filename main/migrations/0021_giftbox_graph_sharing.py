from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0020_giftbox'),
    ]

    operations = [
        # حذف فیلدهای قدیمی متن‌محور
        migrations.RemoveField(model_name='giftbox', name='content_type'),
        migrations.RemoveField(model_name='giftbox', name='content_text'),
        # اضافه کردن فیلدهای جدید گراف‌محور
        migrations.AddField(
            model_name='giftbox',
            name='share_type',
            field=models.CharField(
                choices=[('node','👤 راس'),('edge','🔗 یال'),('data','📊 دیتا')],
                default='node', max_length=8,
            ),
        ),
        migrations.AddField(
            model_name='giftbox',
            name='payload',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='giftbox',
            name='content_added',
            field=models.BooleanField(default=False, verbose_name='اضافه شده به گراف'),
        ),
        migrations.AlterModelOptions(
            name='giftbox',
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'اشتراک‌گذاری',
                'verbose_name_plural': 'اشتراک‌گذاری‌ها',
            },
        ),
    ]
