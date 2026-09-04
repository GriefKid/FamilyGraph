"""Run with: playwright install chromium && E2E_BASE_URL=http://127.0.0.1:8000 pytest e2e/test_smoke.py"""
import os
import re

import pytest
from playwright.sync_api import sync_playwright


def test_login_and_responsive_shell():
    base = os.environ.get('E2E_BASE_URL', 'http://127.0.0.1:8000')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        page.goto(base + '/login/')
        assert page.locator('form').count() >= 1
        assert page.locator('body').get_attribute('dir') in (None, 'rtl')
        browser.close()


def test_public_auth_and_legal_navigation():
    base = os.environ.get('E2E_BASE_URL', 'http://127.0.0.1:8000')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 800})

        login_response = page.goto(base + '/login/')
        assert login_response and login_response.ok
        assert page.locator('a[href="/password-reset/"]').count() == 1
        assert page.locator('a[href="/register/"]').count() == 1

        for path, heading in (('/privacy/', 'حریم خصوصی'), ('/terms/', 'شرایط استفاده')):
            response = page.goto(base + path)
            assert response and response.ok
            assert heading in page.locator('body').inner_text()
            assert page.locator('body').get_attribute('dir') in (None, 'rtl')
        browser.close()


def test_register_shows_required_privacy_consent():
    base = os.environ.get('E2E_BASE_URL', 'http://127.0.0.1:8000')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        response = page.goto(base + '/register/?step=3')
        assert response and response.ok
        consent = page.locator('input[name="privacy_consent"]')
        assert consent.count() == 1
        assert consent.get_attribute('required') is not None
        assert page.locator('a[href="/privacy/"]').count() >= 1
        browser.close()


def _captcha_answer(question):
    digits = str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789')
    match = re.search(r'(\d+)\s*([+−×])\s*(\d+)', question.translate(digits))
    assert match, f'Unexpected captcha question: {question!r}'
    left, operator, right = match.groups()
    left, right = int(left), int(right)
    return {'+': left + right, '−': left - right, '×': left * right}[operator]


@pytest.mark.skipif(
    not os.environ.get('E2E_USER') or not os.environ.get('E2E_PASSWORD'),
    reason='Set E2E_USER and E2E_PASSWORD for authenticated dashboard smoke tests',
)
def test_authenticated_psychology_dashboard_controls():
    base = os.environ.get('E2E_BASE_URL', 'http://127.0.0.1:8000')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 800})
        page.goto(base + '/login/')
        page.fill('#username', os.environ['E2E_USER'])
        page.fill('#password', os.environ['E2E_PASSWORD'])
        page.fill('#captchaAns', str(_captcha_answer(page.locator('#captchaQ').inner_text())))
        page.click('#loginBtn')
        page.wait_for_url(re.compile(r'^(?!.*login/).+$'), timeout=10000)

        response = page.goto(base + '/psychology/')
        assert response and response.ok
        assert page.locator('#psychHeroTitle').count() == 1
        assert page.locator('#psychTheorySearch').count() == 1
        page.locator('[data-filter="sociology"]').click()
        assert page.locator('[data-psych-section~="sociology"]').first.is_visible()
        assert page.locator('[data-psych-section~="network"]').first.is_hidden()
        page.fill('#psychTheorySearch', 'داونبار')
        assert 'کارت پیدا شد' in page.locator('#psychFilterStatus').inner_text()
        browser.close()
