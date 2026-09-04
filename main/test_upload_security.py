import io
import tempfile
import zipfile

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from .models import JournalImage, Node, SocialPost


def _image_upload(name='photo.png', color='purple'):
    output = io.BytesIO()
    Image.new('RGB', (24, 18), color).save(output, 'PNG')
    return SimpleUploadedFile(name, output.getvalue(), content_type='image/png')


class SecureUploadTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.media_settings = override_settings(MEDIA_ROOT=self.media.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        User = get_user_model()
        self.owner = User.objects.create_user(username='upload-owner', password='SecurePass1')
        self.other = User.objects.create_user(username='upload-other', password='SecurePass1')
        self.client.force_login(self.owner)

    def test_journal_upload_rejects_executable_disguised_as_jpeg(self):
        response = self.client.post('/api/journal/upload-image/', {
            'image': SimpleUploadedFile(
                'totally-a-photo.jpg', b'MZ' + b'\0' * 200,
                content_type='image/jpeg',
            ),
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'invalid_image')
        self.assertFalse(JournalImage.objects.filter(owner=self.owner).exists())

    def test_journal_upload_is_normalised_and_randomly_named(self):
        response = self.client.post('/api/journal/upload-image/', {
            'image': _image_upload('identifying filename.png'),
        })
        self.assertEqual(response.status_code, 200)
        image = JournalImage.objects.get(owner=self.owner)
        self.assertTrue(image.image.name.startswith('journal/'))
        self.assertNotIn('identifying', image.image.name)
        with Image.open(image.image.path) as saved:
            self.assertEqual(saved.format, 'PNG')

    def test_private_journal_media_is_only_available_to_its_owner(self):
        image = JournalImage.objects.create(owner=self.owner, image=_image_upload('private.png'))
        response = self.client.get(image.image.url)
        self.assertEqual(response.status_code, 200)
        response.close()

        self.client.force_login(self.other)
        self.assertEqual(self.client.get(image.image.url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(image.image.url).status_code, 404)

    def test_unreferenced_media_file_is_never_served(self):
        path = default_storage.save('journal/orphan.png', _image_upload())
        self.assertEqual(self.client.get('/media/' + path).status_code, 404)

    def test_public_node_image_requires_a_public_owner_and_authenticated_viewer(self):
        self.owner.is_public = True
        self.owner.save(update_fields=['is_public'])
        node = Node.objects.create(
            owner=self.owner, username='public-person', is_public=True,
            picture=_image_upload('person.png'),
        )
        self.client.force_login(self.other)
        response = self.client.get(node.picture.url)
        self.assertEqual(response.status_code, 200)
        response.close()
        self.client.logout()
        self.assertEqual(self.client.get(node.picture.url).status_code, 404)

    def test_stt_rejects_unknown_binary_before_contacting_provider(self):
        response = self.client.post('/api/stt/', {
            'audio': SimpleUploadedFile(
                'voice.webm', b'MZ' + b'\0' * 200,
                content_type='audio/webm',
            ),
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'invalid_audio')

    def test_profile_cover_rejects_a_spoofed_image(self):
        response = self.client.post('/api/social/profile/cover/', {
            'cover_image': SimpleUploadedFile(
                'cover.png', b'MZ' + b'\0' * 200,
                content_type='image/png',
            ),
        })
        self.assertEqual(response.status_code, 400)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.cover_image)

    def test_social_post_rejects_a_spoofed_image(self):
        self.owner.is_public = True
        self.owner.save(update_fields=['is_public'])
        response = self.client.post('/profile/edit/', {
            'action': 'post',
            'body': 'یک پست عمومی',
            'post_image': SimpleUploadedFile(
                'post.jpg', b'MZ' + b'\0' * 200,
                content_type='image/jpeg',
            ),
        })
        self.assertRedirects(response, '/profile/edit/', fetch_redirect_response=False)
        self.assertFalse(SocialPost.objects.filter(author=self.owner).exists())

    def test_csv_preview_rejects_an_oversized_file(self):
        response = self.client.post('/api/relationship-life/import/csv/preview/', {
            'file': SimpleUploadedFile(
                'people.csv', b'x' * (5 * 1024 * 1024 + 1),
                content_type='text/csv',
            ),
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('5', response.json()['error'])

    def test_whatsapp_preview_rejects_a_zip_bomb_ratio(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('chat.txt', 'تکرار' * 500_000)
        response = self.client.post('/api/import/whatsapp/scan/', {
            'file': SimpleUploadedFile(
                'chat.zip', output.getvalue(), content_type='application/zip',
            ),
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('فشرده', response.json()['error'])
