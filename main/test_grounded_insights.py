import json
import time
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from .grounded_insights import cultural_work_analysis
from .models import (
    ChatMessage,
    ChatAnalysis,
    DirectMessage,
    ExtractionSuggestion,
    Friendship,
    Information,
    Interaction,
    JournalEntry,
    Node,
    Relationship,
)


class GroundedInsightApiTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.user = User.objects.create_user(username='grounded-owner', password='SecurePass1')
        self.root = Node.objects.create(owner=self.user, username='me', name='من')
        self.person = Node.objects.create(owner=self.user, username='sara', name='سارا')
        self.user.root_node = self.root
        self.user.save(update_fields=['root_node'])
        Relationship.objects.create(
            owner=self.user, source=self.root, target=self.person,
            rel='دوست', strength=4, status='active',
        )
        Interaction.objects.create(
            owner=self.user, node=self.person, kind='message',
            date=timezone.localdate(), feeling=1,
        )
        self.client.force_login(self.user)

    def _post(self, url, body=None):
        return self.client.post(
            url, data=json.dumps(body or {}), content_type='application/json',
        )

    def test_person_summary_is_fast_and_never_calls_provider(self):
        started = time.monotonic()
        with mock.patch(
            'main.views._get_ai_client_and_model',
            side_effect=AssertionError('provider called'),
        ):
            response = self._post(f'/api/node/{self.person.id}/summary/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['generated_by'], 'grounded_insights_v1')
        self.assertIn('شاهد ثبت‌شده', response.json()['summary'])
        self.assertLess(time.monotonic() - started, 2.0)

    def test_free_chat_degrades_cleanly_when_every_provider_is_down(self):
        with mock.patch(
            'main.views._get_ai_client_and_model',
            side_effect=RuntimeError('HTTP 500 provider failure'),
        ):
            response = self._post('/api/chat/', {
                'message': 'برای امشب یک پیشنهاد عمومی داری؟', 'history': [],
            })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['degraded'])
        self.assertEqual(
            response.json()['reason'], 'generation_provider_unavailable',
        )
        self.assertNotIn('HTTP 500', response.json()['reply'])

    def test_alert_recommendation_and_greeting_work_without_provider(self):
        with mock.patch(
            'main.views_smart_features._ai_client',
            side_effect=AssertionError('provider called'),
        ):
            recommendation = self._post('/api/alerts/recommendation/', {
                'node_id': self.person.id, 'alert_type': 'birthday',
                'title': 'تولد سارا',
            })
            greeting = self._post('/api/alerts/greeting/', {
                'node_id': self.person.id, 'alert_type': 'birthday',
                'title': 'تولد سارا',
            })

        self.assertEqual(recommendation.status_code, 200)
        result = recommendation.json()['result']
        self.assertEqual(result['generated_by'], 'grounded_insights_v1')
        self.assertTrue(result['suggestions'])
        self.assertEqual(greeting.status_code, 200)
        self.assertIn('تولدت مبارک', greeting.json()['greeting'])

    def test_network_analysis_is_local_and_reports_coverage(self):
        started = time.monotonic()
        with mock.patch(
            'main.views_smart_features._ai_client',
            side_effect=AssertionError('provider called'),
        ):
            response = self._post('/api/psychology/analyze/')

        self.assertEqual(response.status_code, 200)
        result = response.json()['result']
        self.assertEqual(result['generated_by'], 'grounded_insights_v1')
        self.assertEqual(result['coverage']['relationships_with_contact_data'], 1)
        self.assertIn('نمی‌توان شخصیت', result['psychological_profile'])
        self.assertLess(time.monotonic() - started, 2.0)

    def test_psychology_page_renders_grounded_metric_labels(self):
        response = self.client.get('/psychology/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'امتیاز پوشش و تازگی داده')
        self.assertContains(response, 'فاصلهٔ تماس ثبت‌شده')
        self.assertContains(response, 'از گراف قابل تشخیص نیست')
        self.assertNotContains(response, 'ریسک تنهایی اجتماعی')

    def test_daily_tips_match_both_frontend_field_shapes_without_provider(self):
        with mock.patch(
            'main.views_smart_features._ai_client',
            side_effect=AssertionError('provider called'),
        ):
            response = self._post('/api/daily/tips/')

        self.assertEqual(response.status_code, 200)
        tip = response.json()['result']['tips'][0]
        self.assertTrue(tip['title'])
        self.assertTrue(tip['action'])
        self.assertTrue(tip['tip'])
        self.assertTrue(tip['why'])

    def test_connect_plan_uses_graph_evidence_without_provider(self):
        bridge = Node.objects.create(owner=self.user, username='bridge', name='رضا')
        Relationship.objects.create(
            owner=self.user, source=self.root, target=bridge, strength=5,
        )
        Relationship.objects.create(
            owner=self.user, source=bridge, target=self.person, strength=5,
        )
        with mock.patch(
            'main.views_smart_features._ai_client',
            side_effect=AssertionError('provider called'),
        ):
            response = self._post(
                f'/api/connect/{self.person.id}/plan/', {'goal': 'friendship'},
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()['result']
        self.assertEqual(result['generated_by'], 'grounded_insights_v1')
        self.assertTrue(result['steps'])
        # A direct relationship already exists, so the plan must accurately
        # start there rather than inventing an introduction path.
        self.assertIn('رابطهٔ موجود', result['steps'][0]['title'])

    def test_other_tenant_node_is_never_available_to_grounded_endpoints(self):
        other = get_user_model().objects.create_user(
            username='grounded-other', password='SecurePass1',
        )
        other_node = Node.objects.create(owner=other, username='private-target')
        response = self._post(f'/api/connect/{other_node.id}/plan/')
        self.assertEqual(response.status_code, 404)

    def test_disabled_journal_is_excluded_from_health_persona_and_mood_alerts(self):
        journal_only = Node.objects.create(
            owner=self.user, username='journal-only', name='مهسا',
        )
        Relationship.objects.create(
            owner=self.user, source=self.root, target=journal_only, strength=3,
        )
        entry = JournalEntry.objects.create(
            owner=self.user, text='راز خصوصی مهسا', mood='خیلی ناراحت',
            ai_analyzed=True, entry_date=timezone.localdate(),
        )
        entry.mentioned_nodes.add(journal_only)
        self.user.ai_journal_enabled = False
        self.user.save(update_fields=['ai_journal_enabled'])

        from .health import compute_health
        from .views_persona import gather_person_signals
        from .views_smart_features import _compute_alerts

        self.assertIsNone(compute_health(self.user)[journal_only.id]['last_date'])
        self.assertNotIn('راز خصوصی مهسا', ' '.join(gather_person_signals(self.user, journal_only)))
        self.assertFalse(any(
            alert.get('type') == 'mood_alert' and alert.get('node_id') == journal_only.id
            for alert in _compute_alerts(self.user)
        ))

    def test_disabled_ai_chat_excludes_companion_messages_from_persona(self):
        ChatMessage.objects.create(
            owner=self.user, role='user', content='سارا و راز خیلی خصوصی چت',
        )
        self.user.ai_chat_enabled = False
        self.user.save(update_fields=['ai_chat_enabled'])

        from .views_persona import gather_person_signals
        signals = ' '.join(gather_person_signals(self.user, self.person))
        self.assertNotIn('راز خیلی خصوصی چت', signals)


class GroundedSourcePipelineTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.user = User.objects.create_user(username='source-owner', password='SecurePass1')
        self.friend_user = User.objects.create_user(
            username='source-friend', password='SecurePass1', is_public=True,
        )
        self.root = Node.objects.create(owner=self.user, username='source-me', name='من')
        self.sara = Node.objects.create(owner=self.user, username='sara', name='سارا')
        self.reza = Node.objects.create(owner=self.user, username='reza', name='رضا')
        self.user.root_node = self.root
        self.user.save(update_fields=['root_node'])
        Friendship.objects.create(user=self.user, friend=self.friend_user)
        Friendship.objects.create(user=self.friend_user, friend=self.user)
        self.client.force_login(self.user)

    def _post(self, url, body=None):
        return self.client.post(
            url, data=json.dumps(body or {}), content_type='application/json',
        )

    def test_social_chat_analysis_is_measurement_only(self):
        DirectMessage.objects.create(
            sender=self.user, receiver=self.friend_user,
            content='سلام، فردا برای پروژه وقت داری؟',
        )
        DirectMessage.objects.create(
            sender=self.friend_user, receiver=self.user,
            content='سلام، بله ساعت پنج خوبه.',
        )

        with mock.patch(
            'main.views_smart_features._ai_client',
            side_effect=AssertionError('provider called'),
        ):
            response = self._post(
                f'/api/social/messages/{self.friend_user.id}/analyze/',
            )

        self.assertEqual(response.status_code, 200)
        analysis = response.json()['analysis']
        self.assertTrue(analysis['grounded'])
        self.assertEqual(analysis['metrics']['messages'], 2)
        self.assertIn('قابل تعیین نیست', analysis['mood'])
        stored = ChatAnalysis.objects.get(user=self.user, friend=self.friend_user)
        self.assertTrue(stored.raw['grounded'])
        self.assertNotIn('person_read', stored.raw)

    def test_telegram_people_have_exact_evidence_and_no_provider_dependency(self):
        cache.set(f'tg_scan_{self.user.id}', {
            'سارا': {
                'msgs': 5, 'days': ['2026-09-01'],
                'sample': 'من: رضا گفت فردا میاد\nاو: باشه بهش خبر بده',
            },
        })
        with mock.patch(
            'main.views_smart_features._ai_client',
            side_effect=AssertionError('provider called'),
        ):
            response = self._post(
                '/api/import/telegram/analyze/', {'name': 'سارا'},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['grounded'])
        reza = next(row for row in payload['people'] if row['name'] == 'رضا')
        self.assertIn('رضا گفت', reza['evidence'])
        self.assertGreaterEqual(reza['confidence'], 70)

    def test_journal_analysis_saves_locally_and_apply_ignores_fake_attributes(self):
        with mock.patch(
            'main.views._get_ai_client_and_model',
            side_effect=AssertionError('provider called'),
        ):
            analyzed = self._post('/api/journal/analyze/', {
                'text': 'سارا دوست جدیدمه',
                'entry_date': str(timezone.localdate()),
                'entry_kind': 'moment',
            })

        self.assertEqual(analyzed.status_code, 200)
        result = analyzed.json()['result']
        self.assertTrue(result['grounded'])
        self.assertEqual(result['generated_by'], 'grounded_insights_v1')
        self.assertEqual(result['attributes'], [])
        self.assertEqual(result['nodes'][0]['username'], 'sara')
        self.assertTrue(ExtractionSuggestion.objects.filter(
            owner=self.user, source='journal', source_id=result['_entry_id'],
        ).exists())

        result['attributes'] = [{
            'username': 'sara', 'personality': ['ادعای جعلی'],
            'relationship_quality': 'قطعی و عالی',
        }]
        applied = self._post('/api/journal/apply/', result)

        self.assertEqual(applied.status_code, 200)
        self.assertEqual(applied.json()['created']['ignored_unverified_attributes'], 1)
        stored_text = json.dumps([
            row.data for row in Information.objects.filter(node=self.sara)
        ], ensure_ascii=False)
        self.assertNotIn('ادعای جعلی', stored_text)
        self.assertNotIn('قطعی و عالی', stored_text)

    def test_journal_apply_rejects_rows_without_server_suggestion_ids(self):
        analyzed = self._post('/api/journal/analyze/', {
            'text': 'یک یادداشت معمولی بدون موجودیت',
        }).json()['result']
        analyzed['nodes'] = [{'username': 'injected', 'name': 'تزریقی'}]
        response = self._post('/api/journal/apply/', analyzed)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Node.objects.filter(owner=self.user, username='injected').exists())

    def test_cultural_work_does_not_claim_personality(self):
        result = cultural_work_analysis('book', 'یک کتاب')
        self.assertTrue(result['grounded'])
        self.assertIn('به‌تنهایی', result['summary'])
        self.assertIn('ثابت نمی‌کند', result['summary'])


class StructuralSignalTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='struct', password='SecurePass1')
        self.root = Node.objects.create(owner=self.user, username='s-root')
        self.user.root_node = self.root
        self.user.save(update_fields=['root_node'])

    def test_break_point_names_the_bridge_and_who_is_cut_off(self):
        from .grounded_insights import network_break_points
        bridge = Node.objects.create(owner=self.user, username='bridge')
        far = Node.objects.create(owner=self.user, username='far')
        Relationship.objects.create(owner=self.user, source=self.root, target=bridge, strength=3)
        Relationship.objects.create(owner=self.user, source=bridge, target=far, strength=3)
        points = network_break_points(self.user)
        self.assertEqual(points[0]['name'], bridge.display_name())
        self.assertIn(far.display_name(), points[0]['isolates'])

    def test_no_break_points_when_the_graph_is_well_connected(self):
        from .grounded_insights import network_break_points
        a = Node.objects.create(owner=self.user, username='a')
        b = Node.objects.create(owner=self.user, username='b')
        for x, y in [(self.root, a), (self.root, b), (a, b)]:
            Relationship.objects.create(owner=self.user, source=x, target=y, strength=3)
        self.assertEqual(network_break_points(self.user), [])

    def test_fading_needs_both_a_strength_drop_and_a_cold_gap(self):
        from .grounded_insights import fading_relationships
        from .models import RelationshipStrengthHistory
        p = Node.objects.create(owner=self.user, username='fade')
        rel = Relationship.objects.create(owner=self.user, source=self.root, target=p, strength=2)
        old = RelationshipStrengthHistory.objects.create(owner=self.user, relationship=rel, strength=5)
        RelationshipStrengthHistory.objects.filter(pk=old.pk).update(
            changed_at=timezone.now() - timedelta(days=90))
        RelationshipStrengthHistory.objects.create(owner=self.user, relationship=rel, strength=2)
        Interaction.objects.create(owner=self.user, node=p, kind='call',
                                   date=timezone.localdate() - timedelta(days=400))
        names = [f['name'] for f in fading_relationships(self.user)]
        self.assertIn(p.display_name(), names)


class DiversityMeterTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='div', password='SecurePass1')
        self.root = Node.objects.create(owner=self.user, username='d-root')
        self.user.root_node = self.root
        self.user.save(update_fields=['root_node'])

    def test_flags_a_one_trade_network(self):
        from .grounded_insights import diversity_meter
        for i in range(6):
            Node.objects.create(owner=self.user, username=f'eng{i}', career='مهندس نرم‌افزار')
        notes = diversity_meter(self.user)['notes']
        self.assertTrue(any('مهندس' in n and '٪' in n for n in notes))

    def test_small_network_is_silent(self):
        from .grounded_insights import diversity_meter
        Node.objects.create(owner=self.user, username='one', career='x')
        self.assertEqual(diversity_meter(self.user)['notes'], [])
