"""Run with: playwright install chromium && pytest e2e/test_smoke.py --base-url http://127.0.0.1:8000"""
import os
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
