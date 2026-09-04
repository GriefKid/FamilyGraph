import json
import time
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from .models import AIRequestMetric


class _ReplyCompletions:
    def __init__(self, content='جواب کوتاه و روشن.'):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            model='specific-free-model',
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
        )


class ChatLatencyContractTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='latency-user', password='SecurePass1'
        )
        self.client.force_login(self.user)

    def test_provider_call_receives_remaining_eight_second_budget(self):
        completions = _ReplyCompletions()
        fake = SimpleNamespace(chat=SimpleNamespace(completions=completions), base_url='https://openrouter.ai/api/v1')

        with mock.patch.dict('os.environ', {
            'AI_PROVIDER': 'openrouter', 'OPENROUTER_API_KEY': 'test-key',
        }, clear=False), mock.patch('main.views._get_ai_client_and_model', return_value=(fake, 'openrouter/free')):
            response = self.client.post('/api/chat/', data=json.dumps({
                'message': 'یک جمله کوتاه درباره دوستی بگو', 'ephemeral': True,
            }), content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json().get('degraded', False))
        self.assertEqual(len(completions.calls), 1)
        self.assertGreater(completions.calls[0]['timeout'], 0)
        self.assertLessEqual(completions.calls[0]['timeout'], 8)
        metric = AIRequestMetric.objects.get(owner=self.user)
        self.assertEqual(metric.status, 'success')
        self.assertEqual(metric.actual_model, 'specific-free-model')
        self.assertEqual(metric.attempts, 1)

    def test_timeout_returns_a_useful_degraded_reply_and_metric(self):
        completions = mock.Mock()
        completions.create.side_effect = TimeoutError('provider timed out')
        fake = SimpleNamespace(chat=SimpleNamespace(completions=completions), base_url='https://api.groq.com/openai/v1')

        started = time.monotonic()
        with mock.patch.dict('os.environ', {
            'AI_PROVIDER': 'groq', 'GROQ_API_KEY': 'test-key',
            'OPENROUTER_API_KEY': '',
        }, clear=False), mock.patch('main.views._get_ai_client_and_model', return_value=(fake, 'test-model')):
            response = self.client.post('/api/chat/', data=json.dumps({
                'message': 'خیلی استرس دارم و نگرانم', 'ephemeral': True,
            }), content_type='application/json')

        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['degraded'])
        self.assertIn('نگرانی', response.json()['reply'])
        metric = AIRequestMetric.objects.get(owner=self.user)
        self.assertEqual(metric.status, 'timeout')
        self.assertNotIn('استرس', metric.__dict__.values())

    def test_context_budget_exhaustion_skips_provider(self):
        with mock.patch('main.views._chat_response_deadline_seconds', return_value=0.001), mock.patch(
            'main.views._get_ai_client_and_model', side_effect=AssertionError('provider must not be called')
        ):
            response = self.client.post('/api/chat/', data=json.dumps({
                'message': 'یک موضوع آزاد و کاملاً عمومی', 'ephemeral': True,
            }), content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['degraded'])
        self.assertEqual(response.json()['reason'], 'generation_deadline')

    def test_small_talk_is_instant_and_measured_locally(self):
        with mock.patch('main.views._get_ai_client_and_model', side_effect=AssertionError('provider called')):
            response = self.client.post('/api/chat/', data=json.dumps({'message': 'سلام'}),
                                        content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['grounded'])
        metric = AIRequestMetric.objects.get(owner=self.user)
        self.assertEqual(metric.provider, 'local-rules')
        self.assertEqual(metric.attempts, 0)


class ProviderLatencyConfigurationTests(TestCase):
    def tearDown(self):
        cache.delete('ai:provider-cooldown:openrouter')

    def test_auto_mode_prefers_groq_over_random_openrouter(self):
        from .views_smart_features import _ai_client, _model

        with mock.patch.dict('os.environ', {
            'AI_PROVIDER': '', 'GROQ_API_KEY': 'groq-key',
            'OPENROUTER_API_KEY': 'openrouter-key', 'MISTRAL_API_KEY': '',
            'GEMINI_API_KEY': '', 'AI_MODEL': '',
        }, clear=False):
            client, configured, provider = _ai_client()
            selected_model = _model()

        self.assertEqual(provider, 'groq')
        self.assertEqual(configured, 'groq-key')
        self.assertEqual(selected_model, 'llama-3.3-70b-versatile')
        self.assertEqual(client.max_retries, 0)
        self.assertLessEqual(float(client.timeout), 7)

    def test_openrouter_failure_cools_down_and_uses_local_budget(self):
        from .views_smart_features import _ChatCompletionFailover

        primary = mock.MagicMock()
        primary.chat.completions.create.side_effect = RuntimeError('HTTP 429 rate limit')
        fallback = mock.MagicMock()
        fallback.chat.completions.create.return_value = SimpleNamespace(model='qwen2.5:3b')
        completions = _ChatCompletionFailover(primary, fallback, 'qwen2.5:3b')

        first = completions.create(
            model='openrouter/free', messages=[{'role': 'user', 'content': 'سلام'}],
            max_tokens=80, timeout=7.5,
        )
        second = completions.create(
            model='openrouter/free', messages=[{'role': 'user', 'content': 'دوباره'}],
            max_tokens=80, timeout=7.5,
        )

        self.assertEqual(first.model, 'qwen2.5:3b')
        self.assertEqual(second.model, 'qwen2.5:3b')
        self.assertEqual(primary.chat.completions.create.call_count, 1)
        self.assertEqual(fallback.chat.completions.create.call_count, 2)
        self.assertLessEqual(fallback.chat.completions.create.call_args.kwargs['timeout'], 7.5)

    def test_openrouter_safety_model_is_never_returned_as_chat(self):
        from .views_smart_features import _ChatCompletionFailover

        primary = mock.MagicMock()
        primary.chat.completions.create.return_value = SimpleNamespace(
            model='vendor/content-safety:free',
            choices=[SimpleNamespace(message=SimpleNamespace(content=''))],
        )
        fallback = mock.MagicMock()
        fallback.chat.completions.create.return_value = SimpleNamespace(
            model='qwen2.5:3b',
            choices=[SimpleNamespace(message=SimpleNamespace(content='پاسخ واقعی'))],
        )
        completions = _ChatCompletionFailover(primary, fallback, 'qwen2.5:3b')

        result = completions.create(
            model='openrouter/free', messages=[{'role': 'user', 'content': 'سلام'}], timeout=8,
        )

        self.assertEqual(result.choices[0].message.content, 'پاسخ واقعی')
        self.assertEqual(completions.last_backend, 'ollama')
