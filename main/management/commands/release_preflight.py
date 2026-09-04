"""Fail-fast checks for a production release."""

import os

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = 'Check required production settings and dependencies before release.'

    def handle(self, *args, **options):
        failures = []
        checks = []

        def check(label, passed, detail):
            checks.append((label, passed, detail))
            if not passed:
                failures.append(f'{label}: {detail}')

        check('DEBUG', not settings.DEBUG, 'must be False.')
        check('SECRET_KEY', len(settings.SECRET_KEY) >= 50 and
              not settings.SECRET_KEY.startswith('django-insecure-'),
              'a random key with at least 50 characters is required.')
        check('ALLOWED_HOSTS', bool(settings.ALLOWED_HOSTS) and '*' not in settings.ALLOWED_HOSTS,
              'set a real domain; wildcard is not allowed in production.')
        check('HTTPS', bool(getattr(settings, 'SECURE_SSL_REDIRECT', False)),
              'SECURE_SSL_REDIRECT must be enabled.')
        check('CSRF origins', bool(getattr(settings, 'CSRF_TRUSTED_ORIGINS', [])),
              'CSRF_TRUSTED_ORIGINS must include the https:// domain.')
        check('Email', 'smtp' in settings.EMAIL_BACKEND.lower() and bool(settings.EMAIL_HOST)
              and bool(settings.DEFAULT_FROM_EMAIL),
              'SMTP and DEFAULT_FROM_EMAIL are required for password reset.')

        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
            check('Database', True, 'ok')
        except Exception as exc:  # noqa: BLE001
            check('Database', False, str(exc)[:180])

        try:
            executor = MigrationExecutor(connection)
            pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
            check('Migrations', not pending,
                  f'{len(pending)} migration(s) pending; run migrate first.')
        except Exception as exc:  # noqa: BLE001
            check('Migrations', False, str(exc)[:180])

        try:
            probe_key = 'release-preflight:cache'
            cache.set(probe_key, 'ok', timeout=10)
            cache_ok = cache.get(probe_key) == 'ok'
            check('Cache', cache_ok, 'ok' if cache_ok else 'cache did not respond.')
        except Exception as exc:  # noqa: BLE001
            check('Cache', False, str(exc)[:180])

        cloud = any(os.environ.get(key, '').strip() for key in (
            'GROQ_API_KEY', 'MISTRAL_API_KEY', 'GEMINI_API_KEY', 'OPENROUTER_API_KEY',
        ))
        local = os.environ.get('OLLAMA_ENABLED', '1').lower() in {'1', 'true', 'yes', 'on'}
        check('AI provider', cloud or local, 'at least one cloud provider or Ollama is required.')
        check('Media root', os.path.isdir(settings.MEDIA_ROOT) and os.access(settings.MEDIA_ROOT, os.W_OK),
              'MEDIA_ROOT must exist and be writable.')

        for label, passed, detail in checks:
            marker = self.style.SUCCESS('OK') if passed else self.style.ERROR('FAIL')
            self.stdout.write(f'[{marker}] {label}: {detail}')
        if failures:
            raise CommandError('Release preflight failed: ' + ' | '.join(failures))
        self.stdout.write(self.style.SUCCESS('Release preflight passed.'))
