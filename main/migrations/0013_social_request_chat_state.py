from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0012_follow'),
    ]

    operations = [
        migrations.AddField(
            model_name='friendrequest',
            name='request_type',
            field=models.CharField(choices=[('follow', 'Follow'), ('connection', 'Connection')], default='connection', max_length=12),
        ),
        migrations.AlterUniqueTogether(
            name='friendrequest',
            unique_together={('sender', 'receiver', 'request_type', 'status')},
        ),
        migrations.AddField(
            model_name='directmessage',
            name='delivered_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='directmessage',
            name='read_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='directmessage',
            name='edited_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='directmessage',
            name='reply_to',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='replies', to='main.directmessage'),
        ),
    ]
