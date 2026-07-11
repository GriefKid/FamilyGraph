from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

KINDS = [('book', 'Book'), ('movie', 'Movie'), ('series', 'Series'), ('music', 'Music')]


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('main', '0017_link_existing_media_items'),
    ]

    operations = [
        migrations.AlterField(
            model_name='artisticwork',
            name='kind',
            field=models.CharField(choices=KINDS, max_length=10),
        ),
        migrations.AlterField(
            model_name='profilemediaitem',
            name='kind',
            field=models.CharField(choices=KINDS, max_length=10),
        ),
        migrations.CreateModel(
            name='PersonaProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('summary', models.TextField(blank=True, default='', verbose_name='جمع‌بندی')),
                ('statements', models.JSONField(blank=True, default=list, verbose_name='جملات شناخت')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('node', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                              related_name='persona', to='main.node', verbose_name='شخص')),
                ('owner', models.ForeignKey(blank=True, null=True,
                                            on_delete=django.db.models.deletion.CASCADE,
                                            related_name='personas',
                                            to=settings.AUTH_USER_MODEL, verbose_name='صاحب')),
            ],
            options={'verbose_name': 'شناخت شخص', 'verbose_name_plural': 'شناخت اشخاص'},
        ),
        migrations.CreateModel(
            name='RelationshipProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('summary', models.TextField(blank=True, default='')),
                ('statements', models.JSONField(blank=True, default=list)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('relationship', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                                      related_name='profile', to='main.relationship',
                                                      verbose_name='رابطه')),
                ('owner', models.ForeignKey(blank=True, null=True,
                                            on_delete=django.db.models.deletion.CASCADE,
                                            related_name='relationship_profiles',
                                            to=settings.AUTH_USER_MODEL, verbose_name='صاحب')),
            ],
            options={'verbose_name': 'شناخت رابطه', 'verbose_name_plural': 'شناخت روابط'},
        ),
    ]
