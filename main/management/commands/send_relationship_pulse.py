"""Send the weekly relationship-pulse Web Push to everyone who opted in.

    python manage.py send_relationship_pulse          # send
    python manage.py send_relationship_pulse --dry-run # just print

Run it from cron once a week. No-ops cleanly when Web Push is not
configured (no VAPID keys / pywebpush not installed).
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from ...models import PushSubscription
from ...push import build_pulse, push_available, send_web_push


class Command(BaseCommand):
    help = 'Send the weekly relationship pulse to subscribed users.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='compose but do not send')

    def handle(self, *args, **options):
        dry = options['dry_run']
        if not dry and not push_available():
            self.stdout.write(self.style.WARNING(
                'Web Push not configured (VAPID keys / pywebpush missing) — nothing sent.'
            ))
            return

        User = get_user_model()
        owner_ids = (PushSubscription.objects
                     .values_list('owner_id', flat=True).distinct())
        sent = skipped = pruned = 0

        for user in User.objects.filter(id__in=list(owner_ids)):
            payload = build_pulse(user)
            if not payload:
                skipped += 1
                continue
            if dry:
                self.stdout.write(f'[{user}] {payload["body"]}')
                sent += 1
                continue
            for sub in PushSubscription.objects.filter(owner=user):
                ok, gone = send_web_push(sub.as_subscription_info(), payload)
                if gone:
                    sub.delete()
                    pruned += 1
                elif ok:
                    sub.last_sent_at = timezone.now()
                    sub.failure_count = 0
                    sub.save(update_fields=['last_sent_at', 'failure_count'])
                    sent += 1
                else:
                    sub.failure_count += 1
                    sub.save(update_fields=['failure_count'])

        self.stdout.write(self.style.SUCCESS(
            f'pulse done — sent {sent}, no-content {skipped}, pruned {pruned}'
        ))
