from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
import json
import io
from datetime import date, timedelta

from .models import AIExtractionTrace, Commitment, Debt, Event, ExtractionSuggestion, FeatureFlag, Follow, FollowUp, Friendship, GiftIdea, Information, Interaction, JournalEntry, KnowledgeTriple, MeetingReflection, MemoryFact, Node, NodeAlias, NodeMergeOperation, NodeSafetySetting, ProfileMediaItem, Relationship, RelationshipRecommendation, SocialCircle, SocialPost
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



class DashboardBriefingTests(TestCase):
    def test_new_workspace_gets_a_clear_first_person_action(self):
        user = get_user_model().objects.create_user(username='briefing-user', password='SecurePass1')
        root = Node.objects.create(owner=user, username='briefing-me', name='من')
        user.root_node = root
        user.save(update_fields=['root_node'])
        self.client.force_login(user)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'افزودن اولین شخص مهم')


    def test_base_navigation_is_keyboard_accessible(self):
        user = get_user_model().objects.create_user(username='accessible-user', password='SecurePass1')
        self.client.force_login(user)
        response = self.client.get('/')
        self.assertContains(response, 'href="#main-content"')
        self.assertContains(response, 'id="main-content" tabindex="-1"')
        self.assertContains(response, 'id="g1-hdr" type="button" aria-expanded="true"')
        self.assertContains(response, 'paletteReturnFocus')

    def test_graph_search_normalizes_persian_characters(self):
        user = get_user_model().objects.create_user(username='graph-search', password='SecurePass1')
        self.client.force_login(user)
        response = self.client.get('/graph/')
        self.assertContains(response, 'replace(/ي/g, "ی")')
        self.assertContains(response, 'openExactGraphMatch')
        self.assertContains(response, 'id="searchStatus"')
        self.assertContains(response, 'aria-label="جستجوی شخص در گراف"')

    def test_relationship_search_normalizes_persian_characters(self):
        user = get_user_model().objects.create_user(username='relationship-search', password='SecurePass1')
        self.client.force_login(user)
        response = self.client.get('/relationships/')
        self.assertContains(response, "replace(/ي/g, 'ی')")
        self.assertContains(response, 'id="rlSearchStatus"')

    def test_people_search_is_server_side_and_owner_scoped(self):
        user = get_user_model().objects.create_user(username='people-search', password='SecurePass1')
        other = get_user_model().objects.create_user(username='other-search', password='SecurePass1')
        Node.objects.create(owner=user, username='far-person', name='Findable Person')
        Node.objects.create(owner=other, username='hidden-person', name='Findable Person')
        self.client.force_login(user)
        response = self.client.get('/nodes/?q=Findable')
        self.assertContains(response, 'far-person')
        self.assertNotContains(response, 'hidden-person')
        self.assertContains(response, 'activePeopleFilters')

    def test_people_search_normalizes_arabic_and_persian_letters(self):
        user = get_user_model().objects.create_user(username='persian-search', password='SecurePass1')
        Node.objects.create(owner=user, username='ali-person', first_name='علي')
        self.client.force_login(user)
        response = self.client.get('/nodes/?q=علی')
        self.assertContains(response, 'ali-person')

    def test_people_directory_can_filter_to_relationships_needing_attention(self):
        user = get_user_model().objects.create_user(username='attention-list', password='SecurePass1')
        root = Node.objects.create(owner=user, username='attention-root', name='Root')
        distant = Node.objects.create(owner=user, username='attention-person', name='Needs attention')
        user.root_node = root
        user.save(update_fields=['root_node'])
        Relationship.objects.create(owner=user, source=root, target=distant, strength=5)
        Interaction.objects.create(owner=user, node=distant, kind='meet', date=date.today() - timedelta(days=100))
        self.client.force_login(user)
        response = self.client.get('/nodes/?focus=attention')
        self.assertContains(response, 'attention-person')

    def test_people_directory_explains_an_empty_filtered_result(self):
        user = get_user_model().objects.create_user(username='empty-people-filter', password='SecurePass1')
        self.client.force_login(user)
        response = self.client.get('/nodes/?q=nobody')
        self.assertContains(response, 'پاک‌کردن فیلترها')

    def test_people_directory_hides_records_merged_into_another_person(self):
        user = get_user_model().objects.create_user(username='merged-list', password='SecurePass1')
        kept = Node.objects.create(owner=user, username='kept-person', name='Kept Person')
        Node.objects.create(owner=user, username='merged-person', name='Merged Person', merged_into=kept)
        self.client.force_login(user)
        response = self.client.get('/nodes/')
        self.assertContains(response, 'kept-person')
        self.assertNotContains(response, 'merged-person')

    def test_people_directory_group_filter_is_owner_scoped(self):
        from .models import Group
        user = get_user_model().objects.create_user(username='group-list', password='SecurePass1')
        other = get_user_model().objects.create_user(username='other-group-list', password='SecurePass1')
        own_group = Group.objects.create(owner=user, name='Friends')
        foreign_group = Group.objects.create(owner=other, name='Hidden Group')
        visible = Node.objects.create(owner=user, username='group-visible', name='Visible')
        hidden = Node.objects.create(owner=user, username='group-hidden', name='Hidden')
        visible.groups.add(own_group)
        hidden.groups.add(foreign_group)
        self.client.force_login(user)
        response = self.client.get(f'/nodes/?group={own_group.id}')
        self.assertContains(response, 'group-visible')
        self.assertNotContains(response, 'group-hidden')

    def test_group_assignment_refuses_someone_elses_person(self):
        user = get_user_model().objects.create_user(username='group-write', password='SecurePass1')
        other = get_user_model().objects.create_user(username='group-write-other', password='SecurePass1')
        foreign_node = Node.objects.create(owner=other, username='foreign-node', name='Foreign')
        self.client.force_login(user)
        response = self.client.post('/api/groups/assign/', data=json.dumps({
            'node_ids': [foreign_node.id], 'group_name': 'Friends', 'action': 'add',
        }), content_type='application/json')
        self.assertEqual(response.status_code, 404)

    def test_event_completion_refuses_someone_elses_event(self):
        user = get_user_model().objects.create_user(username='event-write', password='SecurePass1')
        other = get_user_model().objects.create_user(username='event-write-other', password='SecurePass1')
        event = Event.objects.create(owner=other, title='Private event', date=date.today())
        self.client.force_login(user)
        response = self.client.post(f'/api/events/{event.id}/complete/')
        self.assertEqual(response.status_code, 404)

    def test_clearing_chat_only_removes_the_current_users_messages(self):
        from .models import ChatMessage
        user = get_user_model().objects.create_user(username='chat-clear', password='SecurePass1')
        other = get_user_model().objects.create_user(username='chat-clear-other', password='SecurePass1')
        ChatMessage.objects.create(owner=user, role='user', content='Mine')
        ChatMessage.objects.create(owner=other, role='user', content='Other')
        self.client.force_login(user)
        response = self.client.post('/api/chat/clear/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ChatMessage.objects.filter(owner=user).exists())
        self.assertTrue(ChatMessage.objects.filter(owner=other).exists())

    def test_quick_person_update_refuses_someone_elses_person(self):
        user = get_user_model().objects.create_user(username='quick-write', password='SecurePass1')
        other = get_user_model().objects.create_user(username='quick-write-other', password='SecurePass1')
        foreign_node = Node.objects.create(owner=other, username='quick-foreign', name='Foreign')
        self.client.force_login(user)
        response = self.client.post(f'/api/nodes/{foreign_node.id}/quick-update/',
                                    data=json.dumps({'first_name': 'Changed'}), content_type='application/json')
        self.assertEqual(response.status_code, 404)

    def test_journal_save_cannot_attach_another_users_pending_image(self):
        from .models import JournalImage
        user = get_user_model().objects.create_user(username='journal-images', password='SecurePass1')
        other = get_user_model().objects.create_user(username='journal-images-other', password='SecurePass1')
        image = JournalImage.objects.create(
            owner=other, image=SimpleUploadedFile('private.jpg', b'image-bytes', content_type='image/jpeg')
        )
        self.client.force_login(user)
        response = self.client.post('/api/journal/save/', data=json.dumps({
            'text': 'A private journal entry', 'image_ids': [image.id],
        }), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        image.refresh_from_db()
        self.assertIsNone(image.entry_id)

    def test_journal_image_upload_assigns_the_current_user_as_owner(self):
        from .models import JournalImage
        user = get_user_model().objects.create_user(username='journal-upload', password='SecurePass1')
        self.client.force_login(user)
        response = self.client.post('/api/journal/upload-image/', {
            'image': SimpleUploadedFile('owned.jpg', b'image-bytes', content_type='image/jpeg'),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(JournalImage.objects.get(pk=response.json()['id']).owner, user)


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

    def test_alias_resolves_a_role_to_an_existing_person(self):
        from .extraction import extract_text
        node = Node.objects.create(owner=self.user, username='ali', name='علی')
        NodeAlias.objects.create(owner=self.user, node=node, alias='داداشم')
        rows = extract_text(self.user, 'داداشم ازم ۲۰۰ هزار تومن قرض گرفت', 'journal', 20)
        debt = next(item for item in rows if item.kind == 'debt')
        self.assertEqual(debt.payload['node_id'], node.id)
        self.assertFalse(any(item.kind == 'person' for item in rows))

    def test_relationship_change_is_applied_only_after_confirmation(self):
        from .extraction import extract_text
        root = Node.objects.create(owner=self.user, username='me', name='من')
        sara = Node.objects.create(owner=self.user, username='sara', name='سارا')
        self.user.root_node = root
        self.user.save(update_fields=['root_node'])
        suggestion = next(item for item in extract_text(
            self.user, 'سارا همکار جدیدمه', 'journal', 21) if item.kind == 'relationship')
        self.assertFalse(Relationship.objects.exists())
        self.client.force_login(self.user)
        response = self.client.post(f'/api/extractions/{suggestion.id}/',
                                    data=json.dumps({'action': 'approve', 'node_id': sara.id}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        relationship = Relationship.objects.get(owner=self.user)
        self.assertEqual(relationship.rel, 'همکار')

    def test_confirmed_person_fact_becomes_traceable_memory(self):
        from .extraction import extract_text
        sara = Node.objects.create(owner=self.user, username='sara', name='سارا')
        suggestion = next(item for item in extract_text(
            self.user, 'سارا عاشق کتاب‌های تاریخی است.', 'journal', 22) if item.kind == 'memory')
        self.client.force_login(self.user)
        response = self.client.post(f'/api/extractions/{suggestion.id}/',
                                    data=json.dumps({'action': 'approve', 'node_id': sara.id}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        fact = MemoryFact.objects.get(owner=self.user, node=sara)
        self.assertEqual(fact.category, 'interest')
        self.assertEqual(fact.source_id, 22)
        self.assertEqual(fact.confidence, 80)


class PersianDateExtractionTests(TestCase):
    def test_relative_date_and_tehran_clock_are_parsed(self):
        from .persian_datetime import parse_persian_datetime
        parsed = parse_persian_datetime('فردا ساعت ۸ قرار داریم', base_date=date(2026, 8, 7))
        self.assertEqual(parsed['date'], date(2026, 8, 8))
        self.assertEqual((parsed['time'].hour, parsed['time'].minute), (8, 0))

    def test_persian_week_offset_is_parsed(self):
        from .persian_datetime import parse_persian_datetime
        parsed = parse_persian_datetime('سه هفته دیگه', base_date=date(2026, 8, 7))
        numeric = parse_persian_datetime('۳ هفته دیگه', base_date=date(2026, 8, 7))
        self.assertEqual(parsed['date'], date(2026, 8, 28))
        self.assertEqual(numeric['date'], date(2026, 8, 28))

    def test_named_jalali_date_is_converted(self):
        import jdatetime
        from .persian_datetime import parse_persian_datetime
        parsed = parse_persian_datetime('قرار ۲۵ شهریور ۱۴۰۵ ساعت ۲۰:۳۰')
        self.assertEqual(parsed['date'], jdatetime.date(1405, 6, 25).togregorian())
        self.assertEqual((parsed['time'].hour, parsed['time'].minute), (20, 30))

    def test_event_suggestion_exposes_understood_date_for_review(self):
        from .extraction import extract_text
        user = get_user_model().objects.create_user(username='date-owner', password='SecurePass1')
        suggestion = next(item for item in extract_text(
            user, 'فردا ساعت ۸ با سارا قرار داریم', 'journal', 30) if item.kind == 'event')
        self.assertEqual(suggestion.payload['date'], (timezone.localdate() + timedelta(days=1)).isoformat())
        self.assertEqual(suggestion.payload['time'], '08:00')


class PersianExtractionScenarioTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='scenario-owner', password='SecurePass1')

    def test_relationship_colloquialisms(self):
        from .extraction import extract_text
        cases = [
            ('سارا همکار جدیدمه', 'همکار', 'active'),
            ('رضا دوست جدیدمه', 'دوست', 'active'),
            ('با علی قهر کردم', '', 'distant'),
            ('دیگه با مریم در ارتباط نیستم', '', 'inactive'),
            ('رابطه‌مون با نیما بهتر شده', '', 'active'),
        ]
        for index, (text, rel_type, status) in enumerate(cases, 40):
            with self.subTest(text=text):
                rows = extract_text(self.user, text, 'journal', index)
                relationship = next(item for item in rows if item.kind == 'relationship')
                self.assertEqual(relationship.payload['relationship_type'], rel_type)
                self.assertEqual(relationship.payload['status'], status)

    def test_person_knowledge_categories(self):
        from .extraction import extract_text
        cases = [
            ('سارا عاشق کتاب‌های تاریخی است', 'interest'),
            ('علی از شلوغی بدش میاد', 'sensitivity'),
            ('برای مریم صداقت مهمه', 'value'),
            ('رضا ترجیح میده تلفنی حرف بزنیم', 'preference'),
        ]
        for index, (text, category) in enumerate(cases, 50):
            with self.subTest(text=text):
                rows = extract_text(self.user, text, 'checkin', index)
                fact = next(item for item in rows if item.kind == 'memory')
                self.assertEqual(fact.payload['category'], category)
                self.assertTrue(fact.payload['value'])


class MemoryIntelligenceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='memory-owner', password='SecurePass1')
        self.other = get_user_model().objects.create_user(username='memory-other', password='SecurePass1')
        self.root = Node.objects.create(owner=self.user, username='me', name='من')
        self.ali = Node.objects.create(owner=self.user, username='ali', name='علی')
        self.user.root_node = self.root
        self.user.save(update_fields=['root_node'])
        self.client.force_login(self.user)

    def test_manual_memory_can_be_searched_and_disabled_for_ai(self):
        created = self.client.post('/api/memory/facts/', data=json.dumps({
            'action': 'create', 'node_id': self.ali.id, 'category': 'interest',
            'value': 'کتاب‌های تاریخی', 'confidence': 95,
        }), content_type='application/json')
        self.assertEqual(created.status_code, 200)
        fact = MemoryFact.objects.get(owner=self.user)
        search = self.client.get('/api/memory/search/?q=تاریخی').json()['results']
        self.assertEqual(search[0]['source'], 'manual #—')
        updated = self.client.post(f'/api/memory/facts/{fact.id}/', data=json.dumps({
            'action': 'update', 'ai_usable': False,
        }), content_type='application/json')
        self.assertEqual(updated.status_code, 200)
        fact.refresh_from_db()
        self.assertFalse(fact.ai_usable)

    def test_assistant_uses_only_confirmed_ai_usable_memory_and_accepts_feedback(self):
        MemoryFact.objects.create(owner=self.user, node=self.ali, category='interest',
                                  value='پیاده‌روی', confidence=90, source='manual')
        MemoryFact.objects.create(owner=self.user, node=self.ali, category='sensitivity',
                                  value='شلوغی', confidence=90, source='manual', ai_usable=False)
        data = self.client.get(f'/api/memory/assistant/{self.ali.id}/').json()
        self.assertEqual(data['topic'], 'پیاده‌روی')
        self.assertNotIn('شلوغی', data['avoid'])
        response = self.client.post(f'/api/memory/recommendations/{data["recommendation_id"]}/',
                                    data=json.dumps({'action': 'outcome', 'outcome': 'better', 'helpful': True}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RelationshipRecommendation.objects.get().outcome, 'better')

    def test_merge_preview_apply_and_undo_preserve_existing_primary_links(self):
        duplicate = Node.objects.create(owner=self.user, username='ali2', name='علی رضایی')
        friend = Node.objects.create(owner=self.user, username='friend', name='دوست')
        relationship = Relationship.objects.create(owner=self.user, source=duplicate, target=friend,
                                                   rel='دوست', strength=3)
        interaction = Interaction.objects.create(owner=self.user, node=duplicate, kind='call',
                                                 date=timezone.localdate())
        journal = JournalEntry.objects.create(owner=self.user, text='هر دو علی اینجا هستند')
        journal.mentioned_nodes.add(self.ali, duplicate)
        preview = self.client.get(f'/api/memory/merge/preview/?primary={self.ali.id}&duplicate={duplicate.id}')
        self.assertEqual(preview.json()['moves']['interactions'], 1)
        applied = self.client.post('/api/memory/merge/', data=json.dumps({
            'primary_id': self.ali.id, 'duplicate_id': duplicate.id,
        }), content_type='application/json')
        self.assertEqual(applied.status_code, 200)
        interaction.refresh_from_db(); duplicate.refresh_from_db()
        self.assertEqual(interaction.node, self.ali)
        self.assertEqual(duplicate.merged_into, self.ali)
        relationship.refresh_from_db()
        self.assertEqual(relationship.source, self.ali)
        undone = self.client.post(f'/api/memory/merge/{applied.json()["operation_id"]}/undo/',
                                  data='{}', content_type='application/json')
        self.assertEqual(undone.status_code, 200)
        interaction.refresh_from_db(); duplicate.refresh_from_db(); journal.refresh_from_db()
        self.assertEqual(interaction.node, duplicate)
        self.assertIsNone(duplicate.merged_into)
        relationship.refresh_from_db()
        self.assertEqual(relationship.source, duplicate)
        self.assertSetEqual(set(journal.mentioned_nodes.values_list('id', flat=True)), {self.ali.id, duplicate.id})

    def test_natural_language_memory_question_finds_a_sourced_answer(self):
        MemoryFact.objects.create(owner=self.user, node=self.ali, category='sensitivity',
                                  value='شلوغی', source='journal', source_id=77)
        response = self.client.get('/api/memory/search/?q=کی از شلوغی بدش میاد؟')
        self.assertEqual(response.status_code, 200)
        result = next(row for row in response.json()['results'] if row['kind'] == 'memory')
        self.assertEqual(result['title'], 'علی')
        self.assertEqual(result['source'], 'journal #77')

    def test_memory_endpoints_do_not_cross_tenant_boundary(self):
        fact = MemoryFact.objects.create(owner=self.user, node=self.ali, category='value',
                                         value='صداقت', source='manual')
        self.client.force_login(self.other)
        response = self.client.post(f'/api/memory/facts/{fact.id}/',
                                    data=json.dumps({'action': 'delete'}), content_type='application/json')
        self.assertEqual(response.status_code, 404)
        self.assertTrue(MemoryFact.objects.filter(pk=fact.id).exists())

    def test_memory_hub_and_weekly_story_render(self):
        self.assertEqual(self.client.get('/memory/').status_code, 200)
        weekly = self.client.get('/weekly/')
        self.assertEqual(weekly.status_code, 200)
        self.assertContains(weekly, 'داستان این هفته')


    def test_monthly_recap_is_private_and_renders_user_activity(self):
        Interaction.objects.create(owner=self.user, node=self.ali, kind='call', date=timezone.localdate())
        response = self.client.get('/monthly/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.ali.display_name())
        self.client.force_login(self.other)
        other_response = self.client.get('/monthly/')
        self.assertEqual(other_response.status_code, 200)
        self.assertNotContains(other_response, self.ali.display_name())


class RelationshipLifeCycleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='life-owner', password='SecurePass1')
        self.root = Node.objects.create(owner=self.user, username='me-life', name='من')
        self.sara = Node.objects.create(owner=self.user, username='sara-life', name='سارا')
        self.user.root_node = self.root
        self.user.save(update_fields=['root_node'])
        self.client.force_login(self.user)

    def test_quick_capture_creates_commitment_and_gift(self):
        commitment = self.client.post('/api/relationship-life/capture/', data=json.dumps({
            'kind': 'commitment', 'node_id': self.sara.id, 'text': 'کتاب را پس بدهم', 'responsible': 'me',
        }), content_type='application/json')
        gift = self.client.post('/api/relationship-life/capture/', data=json.dumps({
            'kind': 'gift', 'node_id': self.sara.id, 'text': 'کتاب تاریخ ایران', 'occasion': 'تولد',
        }), content_type='application/json')
        self.assertEqual(commitment.status_code, 200)
        self.assertEqual(gift.status_code, 200)
        self.assertTrue(Commitment.objects.filter(owner=self.user, node=self.sara).exists())
        self.assertTrue(GiftIdea.objects.filter(owner=self.user, node=self.sara).exists())

    def test_open_followup_can_be_snoozed_by_its_owner(self):
        item = FollowUp.objects.create(owner=self.user, node=self.sara, text='تماس بگیر')
        response = self.client.post(f'/api/followups/{item.id}/snooze/', data=json.dumps({'days': 7}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.due_date, timezone.localdate() + timedelta(days=7))

    def test_share_link_exposes_only_the_safe_person_card(self):
        self.sara.phone_number = '09120000000'
        self.sara.save(update_fields=['phone_number'])
        created = self.client.post(f'/api/people/{self.sara.id}/share-link/', data=json.dumps({'days': 7}),
                                   content_type='application/json')
        self.assertEqual(created.status_code, 200)
        public = self.client.get(f'/shared/person/{created.json()["token"]}/')
        self.assertEqual(public.status_code, 200)
        self.assertNotContains(public, '09120000000')
        revoked = self.client.post(f'/api/share-links/{created.json()["token"]}/revoke/')
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(self.client.get(f'/shared/person/{created.json()["token"]}/').status_code, 404)

    def test_person_can_be_created_without_a_technical_username(self):
        form = self.client.get('/nodes/create/')
        self.assertContains(form, 'جزئیات بیشتر، برای بعد')
        response = self.client.post('/nodes/create/', {'first_name': 'رضا'})
        self.assertEqual(response.status_code, 302)
        person = Node.objects.get(owner=self.user, first_name='رضا')
        self.assertTrue(person.username)
        self.assertEqual(response['Location'], f'/nodes/{person.id}/')
        detail = self.client.get(response['Location'])
        self.assertContains(detail, 'لازم نیست همه‌چیز را کامل کنی')
        self.assertContains(detail, 'قدم بعدی: اولین تعامل را ثبت کن')
        self.assertContains(detail, 'آمادگی ملاقات')
        self.assertContains(detail, 'بازتاب ملاقات')
        self.assertContains(detail, 'کپی متن پیام')
        self.assertContains(detail, 'لینک امن')
        relation_form = self.client.get(f'/relationships/create/?target={person.id}')
        self.assertEqual(relation_form.status_code, 200)
        self.assertContains(relation_form, f'value="{person.id}" selected')
        relation = self.client.post(f'/relationships/create/?target={person.id}', {
            'source': self.root.id, 'target': person.id, 'rel': 'دوست', 'strength': 3, 'status': 'active',
        })
        self.assertEqual(relation['Location'], f'/nodes/{person.id}/')

    def test_post_meeting_creates_private_timeline_and_extraction(self):
        response = self.client.post('/api/relationship-life/reflection/', data=json.dumps({
            'node_id': self.sara.id, 'summary': 'سارا عاشق کتاب‌های تاریخی است',
            'relationship_change': 'better', 'feeling': 1,
        }), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(MeetingReflection.objects.filter(owner=self.user).exists())
        self.assertTrue(Interaction.objects.filter(owner=self.user, node=self.sara, kind='meet').exists())
        self.assertTrue(ExtractionSuggestion.objects.filter(owner=self.user, kind='memory').exists())

    def test_briefing_hides_no_ai_memory_and_exposes_sources(self):
        MemoryFact.objects.create(owner=self.user, node=self.sara, category='interest', value='موسیقی',
                                  source='manual', confidentiality='personal')
        MemoryFact.objects.create(owner=self.user, node=self.sara, category='sensitivity', value='محرمانه',
                                  source='manual', confidentiality='no_ai', ai_usable=True)
        data = self.client.get(f'/api/relationship-life/briefing/{self.sara.id}/').json()
        self.assertIn('موسیقی', [row['value'] for row in data['facts']])
        self.assertNotIn('محرمانه', [row['value'] for row in data['facts']])
        self.assertIn('source', data['facts'][0])

    def test_sensitive_mode_blocks_introduction(self):
        ali = Node.objects.create(owner=self.user, username='ali-life', name='علی')
        NodeSafetySetting.objects.create(owner=self.user, node=self.sara,
                                         pause_contact_suggestions=True, boundaries='عدم تماس')
        response = self.client.get(f'/api/relationship-life/introduction/?left={self.sara.id}&right={ali.id}')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['safe_to_suggest'])
        assistant = self.client.get(f'/api/memory/assistant/{self.sara.id}/').json()
        self.assertEqual(assistant['topic'], 'حالت محافظتی فعال است')
        self.assertEqual(assistant['draft'], '')

    def test_csv_has_preview_before_apply_and_person_export_is_owned(self):
        upload = io.BytesIO('username,name,phone\nreza,رضا,09120000000\n'.encode('utf-8'))
        upload.name = 'people.csv'
        preview = self.client.post('/api/relationship-life/import/csv/preview/', {'file': upload})
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(Node.objects.filter(owner=self.user, username='reza').exists())
        applied = self.client.post('/api/relationship-life/import/csv/apply/',
            data=json.dumps({'rows': preview.json()['rows']}), content_type='application/json')
        self.assertEqual(applied.json()['created'], 1)
        exported = self.client.get(f'/api/relationship-life/person/{self.sara.id}/export/')
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported['Content-Type'], 'application/json')

    def test_pwa_assets_and_hub_render(self):
        self.assertEqual(self.client.get('/relationship-life/').status_code, 200)
        self.assertEqual(self.client.get('/trust/').status_code, 200)
        self.assertEqual(self.client.get(f'/people/{self.sara.id}/card/').status_code, 200)
        self.assertEqual(self.client.get('/memory/timeline/').status_code, 200)
        self.assertContains(self.client.get('/memory/timeline/'), 'چاپ timeline')
        entry = JournalEntry.objects.create(owner=self.user, text='خاطرهٔ سارا', entry_date=timezone.localdate())
        entry.mentioned_nodes.add(self.sara)
        filtered = self.client.get(f'/memory/timeline/?person={self.sara.id}')
        self.assertContains(filtered, 'خاطرهٔ سارا')
        sw = self.client.get('/service-worker.js')
        self.assertEqual(sw.status_code, 200)
        self.assertIn('application/javascript', sw['Content-Type'])
        self.assertContains(sw, 'SKIP_WAITING')


class PlatformQualityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='platform-owner', password='SecurePass1')
        self.other = get_user_model().objects.create_user(username='platform-other', password='SecurePass1')
        self.node = Node.objects.create(owner=self.user, username='platform-sara', name='سارا')
        self.client.force_login(self.user)

    def test_regex_extraction_records_private_trace(self):
        from .extraction import extract_text
        extract_text(self.user, 'سارا عاشق کتاب تاریخی است', 'journal', 501)
        trace = AIExtractionTrace.objects.get(owner=self.user)
        self.assertEqual(trace.status, 'regex_only')
        self.assertEqual(trace.source_id, 501)
        self.assertTrue(trace.regex_output)

    def test_manual_memory_also_builds_knowledge_triple(self):
        response = self.client.post('/api/memory/facts/', data=json.dumps({
            'action': 'create', 'node_id': self.node.id, 'category': 'interest', 'value': 'نجوم'}),
            content_type='application/json')
        self.assertEqual(response.status_code, 200)
        triple = KnowledgeTriple.objects.get(owner=self.user)
        self.assertEqual((triple.subject, triple.predicate, triple.object_text), (self.node, 'interest', 'نجوم'))

    def test_command_palette_and_onboarding_are_tenant_scoped(self):
        Node.objects.create(owner=self.user, username='palette-sara', first_name='Sara', last_name='Ahmadi')
        own_results = self.client.get('/api/platform/command-palette/?q=Ahmadi').json()['results']
        self.assertIn('@palette-sara', [row.get('subtitle', '') for row in own_results])
        Node.objects.create(owner=self.other, username='secret-person', name='نباید دیده شود')
        results = self.client.get('/api/platform/command-palette/?q=secret').json()['results']
        self.assertNotIn('secret-person', [row.get('subtitle', '') for row in results])
        onboarding = self.client.get('/api/platform/onboarding/').json()
        self.assertEqual(len(onboarding['steps']), 3)
        set_goal = self.client.post('/api/platform/onboarding/goal/', data=json.dumps({'goal': 'memories'}),
                                    content_type='application/json')
        self.assertEqual(set_goal.status_code, 200)
        ordered = self.client.get('/api/platform/onboarding/').json()
        self.assertEqual(ordered['goal'], 'memories')
        self.assertEqual(ordered['steps'][0]['id'], 'journal')
        timeline = self.client.get('/api/platform/command-palette/?q=خط زمان').json()['results']
        self.assertIn('/memory/timeline/', [row['url'] for row in timeline])

    def test_feature_flag_supports_rollout_and_user_override(self):
        flag = FeatureFlag.objects.get(name='hybrid-ai')
        flag.enabled = False
        flag.save(update_fields=['enabled'])
        self.assertFalse(flag.is_enabled_for(self.user))
        self.user.feature_overrides = {'hybrid-ai': True}
        self.user.save(update_fields=['feature_overrides'])
        self.assertTrue(flag.is_enabled_for(self.user))
        suggestions = FeatureFlag.objects.get(name='relationship-suggestions')
        suggestions.enabled = False
        suggestions.save(update_fields=['enabled'])
        blocked = self.client.get(f'/api/memory/assistant/{self.node.id}/')
        self.assertEqual(blocked.status_code, 404)

    def test_public_health_and_request_id_do_not_require_login(self):
        self.client.logout()
        response = self.client.get('/api/system/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['database'], 'ok')
        self.assertEqual(response.json()['cache'], 'ok')
        self.assertTrue(response['X-Request-ID'])

    def test_daily_action_can_be_snoozed_by_its_owner(self):
        response = self.client.post('/api/daily/snooze/', data=json.dumps({'key': 'checkin'}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertIn('checkin', self.user.feature_overrides['daily_snoozed_until'])

    def test_daily_action_can_be_muted_for_a_month(self):
        response = self.client.post('/api/daily/feedback/', data=json.dumps({'key': 'suggestions'}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertIn('suggestions', self.user.feature_overrides['daily_muted_until'])

    def test_notification_preference_is_saved_only_for_current_user(self):
        response = self.client.post('/api/notifications/preferences/', data=json.dumps({'mode': 'weekly'}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.user.feature_overrides['notification_mode'], 'weekly')
        self.assertNotIn('notification_mode', self.other.feature_overrides)

    def test_notification_preference_rejects_unknown_modes(self):
        response = self.client.post('/api/notifications/preferences/', data=json.dumps({'mode': 'always'}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_notification_link_is_rendered_as_an_action(self):
        from .models import Notification
        Notification.objects.create(user=self.user, message='Follow-up is ready.', link='/checkin/')
        response = self.client.get('/notifications/')
        self.assertContains(response, 'href="/checkin/"')

    def test_marking_notifications_read_does_not_touch_another_user(self):
        from .models import Notification
        mine = Notification.objects.create(user=self.user, message='Mine')
        other = Notification.objects.create(user=self.other, message='Other')
        response = self.client.post('/api/notifications/mark-read/')
        self.assertEqual(response.status_code, 200)
        mine.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(mine.is_read)
        self.assertFalse(other.is_read)

    def test_inbox_count_includes_only_the_owners_unread_items(self):
        from .models import Notification
        Notification.objects.create(user=self.user, message='Mine')
        Notification.objects.create(user=self.other, message='Other')
        self.user.refresh_from_db()
        self.assertEqual(self.user.inbox_count, 1)

    @override_settings(WRITE_RATE_LIMIT=1, WRITE_RATE_LIMIT_WINDOW=60)
    def test_write_rate_limit_blocks_only_excess_requests(self):
        cache.clear()
        first = self.client.post('/api/platform/demo/', data=json.dumps({'action': 'create'}),
                                 content_type='application/json')
        second = self.client.post('/api/platform/demo/', data=json.dumps({'action': 'reset'}),
                                  content_type='application/json')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second['Retry-After'], '60')

    def test_demo_data_can_be_created_and_reset_without_touching_real_node(self):
        created = self.client.post('/api/platform/demo/', data=json.dumps({'action': 'create'}),
                                   content_type='application/json')
        self.assertEqual(created.status_code, 200)
        self.assertEqual(Node.objects.filter(owner=self.user, is_demo=True).count(), 3)
        self.client.post('/api/platform/demo/', data=json.dumps({'action': 'reset'}),
                         content_type='application/json')
        self.assertTrue(Node.objects.filter(pk=self.node.id).exists())
        self.assertFalse(Node.objects.filter(owner=self.user, is_demo=True).exists())

    def test_ai_debug_never_exposes_another_users_raw_text(self):
        superuser = get_user_model().objects.create_superuser(username='debug-root', password='SecurePass1')
        AIExtractionTrace.objects.create(owner=self.other, source='journal', input_text='متن خیلی خصوصی')
        self.client.force_login(superuser)
        response = self.client.get('/platform/ai-debug/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'متن خیلی خصوصی')

    def test_encrypted_backup_requires_password_and_round_trips_preview(self):
        download = self.client.post('/api/platform/backup/download/', {'password': 'StrongBackupPass1'})
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.content.startswith(b'FGB1'))
        encrypted = SimpleUploadedFile('backup.fgb', download.content, 'application/octet-stream')
        preview = self.client.post('/api/platform/backup/preview/', {
            'password': 'StrongBackupPass1', 'file': encrypted})
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.json()['valid'])
        restore_file = SimpleUploadedFile('backup.fgb', download.content, 'application/octet-stream')
        restored = self.client.post('/api/platform/backup/restore/', {
            'password': 'StrongBackupPass1', 'file': restore_file})
        self.assertEqual(restored.status_code, 200)
        self.assertTrue(restored.json()['ok'])
        wrong_file = SimpleUploadedFile('backup.fgb', download.content, 'application/octet-stream')
        wrong = self.client.post('/api/platform/backup/preview/', {'password': 'wrong-pass', 'file': wrong_file})
        self.assertEqual(wrong.status_code, 400)
