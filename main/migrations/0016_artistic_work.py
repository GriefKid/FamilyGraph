from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0015_profile_media_item'),
    ]

    operations = [
        migrations.CreateModel(
            name='ArtisticWork',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('book', 'Book'), ('movie', 'Movie'), ('series', 'Series')], max_length=10)),
                ('title', models.CharField(max_length=240)),
                ('creator', models.CharField(blank=True, max_length=180)),
                ('year', models.PositiveIntegerField(blank=True, null=True)),
                ('description', models.TextField(blank=True)),
                ('genres', models.JSONField(blank=True, default=list)),
                ('analysis', models.JSONField(blank=True, default=dict)),
                ('cover_url', models.URLField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['title'],
                'unique_together': {('kind', 'title')},
            },
        ),
        migrations.AddField(
            model_name='profilemediaitem',
            name='work',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='user_items', to='main.artisticwork'),
        ),
    ]
