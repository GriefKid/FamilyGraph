from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Node


class ProfileRouteRegressionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='route-user', password='SecurePass1',
        )
        self.client.force_login(self.user)

    def test_profile_routes_render_for_a_new_user(self):
        for path in ('/profile/', '/profile/edit/', '/settings/'):
            with self.subTest(path=path):
                response = self.client.get(path, follow=True)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, 'social/profile_edit.html')

    def test_legacy_profile_links_use_one_hop_redirects(self):
        for path in ('/profile/', '/settings/'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response['Location'], '/profile/edit/')

    def test_profile_edit_saves_without_a_self_node(self):
        response = self.client.post('/profile/edit/', {
            'action': 'profile', 'first_name': 'سارا', 'last_name': 'آزمون',
            'birth_date': '1995-05-10', 'bio': 'یک معرفی کوتاه',
        })
        self.assertRedirects(response, '/profile/edit/', fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'سارا')
        self.assertEqual(self.user.birth_date, date(1995, 5, 10))

    def test_settings_root_node_post_is_owner_scoped(self):
        node = Node.objects.create(owner=self.user, username='my-person')
        response = self.client.post('/settings/', {'root_node': str(node.pk)})
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.root_node_id, node.pk)


class FirstVisitOnboardingTests(TestCase):
    def test_new_user_sees_three_simple_first_steps(self):
        user = get_user_model().objects.create_user(
            username='first-visit', password='SecurePass1',
        )
        self.client.force_login(user)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'سه قدم ساده')
        self.assertContains(response, 'افزودن اولین شخص')
        self.assertContains(response, 'ثبت اولین لحظه')
