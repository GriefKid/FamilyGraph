from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.utils import timezone
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
        response = self.client.get('/nodes/?pinned=1')
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
            'q': 'paged-person', 'group': group.id, 'pinned': '1',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'directoryParams')
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
        self.assertContains(response, 'graph-search-results')
        self.assertContains(response, 'focusGraphNode')
        self.assertContains(response, 'zoom.transform')
        self.assertContains(response, "event.key === 'Enter'")

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

    def test_public_profile_hides_private_media_items(self):
        ProfileMediaItem.objects.create(user=self.match, kind='book', title='Public book', is_public=True)
        ProfileMediaItem.objects.create(user=self.match, kind='movie', title='Private movie', is_public=False)
        self.client.force_login(self.me)

        response = self.client.get('/u/match/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Public book')
        self.assertNotContains(response, 'Private movie')

    def test_share_cannot_export_a_legacy_edge_with_a_foreign_endpoint(self):
        User = get_user_model()
        recipient = User.objects.create_user(username='share-recipient', password='SecurePass1')
        foreign_owner = User.objects.create_user(username='share-foreign-owner', password='SecurePass1')
        own_node = Node.objects.create(owner=self.me, username='share-own-node')
        foreign_node = Node.objects.create(owner=foreign_owner, username='share-private-node')
        edge = Relationship.objects.bulk_create([Relationship(
            owner=self.me, source=own_node, target=foreign_node, rel='legacy-share-edge',
        )])[0]
        Follow.objects.create(follower=recipient, target=self.me)
        self.client.force_login(self.me)

        response = self.client.post('/api/social/share/send/', data=json.dumps({
            'item_type': 'edge', 'item_id': edge.id, 'recipient_ids': [recipient.id],
        }), content_type='application/json')

        self.assertEqual(response.status_code, 404)

    def test_gifbox_send_requires_an_allowed_recipient_and_valid_faces(self):
        node = Node.objects.create(owner=self.me, username='giftbox-source')
        self.client.force_login(self.me)
        payload = {
            'recipient_id': self.private.id,
            'share_type': 'node',
            'source_id': node.id,
        }

        blocked = self.client.post(
            '/api/social/gifbox/send/',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertFalse(GiftBox.objects.exists())

        Follow.objects.create(follower=self.match, target=self.me)
        malformed = self.client.post(
            '/api/social/gifbox/send/',
            data=json.dumps({
                **payload,
                'recipient_id': self.match.id,
                'cube_faces': [{'emo': 'x', 'lbl': 'x', 'ci': 0}],
            }),
            content_type='application/json',
        )
        self.assertEqual(malformed.status_code, 400)
        self.assertFalse(GiftBox.objects.exists())

        allowed = self.client.post(
            '/api/social/gifbox/send/',
            data=json.dumps({**payload, 'recipient_id': self.match.id}),
            content_type='application/json',
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertTrue(GiftBox.objects.filter(sender=self.me, recipient=self.match).exists())

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

    def test_social_json_endpoints_reject_non_object_payloads(self):
        self.client.force_login(self.me)

        circle_response = self.client.post(
            '/api/social/circles/', data='[]', content_type='application/json'
        )
        self.assertEqual(circle_response.status_code, 400)
        self.assertFalse(SocialCircle.objects.filter(created_by=self.me).exists())

        Friendship.objects.create(user=self.me, friend=self.match)
        Friendship.objects.create(user=self.match, friend=self.me)
        send_response = self.client.post(
            f'/api/social/messages/{self.match.id}/send/',
            data='[]', content_type='application/json',
        )
        self.assertEqual(send_response.status_code, 400)
        self.assertFalse(DirectMessage.objects.filter(sender=self.me, receiver=self.match).exists())

        node = Node.objects.create(owner=self.me, username='shared-info-node')
        info = Information.objects.create(node=node, visibility='private', data={'phone': 'hidden'})
        share_response = self.client.post(
            f'/api/social/share-info/{info.id}/',
            data='[]', content_type='application/json',
        )
        self.assertEqual(share_response.status_code, 400)
        info.refresh_from_db()
        self.assertEqual(info.visibility, 'private')


class AlertPrivacyTests(TestCase):
    def test_alerts_filter_legacy_foreign_event_and_journal_participants(self):
        User = get_user_model()
        user = User.objects.create_user(username='alerts-owner', password='SecurePass1')
        other = User.objects.create_user(username='alerts-other', password='SecurePass1')
        own_node = Node.objects.create(owner=user, username='alerts-own', name='Owner person')
        foreign_node = Node.objects.create(owner=other, username='alerts-foreign', name='Private foreign person')

        event = Event.objects.create(
            owner=user, title='Owner event', date=timezone.localdate() + timedelta(days=1),
        )
        event.participants.add(own_node)
        Event.participants.through.objects.bulk_create([
            Event.participants.through(event_id=event.id, node_id=foreign_node.id),
        ])
        journal = JournalEntry.objects.create(
            owner=user, text='Owner journal', mood='stress', ai_analyzed=True,
            entry_date=timezone.localdate(),
        )
        journal.mentioned_nodes.add(own_node)
        JournalEntry.mentioned_nodes.through.objects.bulk_create([
            JournalEntry.mentioned_nodes.through(journalentry_id=journal.id, node_id=foreign_node.id),
        ])

        self.client.force_login(user)
        response = self.client.get('/api/alerts/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIn('Owner person', serialized)
        self.assertNotIn('Private foreign person', serialized)


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

    def test_memory_search_matches_arabic_persian_keyboard_variants(self):
        MemoryFact.objects.create(
            owner=self.user,
            node=self.ali,
            category='interest',
            value='\u06a9\u062a\u0627\u0628\u06cc \u062a\u0627\u0631\u06cc\u062e\u06cc',
            source='manual',
        )
        foreign_node = Node.objects.create(owner=self.other, username='foreign-memory', name='Private')
        MemoryFact.objects.create(
            owner=self.other,
            node=foreign_node,
            category='interest',
            value='\u06a9\u062a\u0627\u0628\u06cc \u062e\u0635\u0648\u0635\u06cc',
            source='manual',
        )

        response = self.client.get('/api/memory/search/', {'q': '\u0643\u062a\u0627\u0628\u064a'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['results']), 1)
        self.assertEqual(response.json()['results'][0]['title'], self.ali.display_name())

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
        hub = self.client.get('/relationship-life/')
        self.assertEqual(hub.status_code, 200)
        self.assertContains(hub, '/static/js/relationship_life.js')
        from django.contrib.staticfiles import finders
        script_path = finders.find('js/relationship_life.js')
        with open(script_path, encoding='utf-8') as script_file:
            script = script_file.read()
        self.assertIn('box.replaceChildren()', script)
        self.assertNotIn('box.innerHTML', script)
        self.assertIn('window.loadPalette', script)
        self.assertNotIn('paletteResults.innerHTML', script)
        sw = self.client.get('/service-worker.js')
        self.assertEqual(sw.status_code, 200)
        self.assertIn('application/javascript', sw['Content-Type'])


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

    def test_insight_ai_reply_is_rendered_as_text_not_html(self):
        from django.template.loader import get_template
        source = get_template('insights/insights.html').template.source

        self.assertIn('paragraph.textContent=p', source)
        self.assertIn('error.textContent="خطا: "+(data.error||"نامشخص")', source)
        self.assertNotIn('body.innerHTML=data.reply', source)

    def test_dynamic_chat_and_palette_content_uses_safe_dom_rendering(self):
        from django.template.loader import get_template

        chat_source = get_template('chat/chat.html').template.source
        self.assertIn('paragraph.textContent=part.trim()', chat_source)
        self.assertNotIn('bubble.innerHTML=html', chat_source)

        base_source = get_template('base.html').template.source
        self.assertNotIn('paletteResults.innerHTML', base_source)
        self.assertIn('steps.replaceChildren()', base_source)
        self.assertIn("url.startsWith('/')&&!url.startsWith('//')", base_source)
        self.assertIn('window.loadOnboarding=async function', base_source)

        alerts_source = get_template('alerts/alerts.html').template.source
        self.assertIn('escA(s.action)', alerts_source)
        self.assertIn('escA(s.reason)', alerts_source)
        self.assertNotIn('${s.action||\'\'}', alerts_source)
        self.assertIn('window.loadDailyTips = async function(btn)', alerts_source)
        self.assertIn('res.replaceChildren()', alerts_source)

        home_source = get_template('home.html').template.source
        self.assertIn('escGraph(rel)', home_source)

        profile_edit_source = get_template('social/profile_edit.html').template.source
        self.assertIn('list.replaceChildren()', profile_edit_source)
        self.assertNotIn("getElementById('workSuggest').innerHTML", profile_edit_source)

        circles_source = get_template('social/circles.html').template.source
        self.assertIn('box.replaceChildren()', circles_source)
        self.assertNotIn("getElementById('circleMessages').innerHTML", circles_source)

        psychology_source = get_template('psychology/psychology.html').template.source
        self.assertIn('escapeAIValue(res)', psychology_source)
        self.assertIn('function legacyRenderAI', psychology_source)
        self.assertIn('renderPsychologyError(cont,d.error', psychology_source)

        node_detail_source = get_template('nodes/node_detail.html').template.source
        self.assertIn('window.loadSummary=async function', node_detail_source)
        self.assertIn('body.replaceChildren()', node_detail_source)
        self.assertIn("error.textContent=d.error||'خطا'", node_detail_source)

        discover_source = get_template('social/discover.html').template.source
        self.assertIn('encodeURIComponent(String(u.username||\'\'))', discover_source)
        self.assertIn('&quot;', discover_source)

        profile_source = get_template('social/profile.html').template.source
        self.assertIn('b.replaceChildren(image)', profile_source)

        gifbox_source = get_template('social/gifbox.html').template.source
        self.assertIn('json_script:"gifbox-inbox-data"', gifbox_source)
        self.assertNotIn('inbox_json|safe', gifbox_source)
        self.assertIn("el.textContent=text??''", gifbox_source)
        self.assertNotIn('insertAdjacentHTML', gifbox_source)
        self.assertNotIn('el.innerHTML=`<span class="re">', gifbox_source)

        journal_source = get_template('journal/journal.html').template.source
        self.assertIn('journal-nodes-data', journal_source)
        self.assertNotIn('nodes_json|safe', journal_source)
        self.assertIn("_esc(r.summary||'')", journal_source)
        self.assertIn('journalNodeTokens.set(token,uname)', journal_source)
        self.assertIn('dd.replaceChildren()', journal_source)
        self.assertIn("text.textContent=String(e.text||'')", journal_source)
        self.assertIn("edit.addEventListener('click',()=>loadEntry(", journal_source)
        self.assertIn('function journalSafeURL(value)', journal_source)
        self.assertNotIn('box.innerHTML = d.results.map', journal_source)
        self.assertNotIn("onclick=\"loadEntry('${e.text", journal_source)

        smart_source = Path(__file__).with_name('views_smart_features.py').read_text(encoding='utf-8')
        self.assertNotIn('Node.objects.get(pk=nb)', smart_source)

        checkin_source = get_template('checkin/checkin.html').template.source
        self.assertIn('checkin-people-data', checkin_source)
        self.assertNotIn('people_json|safe', checkin_source)

        telegram_source = get_template('import/telegram.html').template.source
        self.assertIn('telegram-node-options', telegram_source)
        self.assertNotIn('node_opts_json|safe', telegram_source)

        self.assertIn('psychology-moods-data', psychology_source)
        self.assertNotIn('recent_moods_json|safe', psychology_source)

        graph_source = get_template('nodes/graph.html').template.source
        self.assertIn('function graphText', graph_source)
        self.assertIn('function graphAttr', graph_source)
        self.assertIn('graphText(n.label||n.id)', graph_source)

    def test_manual_memory_also_builds_knowledge_triple(self):
        response = self.client.post('/api/memory/facts/', data=json.dumps({
            'action': 'create', 'node_id': self.node.id, 'category': 'interest', 'value': 'نجوم'}),
            content_type='application/json')
        self.assertEqual(response.status_code, 200)
        triple = KnowledgeTriple.objects.get(owner=self.user)
        self.assertEqual((triple.subject, triple.predicate, triple.object_text), (self.node, 'interest', 'نجوم'))

    def test_command_palette_and_onboarding_are_tenant_scoped(self):
        Node.objects.create(owner=self.other, username='secret-person', name='نباید دیده شود')
        results = self.client.get('/api/platform/command-palette/?q=secret').json()['results']
        self.assertNotIn('secret-person', [row.get('subtitle', '') for row in results])
        onboarding = self.client.get('/api/platform/onboarding/').json()
        self.assertEqual(len(onboarding['steps']), 5)

    def test_journal_apply_does_not_copy_unverified_private_public_link(self):
        foreign = Node.objects.create(
            owner=self.other, username='private-source', first_name='PRIVATE FIRST',
            career='PRIVATE CAREER',
        )
        response = self.client.post(
            '/api/journal/apply/',
            data=json.dumps({
                'nodes': [{'username': 'copied-person', 'name': 'Client name'}],
                'node_links': {'copied-person': {'id': foreign.id}},
                'relationships': 'malformed',
                'events': {'malformed': True},
                'attributes': None,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        copied = Node.objects.get(owner=self.user, username='copied-person')
        self.assertIsNone(copied.imported_from)
        self.assertEqual(copied.first_name, '')
        self.assertEqual(copied.career, '')

    def test_gifbox_serializes_user_content_with_json_script(self):
        Node.objects.create(
            owner=self.user, username='gif-script-node',
            name='</script><script>alert(1)</script>',
        )
        response = self.client.get('/social/gifbox/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'gifbox-nodes-data')
        self.assertContains(response, 'jsonData(')
        self.assertNotContains(response, '</script><script>alert(1)</script>')

    def test_journal_serializes_tags_without_executable_inline_data(self):
        JournalEntry.objects.create(
            owner=self.user, text='journal payload',
            tags=['</script><script>alert(1)</script>'],
        )
        response = self.client.get('/journal/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'journal-nodes-data')
        self.assertNotContains(response, "</script><script>alert(1)</script>")

    def test_journal_cannot_attach_another_users_pending_image(self):
        foreign_image = JournalImage.objects.create(
            owner=self.other,
            image='journal/private-pending-image.jpg',
        )

        response = self.client.post(
            '/api/journal/save/',
            data=json.dumps({
                'text': 'یادداشت مالک',
                'image_ids': [foreign_image.id],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        foreign_image.refresh_from_db()
        self.assertIsNone(foreign_image.entry_id)
        self.assertEqual(foreign_image.owner, self.other)

    def test_information_detail_escapes_json_values_in_html(self):
        info = Information.objects.create(
            node=self.node, visibility='private',
            data={'note': '</script><script>alert(1)</script>'},
        )
        legacy = self.client.get(f'/legacy/info/{info.id}/')
        modern = self.client.get(f'/informations/{info.id}/')

        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(modern.status_code, 200)
        self.assertNotContains(legacy, '</script><script>alert(1)</script>')
        self.assertNotContains(modern, '</script><script>alert(1)</script>')

    def test_json_script_backed_pages_render_successfully(self):
        for path in ('/journal/', '/checkin/', '/import/telegram/', '/psychology/', '/social/gifbox/'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

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

    def test_json_write_endpoints_require_object_payloads(self):
        response = self.client.post(
            f'/api/nodes/{self.node.id}/quick-update/',
            data='[]', content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            '/api/relationships/quick-create/',
            data='[]', content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            '/api/psychology/pulse/',
            data='[]', content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

        for path in (
            '/api/journal/save/',
            '/api/journal/analyze/',
            '/api/checkin/',
            '/api/import/whatsapp/apply/',
        ):
            with self.subTest(path=path):
                response = self.client.post(path, data='[]', content_type='application/json')
                self.assertEqual(response.status_code, 400)

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
