"""End-to-end test of the privacy page and the legal notice (sub-step 7.3), in a real browser, at both widths.

Not part of `manage.py test` (e2e/ has no __init__.py). Run it with:

    .venv/bin/python manage.py test e2e.test_legal

With E2E_SCREENSHOTS=<dir>, a capture of each page at each width. axe-core runs on both pages: no serious or critical
violation, and no sideways scroll. Both pages are visited signed out.
"""
import json
import os
from pathlib import Path

# Playwright's sync API runs an event loop in this thread; the test still talks to the database synchronously.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.contrib.staticfiles.testing import StaticLiveServerTestCase  # noqa: E402
from django.test import override_settings  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from config.testing import PLAIN_STATIC  # noqa: E402

AXE = Path(__file__).resolve().parent / "vendor" / "axe.min.js"
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}
BLOCKING = {"serious", "critical"}


@override_settings(STORAGES=PLAIN_STATIC, LEGAL_ADDRESS="1 rue des Essais, 75001 Paris", LEGAL_SIRET="123 456 789 00012")
class LegalPagesEndToEnd(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def check(self, page, name):
        """axe-core on the current page: no serious or critical violation, and no sideways scroll."""
        page.wait_for_load_state("networkidle")
        page.add_script_tag(path=str(AXE))
        result = page.evaluate("async () => await axe.run(document, {resultTypes: ['violations']})")
        blocking = [v for v in result["violations"] if v["impact"] in BLOCKING]
        self.assertEqual(blocking, [], f"{self.width}px {name}: " + json.dumps(
            [(v["id"], v["impact"], [n["target"] for n in v["nodes"]]) for v in blocking], ensure_ascii=False))
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        self.assertLessEqual(overflow, 0, f"{self.width}px {name}: the page scrolls sideways")
        if os.environ.get("E2E_SCREENSHOTS"):
            shots = Path(os.environ["E2E_SCREENSHOTS"])
            shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shots / f"{name}-{self.width}.png"), full_page=True)

    def journey(self, viewport):
        self.width = viewport["width"]
        context = self.browser.new_context(viewport=viewport, locale="fr-FR")
        context.set_default_timeout(15000)
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto(self.live_server_url + "/confidentialite/")
        expect(page.locator("h1")).to_have_text("Ce que nous gardons et pourquoi")
        self.check(page, "confidentialite")
        page.locator("footer").get_by_role("link", name="Mentions légales").click()
        expect(page.locator("h1")).to_have_text("Mentions légales")
        expect(page.get_by_text("1 rue des Essais, 75001 Paris")).to_be_visible()
        self.check(page, "mentions-legales")
        page.locator("footer").get_by_role("link", name="Confidentialité").click()
        expect(page.locator("h1")).to_have_text("Ce que nous gardons et pourquoi")

    def test_phone(self):
        self.journey(PHONE)

    def test_desktop(self):
        self.journey(DESKTOP)
