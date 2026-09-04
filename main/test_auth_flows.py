from django.contrib.auth import get_user_model
from django.test import TestCase


class AuthenticationFlowTests(TestCase):
    def _captcha_answer(self):
        return self.client.session['captcha_answer']

    def test_captcha_refresh_is_get_only_and_rotates_question(self):
        first = self.client.get('/api/captcha/')
        self.assertEqual(first.status_code, 200)
        first_question = first.json()['question']
        second = self.client.get('/api/captcha/')
        self.assertEqual(second.status_code, 200)
        self.assertIn('question', second.json())
        self.assertEqual(self.client.post('/api/captcha/').status_code, 405)
        self.assertEqual(self.client.session['captcha_question'], second.json()['question'])
        self.assertTrue(first_question)

    def test_login_rejects_wrong_captcha_and_rotates_it(self):
        response = self.client.get('/login/')
        self.assertEqual(response.status_code, 200)
        old_question = self.client.session['captcha_question']
        response = self.client.post('/login/', {
            'username': 'nobody', 'password': 'SecurePass1', 'captcha': '999',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'جواب سوال ریاضی اشتباهه')
        self.assertNotEqual(self.client.session['captcha_question'], old_question)

    def test_login_accepts_email_and_never_redirects_to_external_next(self):
        user = get_user_model().objects.create_user(
            username='email-login', email='email-login@example.com', password='SecurePass1',
        )
        self.client.get('/login/')
        response = self.client.post('/login/?next=//evil.example', {
            'username': user.email, 'password': 'SecurePass1',
            'captcha': str(self._captcha_answer()),
        })
        self.assertRedirects(response, '/', fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), user.id)

    def test_authenticated_user_is_redirected_away_from_login_and_register(self):
        user = get_user_model().objects.create_user(username='already-in', password='SecurePass1')
        self.client.force_login(user)
        self.assertRedirects(self.client.get('/login/'), '/', fetch_redirect_response=False)
        self.assertRedirects(self.client.get('/register/'), '/', fetch_redirect_response=False)

    def test_registration_validates_username_duplicate_password_and_confirmation(self):
        cases = (
            ({'username': 'ab', 'password': 'SecurePass1', 'password2': 'SecurePass1'}, 'نام کاربری باید'),
            ({'username': 'valid-user', 'password': 'SecurePass1', 'password2': 'Different1'}, 'یکی نیستن'),
            ({'username': 'valid-user', 'password': 'weak', 'password2': 'weak'}, 'حداقل ۸ کاراکتر'),
        )
        for payload, message in cases:
            response = self.client.post('/register/', {'step': '1', **payload})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, message)

        get_user_model().objects.create_user(username='taken-user', password='SecurePass1')
        response = self.client.post('/register/', {
            'step': '1', 'username': 'taken-user',
            'password': 'SecurePass1', 'password2': 'SecurePass1',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'قبلاً ثبت شده')

    def test_registration_rejects_duplicate_email(self):
        get_user_model().objects.create_user(
            username='existing-email', email='same@example.com', password='SecurePass1',
        )
        response = self.client.post('/register/', {
            'step': '1', 'username': 'new-email-user', 'email': 'same@example.com',
            'password': 'SecurePass1', 'password2': 'SecurePass1',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'این ایمیل قبلاً ثبت شده')
