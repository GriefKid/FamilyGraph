import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import ChatMessage
from .persian_chat import normalize_persian_reply, persian_quality_issues


class _FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.replies.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class HamdamPersianTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='hamdam-owner', password='SecurePass1'
        )
        self.client.force_login(self.user)

    def test_normalizer_fixes_arabic_letters_and_robotic_intro(self):
        result = normalize_persian_reply('به عنوان یک هوش مصنوعی، من درك مي كنم')
        self.assertEqual(result, 'من درک می کنم')
        self.assertEqual(persian_quality_issues('This answer is only English'),
                         ['too_much_non_persian'])

    @patch('main.views._get_ai_client_and_model')
    def test_chat_uses_selected_persian_style_and_examples(self, get_ai):
        completions = _FakeCompletions(['خب، بیا آروم درباره‌ش حرف بزنیم.'])
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        get_ai.return_value = (client, 'test-model')

        response = self.client.post('/api/chat/', data=json.dumps({
            'message': 'حالم خوب نیست', 'style': 'concise',
        }), content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['style'], 'concise')
        messages = completions.calls[0]['messages']
        self.assertIn('حداکثر سه جمله', messages[0]['content'])
        self.assertTrue(any('امروز با دوستم دعوام شد' in m['content'] for m in messages))
        self.assertEqual(ChatMessage.objects.filter(owner=self.user).count(), 2)

    @patch('main.views._get_ai_client_and_model')
    def test_chat_rewrites_non_persian_answer_before_saving(self, get_ai):
        completions = _FakeCompletions([
            'This is a completely English and robotic answer.',
            'حق داری گیج شده باشی؛ بیا قدم‌به‌قدم نگاهش کنیم.',
        ])
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        get_ai.return_value = (client, 'test-model')

        response = self.client.post('/api/chat/', data=json.dumps({
            'message': 'نمی‌دونم باید چی کار کنم',
        }), content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(completions.calls), 2)
        self.assertEqual(response.json()['reply'],
                         'حق داری گیج شده باشی؛ بیا قدم‌به‌قدم نگاهش کنیم.')
        self.assertEqual(ChatMessage.objects.filter(
            owner=self.user, role='assistant').get().content,
            response.json()['reply'])
