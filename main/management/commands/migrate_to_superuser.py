"""
py manage.py migrate_to_superuser

کاری که می‌کنه:
۱. یوزر anarshgriefkid / Mamad123 (superuser) می‌سازه — اگه وجود داشت رمز update می‌کنه
۲. همه داده‌های بی‌صاحب رو به این یوزر assign می‌کنه
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = 'ساخت superuser اولیه و assign کردن همه داده‌ها به اون'

    def handle(self, *args, **options):
        User = get_user_model()

        # ── ساخت / update یوزر ──────────────────────────────
        user, created = User.objects.get_or_create(
            username='anarshgriefkid',
            defaults={
                'email':        'anarshgriefkid@gmail.com',
                'first_name':   'سجاد',
                'last_name':    'انگوتی',
                'is_staff':     True,
                'is_superuser': True,
                'is_active':    True,
            }
        )
        user.set_password('Mamad123')
        user.is_staff     = True
        user.is_superuser = True
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f'✓ یوزر anarshgriefkid ساخته شد'))
        else:
            self.stdout.write(self.style.SUCCESS(f'✓ یوزر anarshgriefkid آپدیت شد'))

        # ── self-node ────────────────────────────────────────
        from main.models import Node
        self_node, node_created = Node.objects.get_or_create(
            username = user.username,
            owner    = user,
            defaults = {
                'first_name':     user.first_name,
                'last_name':      user.last_name,
                'career':         user.career or '',
                'username_locked': True,
            }
        )
        if node_created:
            self.stdout.write(self.style.SUCCESS(f'✓ self-node ساجاد انگوتی ساخته شد'))
        else:
            self.stdout.write(f'  → self-node از قبل وجود داشت')

        # ── assign داده‌ها ───────────────────────────────────
        from main.models import (
            Node, Relationship, Event, Group,
            JournalEntry, AlertAction,
        )

        counts = {}
        for Model in [Node, Relationship, Event, Group, JournalEntry, AlertAction]:
            qs = Model.objects.filter(owner__isnull=True)
            n  = qs.count()
            if n:
                qs.update(owner=user)
            counts[Model.__name__] = n

        for model_name, n in counts.items():
            if n:
                self.stdout.write(f'  → {model_name}: {n} رکورد assign شد')

        self.stdout.write(self.style.SUCCESS('\n✅ migrate_to_superuser کامل شد'))
        self.stdout.write('حالا می‌تونی با anarshgriefkid / Mamad123 لاگین کنی.')
