from django.contrib.auth import get_user_model
from django.test import TestCase
import json

from .models import Follow, Friendship, Information, ProfileMediaItem, SocialCircle, SocialPost


class RegistrationOnboardingTests(TestCase):
    def test_registration_creates_a_private_ai_profile_for_the_root_node(self):
        response = self.client.post('/register/', {
            'step': '1',
            'username': 'onboarding_user',
            'email': 'onboarding@example.com',
            'password': 'SecurePass1',
            'password2': 'SecurePass1',
        })
        self.assertRedirects(response, '/register/?step=2', fetch_redirect_response=False)

        response = self.client.post('/register/', {
            'step': '2',
            'first_name': 'سارا',
            'last_name': 'آزمون',
            'birth_date': '1995-05-10',
            'career': 'طراح محصول',
            'city': 'تهران',
            'country': 'ایران',
            'bio': 'برای ارتباط‌های عمیق و آرام ارزش قائلم.',
            'interests': 'کتاب، موسیقی، پیاده‌روی',
            'values': 'صداقت، احترام',
            'communication_style': 'برای موضوع مهم تماس را ترجیح می‌دهم.',
            'relationship_goal': 'با خانواده منظم‌تر در تماس باشم.',
            'boundaries': 'یادآوری‌های زیاد نفرست.',
            'social_energy': 'balanced',
        })
        self.assertRedirects(response, '/register/?step=3', fetch_redirect_response=False)

        response = self.client.post('/register/', {'step': '3', 'is_public': 'false'})
        self.assertRedirects(response, '/register/?step=4', fetch_redirect_response=False)

        session = self.client.session
        session['captcha_answer'] = 7
        session.save()
        response = self.client.post('/register/', {'step': '4', 'captcha': '7'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)

        user = get_user_model().objects.get(username='onboarding_user')
        self.assertEqual(user.country, 'ایران')
        self.assertEqual(user.root_node.birth_day.isoformat(), '1995-05-10')

        profile = Information.objects.get(node=user.root_node)
        self.assertEqual(profile.visibility, 'private')
        self.assertEqual(profile.data['interests'], ['کتاب', 'موسیقی', 'پیاده‌روی'])
        self.assertEqual(profile.data['values'], ['صداقت', 'احترام'])
        self.assertEqual(profile.data['social_energy'], 'balanced')


class PublicSocialTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user(
            username='me', password='SecurePass1', is_public=True, discoverable=True,
            public_interests=['کتاب', 'موسیقی'], public_values=['صداقت'],
        )
        self.match = User.objects.create_user(
            username='match', password='SecurePass1', is_public=True, discoverable=True,
            public_interests=['کتاب', 'سفر'], public_values=['صداقت'],
        )
        self.private = User.objects.create_user(
            username='private', password='SecurePass1', is_public=False,
        )

    def test_discovery_uses_only_public_signals_and_returns_a_reason(self):
        self.client.force_login(self.me)
        response = self.client.get('/api/social/suggest/')
        self.assertEqual(response.status_code, 200)
        users = json.loads(response.content)['users']
        match = next(card for card in users if card['username'] == 'match')
        self.assertIn('علاقه مشترک: کتاب', match['reasons'])
        self.assertIn('ارزش مشترک: صداقت', match['reasons'])
        self.assertNotIn('private', [card['username'] for card in users])

    def test_social_feed_excludes_posts_from_private_profiles(self):
        Follow.objects.create(follower=self.me, target=self.match)
        Follow.objects.create(follower=self.me, target=self.private)
        SocialPost.objects.create(author=self.match, body='این پست عمومی است.')
        SocialPost.objects.create(author=self.private, body='این پست نباید دیده شود.')
        self.client.force_login(self.me)
        response = self.client.get('/social/')
        self.assertContains(response, 'این پست عمومی است.')
        self.assertNotContains(response, 'این پست نباید دیده شود.')

    def test_public_post_requires_a_public_profile(self):
        self.client.force_login(self.private)
        response = self.client.post(
            '/api/social/posts/',
            data=json.dumps({'body': 'نباید ساخته شود'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SocialPost.objects.exists())

    def test_circle_only_adds_existing_connections(self):
        Friendship.objects.create(user=self.me, friend=self.match)
        Friendship.objects.create(user=self.match, friend=self.me)
        self.client.force_login(self.me)
        response = self.client.post(
            '/api/social/circles/',
            data=json.dumps({
                'name': 'Book club',
                'member_ids': [self.match.id, self.private.id],
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        circle = SocialCircle.objects.get(name='Book club')
        self.assertSetEqual(
            set(circle.members.values_list('id', flat=True)),
            {self.me.id, self.match.id},
        )
