"""Synthesise an AI knowledge profile for every person and every relationship.

    python manage.py synthesize_personas                 # all users
    python manage.py synthesize_personas --user alice     # one user

Needs a working AI provider (AI_PROVIDER / *_API_KEY, or local Ollama).
Safe to re-run; each pass keeps the previous statements as a version.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from ...views_persona import synthesize_everything


class Command(BaseCommand):
    help = 'Synthesise knowledge profiles for all people and relationships.'

    def add_arguments(self, parser):
        parser.add_argument('--user', help='username to limit the run to')

    def handle(self, *args, **options):
        User = get_user_model()
        users = User.objects.all()
        if options.get('user'):
            users = users.filter(username=options['user'])
            if not users.exists():
                self.stderr.write(f"no user named {options['user']!r}")
                return

        for user in users:
            self.stdout.write(f'· {user.username} …')
            state = synthesize_everything(user)
            self.stdout.write(self.style.SUCCESS(
                f"  people {state['people_ok']} · relationships {state['rel_ok']} · "
                f"skipped {state['skipped']} · failed {state['failed']}"
            ))
            if state.get('error'):
                self.stderr.write(f"  first error: {state['error']}")
