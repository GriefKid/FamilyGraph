from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('main', '0010_lifeevent_goal'),
    ]

    operations = [
        migrations.AlterField(
            model_name='information',
            name='visibility',
            field=models.CharField(
                choices=[
                    ('public', 'Everyone can see'),
                    ('friends', 'Friends can see'),
                    ('selected', 'Selected friends can see'),
                    ('shared', 'Someone can see'),
                    ('private', 'Nobody'),
                ],
                default='private',
                max_length=10,
            ),
        ),
        migrations.CreateModel(
            name='ChatAnalysis',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('summary', models.TextField(blank=True)),
                ('mood', models.CharField(blank=True, max_length=120)),
                ('topics', models.JSONField(blank=True, default=list)),
                ('signals', models.JSONField(blank=True, default=list)),
                ('suggestions', models.JSONField(blank=True, default=list)),
                ('raw', models.JSONField(blank=True, default=dict)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('friend', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chat_analysed_by', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chat_analyses', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-updated_at'], 'unique_together': {('user', 'friend')}},
        ),
        migrations.CreateModel(
            name='DirectMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('content', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('analyzed', models.BooleanField(default=False)),
                ('receiver', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='received_direct_messages', to=settings.AUTH_USER_MODEL)),
                ('sender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sent_direct_messages', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['created_at']},
        ),
        migrations.CreateModel(
            name='FriendRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('message', models.CharField(blank=True, max_length=240)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')], default='pending', max_length=12)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('receiver', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='received_friend_requests', to=settings.AUTH_USER_MODEL)),
                ('sender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sent_friend_requests', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at'], 'unique_together': {('sender', 'receiver', 'status')}},
        ),
        migrations.CreateModel(
            name='Friendship',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('friend', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='friend_of', to=settings.AUTH_USER_MODEL)),
                ('relationship', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='friendships', to='main.relationship')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='friendships', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['friend__username'], 'unique_together': {('user', 'friend')}},
        ),
        migrations.AddField(
            model_name='information',
            name='shared_with',
            field=models.ManyToManyField(blank=True, related_name='shared_informations', to=settings.AUTH_USER_MODEL, verbose_name='Shared with selected friends'),
        ),
    ]
