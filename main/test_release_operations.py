import io
import os
from tempfile import TemporaryDirectory
from unittest import mock

from django.core import management
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings


class ReleaseOperationsTests(TestCase):
    def test_system_health_is_public_and_reports_database_and_cache(self):
        response = Client().get('/api/system/health/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['database'], 'ok')
        self.assertEqual(payload['cache'], 'ok')
        self.assertIn('time', payload)

    def test_release_preflight_passes_with_production_dependencies(self):
        with TemporaryDirectory() as media_root, override_settings(
            DEBUG=False,
            SECRET_KEY='release-test-secret-' + ('x' * 50),
            ALLOWED_HOSTS=['example.com'],
            SECURE_SSL_REDIRECT=True,
            CSRF_TRUSTED_ORIGINS=['https://example.com'],
            EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
            EMAIL_HOST='smtp.example.com',
            DEFAULT_FROM_EMAIL='noreply@example.com',
            MEDIA_ROOT=media_root,
        ), mock.patch.dict(os.environ, {'OLLAMA_ENABLED': '1'}):
            output = io.StringIO()
            management.call_command('release_preflight', stdout=output)

        self.assertIn('Release preflight passed.', output.getvalue())
        self.assertIn('[OK] Migrations:', output.getvalue())

    def test_release_preflight_fails_fast_for_debug_mode(self):
        with override_settings(DEBUG=True), mock.patch.dict(
            os.environ, {'OLLAMA_ENABLED': '0'}, clear=False,
        ):
            with self.assertRaisesRegex(CommandError, 'DEBUG:'):
                management.call_command('release_preflight', stdout=io.StringIO())
