from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.utils import timezone
from unittest import mock
import json
import io
from datetime import date, timedelta
from pathlib import Path

from .models import AIExtractionTrace, Commitment, Debt, DirectMessage, Event, ExtractionSuggestion, FeatureFlag, Follow, FollowUp, Friendship, GiftBox, GiftIdea, Information, Interaction, JournalEntry, JournalImage, KnowledgeTriple, LifeEvent, MeetingReflection, MemoryFact, Node, NodeAlias, NodeMergeOperation, NodeSafetySetting, ProfileMediaItem, Relationship, RelationshipRecommendation, SocialCircle, SocialPost
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
        self.assertContains(response, 'focusGraphMatches')
        self.assertContains(response, 'Enter برای تمرکز')
        self.assertContains(response, 'id="searchStatus"')
        self.assertContains(response, 'aria-label="جستجوی شخص در گراف"')

    def test_relationship_search_normalizes_persian_characters(self):
        user = get_user_model().objects.create_user(username='relationship-search', password='SecurePass1')
        source = Node.objects.create(owner=user, username='relationship-source', name='علي')
        target = Node.objects.create(owner=user, username='relationship-target', name='رضا')
        relationship = Relationship.objects.create(
            owner=user, source=source, target=target, rel='دوست',
        )
        self.client.force_login(user)
        response = self.client.get('/relationships/?q=علی')
        self.assertEqual(list(response.context['relationships']), [relationship])
        self.assertContains(response, 'rlParams')

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

    def test_person_pin_is_owner_scoped_and_sorts_first(self):
        user = get_user_model().objects.create_user(username='pinned-user', password='SecurePass1')
        other = get_user_model().objects.create_user(username='pinned-other', password='SecurePass1')
        pinned = Node.objects.create(owner=user, username='z-pinned', name='Pinned')
        Node.objects.create(owner=user, username='a-normal', name='Normal')
        foreign = Node.objects.create(owner=other, username='foreign-pin', name='Foreign')
        self.client.force_login(user)
        self.assertEqual(self.client.post(f'/api/nodes/{pinned.id}/pin/').status_code, 200)
        self.assertEqual(self.client.post(f'/api/nodes/{foreign.id}/pin/').status_code, 404)
        response = self.client.get('/nodes/')
        self.assertLess(response.content.find(b'z-pinned'), response.content.find(b'a-normal'))
        self.assertContains(response, 'title="پین‌شده"')

    def test_people_directory_can_filter_to_own_pinned_people(self):
        user = get_user_model().objects.create_user(username='pinned-filter-user', password='SecurePass1')
        other = get_user_model().objects.create_user(username='pinned-filter-other', password='SecurePass1')
        Node.objects.create(owner=user, username='pinned-visible', name='Pinned visible', is_pinned=True)
        Node.objects.create(owner=user, username='unpinned-hidden', name='Unpinned hidden')
        Node.objects.create(owner=other, username='foreign-pinned-hidden', name='Foreign pinned', is_pinned=True)
        self.client.force_login(user)

        response = self.client.get('/nodes/?focus=pinned')

        self.assertContains(response, 'pinned-visible')
        self.assertNotContains(response, 'unpinned-hidden')
        self.assertNotContains(response, 'foreign-pinned-hidden')
        self.assertEqual(response.context['selected_focus'], 'pinned')

    def test_people_directory_group_filter_is_owner_scoped(self):
        from .models import Group
        user = get_user_model().objects.create_user(username='group-list', password='SecurePass1')
        other = get_user_model().objects.create_user(username='other-group-list', password='SecurePass1')
        own_group = Group.objects.create(owner=user, name='Friends')
        foreign_group = Group.objects.create(owner=other, name='Hidden Group')
        visible = Node.objects.create(owner=user, username='group-visible', name='Visible')
        hidden = Node.objects.create(owner=user, username='group-hidden', name='Hidden')
        visible.groups.add(own_group)
        # Simulate a legacy inconsistent through-row without bypassing the
        # current direct-write ownership guard in normal application code.
        Node.groups.through.objects.create(node_id=hidden.id, group_id=foreign_group.id)
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


class PinnedPeopleTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='pin-owner', password='SecurePass1')
        self.other = User.objects.create_user(username='pin-other', password='SecurePass1')
        self.pinned = Node.objects.create(owner=self.user, username='z-pinned', name='Pinned')
        self.normal = Node.objects.create(owner=self.user, username='a-normal', name='Normal')
        self.foreign = Node.objects.create(owner=self.other, username='foreign', name='Foreign')
        self.client.force_login(self.user)

    def test_toggle_pin_is_owner_scoped_and_reversible(self):
        response = self.client.post(f'/api/nodes/{self.pinned.id}/pin/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['is_pinned'])
        self.pinned.refresh_from_db()
        self.assertTrue(self.pinned.is_pinned)

        response = self.client.post(f'/api/nodes/{self.pinned.id}/pin/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['is_pinned'])

        self.assertEqual(self.client.post(f'/api/nodes/{self.foreign.id}/pin/').status_code, 404)
        self.foreign.refresh_from_db()
        self.assertFalse(self.foreign.is_pinned)

    def test_pinned_filter_excludes_unpinned_and_foreign_people(self):
        self.pinned.is_pinned = True
        self.pinned.save(update_fields=['is_pinned'])
        response = self.client.get('/nodes/?focus=pinned')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'z-pinned')
        self.assertNotContains(response, 'a-normal')
        self.assertNotContains(response, 'foreign')

    def test_directory_sorts_pinned_first_and_pin_endpoint_requires_post(self):
        self.pinned.is_pinned = True
        self.pinned.save(update_fields=['is_pinned'])
        response = self.client.get('/nodes/')
        self.assertLess(response.content.find(b'z-pinned'), response.content.find(b'a-normal'))
        self.assertEqual(self.client.get(f'/api/nodes/{self.pinned.id}/pin/').status_code, 405)

    def test_dashboard_shows_only_owned_pinned_people(self):
        self.pinned.is_pinned = True
        self.pinned.save(update_fields=['is_pinned'])
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'افراد مهم')
        self.assertContains(response, 'Pinned')
        self.assertNotContains(response, 'Foreign')


class DashboardFollowupTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='dashboard-followup-owner', password='SecurePass1')
        self.other = User.objects.create_user(username='dashboard-followup-other', password='SecurePass1')
        self.node = Node.objects.create(owner=self.user, username='dashboard-person', name='Dashboard person')
        self.foreign_node = Node.objects.create(owner=self.other, username='foreign-dashboard-person', name='Foreign dashboard person')
        self.client.force_login(self.user)

    def test_dashboard_highlights_only_owned_overdue_followups(self):
        FollowUp.objects.create(
            owner=self.user, node=self.node, text='Owner overdue task',
            due_date=timezone.localdate() - timedelta(days=2),
        )
        FollowUp.objects.create(
            owner=self.other, node=self.foreign_node, text='Foreign overdue secret',
            due_date=timezone.localdate() - timedelta(days=2),
        )

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Owner overdue task')
        self.assertContains(response, 'پیگیری عقب‌افتاده')
        self.assertNotContains(response, 'Foreign overdue secret')
        self.assertEqual(len(response.context['overdue_followups']), 1)

    def test_dashboard_overdue_summary_is_empty_when_all_open_items_are_future(self):
        FollowUp.objects.create(
            owner=self.user, node=self.node, text='Future task',
            due_date=timezone.localdate() + timedelta(days=2),
        )

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['overdue_followups'])
        self.assertNotContains(response, 'پیگیری عقب‌افتاده')


class InteractionAPIContractTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='interaction-api-owner', password='SecurePass1')
        self.other = User.objects.create_user(username='interaction-api-other', password='SecurePass1')
        self.node = Node.objects.create(owner=self.user, username='interaction-api-node')
        self.foreign_node = Node.objects.create(owner=self.other, username='private-interaction-node')
        self.client.force_login(self.user)

    def test_log_returns_serialized_interaction_and_health(self):
        response = self.client.post('/api/interactions/log/', data=json.dumps({
            'node_id': self.node.id, 'kind': 'call', 'feeling': 1,
            'note': 'A short note', 'date': str(timezone.localdate()),
        }), content_type='application/json')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['interaction']['node_id'], self.node.id)
        self.assertEqual(payload['interaction']['note'], 'A short note')
        self.assertIn('health', payload)

    def test_log_rejects_future_dates_and_foreign_nodes(self):
        future = self.client.post('/api/interactions/log/', data=json.dumps({
            'node_id': self.node.id, 'date': str(timezone.localdate() + timedelta(days=1)),
        }), content_type='application/json')
        foreign = self.client.post('/api/interactions/log/', data=json.dumps({
            'node_id': self.foreign_node.id,
        }), content_type='application/json')

        self.assertEqual(future.status_code, 400)
        self.assertEqual(foreign.status_code, 404)
        self.assertFalse(Interaction.objects.exists())

    def test_recent_does_not_return_inconsistent_foreign_node_records(self):
        Interaction.objects.bulk_create([Interaction(
            owner=self.user, node=self.foreign_node, kind='call', date=timezone.localdate(),
        )])

        response = self.client.get('/api/interactions/recent/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['interactions'], [])


class DirectorySearchTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='directory-owner', password='SecurePass1')
        self.other = User.objects.create_user(username='directory-other', password='SecurePass1')
        self.client.force_login(self.user)

    def test_search_finds_records_beyond_first_page(self):
        for index in range(30):
            Node.objects.create(
                owner=self.user,
                username=f'server-search-{index:02d}',
                name='Directory result',
            )

        response = self.client.get('/nodes/?q=server-search')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].paginator.count, 30)
        self.assertTrue(response.context['is_paginated'])
        self.assertContains(response, 'value="server-search"')

    def test_search_normalizes_arabic_and_persian_kaf_and_yeh(self):
        Node.objects.create(
            owner=self.user,
            username='persian-person',
            name='\u06a9\u06cc\u0627\u0646',
        )

        response = self.client.get('/nodes/', {'q': '\u0643\u064a\u0627\u0646'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'persian-person')

    def test_group_filter_is_owner_scoped(self):
        from .models import Group
        own_group = Group.objects.create(owner=self.user, name='My group')
        foreign_group = Group.objects.create(owner=self.other, name='Other group')
        own_node = Node.objects.create(owner=self.user, username='group-member', name='Member')
        own_node.groups.add(own_group)
        Node.objects.create(owner=self.user, username='ungrouped-person', name='No group')
        foreign_node = Node.objects.create(owner=self.other, username='foreign-member', name='Private')
        foreign_node.groups.add(foreign_group)

        response = self.client.get('/nodes/', {'group': own_group.id})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'group-member')
        self.assertNotContains(response, 'ungrouped-person')
        self.assertNotContains(response, 'foreign-member')
        self.assertNotContains(response, 'Other group')

    def test_pagination_script_preserves_search_group_and_pin_filters(self):
        from .models import Group
        group = Group.objects.create(owner=self.user, name='Paged group')
        for index in range(30):
            node = Node.objects.create(
                owner=self.user,
                username=f'paged-person-{index:02d}',
                name='Paged result',
                is_pinned=True,
            )
            node.groups.add(group)

        response = self.client.get('/nodes/', {
            'q': 'paged-person', 'group': group.id, 'focus': 'pinned',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'activePeopleFilters')
        self.assertContains(response, 'value="paged-person"')
        self.assertEqual(response.context['page_obj'].paginator.count, 30)


class RelationshipDirectoryTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='relationship-directory-owner', password='SecurePass1')
        self.other = User.objects.create_user(username='relationship-directory-other', password='SecurePass1')
        self.client.force_login(self.user)

    def test_relationship_search_is_server_side_and_tenant_scoped(self):
        left = Node.objects.create(owner=self.user, username='search-left', name='Search left')
        right = Node.objects.create(owner=self.user, username='search-right', name='Search right')
        Relationship.objects.create(owner=self.user, source=left, target=right, rel='colleague')
        foreign_left = Node.objects.create(owner=self.other, username='private-left', name='Private')
        foreign_right = Node.objects.create(owner=self.other, username='private-right', name='Private')
        Relationship.objects.create(owner=self.other, source=foreign_left, target=foreign_right, rel='private-match')

        response = self.client.get('/relationships/', {'q': 'search-right'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Search left')
        self.assertNotContains(response, 'private-match')
        self.assertEqual(response.context['paginator'].count, 1)

    def test_relationship_status_filter_and_pagination_preserve_query(self):
        left = Node.objects.create(owner=self.user, username='status-left', name='Status left')
        right = Node.objects.create(owner=self.user, username='status-right', name='Status right')
        for index in range(26):
            Relationship.objects.create(
                owner=self.user,
                source=left,
                target=right,
                rel=f'status-{index}',
                status='distant' if index == 0 else 'active',
            )

        response = self.client.get('/relationships/', {'q': 'status-', 'status': 'active'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['paginator'].count, 25)
        self.assertNotContains(response, 'status-0')
        self.assertContains(response, 'rlParams')
        self.assertContains(response, 'status')

    def test_relationship_directory_hides_legacy_foreign_endpoints(self):
        own_node = Node.objects.create(owner=self.user, username='visible-relationship-node')
        foreign_node = Node.objects.create(owner=self.other, username='private-relationship-node')
        Relationship.objects.bulk_create([Relationship(
            owner=self.user, source=own_node, target=foreign_node, rel='legacy-secret-edge',
        )])

        response = self.client.get('/relationships/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'legacy-secret-edge')
        self.assertEqual(response.context['paginator'].count, 0)


class EventDirectoryTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='event-directory-owner', password='SecurePass1')
        self.other = User.objects.create_user(username='event-directory-other', password='SecurePass1')
        self.client.force_login(self.user)

    def test_event_search_and_participants_are_owner_scoped(self):
        own_node = Node.objects.create(owner=self.user, username='event-person', name='Event person')
        foreign_node = Node.objects.create(owner=self.other, username='private-participant', name='Private participant')
        event = Event.objects.create(
            owner=self.user,
            title='Planning session',
            description='A searchable event',
            date=timezone.localdate(),
        )
        event.participants.add(own_node)
        # Simulate a legacy/inconsistent through-table row; reads must still hide it.
        Event.participants.through.objects.bulk_create([
            Event.participants.through(event_id=event.id, node_id=foreign_node.id),
        ])
        Event.objects.create(owner=self.other, title='Private event', date=timezone.localdate())

        response = self.client.get('/events/', {'q': 'Planning'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Planning session')
        self.assertContains(response, 'Event person')
        self.assertNotContains(response, 'Private participant')
        self.assertNotContains(response, 'Private event')

    def test_event_scope_filter_returns_only_future_or_past(self):
        Event.objects.create(owner=self.user, title='Future event', date=timezone.localdate() + timedelta(days=2))
        Event.objects.create(owner=self.user, title='Past event', date=timezone.localdate() - timedelta(days=2))

        response = self.client.get('/events/', {'scope': 'upcoming'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Future event')
        self.assertNotContains(response, 'Past event')
        self.assertEqual(response.context['current_scope'], 'upcoming')

    def test_event_pagination_preserves_search_and_scope_filters(self):
        for index in range(25):
            Event.objects.create(
                owner=self.user,
                title=f'Paged planning {index:02d}',
                date=timezone.localdate() + timedelta(days=index + 1),
            )

        response = self.client.get('/events/', {
            'q': 'Paged planning', 'scope': 'upcoming', 'page': '2',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].number, 2)
        self.assertEqual(response.context['page_obj'].paginator.count, 25)
        self.assertContains(response, 'Paged planning 24')
        self.assertContains(response, 'q=Paged+planning')
        self.assertContains(response, 'scope=upcoming')

    def test_event_completion_does_not_create_interactions_for_legacy_foreign_participants(self):
        own_node = Node.objects.create(owner=self.user, username='completion-owner-node')
        foreign_node = Node.objects.create(owner=self.other, username='completion-foreign-node')
        event = Event.objects.create(owner=self.user, title='Completion event', date=timezone.localdate())
        event.participants.add(own_node)
        Event.participants.through.objects.bulk_create([
            Event.participants.through(event_id=event.id, node_id=foreign_node.id),
        ])

        response = self.client.post(f'/api/events/{event.id}/complete/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['logged'], 1)
        self.assertTrue(Interaction.objects.filter(owner=self.user, node=own_node).exists())
        self.assertFalse(Interaction.objects.filter(owner=self.user, node=foreign_node).exists())


class QueryIndexCoverageTests(TestCase):
    def test_hot_owner_queries_have_composite_indexes(self):
        expected = {
            Node: {'node_owner_username', 'node_owner_merge_pin'},
            Relationship: {'rel_owner_status_strength', 'rel_owner_source', 'rel_owner_target'},
            Event: {'event_owner_date'},
            FollowUp: {'follow_owner_done_due'},
            MemoryFact: {'memory_owner_node_active'},
        }
        for model, names in expected.items():
            with self.subTest(model=model.__name__):
                actual = {index.name for index in model._meta.indexes}
                self.assertTrue(names <= actual)

    def test_tenant_models_have_an_owner_leading_index(self):
        for model in (Node, Relationship, Event, FollowUp, MemoryFact):
            with self.subTest(model=model.__name__):
                self.assertTrue(
                    any(index.fields and index.fields[0] == 'owner' for index in model._meta.indexes),
                    f'{model.__name__} should index owner-scoped queries',
                )


class RelationshipDataIntegrityTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username='integrity-owner', password='SecurePass1')
        self.other = User.objects.create_user(username='integrity-other', password='SecurePass1')
        self.own_node = Node.objects.create(owner=self.owner, username='integrity-own')
        self.foreign_node = Node.objects.create(owner=self.other, username='integrity-foreign')

    def test_relationship_endpoints_must_match_relationship_owner(self):
        with self.assertRaisesMessage(Exception, 'Relationship endpoints must belong to the same owner.'):
            Relationship.objects.create(
                owner=self.owner, source=self.own_node, target=self.foreign_node, rel='invalid',
            )

    def test_person_linked_records_reject_foreign_nodes(self):
        cases = [
            (Interaction, {
                'node': self.foreign_node, 'kind': 'call', 'date': timezone.localdate(),
                'owner': self.owner,
            }),
            (FollowUp, {'node': self.foreign_node, 'text': 'invalid follow-up', 'owner': self.owner}),
            (Debt, {
                'node': self.foreign_node, 'direction': 'i_owe', 'amount': 100,
                'date': timezone.localdate(), 'owner': self.owner,
            }),
            (LifeEvent, {
                'node': self.foreign_node, 'kind': 'other', 'date': timezone.localdate(),
                'owner': self.owner,
            }),
        ]
        for model, fields in cases:
            with self.subTest(model=model.__name__):
                with self.assertRaises(Exception):
                    model.objects.create(**fields)

    def test_person_linked_records_allow_their_owner_node(self):
        Interaction.objects.create(
            node=self.own_node, kind='call', date=timezone.localdate(), owner=self.owner,
        )
        FollowUp.objects.create(node=self.own_node, text='valid follow-up', owner=self.owner)
        Debt.objects.create(
            node=self.own_node, direction='i_owe', amount=100,
            date=timezone.localdate(), owner=self.owner,
        )
        LifeEvent.objects.create(
            node=self.own_node, kind='other', date=timezone.localdate(), owner=self.owner,
        )

    def test_event_rejects_foreign_participant_on_direct_m2m_write(self):
        event = Event.objects.create(owner=self.owner, title='Owner event', date=timezone.localdate())

        with self.assertRaisesMessage(Exception, 'Event participants must belong to the same owner.'):
            with transaction.atomic():
                event.participants.add(self.foreign_node)

        self.assertFalse(event.participants.exists())

    def test_secondary_owner_models_reject_foreign_nodes_and_relationships(self):
        from .models import (
            Commitment, GiftIdea, KnowledgeTriple, MeetingReflection, NodeCloseness,
            NodeMergeOperation, NodeSafetySetting, PersonaProfile, RelationshipGoal,
            RelationshipProfile, RelationshipRecommendation, RelationshipPulse,
        )
        foreign_target = Node.objects.create(owner=self.other, username='integrity-foreign-target')
        foreign_relationship = Relationship.objects.create(
            owner=self.other, source=self.foreign_node, target=foreign_target,
            rel='foreign',
        )
        cases = [
            (RelationshipRecommendation, {
                'owner': self.owner, 'node': self.foreign_node,
                'title': 'invalid', 'suggestion': 'invalid',
            }),
            (NodeMergeOperation, {
                'owner': self.owner, 'primary_node': self.own_node,
                'duplicate_node': self.foreign_node, 'snapshot': {},
            }),
            (Commitment, {
                'owner': self.owner, 'node': self.foreign_node,
                'responsible': 'me', 'text': 'invalid',
            }),
            (GiftIdea, {'owner': self.owner, 'node': self.foreign_node, 'title': 'invalid'}),
            (MeetingReflection, {
                'owner': self.owner, 'node': self.foreign_node, 'summary': 'invalid',
            }),
            (NodeSafetySetting, {'owner': self.owner, 'node': self.foreign_node}),
            (KnowledgeTriple, {
                'owner': self.owner, 'subject': self.foreign_node,
                'predicate': 'interest', 'source': 'manual',
            }),
            (NodeCloseness, {'owner': self.owner, 'node': self.foreign_node, 'tier': 'friend'}),
            (RelationshipGoal, {'owner': self.owner, 'node': self.foreign_node, 'text': 'invalid'}),
            (PersonaProfile, {'owner': self.owner, 'node': self.foreign_node}),
            (RelationshipProfile, {'owner': self.owner, 'relationship': foreign_relationship}),
            (RelationshipPulse, {'owner': self.owner, 'node': self.foreign_node}),
        ]
        for model, fields in cases:
            with self.subTest(model=model.__name__):
                with self.assertRaises(Exception):
                    model.objects.create(**fields)

    def test_node_and_journal_many_to_many_links_reject_foreign_records(self):
        from .models import Group
        own_group = Group.objects.create(owner=self.owner, name='own group')
        foreign_group = Group.objects.create(owner=self.other, name='foreign group')
        with self.assertRaisesMessage(Exception, 'Node groups must belong to the same owner.'):
            with transaction.atomic():
                self.own_node.groups.add(foreign_group)
        self.assertFalse(self.own_node.groups.filter(pk=foreign_group.pk).exists())

        journal = JournalEntry.objects.create(owner=self.owner, text='owner journal')
        with self.assertRaisesMessage(Exception, 'Journal mentions must belong to the same owner.'):
            with transaction.atomic():
                journal.mentioned_nodes.add(self.foreign_node)
        self.assertFalse(journal.mentioned_nodes.filter(pk=self.foreign_node.pk).exists())
        self.own_node.groups.add(own_group)


class NodeDetailTenantSafetyTests(TestCase):
    def test_node_detail_hides_foreign_events_journals_and_participants(self):
        User = get_user_model()
        user = User.objects.create_user(username='detail-owner', password='SecurePass1')
        other = User.objects.create_user(username='detail-other', password='SecurePass1')
        node = Node.objects.create(owner=user, username='detail-person', name='Detail person')
        own_participant = Node.objects.create(owner=user, username='own-participant', name='Own participant')
        foreign_participant = Node.objects.create(owner=other, username='foreign-participant', name='Foreign participant')
        own_event = Event.objects.create(owner=user, title='Owner detail event', date=timezone.localdate())
        own_event.participants.add(node, own_participant)
        Event.participants.through.objects.bulk_create([
            Event.participants.through(event_id=own_event.id, node_id=foreign_participant.id),
        ])
        foreign_event = Event.objects.create(owner=other, title='Foreign detail event', date=timezone.localdate())
        Event.participants.through.objects.bulk_create([
            Event.participants.through(event_id=foreign_event.id, node_id=node.id),
        ])
        JournalEntry.objects.create(owner=user, text='Owner detail note', entry_date=timezone.localdate()).mentioned_nodes.add(node)
        foreign_journal = JournalEntry.objects.create(
            owner=other, text='FOREIGN DETAIL SECRET', entry_date=timezone.localdate()
        )
        JournalEntry.mentioned_nodes.through.objects.bulk_create([
            JournalEntry.mentioned_nodes.through(
                journalentry_id=foreign_journal.id, node_id=node.id,
            ),
        ])
        self.client.force_login(user)

        response = self.client.get(f'/nodes/{node.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Owner detail event')
        self.assertContains(response, 'Owner detail note')
        self.assertNotContains(response, 'Foreign detail event')
        self.assertNotContains(response, 'FOREIGN DETAIL SECRET')

    def test_contact_named_after_another_account_stays_in_private_detail(self):
        User = get_user_model()
        owner = User.objects.create_user(username='contact-owner', password='SecurePass1')
        account = User.objects.create_user(username='existing-account', password='SecurePass1')
        contact = Node.objects.create(owner=owner, username=account.username, name='Private contact')
        self.client.force_login(owner)

        response = self.client.get(f'/nodes/{contact.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Private contact')

    def test_persona_does_not_match_another_account_by_contact_username(self):
        from .models import ProfileMediaItem
        from .views_persona import gather_person_signals
        User = get_user_model()
        owner = User.objects.create_user(username='persona-contact-owner', password='SecurePass1')
        account = User.objects.create_user(username='persona-account', password='SecurePass1', is_public=True)
        contact = Node.objects.create(owner=owner, username=account.username, name='Private persona contact')
        ProfileMediaItem.objects.create(user=account, kind='book', title='Account-only media', is_public=True)

        signals = gather_person_signals(owner, contact)

        self.assertFalse(any('Account-only media' in signal for signal in signals))


class GraphInteractionTests(TestCase):
    def test_psychology_graph_build_batches_node_and_relationship_queries(self):
        user = get_user_model().objects.create_user(username='graph-query-owner', password='SecurePass1')
        first = Node.objects.create(owner=user, username='query-first')
        second = Node.objects.create(owner=user, username='query-second')
        Relationship.objects.create(owner=user, source=first, target=second, rel='friend')

        from .views_smart_features import _build_nx
        with CaptureQueriesContext(connection) as queries:
            graph, nodes, relationships = _build_nx(user)

        self.assertEqual(len(nodes), 2)
        self.assertEqual(len(relationships), 1)
        self.assertEqual(graph.number_of_edges(), 1)
        self.assertEqual(len(queries), 2)

    def test_graph_page_exposes_keyboard_search_and_focus_zoom_behavior(self):
        user = get_user_model().objects.create_user(username='graph-owner', password='SecurePass1')
        self.client.force_login(user)
        response = self.client.get('/graph/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'searchStatus')
        self.assertContains(response, 'openExactGraphMatch')
        self.assertContains(response, 'focusGraphMatches')
        self.assertContains(response, 'zoom.transform')
        self.assertContains(response, "event.key==='Enter'")

    def test_graph_api_is_tenant_scoped(self):
        User = get_user_model()
        other = User.objects.create_user(username='graph-other', password='SecurePass1')
        Node.objects.create(owner=other, username='private-graph-node', name='Private')
        user = get_user_model().objects.get(username='graph-owner') if get_user_model().objects.filter(username='graph-owner').exists() else User.objects.create_user(username='graph-viewer', password='SecurePass1')
        self.client.force_login(user)
        response = self.client.get('/api/graph/all/')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('private-graph-node', [node['username'] for node in response.json()['nodes']])

    def test_graph_omits_merged_people_and_edges_to_them(self):
        User = get_user_model()
        user = User.objects.create_user(username='graph-merged-owner', password='SecurePass1')
        active = Node.objects.create(owner=user, username='active-graph-node', name='Active')
        merged = Node.objects.create(owner=user, username='merged-graph-node', name='Merged')
        merged.merged_into = active
        merged.save(update_fields=['merged_into'])
        Relationship.objects.create(owner=user, source=active, target=merged, rel='hidden-edge')
        self.client.force_login(user)

        response = self.client.get('/api/graph/all/')

        self.assertEqual(response.status_code, 200)
        usernames = [node['username'] for node in response.json()['nodes']]
        self.assertIn('active-graph-node', usernames)
        self.assertNotIn('merged-graph-node', usernames)
        self.assertEqual(response.json()['edges'], [])

    def test_graph_omits_legacy_edges_to_foreign_people(self):
        User = get_user_model()
        user = User.objects.create_user(username='graph-legacy-owner', password='SecurePass1')
        other = User.objects.create_user(username='graph-legacy-other', password='SecurePass1')
        own_node = Node.objects.create(owner=user, username='graph-safe-node')
        foreign_node = Node.objects.create(owner=other, username='graph-private-node')
        Relationship.objects.bulk_create([Relationship(
            owner=user, source=own_node, target=foreign_node, rel='legacy-private-edge',
        )])
        self.client.force_login(user)

        response = self.client.get('/api/graph/all/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['edges'], [])

    def test_psychology_analysis_omits_legacy_edges_to_foreign_people(self):
        User = get_user_model()
        user = User.objects.create_user(username='psychology-legacy-owner', password='SecurePass1')
        other = User.objects.create_user(username='psychology-legacy-other', password='SecurePass1')
        own_node = Node.objects.create(owner=user, username='psychology-safe-node')
        foreign_node = Node.objects.create(owner=other, username='psychology-private-node')
        Relationship.objects.bulk_create([Relationship(
            owner=user, source=own_node, target=foreign_node, rel='legacy-private-edge',
        )])
        self.client.force_login(user)

        response = self.client.get('/psychology/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'psychology-private-node')


class JournalFilterUXTests(TestCase):
    def test_filtered_journal_results_are_owner_scoped(self):
        User = get_user_model()
        user = User.objects.create_user(username='journal-filter-owner', password='SecurePass1')
        other = User.objects.create_user(username='journal-filter-other', password='SecurePass1')
        JournalEntry.objects.create(owner=user, text='جلسه با سارا', entry_date=date(2026, 8, 30))
        JournalEntry.objects.create(owner=other, text='یادداشت خصوصی سارا', entry_date=date(2026, 8, 30))
        self.client.force_login(user)
        response = self.client.get('/api/journal/entries/?q=سارا')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([entry['text'] for entry in response.json()['entries']], ['جلسه با سارا'])

    def test_journal_list_has_result_count_empty_recovery_and_debounced_search(self):
        user = get_user_model().objects.create_user(username='journal-filter-page', password='SecurePass1')
        self.client.force_login(user)
        response = self.client.get('/journal/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'journalResultSummary')
        self.assertContains(response, 'پاک‌کردن فیلترها')
        self.assertContains(response, 'scheduleLoadEntries')
        self.assertContains(response, 'تعداد نتایج')


class ReviewReportTests(TestCase):
    def test_weekly_and_monthly_reviews_include_owned_events_and_memories(self):
        User = get_user_model()
        user = User.objects.create_user(username='review-owner', password='SecurePass1')
        other = User.objects.create_user(username='review-other', password='SecurePass1')
        node = Node.objects.create(owner=user, username='review-sara', name='سارا')
        foreign_node = Node.objects.create(owner=other, username='review-foreign', name='نباید دیده شود')
        Event.objects.create(owner=user, title='قرار کتاب', date=timezone.localdate())
        Event.objects.create(owner=other, title='رویداد خصوصی دیگران', date=timezone.localdate())
        MemoryFact.objects.create(owner=user, node=node, category='interest', value='کتاب تاریخی', source='manual')
        MemoryFact.objects.create(owner=other, node=foreign_node, category='interest', value='نباید دیده شود', source='manual')
        self.client.force_login(user)

        weekly = self.client.get('/weekly/')
        self.assertEqual(weekly.status_code, 200)
        self.assertContains(weekly, 'class="review-switch"')
        self.assertContains(weekly, 'href="/relationship-work/"')
        self.assertContains(weekly, 'قرار کتاب')
        self.assertContains(weekly, 'کتاب تاریخی')
        self.assertNotContains(weekly, 'رویداد خصوصی دیگران')
        self.assertNotContains(weekly, 'نباید دیده شود')

        monthly = self.client.get('/monthly/')
        self.assertEqual(monthly.status_code, 200)
        self.assertContains(monthly, 'class="review-switch"')
        self.assertContains(monthly, 'href="/relationship-work/"')
        self.assertContains(monthly, 'حافظه‌های')
        self.assertContains(monthly, 'کتاب تاریخی')
        self.assertNotContains(monthly, 'نباید دیده شود')


class CSRFProtectionTests(TestCase):
    def setUp(self):
        from .models import Notification
        self.user = get_user_model().objects.create_user(username='csrf-owner', password='SecurePass1')
        self.node = Node.objects.create(owner=self.user, username='csrf-node', name='CSRF')
        self.notification = Notification.objects.create(user=self.user, message='CSRF notification')
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def test_internal_state_changes_require_csrf_token(self):
        pin_response = self.client.post(f'/api/nodes/{self.node.id}/pin/')
        journal_response = self.client.post(
            '/api/journal/save/',
            data=json.dumps({'text': 'بدون توکن نباید ذخیره شود'}),
            content_type='application/json',
        )
        notification_response = self.client.post(
            f'/api/notifications/{self.notification.id}/read/'
        )
        read_all_response = self.client.post('/api/notifications/read-all/')
        self.assertEqual(pin_response.status_code, 403)
        self.assertEqual(journal_response.status_code, 403)
        self.assertEqual(notification_response.status_code, 403)
        self.assertEqual(read_all_response.status_code, 403)
        self.node.refresh_from_db()
        self.notification.refresh_from_db()
        self.assertFalse(self.node.is_pinned)
        self.assertFalse(self.notification.is_read)
        self.assertFalse(JournalEntry.objects.filter(owner=self.user).exists())


class NotificationReadTests(TestCase):
    def setUp(self):
        from .models import Notification
        User = get_user_model()
        self.user = User.objects.create_user(username='notification-owner', password='SecurePass1')
        self.other = User.objects.create_user(username='notification-other', password='SecurePass1')
        self.notification = Notification.objects.create(
            user=self.user, message='Unread owner notification', notif_type='system',
        )
        self.foreign = Notification.objects.create(
            user=self.other, message='Foreign notification', notif_type='system',
        )
        self.client.force_login(self.user)

    def test_opening_notifications_does_not_mark_them_read(self):
        response = self.client.get('/notifications/')
        self.assertEqual(response.status_code, 200)
        self.notification.refresh_from_db()
        self.assertFalse(self.notification.is_read)
        self.assertContains(response, 'Unread owner notification')
        self.assertContains(response, 'unreadCount')

    def test_sidebar_badge_includes_unread_general_notifications(self):
        response = self.client.get('/notifications/')
        self.assertContains(response, '<span class="nav-badge">1</span>')

    def test_read_endpoint_is_post_only_and_owner_scoped(self):
        self.assertEqual(self.client.get(f'/api/notifications/{self.notification.id}/read/').status_code, 405)
        response = self.client.post(f'/api/notifications/{self.notification.id}/read/')
        self.assertEqual(response.status_code, 200)
        self.notification.refresh_from_db()
        self.assertTrue(self.notification.is_read)
        self.assertEqual(response.json()['unread_count'], 0)
        self.assertEqual(self.client.post(f'/api/notifications/{self.foreign.id}/read/').status_code, 404)
        self.foreign.refresh_from_db()
        self.assertFalse(self.foreign.is_read)

    def test_read_all_changes_only_current_users_notifications(self):
        from .models import Notification
        Notification.objects.create(user=self.user, message='Second unread')
        response = self.client.post('/api/notifications/read-all/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['updated'], 2)
        self.assertFalse(Notification.objects.filter(user=self.user, is_read=False).exists())
        self.assertTrue(Notification.objects.filter(pk=self.foreign.id, is_read=False).exists())


class RelationshipWorkHubTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='hub-owner', password='SecurePass1')
        self.other = User.objects.create_user(username='hub-other', password='SecurePass1')
        self.node = Node.objects.create(owner=self.user, username='hub-sara', name='سارا')
        self.foreign_node = Node.objects.create(owner=self.other, username='hub-foreign', name='نباید دیده شود')
        self.client.force_login(self.user)

    def test_relationship_work_hub_is_single_entry_point_for_review_cycles(self):
        response = self.client.get('/relationship-work/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/weekly/"')
        self.assertContains(response, 'href="/monthly/"')
        self.assertContains(response, 'href="/daily/"')
        self.assertContains(response, 'href="/relationship-life/"')

    def test_review_queue_combines_owned_work_and_filters_by_due_window(self):
        today = timezone.localdate()
        FollowUp.objects.create(
            owner=self.user, node=self.node, text='پیگیری عقب‌افتاده من',
            due_date=today - timedelta(days=1),
        )
        Commitment.objects.create(
            owner=self.user, node=self.node, responsible='me',
            text='تعهد هفته من', due_date=today + timedelta(days=2),
        )
        Debt.objects.create(
            owner=self.user, node=self.node, direction='they_owe',
            amount=500000, currency='تومان', date=today, note='حساب بدون تاریخ',
        )
        Event.objects.create(owner=self.user, title='قرار امروز من', date=today)

        FollowUp.objects.create(
            owner=self.other, node=self.foreign_node, text='پیگیری خصوصی دیگری',
            due_date=today - timedelta(days=2),
        )
        Commitment.objects.create(
            owner=self.other, node=self.foreign_node, responsible='me',
            text='تعهد خصوصی دیگری', due_date=today + timedelta(days=1),
        )
        Debt.objects.create(
            owner=self.other, node=self.foreign_node, direction='i_owe',
            amount=900000, currency='تومان', date=today, note='حساب خصوصی دیگری',
        )
        Event.objects.create(owner=self.other, title='رویداد خصوصی دیگری', date=today)

        focus = self.client.get('/relationship-work/')
        self.assertEqual(focus.status_code, 200)
        self.assertContains(focus, 'پیگیری عقب‌افتاده من')
        self.assertContains(focus, 'تعهد هفته من')
        self.assertContains(focus, 'حساب بدون تاریخ')
        self.assertContains(focus, 'قرار امروز من')
        self.assertNotContains(focus, 'خصوصی دیگری')
        self.assertEqual(focus.context['counts'], {
            'followups': 1, 'commitments': 1, 'debts': 1, 'events': 1,
        })
        self.assertEqual(focus.context['queue'][0]['due_state'], 'overdue')

        overdue = self.client.get('/relationship-work/?scope=overdue')
        self.assertContains(overdue, 'پیگیری عقب‌افتاده من')
        self.assertNotContains(overdue, 'تعهد هفته من')
        self.assertNotContains(overdue, 'قرار امروز من')
        self.assertNotContains(overdue, 'حساب بدون تاریخ')

        week = self.client.get('/relationship-work/?scope=week')
        self.assertContains(week, 'تعهد هفته من')
        self.assertContains(week, 'قرار امروز من')
        self.assertNotContains(week, 'پیگیری عقب‌افتاده من')
        self.assertNotContains(week, 'حساب بدون تاریخ')

    def test_review_quick_actions_use_owned_post_endpoints(self):
        today = timezone.localdate()
        followup = FollowUp.objects.create(
            owner=self.user, node=self.node, text='تکمیل سریع پیگیری',
        )
        commitment = Commitment.objects.create(
            owner=self.user, node=self.node, responsible='me', text='تکمیل سریع تعهد',
        )
        foreign = Commitment.objects.create(
            owner=self.other, node=self.foreign_node, responsible='me', text='تعهد خارجی',
        )
        event = Event.objects.create(owner=self.user, title='قرار قابل تکمیل', date=today)
        event.participants.add(self.node)
        foreign_event = Event.objects.create(
            owner=self.other, title='رویداد خارجی', date=today,
        )

        followup_response = self.client.post(f'/api/followups/{followup.id}/toggle/')
        commitment_response = self.client.post(
            f'/api/relationship-life/commitments/{commitment.id}/',
            data=json.dumps({'action': 'done'}),
            content_type='application/json',
        )
        foreign_response = self.client.post(
            f'/api/relationship-life/commitments/{foreign.id}/',
            data=json.dumps({'action': 'done'}),
            content_type='application/json',
        )
        event_response = self.client.post(f'/api/events/{event.id}/complete/')
        foreign_event_response = self.client.post(f'/api/events/{foreign_event.id}/complete/')

        self.assertEqual(followup_response.status_code, 200)
        self.assertEqual(commitment_response.status_code, 200)
        self.assertEqual(foreign_response.status_code, 404)
        self.assertEqual(event_response.status_code, 200)
        self.assertEqual(foreign_event_response.status_code, 404)
        followup.refresh_from_db()
        commitment.refresh_from_db()
        foreign.refresh_from_db()
        self.assertTrue(followup.done)
        self.assertEqual(commitment.status, 'done')
        self.assertEqual(foreign.status, 'open')
        self.assertTrue(Interaction.objects.filter(
            owner=self.user, node=self.node, kind='meet', date=today,
        ).exists())
        self.assertNotContains(self.client.get('/relationship-work/'), 'قرار قابل تکمیل')

        from django.template.loader import get_template
        source = get_template('hubs/relationship_work.html').template.source
        self.assertIn("document.querySelectorAll('[data-review-complete]')", source)
        self.assertIn("body:{action:'done'}", source)
        self.assertIn("headers:{'X-CSRFToken':getCookie('csrftoken')}", source)

    def test_review_hub_requires_login_and_handles_scope_edges(self):
        today = timezone.localdate()
        later_followup = FollowUp.objects.create(
            owner=self.user,
            node=self.node,
            text='پیگیری خارج از بازه تمرکز',
            due_date=today + timedelta(days=12),
        )
        future_event = Event.objects.create(
            owner=self.user,
            title='قرار آینده غیرقابل تکمیل',
            date=today + timedelta(days=12),
        )

        self.client.logout()
        anonymous = self.client.get('/relationship-work/')
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn('/login/', anonymous.url)

        self.client.force_login(self.user)
        invalid_scope = self.client.get('/relationship-work/?scope=unexpected')
        self.assertEqual(invalid_scope.status_code, 200)
        self.assertEqual(invalid_scope.context['scope'], 'focus')
        self.assertNotContains(invalid_scope, later_followup.text)
        self.assertNotContains(invalid_scope, future_event.title)

        all_scope = self.client.get('/relationship-work/?scope=all')
        self.assertContains(all_scope, later_followup.text)
        self.assertContains(all_scope, future_event.title)
        future_row = next(
            item for item in all_scope.context['queue']
            if item['kind'] == 'event' and item['object_id'] == future_event.id
        )
        self.assertFalse(future_row['can_complete'])

    def test_review_queue_prefetches_event_participants(self):
        today = timezone.localdate()
        for index in range(15):
            event = Event.objects.create(
                owner=self.user, title=f'قرار {index}', date=today + timedelta(days=index % 7),
            )
            event.participants.add(self.node)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/relationship-work/')

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 25)
        self.assertContains(response, 'قرار 0')


class FollowupInboxTests(TestCase):
    def test_followup_inbox_is_owner_scoped_and_supports_status_filters(self):
        User = get_user_model()
        user = User.objects.create_user(username='followup-inbox-owner', password='SecurePass1')
        other = User.objects.create_user(username='followup-inbox-other', password='SecurePass1')
        node = Node.objects.create(owner=user, username='inbox-sara', name='سارا')
        foreign_node = Node.objects.create(owner=other, username='inbox-foreign', name='دیگری')
        from .models import FollowUp
        FollowUp.objects.create(owner=user, node=node, text='موضوع باز من')
        FollowUp.objects.create(owner=user, node=node, text='موضوع انجام‌شده', done=True, done_at=timezone.now())
        FollowUp.objects.create(owner=other, node=foreign_node, text='موضوع خصوصی دیگران')
        self.client.force_login(user)

        response = self.client.get('/followups/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'موضوع باز من')
        self.assertNotContains(response, 'موضوع انجام‌شده')
        self.assertNotContains(response, 'موضوع خصوصی دیگران')
        response = self.client.get('/followups/?show=done')
        self.assertContains(response, 'موضوع انجام‌شده')
        self.assertNotContains(response, 'موضوع باز من')


class FollowupOverdueFilterTests(TestCase):
    def test_overdue_filter_excludes_done_and_foreign_items(self):
        from .models import FollowUp
        User = get_user_model()
        user = User.objects.create_user(username='overdue-owner', password='SecurePass1')
        other = User.objects.create_user(username='overdue-other', password='SecurePass1')
        node = Node.objects.create(owner=user, username='overdue-person', name='Overdue person')
        foreign_node = Node.objects.create(owner=other, username='foreign-overdue-person', name='Foreign')
        FollowUp.objects.create(
            owner=user, node=node, text='Overdue owner item',
            due_date=timezone.localdate() - timedelta(days=1),
        )
        FollowUp.objects.create(
            owner=user, node=node, text='Future owner item',
            due_date=timezone.localdate() + timedelta(days=1),
        )
        FollowUp.objects.create(
            owner=user, node=node, text='Done overdue owner item', done=True,
            due_date=timezone.localdate() - timedelta(days=2), done_at=timezone.now(),
        )
        FollowUp.objects.create(
            owner=other, node=foreign_node, text='Foreign overdue item',
            due_date=timezone.localdate() - timedelta(days=1),
        )
        self.client.force_login(user)

        response = self.client.get('/followups/?show=overdue')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Overdue owner item')
        self.assertNotContains(response, 'Future owner item')
        self.assertNotContains(response, 'Done overdue owner item')
        self.assertNotContains(response, 'Foreign overdue item')
        self.assertEqual(response.context['show'], 'overdue')


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


class ChatAnalysisAsyncTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user(username='dm-me', password='SecurePass1')
        self.friend = User.objects.create_user(username='dm-friend', password='SecurePass1')
        Friendship.objects.create(user=self.me, friend=self.friend)
        Friendship.objects.create(user=self.friend, friend=self.me)
        self.client.force_login(self.me)
        cache.clear()

    def _send(self, text='سلام رفیق'):
        return self.client.post(
            f'/api/social/messages/{self.friend.id}/send/',
            data=json.dumps({'content': text}),
            content_type='application/json',
        )

    def test_send_does_not_run_analysis_on_the_request_path(self):
        from .models import DirectMessage
        with mock.patch(
            'main.views_social._chat_analysis_for',
            side_effect=AssertionError('AI analysis must not run synchronously during send'),
        ):
            response = self._send()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(DirectMessage.objects.filter(sender=self.me, receiver=self.friend).exists())

    def test_send_schedules_background_analysis_for_both_directions(self):
        with mock.patch('main.views_social._schedule_chat_analysis') as scheduled:
            self._send()
        pairs = {(args[0].id, args[1].id) for args, _ in scheduled.call_args_list}
        self.assertIn((self.me.id, self.friend.id), pairs)
        self.assertIn((self.friend.id, self.me.id), pairs)

    def test_background_scheduler_debounces_repeat_sends(self):
        started = []
        real_thread = __import__('threading').Thread

        def fake_thread(*a, **kw):
            started.append(kw.get('name'))
            t = real_thread(target=lambda: None)
            return t

        with mock.patch('main.views_social._chat_analysis_for'), \
             mock.patch('main.views_social.threading.Thread', side_effect=fake_thread):
            for _ in range(6):
                self._send('پیام تکراری برای تست')
        # The cache lock keeps concurrent sends from each spawning a worker.
        self.assertLessEqual(len(started), 4)


class GraphHealthRingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='graph-health', password='SecurePass1')
        self.root = Node.objects.create(owner=self.user, username='root-me')
        self.user.root_node = self.root
        self.user.save(update_fields=['root_node'])
        self.friend = Node.objects.create(owner=self.user, username='old-friend')
        Relationship.objects.create(owner=self.user, source=self.root, target=self.friend, strength=3)
        self.client.force_login(self.user)

    def test_graph_nodes_carry_relationship_health_for_the_ring(self):
        response = self.client.get('/api/graph/all/')
        self.assertEqual(response.status_code, 200)
        nodes = {n['username']: n for n in json.loads(response.content)['nodes']}
        self.assertIn('health_status', nodes['old-friend'])
        # A long-dormant connection should not read as green.
        Interaction.objects.create(
            owner=self.user, node=self.friend, kind='call',
            date=timezone.localdate() - timedelta(days=120),
        )
        payload = json.loads(self.client.get('/api/graph/all/').content)
        friend = next(n for n in payload['nodes'] if n['username'] == 'old-friend')
        self.assertIn(friend['health_status'], {'red', 'yellow', 'green', 'unknown', None})
        self.assertIn('health_counts', payload)

    def test_graph_page_documents_the_health_ring(self):
        response = self.client.get('/graph/')
        self.assertContains(response, 'حلقه = سلامت رابطه')


class AvatarPresentationTests(TestCase):
    def test_initials_take_first_and_last_word(self):
        from main.templatetags.people_tags import person_initials, avatar_style

        class _N:
            def __init__(self, n): self._n = n
            def display_name(self): return self._n

        self.assertEqual(person_initials(_N('سارا احمدی')), 'سا')
        self.assertEqual(person_initials(_N('Jane Doe')), 'JD')
        self.assertEqual(person_initials(_N('کیان')), 'کی')
        self.assertEqual(person_initials(_N('')), '؟')
        # Same key always yields the same colour; different keys usually differ.
        self.assertEqual(avatar_style('sara'), avatar_style('sara'))
        self.assertIn('hsl(', avatar_style('sara'))

    def test_directory_uses_coloured_initials_for_people_without_a_photo(self):
        User = get_user_model()
        user = User.objects.create_user(username='avatar-owner', password='SecurePass1')
        Node.objects.create(owner=user, username='no-photo', name='بدون عکس')
        self.client.force_login(user)
        response = self.client.get('/nodes/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'nc-avatar-ph" style="background:hsl(')


class WarmestIntroPathTests(TestCase):
    def _adj(self, edges):
        adj = {}
        for a, b, s in edges:
            adj.setdefault(a, {})[b] = ('', s)
            adj.setdefault(b, {})[a] = ('', s)
        return adj

    def test_prefers_a_chain_of_strong_ties_over_one_weak_hop(self):
        from main.views_connect import _warmest_path, _shortest_path
        # 1->4 direct but weak (1); 1-2-3-4 all strong (5).
        adj = self._adj([(1, 4, 1), (1, 2, 5), (2, 3, 5), (3, 4, 5)])
        self.assertEqual(_shortest_path(adj, 1, 4), [1, 4])
        self.assertEqual(_warmest_path(adj, 1, 4), [1, 2, 3, 4])

    def test_falls_back_to_fewest_hops_when_warm_path_detours_too_far(self):
        from main.views_connect import _warmest_path
        # Short weak path (2 hops) vs very long strong path (6 hops).
        edges = [(1, 9, 2), (9, 4, 2)]
        chain = [1, 2, 3, 5, 6, 7, 4]
        for a, b in zip(chain, chain[1:]):
            edges.append((a, b, 5))
        adj = self._adj(edges)
        self.assertEqual(_warmest_path(adj, 1, 4), [1, 9, 4])

    def test_connect_api_returns_a_path_through_the_graph(self):
        User = get_user_model()
        user = User.objects.create_user(username='intro-owner', password='SecurePass1')
        me = Node.objects.create(owner=user, username='intro-me')
        bridge = Node.objects.create(owner=user, username='bridge')
        target = Node.objects.create(owner=user, username='target')
        user.root_node = me
        user.save(update_fields=['root_node'])
        Relationship.objects.create(owner=user, source=me, target=bridge, strength=5)
        Relationship.objects.create(owner=user, source=bridge, target=target, strength=4)
        self.client.force_login(user)
        response = self.client.get(f'/api/connect/{target.id}/')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual([step['id'] for step in data['path']], [me.id, bridge.id, target.id])
        self.assertEqual(data['hops'], 2)


class AIPanelRenderingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='ai-panel', password='SecurePass1')
        self.client.force_login(self.user)

    def test_person_page_uses_a_skeleton_while_ai_panels_load(self):
        node = Node.objects.create(owner=self.user, username='ai-panel-friend')
        from django.template.loader import get_template
        source = get_template('nodes/node_detail.html').template.source
        self.assertIn('function aiSkel', source)
        self.assertIn('aiSkel(', source.split('function aiSkel', 1)[1])
        response = self.client.get(f'/nodes/{node.id}/')
        self.assertEqual(response.status_code, 200)

    def test_alerts_recommendation_panel_escapes_model_output(self):
        from django.template.loader import get_template
        source = get_template('alerts/alerts.html').template.source
        self.assertIn('const escA =', source)
        self.assertIn('${escA(s.action)}', source)
        self.assertNotIn("${s.action||''}", source)


class PersonaBatchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='batch', password='SecurePass1')
        self.root = Node.objects.create(owner=self.user, username='batch-root')
        self.user.root_node = self.root
        self.user.save(update_fields=['root_node'])
        self.a = Node.objects.create(owner=self.user, username='batch-a', name='الف')
        self.b = Node.objects.create(owner=self.user, username='batch-b', name='ب')
        Relationship.objects.create(owner=self.user, source=self.root, target=self.a, strength=3)
        for i in range(3):
            Interaction.objects.create(owner=self.user, node=self.a, kind='call',
                                       date=date(2025, 1, i + 1))
        self.client.force_login(self.user)

    def test_synthesize_everything_covers_people_and_relationships(self):
        from main.views_persona import synthesize_everything
        with mock.patch('main.views_persona._synthesize',
                        return_value=([{'text': 'قهوه', 'kind': 'سلیقه'}], 'خلاصه')):
            state = synthesize_everything(self.user)
        self.assertFalse(state['running'])
        self.assertGreaterEqual(state['people_ok'], 1)
        self.assertGreaterEqual(state['rel_ok'], 1)
        from main.models import PersonaProfile, RelationshipProfile
        self.assertTrue(PersonaProfile.objects.filter(owner=self.user, node=self.a).exists())
        self.assertTrue(RelationshipProfile.objects.filter(owner=self.user).exists())

    def test_one_failure_does_not_stop_the_batch(self):
        from main.views_persona import synthesize_everything
        calls = {'n': 0}

        def flaky(label, signals):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('AI hiccup')
            return ([{'text': 'x', 'kind': ''}], 's')

        with mock.patch('main.views_persona._synthesize', side_effect=flaky):
            state = synthesize_everything(self.user)
        self.assertEqual(state['failed'], 1)
        self.assertGreaterEqual(state['people_ok'] + state['rel_ok'], 1)

    def test_start_endpoint_is_async_and_status_is_owner_scoped(self):
        with mock.patch('main.views_persona.synthesize_everything'):
            r1 = self.client.post('/api/persona/synthesize-all/')
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.json().get('started') or r1.json().get('already_running'))
        other = get_user_model().objects.create_user(username='batch-other', password='SecurePass1')
        self.client.force_login(other)
        self.assertIsNone(self.client.get('/api/persona/batch-status/').json()['progress'])


class PersonaVersioningTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='persona-v', password='SecurePass1')
        self.node = Node.objects.create(owner=self.user, username='persona-friend')

    def test_payload_flags_statements_added_since_the_previous_synthesis(self):
        from main.models import PersonaProfile
        from main.views_persona import _payload
        p = PersonaProfile.objects.create(
            node=self.node, owner=self.user,
            previous_statements=[{'text': 'قهوه دوست دارد', 'kind': 'سلیقه'}],
            statements=[
                {'text': 'قهوه دوست دارد', 'kind': 'سلیقه'},
                {'text': 'شب‌ها سرحال‌تر است', 'kind': 'عادت'},
            ],
        )
        data = _payload(p)
        self.assertTrue(data['had_previous'])
        self.assertEqual(data['new_statements'], ['شب‌ها سرحال‌تر است'])

    def test_first_synthesis_has_no_new_badge(self):
        from main.models import PersonaProfile
        from main.views_persona import _payload
        p = PersonaProfile.objects.create(
            node=self.node, owner=self.user,
            statements=[{'text': 'اهل کتاب است', 'kind': 'سلیقه'}],
        )
        self.assertFalse(_payload(p)['had_previous'])
        self.assertEqual(_payload(p)['new_statements'], [])

    def test_synthesize_snapshots_the_prior_statements(self):
        from main.models import PersonaProfile
        PersonaProfile.objects.create(
            node=self.node, owner=self.user,
            statements=[{'text': 'نسخهٔ اول', 'kind': ''}],
        )
        self.client.force_login(self.user)
        with mock.patch(
            'main.views_persona._synthesize',
            return_value=([{'text': 'نسخهٔ دوم', 'kind': ''}], 'جمع‌بندی'),
        ), mock.patch('main.views_persona.gather_person_signals', return_value=['a', 'b', 'c']):
            response = self.client.post(f'/api/persona/node/{self.node.id}/synthesize/')
        self.assertEqual(response.status_code, 200)
        p = PersonaProfile.objects.get(node=self.node)
        self.assertEqual([s['text'] for s in p.previous_statements], ['نسخهٔ اول'])
        self.assertEqual([s['text'] for s in p.statements], ['نسخهٔ دوم'])


class AttentionPriorityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='attn', password='SecurePass1')
        self.root = Node.objects.create(owner=self.user, username='attn-root')
        self.user.root_node = self.root
        self.user.save(update_fields=['root_node'])
        self.a = Node.objects.create(owner=self.user, username='green-but-overdue-task', name='آدم اول')
        self.b = Node.objects.create(owner=self.user, username='just-quiet', name='آدم دوم')
        Relationship.objects.create(owner=self.user, source=self.root, target=self.a, strength=4)
        Relationship.objects.create(owner=self.user, source=self.root, target=self.b, strength=4)

    def test_overdue_followup_lifts_priority_and_explains_why(self):
        from main.health import attention_priority
        Interaction.objects.create(
            owner=self.user, node=self.a, kind='call',
            date=timezone.localdate() - timedelta(days=2),
        )
        FollowUp.objects.create(
            owner=self.user, node=self.a, text='زنگ بزن',
            due_date=timezone.localdate() - timedelta(days=5),
        )
        prio = attention_priority(self.user)
        self.assertGreater(prio[self.a.id]['score'], prio[self.b.id]['score'])
        self.assertIn('پیگیری عقب‌افتاده دارد', prio[self.a.id]['factors'])

    def test_dashboard_lists_a_person_flagged_only_by_an_overdue_task(self):
        Interaction.objects.create(
            owner=self.user, node=self.a, kind='call', date=timezone.localdate(),
        )
        FollowUp.objects.create(
            owner=self.user, node=self.a, text='قرار بود کتاب را برگردانم',
            due_date=timezone.localdate() - timedelta(days=3),
        )
        self.client.force_login(self.user)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        names = [item['node'].id for item in response.context['attention']]
        self.assertIn(self.a.id, names)


class SuggestedCirclesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='circles', password='SecurePass1')
        self.root = Node.objects.create(owner=self.user, username='circ-root')
        self.user.root_node = self.root
        self.user.save(update_fields=['root_node'])
        self.people = [Node.objects.create(owner=self.user, username=f'c{i}') for i in range(6)]
        for a, b in [(0, 1), (1, 2), (0, 2), (3, 4), (4, 5), (3, 5)]:
            Relationship.objects.create(
                owner=self.user, source=self.people[a], target=self.people[b], strength=4,
            )
        self.client.force_login(self.user)

    def test_detects_ungrouped_clusters_of_three_or_more(self):
        data = json.loads(self.client.get('/api/groups/suggested-circles/').content)
        self.assertTrue(data['ok'])
        self.assertTrue(all(c['size'] >= 3 for c in data['circles']))
        self.assertGreaterEqual(len(data['circles']), 1)

    def test_already_grouped_people_drop_out_of_the_suggestions(self):
        from main.models import Group
        g = Group.objects.create(owner=self.user, name='خانواده')
        for p in self.people[:3]:
            p.groups.add(g)
        circles = json.loads(self.client.get('/api/groups/suggested-circles/').content)['circles']
        flat = {nid for c in circles for nid in c['node_ids']}
        self.assertTrue(flat.isdisjoint({p.id for p in self.people[:3]}))

    def test_suggestions_are_owner_scoped(self):
        other = get_user_model().objects.create_user(username='circ-other', password='SecurePass1')
        self.client.force_login(other)
        data = json.loads(self.client.get('/api/groups/suggested-circles/').content)
        self.assertEqual(data['circles'], [])


class OccasionGreetingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='greet', password='SecurePass1')
        self.node = Node.objects.create(owner=self.user, username='greet-friend', name='مینا')
        self.client.force_login(self.user)

    def _fake_ai(self, text='تولدت مبارک مینا جان! امیدوارم سالِ خوبی پیش رو داشته باشی.'):
        client = mock.MagicMock()
        client.chat.completions.create.return_value.choices = [
            mock.MagicMock(message=mock.MagicMock(content=text))
        ]
        return (client, 'key', 'groq')

    def test_greeting_is_personalised_and_owner_scoped(self):
        with mock.patch('main.views_smart_features._ai_client', return_value=self._fake_ai()):
            response = self.client.post(
                '/api/alerts/greeting/',
                data=json.dumps({'node_id': self.node.id, 'alert_type': 'birthday', 'title': 'تولد مینا'}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn('مینا', json.loads(response.content)['greeting'])

        other = get_user_model().objects.create_user(username='greet-other', password='SecurePass1')
        self.client.force_login(other)
        with mock.patch('main.views_smart_features._ai_client', return_value=self._fake_ai()):
            denied = self.client.post(
                '/api/alerts/greeting/',
                data=json.dumps({'node_id': self.node.id, 'alert_type': 'birthday', 'title': 'x'}),
                content_type='application/json',
            )
        self.assertEqual(denied.status_code, 404)

    def test_greeting_requires_post(self):
        self.assertEqual(self.client.get('/api/alerts/greeting/').status_code, 405)

    def test_second_call_is_served_from_cache_without_a_new_ai_call(self):
        cache.clear()
        fake = self._fake_ai()
        with mock.patch('main.views_smart_features._ai_client', return_value=fake):
            self.client.post('/api/alerts/greeting/', data=json.dumps(
                {'node_id': self.node.id, 'alert_type': 'birthday', 'title': 't'}),
                content_type='application/json')
            self.client.post('/api/alerts/greeting/', data=json.dumps(
                {'node_id': self.node.id, 'alert_type': 'birthday', 'title': 't'}),
                content_type='application/json')
        self.assertEqual(fake[0].chat.completions.create.call_count, 1)


class ChatRetrievalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='rag', password='SecurePass1')
        self.sara = Node.objects.create(owner=self.user, username='sara', name='سارا')
        self.old = JournalEntry.objects.create(
            owner=self.user, entry_date=date(2024, 1, 5),
            text='با سارا رفتیم کافه و درباره سفر شمال حرف زدیم.',
        )
        self.old.mentioned_nodes.add(self.sara)
        for i in range(12):
            JournalEntry.objects.create(
                owner=self.user, entry_date=date(2025, 6, i + 1),
                text=f'یادداشت روزمرهٔ بی‌ربط شمارهٔ {i}',
            )

    def test_retrieval_surfaces_an_old_entry_the_recent_window_would_miss(self):
        from main.views import _retrieve_context
        ctx = _retrieve_context(self.user, 'آخرین بار کی با سارا رفتم کافه؟')
        self.assertIn('کافه', ctx)
        self.assertIn('2024-01-05', ctx)

    def test_retrieval_is_empty_for_a_contentless_question(self):
        from main.views import _retrieve_context
        self.assertEqual(_retrieve_context(self.user, 'سلام'), '')

    def test_retrieval_stays_within_the_owner(self):
        from main.views import _retrieve_context
        other = get_user_model().objects.create_user(username='rag-other', password='SecurePass1')
        self.assertEqual(_retrieve_context(other, 'سارا کافه شمال'), '')


class GraphTimelapseTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='tl', password='SecurePass1')
        self.root = Node.objects.create(owner=self.user, username='tl-root')
        self.user.root_node = self.root
        self.user.save(update_fields=['root_node'])
        self.old_friend = Node.objects.create(owner=self.user, username='tl-old')
        self.new_friend = Node.objects.create(owner=self.user, username='tl-new')
        Relationship.objects.create(
            owner=self.user, source=self.root, target=self.old_friend,
            strength=3, met_at=date(2019, 1, 1),
        )
        Relationship.objects.create(
            owner=self.user, source=self.root, target=self.new_friend, strength=3,
        )
        Interaction.objects.create(
            owner=self.user, node=self.old_friend, kind='call', date=date(2018, 6, 1),
        )

    def test_each_node_and_edge_carries_a_since_date(self):
        data = json.loads(self.client_login().get('/api/graph/all/').content)
        by_name = {n['username']: n for n in data['nodes']}
        self.assertEqual(by_name['tl-old']['since'], '2018-06-01')
        self.assertIsNotNone(by_name['tl-new']['since'])
        self.assertTrue(all('since' in e for e in data['edges']))

    def test_graph_page_exposes_the_timelapse_control(self):
        response = self.client_login().get('/graph/')
        self.assertContains(response, 'id="timelapse-wrap"')
        self.assertContains(response, 'toggleTimelapse()')

    def client_login(self):
        self.client.force_login(self.user)
        return self.client


class WebPushPulseTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='push-user', password='SecurePass1')
        self.client.force_login(self.user)

    def _sub_body(self, endpoint='https://push.example/abc'):
        return {'endpoint': endpoint, 'keys': {'p256dh': 'x' * 20, 'auth': 'y' * 16}}

    def test_subscribe_then_unsubscribe_is_owner_scoped(self):
        from main.models import PushSubscription
        r = self.client.post('/api/push/subscribe/', data=json.dumps(self._sub_body()),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(PushSubscription.objects.filter(owner=self.user).count(), 1)

        other = get_user_model().objects.create_user(username='push-other', password='SecurePass1')
        self.client.force_login(other)
        self.client.post('/api/push/unsubscribe/', data=json.dumps({'endpoint': 'https://push.example/abc'}),
                         content_type='application/json')
        self.assertEqual(PushSubscription.objects.filter(owner=self.user).count(), 1)

        self.client.force_login(self.user)
        self.client.post('/api/push/unsubscribe/', data='{}', content_type='application/json')
        self.assertEqual(PushSubscription.objects.filter(owner=self.user).count(), 0)

    def test_subscribe_rejects_incomplete_payloads(self):
        r = self.client.post('/api/push/subscribe/', data=json.dumps({'endpoint': 'https://push.example/x'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_pulse_digest_summarises_what_is_due(self):
        from main.push import build_pulse
        root = Node.objects.create(owner=self.user, username='pp-root')
        self.user.root_node = root
        self.user.save(update_fields=['root_node'])
        friend = Node.objects.create(owner=self.user, username='pp-friend')
        Relationship.objects.create(owner=self.user, source=root, target=friend, strength=4)
        FollowUp.objects.create(owner=self.user, node=friend, text='زنگ بزن',
                                due_date=timezone.localdate() - timedelta(days=4))
        payload = build_pulse(self.user)
        self.assertIsNotNone(payload)
        self.assertEqual(payload['url'], '/weekly/')
        self.assertIn('پیگیری عقب‌افتاده', payload['body'])

    def test_send_command_prunes_dead_subscriptions(self):
        from main.models import PushSubscription
        from django.core.management import call_command
        root = Node.objects.create(owner=self.user, username='pc-root')
        self.user.root_node = root
        self.user.save(update_fields=['root_node'])
        friend = Node.objects.create(owner=self.user, username='pc-friend')
        Relationship.objects.create(owner=self.user, source=root, target=friend, strength=4)
        FollowUp.objects.create(owner=self.user, node=friend, text='x',
                                due_date=timezone.localdate() - timedelta(days=2))
        PushSubscription.objects.create(owner=self.user, endpoint='https://push.example/dead',
                                        p256dh='p' * 20, auth='a' * 16)
        cmd = 'main.management.commands.send_relationship_pulse'
        with mock.patch(f'{cmd}.push_available', return_value=True), \
             mock.patch(f'{cmd}.send_web_push', return_value=(False, True)):
            call_command('send_relationship_pulse')
        self.assertEqual(PushSubscription.objects.count(), 0)


class JournalImageOCRTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='ocr', password='SecurePass1')
        self.client.force_login(self.user)
        try:
            from PIL import Image
            buf = io.BytesIO()
            Image.new('RGB', (16, 16), 'white').save(buf, 'PNG')
            data = buf.getvalue()
        except Exception:
            data = b'\x89PNG\r\n\x1a\n' + b'0' * 64
        from main.models import JournalImage
        self.img = JournalImage.objects.create(
            owner=self.user,
            image=SimpleUploadedFile('note.png', data, content_type='image/png'),
        )

    def _fake_ai(self, text='خرید: نان، شیر، تخم‌مرغ'):
        client = mock.MagicMock()
        client.chat.completions.create.return_value.choices = [
            mock.MagicMock(message=mock.MagicMock(content=text))
        ]
        return (client, 'vision-model')

    def test_ocr_returns_recognised_text_for_the_owner(self):
        with mock.patch('main.views._get_ai_client_and_model', return_value=self._fake_ai()):
            r = self.client.post('/api/journal/image-ocr/',
                                 data=json.dumps({'image_id': self.img.id}),
                                 content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertIn('تخم‌مرغ', json.loads(r.content)['text'])

    def test_ocr_is_owner_scoped(self):
        other = get_user_model().objects.create_user(username='ocr-other', password='SecurePass1')
        self.client.force_login(other)
        with mock.patch('main.views._get_ai_client_and_model', return_value=self._fake_ai()):
            r = self.client.post('/api/journal/image-ocr/',
                                 data=json.dumps({'image_id': self.img.id}),
                                 content_type='application/json')
        self.assertEqual(r.status_code, 404)

    def test_ocr_requires_an_image_id_and_post(self):
        self.assertEqual(self.client.get('/api/journal/image-ocr/').status_code, 405)
        r = self.client.post('/api/journal/image-ocr/', data='{}', content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_empty_recognition_is_reported_cleanly(self):
        with mock.patch('main.views._get_ai_client_and_model',
                        return_value=self._fake_ai('(متنی یافت نشد)')):
            r = self.client.post('/api/journal/image-ocr/',
                                 data=json.dumps({'image_id': self.img.id}),
                                 content_type='application/json')
        body = json.loads(r.content)
        self.assertTrue(body['ok'])
        self.assertTrue(body['empty'])


class DirectoryAppUserTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.me = U.objects.create_user(username='dir-me', password='SecurePass1', is_public=True)
        self.app_friend = U.objects.create_user(username='dir-appfriend', password='SecurePass1')
        self.app_stranger = U.objects.create_user(username='dir-appstranger', password='SecurePass1')
        Node.objects.create(owner=self.me, username='dir-appfriend', name='رفیق اپی')
        Node.objects.create(owner=self.me, username='dir-appstranger', name='غریبهٔ اپی')
        Node.objects.create(owner=self.me, username='dir-plainguy', name='مخاطب ساده')
        from main.models import Friendship
        Friendship.objects.create(user=self.me, friend=self.app_friend)
        self.client.force_login(self.me)

    def test_directory_marks_app_users_and_offers_chat_only_for_connections(self):
        r = self.client.get('/nodes/')
        self.assertEqual(r.status_code, 200)
        by_name = {n.username: n for n in r.context['nodes']}
        self.assertTrue(by_name['dir-appfriend'].is_app_user)
        self.assertTrue(by_name['dir-appstranger'].is_app_user)
        self.assertFalse(by_name['dir-plainguy'].is_app_user)
        # Chat only where there is a connection and I can use chat.
        self.assertTrue(by_name['dir-appfriend'].can_chat)
        self.assertEqual(by_name['dir-appfriend'].chat_user_id, self.app_friend.id)
        self.assertFalse(by_name['dir-appstranger'].can_chat)
        self.assertFalse(by_name['dir-plainguy'].can_chat)
        self.assertContains(r, 'در اپ')
        self.assertContains(r, 'فقط مخاطب')
        self.assertContains(r, 'chatWith(event,%d)' % self.app_friend.id)

    def test_focus_filter_splits_app_users_from_contacts(self):
        app_only = self.client.get('/nodes/?focus=app')
        self.assertEqual(
            {n.username for n in app_only.context['nodes']},
            {'dir-appfriend', 'dir-appstranger'},
        )
        contacts_only = self.client.get('/nodes/?focus=offline')
        self.assertEqual(
            {n.username for n in contacts_only.context['nodes']},
            {'dir-plainguy'},
        )

    def test_non_public_account_gets_the_badges_but_no_chat_button(self):
        self.me.is_public = False
        self.me.save(update_fields=['is_public'])
        r = self.client.get('/nodes/')
        by_name = {n.username: n for n in r.context['nodes']}
        self.assertTrue(by_name['dir-appfriend'].is_app_user)
        self.assertFalse(by_name['dir-appfriend'].can_chat)
        self.assertNotContains(r, 'chatWith(event')


class AIProviderConfigTests(TestCase):
    CLOUD_ENV = {
        'OPENROUTER_API_KEY': '',
        'GEMINI_API_KEY': '',
        'MISTRAL_API_KEY': '',
        'GROQ_API_KEY': '',
    }

    def test_ai_provider_env_pins_the_backend(self):
        from main.views_smart_features import _ai_client, _model
        with mock.patch.dict('os.environ', {
            'AI_PROVIDER': 'groq', 'GROQ_API_KEY': 'k', 'OPENROUTER_API_KEY': 'stale',
        }, clear=False):
            _client, _key, provider = _ai_client()
            self.assertEqual(provider, 'groq')
            self.assertEqual(_model(), 'llama-3.3-70b-versatile')

    def test_forced_provider_without_its_key_falls_back_to_auto(self):
        from main.views_smart_features import _ai_client, _model
        with mock.patch.dict('os.environ', {
            'AI_PROVIDER': 'mistral', 'MISTRAL_API_KEY': '', 'GROQ_API_KEY': 'g',
            'OPENROUTER_API_KEY': '', 'GEMINI_API_KEY': '',
        }, clear=False):
            _client, _key, provider = _ai_client()
            self.assertEqual(provider, 'groq')
            self.assertEqual(_model(), 'llama-3.3-70b-versatile')

    def test_reasoning_blocks_are_stripped_before_parsing(self):
        from main.views_smart_features import _strip_reasoning, _extract_json
        self.assertEqual(_strip_reasoning('<think>ummm</think>\n{"ok": 1}'), '{"ok": 1}')
        self.assertEqual(_extract_json('<think>x</think> noise {"v": 3} tail'), {'v': 3})
        self.assertEqual(_extract_json('```json\n{"z": 9}\n```'), {'z': 9})

    def test_stale_ollama_model_falls_back_to_an_installed_preferred_model(self):
        from main.views_smart_features import _ollama_model

        selected = _ollama_model(
            models=('deepseek-r1:14b', 'qwen3:8b', 'hamdam-fa:latest'),
            configured_model='qwen2.5:3b',
        )

        self.assertEqual(selected, 'hamdam-fa:latest')

    def test_local_provider_uses_an_installed_model_instead_of_stale_env(self):
        from main.views_smart_features import _ai_client, _model

        env = {
            **self.CLOUD_ENV,
            'AI_PROVIDER': '',
            'AI_MODEL': '',
            'OLLAMA_ENABLED': '1',
            'OLLAMA_MODEL': 'qwen2.5:3b',
        }
        with mock.patch.dict('os.environ', env, clear=False), mock.patch(
            'main.views_smart_features._ollama_model_names',
            return_value=('deepseek-r1:14b',),
        ):
            _client, configured, provider = _ai_client()
            self.assertEqual(configured, 'ollama')
            self.assertEqual(provider, 'ollama')
            self.assertEqual(_model(), 'deepseek-r1:14b')

    def test_ollama_without_installed_models_is_not_treated_as_configured(self):
        from main.views_smart_features import _ai_client

        env = {
            **self.CLOUD_ENV,
            'AI_PROVIDER': '',
            'AI_MODEL': '',
            'OLLAMA_ENABLED': '1',
        }
        with mock.patch.dict('os.environ', env, clear=False), mock.patch(
            'main.views_smart_features._ollama_model_names', return_value=(),
        ):
            client, configured, provider = _ai_client()

        self.assertIsNone(client)
        self.assertEqual(configured, '')
        self.assertEqual(provider, '')

    def test_openrouter_does_not_attach_an_unavailable_local_fallback(self):
        from main.views_smart_features import _AIClientFailover, _ai_client

        env = {
            **self.CLOUD_ENV,
            'AI_PROVIDER': 'openrouter',
            'OPENROUTER_API_KEY': 'key',
            'OLLAMA_ENABLED': '1',
        }
        with mock.patch.dict('os.environ', env, clear=False), mock.patch(
            'main.views_smart_features._ollama_model_names', return_value=(),
        ):
            client, configured, provider = _ai_client()

        self.assertNotIsInstance(client, _AIClientFailover)
        self.assertEqual(configured, 'key')
        self.assertEqual(provider, 'openrouter')

    def test_native_ollama_adapter_disables_thinking_and_maps_the_response(self):
        from main.views_smart_features import _OllamaChatCompletions

        raw_response = mock.MagicMock()
        raw_response.read.return_value = json.dumps({
            'model': 'hamdam-fa:latest',
            'message': {'content': 'می‌فهمم؛ دوست داری بیشتر تعریف کنی؟'},
        }).encode('utf-8')
        context = mock.MagicMock()
        context.__enter__.return_value = raw_response
        with mock.patch(
            'main.views_smart_features.urlopen', return_value=context
        ) as opened:
            result = _OllamaChatCompletions('http://127.0.0.1:11434').create(
                model='hamdam-fa:latest',
                messages=[{'role': 'user', 'content': 'حالم خوب نیست'}],
                max_tokens=300,
                temperature=0.5,
            )

        request = opened.call_args.args[0]
        payload = json.loads(request.data.decode('utf-8'))
        self.assertIs(payload['think'], False)
        self.assertEqual(payload['options']['num_predict'], 300)
        self.assertEqual(
            result.choices[0].message.content,
            'می‌فهمم؛ دوست داری بیشتر تعریف کنی؟',
        )


class JournalMomentTests(TestCase):
    def test_checkin_submission_requires_a_csrf_token(self):
        user = get_user_model().objects.create_user(username='checkin-csrf', password='SecurePass1')
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)

        response = client.post('/api/checkin/', data=json.dumps({}), content_type='application/json')

        self.assertEqual(response.status_code, 403)

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

    def test_journal_search_normalizes_arabic_and_persian_letters(self):
        user = get_user_model().objects.create_user(username='journal-search', password='SecurePass1')
        JournalEntry.objects.create(owner=user, text='دوستم علي کتاب خواند', entry_date=date.today())
        self.client.force_login(user)
        response = self.client.get('/api/journal/entries/?q=علی')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['entries']), 1)
        self.assertEqual(response.json()['total'], 1)

    def test_journal_filtering_has_a_result_status_and_recovery_action(self):
        user = get_user_model().objects.create_user(username='journal-filter-ui', password='SecurePass1')
        self.client.force_login(user)
        response = self.client.get('/journal/')
        self.assertContains(response, 'id="journalResultSummary"')
        self.assertContains(response, 'scheduleLoadEntries')
        self.assertContains(response, 'با این فیلترها یادداشتی پیدا نشد')


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

    def test_memory_search_normalizes_arabic_and_persian_letters(self):
        MemoryFact.objects.create(owner=self.user, node=self.ali, category='interest', value='دوستم علي')
        results = self.client.get('/api/memory/search/?q=علی').json()['results']
        self.assertTrue(any(row['kind'] == 'memory' for row in results))

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

    def test_palette_and_briefing_use_the_safe_dom_renderer(self):
        from django.template.loader import get_template
        base = get_template('base.html').template.source
        # The DOM-only renderer must be loaded so palette results (person
        # names) are not injected via innerHTML.
        self.assertIn('js/relationship_life.js', base)

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
