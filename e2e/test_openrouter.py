"""End-to-end look at the OpenRouter screens (plan 2026-10-27, step B), in a real browser, at both widths: the education
screen, the tutorial, the limit advice after the connection and « Mes données ». axe-core runs on each.

Not part of `manage.py test` (e2e/ has no __init__.py). Run it with:

    .venv/bin/python manage.py test e2e.test_openrouter

and, for a full-page screenshot of each screen at both widths:

    E2E_SCREENSHOTS=/tmp/e2e-openrouter .venv/bin/python manage.py test e2e.test_openrouter
"""
import json
import os
import re
from pathlib import Path

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.contrib.staticfiles.testing import StaticLiveServerTestCase  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from accounts.models import OpenRouterKey  # noqa: E402
from accounts.testing import make_finished_profile  # noqa: E402
from config.testing import PLAIN_STATIC  # noqa: E402

AXE = Path(__file__).resolve().parent / "vendor" / "axe.min.js"
VIEWPORTS = {"390": {"width": 390, "height": 844}, "1440": {"width": 1440, "height": 900}}
BLOCKING = {"serious", "critical"}


@override_settings(STORAGES=PLAIN_STATIC, DEV_EMAILS=[], CLAUDE_ENGINE="api")
class OpenRouterScreens(StaticLiveServerTestCase):
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
        self.user = make_finished_profile().user
        client = Client()
        client.force_login(self.user)
        self.session = client.cookies["sessionid"].value
        self.shots = Path(os.environ["E2E_SCREENSHOTS"]) if os.environ.get("E2E_SCREENSHOTS") else None
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)

    def look(self, name, path):
        for width, viewport in VIEWPORTS.items():
            context = self.browser.new_context(viewport=viewport, locale="fr-FR")
            self.addCleanup(context.close)
            context.add_cookies([{"name": "sessionid", "value": self.session, "url": self.live_server_url}])
            # Only the live server: nothing leaves the machine (the links to openrouter.ai are not followed).
            context.route(re.compile(r"^(?!" + re.escape(self.live_server_url) + r")"), lambda route: route.abort())
            page = context.new_page()
            page.goto(self.live_server_url + path)
            page.wait_for_load_state("networkidle")
            page.add_script_tag(path=str(AXE))
            result = page.evaluate("async () => await axe.run(document, {resultTypes: ['violations']})")
            blocking = [v for v in result["violations"] if v["impact"] in BLOCKING]
            self.assertEqual(blocking, [], f"{width}px {name}: " + json.dumps(
                [(v["id"], [n["target"] for n in v["nodes"]]) for v in blocking], ensure_ascii=False))
            print(f"axe {width}px {name}: {len(result['violations'])} violation(s), none serious or critical.")
            if self.shots:
                page.screenshot(path=str(self.shots / f"{width}-{name}.png"), full_page=True)

    def test_education_screen(self):
        self.look("01-education", "/openrouter/")

    def test_tutorial(self):
        self.look("02-tutorial", "/openrouter/tuto/")

    def test_limit_advice_after_the_connection(self):
        OpenRouterKey.objects.create(user=self.user, encrypted_key="x", fingerprint="a" * 64)
        self.look("03-done", "/openrouter/connecte/")
        self.look("04-connected", "/openrouter/")
        self.look("05-my-data", "/mes-donnees/")
