import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .models import Interaction, Node
from .views_whatsapp_import import parse_whatsapp_export


class ConsolidatedNavigationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='navigation-owner', password='SecurePass1')
        self.other = get_user_model().objects.create_user(
            username='navigation-other', password='SecurePass1')
        self.client.force_login(self.user)

    def test_all_parent_pages_render_and_old_pages_stay_available(self):
        parent_pages = ('/people/', '/insight-center/', '/relationship-work/', '/import/')
        old_pages = ('/nodes/', '/graph/', '/relationships/', '/psychology/',
                     '/memory/', '/relationship-life/', '/import/telegram/')
        for url in parent_pages + old_pages:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_parent_pages_require_login(self):
        self.client.logout()
        for url in ('/people/', '/insight-center/', '/relationship-work/', '/import/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 302)

    def test_whatsapp_parser_supports_android_and_ios_exports(self):
        parsed = parse_whatsapp_export(
            '07/08/2026, 09:10 - سارا: سلام\n'
            '[07/08/2026, 10:11:12] علی: حالت چطوره؟\n'
            'این خط ادامهٔ پیام است\n'
        )
        self.assertEqual(parsed['سارا']['messages'], 1)
        self.assertEqual(parsed['علی']['messages'], 1)
        self.assertEqual(parsed['سارا']['dates'], {'2026-08-07'})

    def test_whatsapp_preview_and_apply_are_tenant_scoped(self):
        own_node = Node.objects.create(owner=self.user, username='sara', name='سارا')
        other_node = Node.objects.create(owner=self.other, username='private', name='خصوصی')
        upload = SimpleUploadedFile(
            'chat.txt', '07/08/2026, 09:10 - سارا: سلام'.encode('utf-8'),
            content_type='text/plain')
        preview = self.client.post('/api/import/whatsapp/scan/', {'file': upload})
        self.assertEqual(preview.status_code, 200)
        contact = preview.json()['contacts'][0]
        self.assertEqual(contact['suggested']['id'], own_node.id)

        applied = self.client.post('/api/import/whatsapp/apply/', data=json.dumps({
            'mapping': [{'name': 'سارا', 'action': f'node:{other_node.id}'}],
            'make_edges': False,
        }), content_type='application/json')
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(applied.json()['stats']['contacts'], 0)
        self.assertFalse(Interaction.objects.filter(owner=self.user).exists())

    def test_whatsapp_apply_creates_confirmed_person_and_deduplicates_days(self):
        content = ('07/08/2026, 09:10 - کامی: سلام\n'
                   '07/08/2026, 11:20 - کامی: خوبی؟').encode('utf-8')
        for expected in (1, 0):
            upload = SimpleUploadedFile('chat.txt', content, content_type='text/plain')
            self.assertEqual(self.client.post(
                '/api/import/whatsapp/scan/', {'file': upload}).status_code, 200)
            action = 'new' if expected else 'node:' + str(Node.objects.get(
                owner=self.user, username='کامی').id)
            response = self.client.post('/api/import/whatsapp/apply/', data=json.dumps({
                'mapping': [{'name': 'کامی', 'action': action}], 'make_edges': False,
            }), content_type='application/json')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['stats']['interactions'], expected)
        self.assertEqual(Interaction.objects.filter(owner=self.user).count(), 1)
