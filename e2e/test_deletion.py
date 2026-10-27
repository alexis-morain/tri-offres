"""End-to-end test of account deletion (sub-step 7.1), in a real browser, at both widths.

Not part of `manage.py test` (e2e/ has no __init__.py). Run it with:

    .venv/bin/python manage.py test e2e.test_deletion

axe-core runs on every page: no serious or critical violation. Mail goes to the in-memory backend.
"""
import json
import os
from pathlib import Path

# Playwright's sync API runs an event loop in this thread; the test still talks to the database synchronously.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.conf import settings  # noqa: E402
from django.contrib.staticfiles.testing import StaticLiveServerTestCase  # noqa: E402
from django.core import mail  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from accounts.models import Profile, User  # noqa: E402
from accounts.testing import make_finished_profile  # noqa: E402
from config.testing import PLAIN_STATIC  # noqa: E402

AXE = Path(__file__).resolve().parent / "vendor" / "axe.min.js"
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}
BLOCKING = {"serious", "critical"}
EMAIL = "nora@example.fr"


@override_settings(STORAGES=PLAIN_STATIC, DEV_EMAILS=[], CLAUDE_ENGINE="fake",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class DeletionEndToEnd(StaticLiveServerTestCase):
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

    def setUp(self):
        self.user = make_finished_profile(EMAIL).user

    def open(self, viewport):
        context = self.browser.new_context(viewport=viewport, locale="fr-FR")
        context.set_default_timeout(15000)
        self.addCleanup(context.close)
        client = Client()
        client.force_login(self.user)
        context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                              "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                              "url": self.live_server_url}])
        self.width = viewport["width"]
        self.page = context.new_page()
        return self.page

    def check(self, name):
        """axe-core on the current page: no serious or critical violation, and no sideways scroll."""
        page = self.page
        page.wait_for_load_state("networkidle")
        page.add_script_tag(path=str(AXE))
        result = page.evaluate("async () => await axe.run(document, {resultTypes: ['violations']})")
        blocking = [v for v in result["violations"] if v["impact"] in BLOCKING]
        self.assertEqual(blocking, [], f"{self.width}px {name}: " + json.dumps(
            [(v["id"], v["impact"], [n["target"] for n in v["nodes"]]) for v in blocking], ensure_ascii=False))
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        self.assertLessEqual(overflow, 0, f"{self.width}px {name}: the page scrolls sideways")

    def journey(self, viewport):
        page = self.open(viewport)
        page.goto(self.live_server_url + "/mes-donnees/")
        self.check("mes-donnees")
        page.get_by_role("link", name="Supprimer mon compte").click()
        expect(page.locator("h1")).to_have_text("Supprimer mon compte")
        self.check("confirmation")
        page.get_by_role("link", name="Annuler").click()  # nothing is erased by leaving
        expect(page).to_have_url(self.live_server_url + "/mes-donnees/")
        self.assertTrue(User.objects.filter(email=EMAIL).exists())
        page.get_by_role("link", name="Supprimer mon compte").click()
        page.get_by_role("button", name="Supprimer définitivement mon compte").click()
        expect(page).to_have_url(self.live_server_url + "/compte-supprime/")
        expect(page.locator("h1")).to_have_text("Votre compte est supprimé")
        self.check("compte-supprime")
        self.assertFalse(User.objects.filter(email=EMAIL).exists())
        self.assertFalse(Profile.objects.exists())
        self.assertEqual([message.to for message in mail.outbox], [[EMAIL]])
        page.goto(self.live_server_url + "/mes-donnees/")  # signed out: sent to the sign-in page
        self.assertNotIn("/mes-donnees/", page.url.split("?")[0])

    def test_phone(self):
        self.journey(PHONE)

    def test_desktop(self):
        self.journey(DESKTOP)
