"""End-to-end test of the texts after the end of the trial (sub-step 8.2), in a real browser, at both widths.

Not part of `manage.py test` (e2e/ has no __init__.py). Run it with:

    .venv/bin/python manage.py test e2e.test_trial_end

« Mes données » and the sort page are visited signed in, outside the beta, with a trial that ended: axe-core finds
no serious or critical violation and the page does not scroll sideways.
"""
import json
import os
from datetime import datetime, timedelta, timezone as utc
from pathlib import Path

# Playwright's sync API runs an event loop in this thread; the test still talks to the database synchronously.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.conf import settings  # noqa: E402
from django.contrib.staticfiles.testing import StaticLiveServerTestCase  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.utils import timezone  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from accounts.models import User  # noqa: E402
from accounts.testing import make_finished_profile  # noqa: E402
from config.testing import PLAIN_STATIC  # noqa: E402
from jobs.testing import make_offer, make_source  # noqa: E402
from matching.models import Score  # noqa: E402

AXE = Path(__file__).resolve().parent / "vendor" / "axe.min.js"
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}
BLOCKING = {"serious", "critical"}
NOTICE = "Votre essai est terminé : aucune nouvelle offre n’arrive."


@override_settings(STORAGES=PLAIN_STATIC, DEV_EMAILS=[], CLAUDE_ENGINE="fake", BETA=False)
class TrialEndEndToEnd(StaticLiveServerTestCase):
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
        self.user = make_finished_profile("nora@example.fr").user
        User.objects.filter(pk=self.user.pk).update(trial_ends_at=timezone.now() - timedelta(days=1))
        offer = make_offer(make_source(name="Source fictive", domains=[]), external_id="1", title="Chargée de mission",
                           employer="Association fictive", location="Nantes", contract="CDI",
                           summary="Une mission fictive.", url="https://example.fr/offre/1",
                           source_license="Données Source fictive, licence ouverte",
                           published_at=datetime(2026, 10, 14, 9, tzinfo=utc.utc))
        Score.objects.create(user=self.user, offer=offer, score=7, reason="Un bon accord.",
                             category=Score.Category.TO_REVIEW)

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
        expect(page.get_by_text("Terminé le")).to_be_visible()
        expect(page.get_by_role("button", name="Recevoir de nouveau le mail du matin")).to_have_count(0)
        self.check("mes-donnees")
        page.goto(self.live_server_url + "/tri/")
        expect(page.get_by_text(NOTICE)).to_be_visible()
        self.check("tri")

    def test_phone(self):
        self.journey(PHONE)

    def test_desktop(self):
        self.journey(DESKTOP)
