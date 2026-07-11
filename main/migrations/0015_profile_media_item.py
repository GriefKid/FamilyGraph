from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0014_user_profile_cover'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProfileMediaItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('book', 'Book'), ('movie', 'Movie'), ('series', 'Series')], max_length=10)),
                ('title', models.CharField(max_length=240)),
                ('creator', models.CharField(blank=True, max_length=180)),
                ('rating', models.FloatField(default=0)),
                ('completed_on', models.DateField(blank=True, null=True)),
                ('source', models.CharField(choices=[('manual', 'Manual'), ('journal', 'Journal'), ('imported', 'Imported')], default='manual', max_length=12)),
                ('notes', models.TextField(blank=True)),
                ('analysis', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('source_journal', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='detected_media_items', to='main.journalentry')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='profile_media_items', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-completed_on', '-created_at'],
                'unique_together': {('user', 'kind', 'title')},
            },
        ),
    ]
