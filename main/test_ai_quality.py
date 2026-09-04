import json

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .ai_quality import percentile, run_persian_extraction_eval
from .models import (AIExtractionTrace, AIQualityEvaluation, AIRequestMetric, ExtractionSuggestion,
                     Node, ObservabilityEvent, RelationshipRecommendation)


class PersianExtractionEvaluationTests(SimpleTestCase):
    def test_checked_in_suite_passes_without_database_or_provider(self):
        report = run_persian_extraction_eval()

        self.assertEqual(report['suite_version'], 'persian-extraction-v1')
        self.assertEqual(report['total_cases'], 22)
        self.assertEqual(report['passed_cases'], report['total_cases'])
        self.assertEqual(report['precision'], 100.0)
        self.assertEqual(report['recall'], 100.0)
        self.assertEqual(report['failures'], [])

    def test_report_contains_no_input_text(self):
        serialized = json.dumps(run_persian_extraction_eval(), ensure_ascii=False)

        self.assertNotIn('سارا ازم', serialized)
        self.assertNotIn('"text"', serialized)

    def test_percentile_uses_nearest_rank(self):
        values = [100, 9000, 12000]

        self.assertEqual(percentile(values, 50), 9000)
        self.assertEqual(percentile(values, 95), 12000)
        self.assertEqual(percentile([], 95), 0)


class AIQualityDashboardTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username='quality-admin', email='quality@example.com', password='strong-test-pass'
        )
        self.member = user_model.objects.create_user(username='quality-member', password='strong-test-pass')
        self.node = Node.objects.create(owner=self.admin, username='quality-person', name='شخص آزمایشی')

        for index, status in enumerate(('approved', 'approved', 'dismissed', 'pending'), start=1):
            ExtractionSuggestion.objects.create(
                owner=self.admin, source='journal', source_id=index, kind='memory',
                status=status, fingerprint=f'{index:064d}', payload={'synthetic': True},
            )
        AIExtractionTrace.objects.create(
            owner=self.admin, source='journal', input_text='راز شخصی کاربر', duration_ms=100,
            status='regex_only', provider='',
        )
        AIExtractionTrace.objects.create(
            owner=self.admin, source='journal', input_text='متن خصوصی دیگر', duration_ms=9000,
            status='hybrid', provider='groq',
        )
        AIExtractionTrace.objects.create(
            owner=self.admin, source='journal', input_text='متن خطادار خصوصی', duration_ms=12000,
            status='ai_failed', provider='groq', error_code='TimeoutError',
        )
        RelationshipRecommendation.objects.create(
            owner=self.admin, node=self.node, kind='connect', title='آزمایشی', suggestion='آزمایشی',
            status='completed', outcome='better', helpful=True,
        )
        RelationshipRecommendation.objects.create(
            owner=self.admin, node=self.node, kind='connect', title='آزمایشی دوم', suggestion='آزمایشی',
            status='completed', outcome='same', helpful=False,
        )
        ObservabilityEvent.objects.create(
            owner=self.admin, level='error', area='ai', code='provider_error',
            path='/private/', message='جزئیات بسیار خصوصی', request_id='safe-request-id',
        )

    def test_dashboard_is_superuser_only_and_exposes_aggregate_metrics(self):
        self.client.force_login(self.member)
        denied = self.client.get(reverse('ai_quality_dashboard'))
        self.assertEqual(denied.status_code, 302)

        self.client.force_login(self.admin)
        response = self.client.get(reverse('ai_quality_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['p50_ms'], 9000)
        self.assertEqual(response.context['p95_ms'], 12000)
        self.assertEqual(response.context['under_10s_rate'], 66.7)
        self.assertEqual(response.context['feedback_total'], 2)
        self.assertEqual(response.context['helpful_rate'], 50.0)
        memory = next(row for row in response.context['suggestion_rows'] if row['kind'] == 'memory')
        self.assertEqual(memory['approval_rate'], 66.7)
        groq = next(row for row in response.context['provider_rows'] if row['provider'] == 'groq')
        self.assertEqual(groq['error_rate'], 50.0)
        self.assertNotContains(response, 'راز شخصی کاربر')
        self.assertNotContains(response, 'جزئیات بسیار خصوصی')

    def test_run_endpoint_persists_only_synthetic_aggregate_report(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse('ai_quality_run'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total_cases'], 22)
        evaluation = AIQualityEvaluation.objects.get()
        self.assertEqual(evaluation.run_by, self.admin)
        self.assertEqual(evaluation.passed_cases, evaluation.total_cases)
        serialized = json.dumps(evaluation.report, ensure_ascii=False)
        self.assertNotIn('راز شخصی کاربر', serialized)
        self.assertNotIn('"text"', serialized)

    def test_dashboard_prefers_real_chat_latency_telemetry(self):
        AIRequestMetric.objects.create(
            owner=self.admin, feature='chat', provider='openrouter',
            requested_model='minimax/minimax-m3:free',
            actual_model='minimax/minimax-m3:free', duration_ms=6100,
            deadline_ms=8000, status='success', attempts=1,
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse('ai_quality_dashboard'))

        self.assertEqual(response.context['metric_source'], 'چت همدم')
        self.assertEqual(response.context['p50_ms'], 6100)
        self.assertEqual(response.context['under_10s_rate'], 100.0)
        self.assertContains(response, 'minimax/minimax-m3:free')

    def test_management_command_can_save_the_report(self):
        call_command('evaluate_ai_quality', '--save', '--fail-under', '100')

        self.assertEqual(AIQualityEvaluation.objects.count(), 1)
        self.assertEqual(AIQualityEvaluation.objects.get().pass_rate, 100.0)
