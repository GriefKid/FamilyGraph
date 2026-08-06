from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
import json
from datetime import date

from .models import Debt, ExtractionSuggestion, Follow, Friendship, Information, JournalEntry, Node, ProfileMediaItem, SocialCircle, SocialPost
from .templatetags.jalali_tags import jalali_date


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


class JournalMomentTests(TestCase):
    def test_quick_moment_keeps_the_event_time_and_is_private_to_its_owner(self):
        User = get_user_model()
        user = User.objects.create_user(username='journal-owner', password='SecurePass1')
        other = User.objects.create_user(username='other-user', password='SecurePass1')
        self.client.force_login(user)

        response = self.client.post(
            '/api/journal/save/',
            data=json.dumps({
                'text': 'یک گفت‌وگوی خوب با یک دوست داشتم.',
                'entry_date': '2026-08-06',
                'occurred_at': '2026-08-06T14:35',
                'entry_kind': 'moment',
                'tags': ['دوستی'],
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        entry = JournalEntry.objects.get(owner=user)
        self.assertEqual(entry.entry_kind, 'moment')
        local_time = timezone.localtime(entry.occurred_at)
        self.assertEqual(local_time.hour, 14)
        self.assertEqual(local_time.minute, 35)

        self.client.force_login(other)
        response = self.client.get('/api/journal/entries/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['entries'], [])


class JalaliPresentationTests(TestCase):
    def test_jalali_filter_uses_persian_calendar_and_digits(self):
        rendered = jalali_date(date(2026, 8, 6), 'compact')
        self.assertEqual(rendered, '۱۴۰۵/۰۵/۱۵')


class ExtractionWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='extract-owner', password='SecurePass1')
        self.other = get_user_model().objects.create_user(username='extract-other', password='SecurePass1')

    def test_persian_word_debt_is_explainable_and_not_duplicated(self):
        from .extraction import extract_text
        first = extract_text(self.user, 'کامی ازم سیصد هزار تومن قرض گرفت', 'journal', 11)
        second = extract_text(self.user, 'کامی ازم سیصد هزار تومن قرض گرفت', 'journal', 11)
        debt = next(item for item in first if item.kind == 'debt')
        self.assertEqual(debt.payload['amount_value'], 300000)
        self.assertEqual(debt.payload['direction'], 'they_owe')
        self.assertIn('explanation', debt.payload)
        self.assertEqual(second, [])

    def test_source_privacy_switch_prevents_extraction(self):
        from .extraction import extract_text
        self.user.ai_journal_enabled = False
        self.user.save(update_fields=['ai_journal_enabled'])
        self.assertEqual(extract_text(self.user, 'الی ازم سیصد هزار تومن قرض گرفت', 'journal', 12), [])

    def test_numeric_thousand_amount_is_understood(self):
        from .extraction import extract_text
        rows = extract_text(self.user, 'الی ازم 300 هزار تومان قرض گرفت', 'journal', 13)
        debt = next(item for item in rows if item.kind == 'debt')
        self.assertEqual(debt.payload['amount_value'], 300000)

    def test_approval_and_undo_respect_owner(self):
        node = Node.objects.create(owner=self.user, username='کامی', name='کامی')
        suggestion = ExtractionSuggestion.objects.create(
            owner=self.user, source='journal', source_id=1, kind='debt',
            payload={'amount_value': 300000, 'direction': 'they_owe', 'snippet': 'قرض'},
        )
        self.client.force_login(self.other)
        denied = self.client.post(f'/api/extractions/{suggestion.id}/',
                                  data=json.dumps({'action': 'approve', 'node_id': node.id}),
                                  content_type='application/json')
        self.assertEqual(denied.status_code, 404)
        self.client.force_login(self.user)
        approved = self.client.post(f'/api/extractions/{suggestion.id}/',
                                    data=json.dumps({'action': 'approve', 'node_id': node.id}),
                                    content_type='application/json')
        self.assertEqual(approved.status_code, 200)
        self.assertTrue(Debt.objects.filter(owner=self.user, amount=300000).exists())
        undone = self.client.post(f'/api/extractions/{suggestion.id}/',
                                  data=json.dumps({'action': 'undo'}), content_type='application/json')
        self.assertEqual(undone.status_code, 200)
        self.assertFalse(Debt.objects.filter(owner=self.user, amount=300000).exists())
