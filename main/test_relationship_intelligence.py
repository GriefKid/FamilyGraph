import time
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from .models import (
    Interaction,
    Information,
    JournalEntry,
    MemoryFact,
    Node,
    Relationship,
    RelationshipPulse,
)
from .relationship_intelligence import analyze_person_relationship


class RelationshipIntelligenceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username='evidence-owner', password='SecurePass1',
        )
        self.root = Node.objects.create(owner=self.user, username='evidence-root', name='من')
        self.person = Node.objects.create(owner=self.user, username='sara', name='سارا')
        self.user.root_node = self.root
        self.user.save(update_fields=['root_node'])

    def test_sparse_data_does_not_invent_personality_or_zero_score(self):
        result = analyze_person_relationship(self.user, self.person)

        self.assertIsNone(result['friendship_score'])
        self.assertIn('داده کافی', result['personality'])
        self.assertEqual(result['confidence'], 0)
        self.assertEqual(result['generated_by'], 'evidence_engine_v1')

    def test_score_and_claims_explain_their_evidence(self):
        Relationship.objects.create(
            owner=self.user, source=self.root, target=self.person,
            rel='دوست', strength=4, status='active',
        )
        today = timezone.localdate()
        for days, feeling in ((2, 1), (14, 1), (35, 0), (70, 1)):
            Interaction.objects.create(
                owner=self.user, node=self.person, kind='message',
                date=today - timedelta(days=days), feeling=feeling,
            )
        MemoryFact.objects.create(
            owner=self.user, node=self.person, category='interest', value='کوهنوردی را دوست دارد',
            confidence=90, source='manual', confidentiality='normal',
        )
        MemoryFact.objects.create(
            owner=self.user, node=self.person, category='communication',
            value='برای موضوع مهم تماس را ترجیح می‌دهد', confidence=85,
            source='manual', confidentiality='normal',
        )
        RelationshipPulse.objects.create(
            owner=self.user, node=self.person, support=4, autonomy=4,
            belonging=5, trust=4, voice=4,
        )

        result = analyze_person_relationship(self.user, self.person)

        self.assertIsInstance(result['friendship_score'], int)
        self.assertGreaterEqual(result['confidence'], 70)
        self.assertIn('کوهنوردی را دوست دارد', result['interests'])
        self.assertTrue(result['score_reasons'])
        self.assertGreater(result['data_coverage']['source_count'], 2)
        self.assertTrue(all(item.get('source_label') for item in result['evidence']))

    def test_no_ai_and_other_tenant_facts_never_enter_evidence(self):
        MemoryFact.objects.create(
            owner=self.user, node=self.person, category='value', value='صداقت مهم است',
            confidence=90, source='manual', confidentiality='normal',
        )
        MemoryFact.objects.create(
            owner=self.user, node=self.person, category='other', value='راز ممنوع خودم',
            confidence=100, source='manual', confidentiality='no_ai', ai_usable=True,
        )
        other = get_user_model().objects.create_user(
            username='evidence-other', password='SecurePass1',
        )
        other_node = Node.objects.create(owner=other, username='other-sara', name='سارا')
        MemoryFact.objects.create(
            owner=other, node=other_node, category='other', value='راز کاربر دیگر',
            confidence=100, source='manual', confidentiality='normal',
        )

        result = analyze_person_relationship(self.user, self.person)
        evidence_text = ' '.join(item['text'] for item in result['evidence'])

        self.assertIn('صداقت مهم است', evidence_text)
        self.assertNotIn('راز ممنوع خودم', evidence_text)
        self.assertNotIn('راز کاربر دیگر', evidence_text)

    def test_disabled_journal_ai_keeps_journal_out_of_analysis_and_chat_retrieval(self):
        entry = JournalEntry.objects.create(
            owner=self.user,
            text='راز ژورنال درباره سفر سارا',
            entry_date=timezone.localdate(),
        )
        entry.mentioned_nodes.add(self.person)
        self.user.ai_journal_enabled = False
        self.user.save(update_fields=['ai_journal_enabled'])

        result = analyze_person_relationship(self.user, self.person)
        evidence_text = ' '.join(item['text'] for item in result['evidence'])
        from .views import _retrieve_context
        retrieved = _retrieve_context(self.user, 'سفر سارا')

        self.assertNotIn('راز ژورنال', evidence_text)
        self.assertNotIn('راز ژورنال', retrieved)

    def test_analysis_endpoint_is_fast_and_does_not_call_provider(self):
        Relationship.objects.create(
            owner=self.user, source=self.root, target=self.person, strength=3,
        )
        Interaction.objects.create(
            owner=self.user, node=self.person, kind='call',
            date=timezone.localdate(), feeling=1,
        )
        self.client.force_login(self.user)

        started = time.monotonic()
        with mock.patch('main.views_smart_features._ai_client', side_effect=AssertionError('provider called')):
            response = self.client.post(f'/api/nodes/{self.person.id}/relation-analyze/')
        elapsed = time.monotonic() - started

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertLess(elapsed, 2.0)

    def test_direct_chat_analysis_skips_provider_and_returns_coverage(self):
        Relationship.objects.create(
            owner=self.user, source=self.root, target=self.person,
            rel='دوست', strength=4,
        )
        Interaction.objects.create(
            owner=self.user, node=self.person, kind='message',
            date=timezone.localdate(), feeling=1,
        )
        self.client.force_login(self.user)

        started = time.monotonic()
        with mock.patch('main.views._get_ai_client_and_model',
                        side_effect=AssertionError('provider called')):
            response = self.client.post(
                '/api/chat/',
                data='{"message":"رابطه‌ام با سارا رو تحلیل کن","history":[]}',
                content_type='application/json',
            )
        elapsed = time.monotonic() - started

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertIn('analysis', payload)
        self.assertIn('اطمینان', payload['reply'])
        self.assertLess(elapsed, 2.0)

    def test_telegram_relation_analysis_uses_imported_records_not_provider(self):
        Relationship.objects.create(
            owner=self.user, source=self.root, target=self.person,
            rel='تلگرام', strength=3,
        )
        Interaction.objects.create(
            owner=self.user, node=self.person, kind='message',
            date=timezone.localdate(), feeling=0, note='ایمپورت تلگرام',
        )
        cache.set(f'tg_scan_{self.user.id}', {
            'سارا': {'msgs': 240, 'days': ['2026-09-01', '2026-09-02'],
                     'sample': 'من: سلام\nاو: سلام'},
        })
        cache.set(f'tg_map_{self.user.id}', {'سارا': self.person.id})
        self.client.force_login(self.user)

        with mock.patch('main.views_smart_features._ai_client',
                        side_effect=AssertionError('provider called')):
            response = self.client.post(
                '/api/import/telegram/relation/',
                data=f'{{"name":"سارا","node_id":{self.person.id}}}',
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()['result']
        self.assertEqual(result['import_observation']['messages'], 240)
        self.assertEqual(result['generated_by'], 'evidence_engine_v1')

    def test_save_relation_recomputes_and_rejects_client_ai_claims(self):
        Relationship.objects.create(
            owner=self.user, source=self.root, target=self.person,
            rel='دوست', strength=4,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            '/api/import/telegram/save-relation/',
            data=(
                f'{{"node_id":{self.person.id},"data":'
                '{"personality":"ادعای جعلی کلاینت","friendship_score":1},'
                '"set_strength":false}'
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        stored = Information.objects.get(node=self.person).data
        self.assertNotEqual(stored.get('personality'), 'ادعای جعلی کلاینت')
        self.assertNotEqual(stored.get('friendship_score'), 1)
        self.assertEqual(stored.get('generated_by'), 'evidence_engine_v1')
