from django.db import migrations


def forwards(apps, schema_editor):
    ArtisticWork = apps.get_model('main', 'ArtisticWork')
    ProfileMediaItem = apps.get_model('main', 'ProfileMediaItem')
    for item in ProfileMediaItem.objects.filter(work__isnull=True):
        work, _ = ArtisticWork.objects.get_or_create(
            kind=item.kind,
            title=item.title,
            defaults={
                'creator': item.creator,
                'analysis': {
                    'summary': 'این اثر برای شناخت سلیقه، ارزش‌ها و جهان ذهنی فرد استفاده می‌شود.',
                    'personality_signals': [],
                    'relationship_signals': [],
                },
            },
        )
        item.work = work
        item.save(update_fields=['work'])


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0016_artistic_work'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
