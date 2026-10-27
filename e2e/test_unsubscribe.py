"""End-to-end test of unsubscribing and re-subscribing (sub-step 7.2), in a real browser, at both widths.

Not part of `manage.py test` (e2e/ has no __init__.py). Run it with:

    .venv/bin/python manage.py test e2e.test_unsubscribe

axe-core runs on every page: no serious or critical violation. The unsubscribe pages are visited signed out; « Mes
données » is visited signed in.
"""
import json
import os
from pathlib import Path

# Playwright's sync API runs an event loop in this thread; the test still talks to the database synchronously.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.conf import settings  # noqa: E402
from django.contrib.staticfiles.testing import StaticLiveServerTestCase  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from accounts import unsubscribe  # noqa: E402
from accounts.models import User  # noqa: E402
from accounts.testing import make_finished_profile  # noqa: E402
from config.testing import PLAIN_STATIC  # noqa: E402

AXE = Path(__file__).resolve().parent / "vendor" / "axe.min.js"
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}
BLOCKING = {"serious", "critical"}
EMAIL = "nora@example.fr"


@override_settings(STORAGES=PLAIN_STATIC, DEV_EMAILS=[], CLAUDE_ENGINE="fake",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class UnsubscribeEndToEnd(StaticLiveServerTestCase):
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
        self.token = unsubscribe.make_token(self.user)

    def status(self):
        return User.objects.get(pk=self.user.pk).status

    def open(self, viewport):
        context = self.browser.new_context(viewport=viewport, locale="fr-FR")
        context.set_default_timeout(15000)
        self.addCleanup(context.close)
        self.context = context
        self.width = viewport["width"]
        self.page = context.new_page()
        return self.page

    def sign_in(self):
        client = Client()
        client.force_login(self.user)
        self.context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                                   "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                                   "url": self.live_server_url}])

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
        page.goto(self.live_server_url + f"/desinscription/{self.token}/")
        expect(page.locator("h1")).to_have_text("Ne plus recevoir le mail du matin")
        self.check("desinscription")
        self.assertEqual(self.status(), User.Status.ACTIVE)  # opening the link, as a mail scanner does, changes nothing
        page.get_by_role("button", name="Arrêter les mails").click()
        expect(page.locator("h1")).to_have_text("C’est fait, vous ne recevrez plus de mail.")
        self.check("desinscription-fait")
        self.assertEqual(self.status(), User.Status.UNSUBSCRIBED)
        self.sign_in()
        page.goto(self.live_server_url + "/mes-donnees/")
        expect(page.get_by_text("Vous ne recevez plus de mail.")).to_be_visible()
        self.check("mes-donnees-desinscrit")
        page.get_by_role("button", name="Recevoir de nouveau le mail du matin").click()
        expect(page).to_have_url(self.live_server_url + "/mes-donnees/")
        expect(page.get_by_text("Vous recevez le mail du matin.")).to_be_visible()
        self.check("mes-donnees-reabonne")
        self.assertEqual(self.status(), User.Status.ACTIVE)

    def test_phone(self):
        self.journey(PHONE)

    def test_desktop(self):
        self.journey(DESKTOP)
