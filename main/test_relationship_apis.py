import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import FollowUp, Interaction, Node, NodeCloseness, Relationship


class RelationshipApiContractTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='api-owner', password='SecurePass1')
        self.other_user = User.objects.create_user(username='api-other', password='SecurePass1')
        self.root = Node.objects.create(owner=self.user, username='api-me', name='من')
        self.friend = Node.objects.create(owner=self.user, username='api-friend', name='دوست')
        self.foreign = Node.objects.create(owner=self.other_user, username='api-foreign', name='دیگری')
        self.user.root_node = self.root
        self.user.save(update_fields=['root_node'])
        Relationship.objects.create(
            owner=self.user, source=self.root, target=self.friend,
            rel='دوست', strength=4, status='active',
        )
        self.client.force_login(self.user)

    def _post(self, path, payload):
        return self.client.post(path, data=json.dumps(payload), content_type='application/json')

    def test_unauthenticated_relationship_api_redirects(self):
        self.client.logout()
        response = self.client.get('/api/followups/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_followup_create_validates_method_json_text_node_and_date(self):
        self.assertEqual(self.client.get('/api/followups/create/').status_code, 405)
        invalid_json = self.client.post(
            '/api/followups/create/', data='{', content_type='application/json',
        )
        self.assertEqual(invalid_json.status_code, 400)
        self.assertEqual(self._post('/api/followups/create/', {'node_id': self.friend.id, 'text': '  '} ).status_code, 400)
        self.assertEqual(self._post('/api/followups/create/', {'node_id': self.foreign.id, 'text': 'نباید ثبت شود'}).status_code, 404)
        self.assertEqual(self._post('/api/followups/create/', {'node_id': self.friend.id, 'text': 'موضوع', 'due_date': '1403'}).status_code, 400)

    def test_followup_create_truncates_text_and_serializes_due_date(self):
        response = self._post('/api/followups/create/', {
            'node_id': self.friend.id,
            'text': 'x' * 350,
            'due_date': '2030-01-02',
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()['followup']
        self.assertEqual(len(payload['text']), 300)
        self.assertEqual(payload['due_date'], '2030-01-02')
        self.assertFalse(payload['done'])
        self.assertTrue(FollowUp.objects.filter(owner=self.user, node=self.friend).exists())

    def test_followup_toggle_snooze_delete_are_owner_scoped(self):
        followup = FollowUp.objects.create(
            owner=self.user, node=self.friend, text='تماس بگیر',
            due_date=timezone.localdate() - timedelta(days=2),
        )
        toggled = self._post(f'/api/followups/{followup.id}/toggle/', {})
        self.assertEqual(toggled.status_code, 200)
        followup.refresh_from_db()
        self.assertTrue(followup.done)
        self.assertIsNotNone(followup.done_at)

        open_followup = FollowUp.objects.create(
            owner=self.user, node=self.friend, text='پیگیری کن',
        )
        snoozed = self._post(f'/api/followups/{open_followup.id}/snooze/', {'days': 999})
        self.assertEqual(snoozed.status_code, 200)
        open_followup.refresh_from_db()
        self.assertEqual(open_followup.due_date, timezone.localdate() + timedelta(days=90))

        foreign_followup = FollowUp.objects.create(
            owner=self.other_user, node=self.foreign, text='خصوصی',
        )
        self.assertEqual(self._post(f'/api/followups/{foreign_followup.id}/toggle/', {}).status_code, 404)
        self.assertEqual(self._post(f'/api/followups/{foreign_followup.id}/snooze/', {}).status_code, 404)
        self.assertEqual(self._post(f'/api/followups/{foreign_followup.id}/delete/', {}).status_code, 200)
        self.assertTrue(FollowUp.objects.filter(pk=foreign_followup.id).exists())
        self.assertEqual(self._post(f'/api/followups/{open_followup.id}/delete/', {}).status_code, 200)
        self.assertFalse(FollowUp.objects.filter(pk=open_followup.id).exists())

    def test_followup_invalid_snooze_and_list_filtering(self):
        open_item = FollowUp.objects.create(owner=self.user, node=self.friend, text='باز')
        done_item = FollowUp.objects.create(owner=self.user, node=self.friend, text='تمام', done=True)
        FollowUp.objects.create(owner=self.other_user, node=self.foreign, text='نباید دیده شود')
        self.assertEqual(self._post(f'/api/followups/{open_item.id}/snooze/', {'days': 'bad'}).status_code, 400)
        self.assertEqual(self._post(f'/api/followups/{done_item.id}/snooze/', {'days': 7}).status_code, 404)

        response = self.client.get('/api/followups/', {'node_id': self.friend.id})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([item['text'] for item in data['open']], ['باز'])
        self.assertEqual([item['text'] for item in data['done']], ['تمام'])

    def test_interaction_log_validates_and_returns_followups_and_health(self):
        followup = FollowUp.objects.create(owner=self.user, node=self.friend, text='قول باز')
        self.assertEqual(self.client.get('/api/interactions/log/').status_code, 405)
        self.assertEqual(self.client.post('/api/interactions/log/', data='{', content_type='application/json').status_code, 400)
        self.assertEqual(self._post('/api/interactions/log/', {'node_id': self.foreign.id}).status_code, 404)
        future = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(self._post('/api/interactions/log/', {'node_id': self.friend.id, 'date': future}).status_code, 400)
        self.assertEqual(self._post('/api/interactions/log/', {'node_id': self.friend.id, 'date': '1403'}).status_code, 400)

        response = self._post('/api/interactions/log/', {
            'node_id': self.friend.id,
            'kind': 'not-a-kind',
            'feeling': 99,
            'support_kind': 'not-supported',
            'note': 'n' * 350,
        })
        self.assertEqual(response.status_code, 200)
        item = response.json()['interaction']
        self.assertEqual(item['kind'], 'other')
        self.assertEqual(item['feeling'], 1)
        self.assertEqual(len(item['note']), 300)
        self.assertEqual(response.json()['open_followups'][0]['id'], followup.id)
        self.assertTrue(Interaction.objects.filter(owner=self.user, node=self.friend).exists())

    def test_interactions_recent_is_scoped_and_serialized(self):
        Interaction.objects.create(owner=self.user, node=self.friend, kind='call', date=timezone.localdate(), feeling=-1, note='یادداشت')
        Interaction.objects.create(owner=self.other_user, node=self.foreign, kind='meet', date=timezone.localdate())
        response = self.client.get('/api/interactions/recent/')
        self.assertEqual(response.status_code, 200)
        items = response.json()['interactions']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['node_name'], 'دوست')
        self.assertEqual(items[0]['feeling_emoji'], '😕')

    def test_closeness_set_clear_and_foreign_node(self):
        self.assertEqual(self.client.get(f'/api/nodes/{self.friend.id}/closeness/').status_code, 405)
        self.assertEqual(self._post(f'/api/nodes/{self.friend.id}/closeness/', {'closeness': 'invalid'}).status_code, 400)
        response = self._post(f'/api/nodes/{self.friend.id}/closeness/', {'closeness': 'close'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['closeness'], 'close')
        self.assertEqual(NodeCloseness.objects.get(node=self.friend).tier, 'close')
        cleared = self._post(f'/api/nodes/{self.friend.id}/closeness/', {'closeness': ''})
        self.assertEqual(cleared.status_code, 200)
        self.assertFalse(NodeCloseness.objects.filter(node=self.friend).exists())
        self.assertEqual(self._post(f'/api/nodes/{self.foreign.id}/closeness/', {'closeness': 'close'}).status_code, 404)

    def test_relation_analysis_and_health_are_scoped(self):
        with mock.patch('main.relationship_intelligence.analyze_person_relationship', return_value={'score': 4}):
            response = self._post(f'/api/nodes/{self.friend.id}/relation-analyze/', {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['result']['score'], 4)
        self.assertEqual(self._post(f'/api/nodes/{self.foreign.id}/relation-analyze/', {}).status_code, 404)
        health = self.client.get('/api/health/')
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()['ok'])
