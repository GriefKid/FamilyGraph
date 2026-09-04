import io
import tempfile
from django.core import mail

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from PIL import Image

from .models import FollowUp, JournalImage, Node, SocialPost


def _png(name):
    output = io.BytesIO()
    Image.new('RGB', (12, 12), 'green').save(output, 'PNG')
    return SimpleUploadedFile(name, output.getvalue(), content_type='image/png')


class AccountDeletionTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.media_settings = override_settings(MEDIA_ROOT=self.media.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        User = get_user_model()
        self.user = User.objects.create_user(username='delete-me', password='SecurePass1')
        self.other = User.objects.create_user(username='keep-me', password='SecurePass1')
        self.client.force_login(self.user)

    def test_wrong_password_or_username_does_not_delete_account(self):
        response = self.client.post('/account/delete/', {
            'confirmation': 'someone-else', 'password': 'SecurePass1',
        })
        self.assertRedirects(response, '/profile/edit/', fetch_redirect_response=False)
        response = self.client.post('/account/delete/', {
            'confirmation': self.user.username, 'password': 'wrong-password',
        })
        self.assertRedirects(response, '/profile/edit/', fetch_redirect_response=False)
        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())

    def test_confirmed_delete_removes_tenant_rows_and_its_files(self):
        self.user.avatar = _png('avatar.png')
        self.user.save(update_fields=['avatar'])
        node = Node.objects.create(
            owner=self.user, username='private-person', picture=_png('person.png'),
        )
        journal_image = JournalImage.objects.create(
            owner=self.user, image=_png('journal.png'),
        )
        post = SocialPost.objects.create(
            author=self.user, body='public', image=_png('post.png'),
        )
        file_names = [self.user.avatar.name, node.picture.name,
                      journal_image.image.name, post.image.name]
        self.assertTrue(all(default_storage.exists(name) for name in file_names))

        response = self.client.post('/account/delete/', {
            'confirmation': self.user.username, 'password': 'SecurePass1',
        })
        self.assertRedirects(response, '/login/', fetch_redirect_response=False)
        self.assertFalse(get_user_model().objects.filter(username='delete-me').exists())
        self.assertTrue(get_user_model().objects.filter(username='keep-me').exists())
        self.assertFalse(any(default_storage.exists(name) for name in file_names))


class BrowserSecurityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='browser-security', password='SecurePass1',
        )

    def test_responses_include_baseline_security_policy(self):
        response = self.client.get('/login/')
        policy = response.headers['Content-Security-Policy']
        self.assertIn("object-src 'none'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertEqual(response.headers['Referrer-Policy'], 'strict-origin-when-cross-origin')
        self.assertIn('microphone=(self)', response.headers['Permissions-Policy'])

    def test_followup_snooze_rejects_missing_csrf_token(self):
        node = Node.objects.create(owner=self.user, username='csrf-person')
        followup = FollowUp.objects.create(owner=self.user, node=node, text='پیگیری')
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(
            f'/api/followups/{followup.pk}/snooze/',
            data='{"days": 3}', content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_base_shell_uses_dom_rendering_for_api_driven_navigation(self):
        self.client.force_login(self.user)
        response = self.client.get('/')
        self.assertContains(response, 'paletteResults.replaceChildren()')
        self.assertContains(response, "link.href=_safeLocalUrl(item.url)")
        self.assertNotContains(response, 'paletteResults.innerHTML=')


class PasswordResetTests(TestCase):
    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_password_reset_sends_a_non_disclosing_email(self):
        User = get_user_model()
        User.objects.create_user(
            username='reset-user', email='reset@example.com', password='SecurePass1',
        )

        response = self.client.post('/password-reset/', {'email': 'reset@example.com'})

        self.assertRedirects(response, '/password-reset/done/', fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/reset/', mail.outbox[0].body)

        mail.outbox = []
        self.client.post('/password-reset/', {'email': 'missing@example.com'})
        self.assertEqual(mail.outbox, [])

    def test_privacy_page_is_public_and_reset_pages_are_not_login_redirected(self):
        self.assertEqual(self.client.get('/privacy/').status_code, 200)
        self.assertEqual(self.client.get('/terms/').status_code, 200)
        self.assertEqual(self.client.get('/password-reset/').status_code, 200)
