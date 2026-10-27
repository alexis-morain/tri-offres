"""End-to-end test of the sort page (spec 6.2), in a real browser, at both widths.

Not part of `manage.py test` (e2e/ has no __init__.py). Run it with:

    .venv/bin/python manage.py test e2e.test_sort

and, to save a full-page screenshot of every state at both widths for the design review:

    E2E_SCREENSHOTS=/tmp/e2e-screens .venv/bin/python manage.py test e2e.test_sort

Needs requirements-dev.txt (Playwright). axe-core runs on every state: no serious or critical violation. Offers are
fictitious; the offers and their scores are made directly, no engine is called.
"""
import json
import os
import re
from datetime import datetime, timezone as utc
from pathlib import Path

# Playwright's sync API runs an event loop in this thread; the test still talks to the database synchronously.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.conf import settings  # noqa: E402
from django.contrib.staticfiles.testing import StaticLiveServerTestCase  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from accounts.testing import make_finished_profile  # noqa: E402
from config.testing import PLAIN_STATIC  # noqa: E402
from jobs.testing import make_offer, make_source  # noqa: E402
from matching.models import Feedback, Score  # noqa: E402

AXE = Path(__file__).resolve().parent / "vendor" / "axe.min.js"
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}
BLOCKING = {"serious", "critical"}
YES, NO, MAYBE = Feedback.Verdict.YES, Feedback.Verdict.NO, Feedback.Verdict.MAYBE

# (title, employer, place, contract, score, exploration, reason, summary)
OFFERS = [
    ("Chargée de médiation culturelle", "Musée fictif des Beaux-Arts", "Lyon", "CDI", 9, False,
     "Vous voulez parler aux publics : le poste est à moitié sur le terrain.",
     "Accueil des groupes scolaires et conception d’ateliers. Deux jours par semaine en salle."),
    ("Régisseuse de festival", "Association Scènes ouvertes", "Nantes", "CDD", 8, True,
     "Une piste à côté de la médiation : même public, métier plus technique.",
     "Préparation technique d’un festival d’été, de mai à août."),
    ("Responsable adjointe de la programmation jeune public et des actions de médiation territoriale",
     "Communauté de communes fictive du Val de Loire et des coteaux", "Villeurbanne", "CDI", 7, False, "", ""),
    ("Assistante d’édition jeunesse", "Maison d’édition indépendante", "Paris", "CDD", 6, False,
     "L’édition jeunesse est dans vos métiers visés ; CDD de 8 mois.", "Relecture et suivi de fabrication."),
]


@override_settings(STORAGES=PLAIN_STATIC, DEV_EMAILS=[], CLAUDE_ENGINE="fake")
class SortEndToEnd(StaticLiveServerTestCase):
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
        self.shots = Path(os.environ["E2E_SCREENSHOTS"]) if os.environ.get("E2E_SCREENSHOTS") else None
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)
        self.user = make_finished_profile("nora@example.fr").user
        source = make_source(name="Source fictive", domains=[])
        self.offers = []
        for number, (title, employer, place, contract, score, exploration, reason, summary) in enumerate(OFFERS):
            offer = make_offer(source, external_id=str(number), title=title, employer=employer, location=place,
                               contract=contract, summary=summary, url=f"https://example.fr/offre/{number}",
                               source_license="Données Source fictive, licence ouverte",
                               published_at=datetime(2026, 10, 14, 9, tzinfo=utc.utc))
            Score.objects.create(user=self.user, offer=offer, score=score, reason=reason, is_exploration=exploration,
                                 category=Score.Category.TO_REVIEW)
            self.offers.append(offer)
        self.violations = []

    def open(self, viewport, javascript=True):
        context = self.browser.new_context(viewport=viewport, locale="fr-FR", java_script_enabled=javascript)
        context.set_default_timeout(15000)
        self.addCleanup(context.close)
        client = Client()
        client.force_login(self.user)
        cookie = client.cookies[settings.SESSION_COOKIE_NAME]
        context.add_cookies([{"name": settings.SESSION_COOKIE_NAME, "value": cookie.value,
                              "url": self.live_server_url}])
        self.context = context
        self.page = context.new_page()
        self.width = viewport["width"]
        self.shot_number = 0
        return self.page

    def check(self, name):
        """axe-core on the current state (no serious or critical violation), and its screenshot when asked."""
        page = self.page
        page.wait_for_load_state("networkidle")
        if not page.evaluate("typeof window.axe !== 'undefined'"):
            page.add_script_tag(path=str(AXE))
        result = page.evaluate("async () => await axe.run(document, {resultTypes: ['violations']})")
        for violation in result["violations"]:
            self.violations.append((f"{self.width} {name}", violation["id"], violation["impact"]))
        blocking = [v for v in result["violations"] if v["impact"] in BLOCKING]
        self.assertEqual(blocking, [], f"{self.width}px {name}: " + json.dumps(
            [(v["id"], v["impact"], [n["target"] for n in v["nodes"]]) for v in blocking], ensure_ascii=False))
        self.capture(name)
        page.evaluate("window.scrollTo(0, 0)")
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        self.assertLessEqual(overflow, 0, f"{self.width}px {name}: the page scrolls sideways")

    def capture(self, name):
        if not self.shots:
            return
        self.shot_number += 1
        self.page.wait_for_timeout(400)
        stem = self.shots / f"{self.width}-{self.shot_number:02d}-{name}"
        self.page.screenshot(path=f"{stem}.png", full_page=True)
        Path(f"{stem}.html").write_text(self.page.content(), encoding="utf-8")

    def verdict_of(self, offer):
        return Feedback.objects.current_verdict(self.user, offer)

    def eventually(self, check, what):
        for _ in range(50):
            if check():
                return
            self.page.wait_for_timeout(100)
        self.fail(what)

    def title(self, expected):
        expect(self.page.locator(".sort-card--top .sort-card__title")).to_have_text(expected)

    def live(self, expected):
        expect(self.page.locator("[data-sort-live]")).to_have_text(expected)

    # Without JavaScript: the server's sheet, three buttons, one undo.

    def test_without_javascript_three_verdicts_then_undo(self):
        page = self.open(PHONE, javascript=False)
        page.goto(self.live_server_url + "/tri/")
        self.capture("sans-js")
        expect(page.locator("h1")).to_have_text("Vos offres à trier")
        expect(page.locator("article h2")).to_have_text(OFFERS[0][0])
        expect(page.locator("[data-sort-count]")).to_have_text("4 offres")
        page.get_by_role("button", name="Ça m’intéresse").click()
        expect(page.locator("article h2")).to_have_text(OFFERS[1][0])
        page.get_by_role("button", name="Peut-être").click()
        expect(page.locator("article h2")).to_have_text(OFFERS[2][0])
        page.get_by_role("button", name="Pas pour moi").click()
        expect(page.locator("article h2")).to_have_text(OFFERS[3][0])
        self.assertEqual([self.verdict_of(offer) for offer in self.offers[:3]], [YES, MAYBE, NO])
        page.get_by_role("button", name="Annuler mon dernier avis").click()
        expect(page.locator("article h2")).to_have_text(OFFERS[2][0])
        self.assertIsNone(self.verdict_of(self.offers[2]))

    # With JavaScript: keys, swipe, offline queue, end of the deck.

    def journey(self, viewport):
        page = self.open(viewport)
        page.goto(self.live_server_url + "/tri/")
        self.title(OFFERS[0][0])
        expect(page.locator(".sort-card--next")).to_have_count(1)
        expect(page.get_by_role("button", name="Annuler mon dernier avis")).to_be_disabled()
        card = page.locator(".sort-card--top")
        expect(card).to_contain_text("publiée le 14/10")
        expect(card).to_contain_text("9 sur 10")
        expect(card.get_by_role("link", name=re.compile("^Voir l’offre"))).to_have_attribute("target", "_blank")
        self.check("pile")

        page.keyboard.press("ArrowRight")  # yes
        self.title(OFFERS[1][0])
        self.live(f"Ça m’intéresse : {OFFERS[0][0]}. 3 offres restantes.")
        expect(card.locator(".sort-card__explore")).to_be_visible()
        expect(page.locator(".sort-card--top .sort-card__title")).to_be_focused()
        self.eventually(lambda: self.verdict_of(self.offers[0]) == YES, "yes was not sent")
        page.keyboard.press("ArrowDown")  # maybe
        self.title(OFFERS[2][0])
        self.check("titre-long")
        page.keyboard.press("ArrowLeft")  # no
        self.title(OFFERS[3][0])
        self.eventually(lambda: [self.verdict_of(o) for o in self.offers[1:3]] == [MAYBE, NO], "verdicts not sent")

        page.keyboard.press("Control+z")  # undo the last one: the long title comes back on top
        self.title(OFFERS[2][0])
        self.live(f"Avis annulé : {OFFERS[2][0]}. 2 offres restantes.")
        self.eventually(lambda: self.verdict_of(self.offers[2]) is None, "undo was not sent")

        # Swipe right with the mouse: past 30 % of the width it decides, and the yes button shows it is armed.
        box = card.bounding_box()
        y = box["y"] + 120
        page.mouse.move(box["x"] + 60, y)
        page.mouse.down()
        page.mouse.move(box["x"] + 60 + box["width"] * 0.1, y, steps=4)
        page.mouse.move(box["x"] + 60 + box["width"] * 0.4, y, steps=6)
        expect(page.get_by_role("button", name="Ça m’intéresse")).to_have_class(re.compile(r"is-armed"))
        self.capture("glisser")
        page.mouse.up()
        self.title(OFFERS[3][0])
        self.eventually(lambda: self.verdict_of(self.offers[2]) == YES, "swipe was not sent")

        # A short drag goes back; the card stays.
        box = card.bounding_box()
        page.mouse.move(box["x"] + 60, y)
        page.mouse.down()
        page.mouse.move(box["x"] + 60 + box["width"] * 0.1, y, steps=4)
        page.mouse.up()
        page.wait_for_timeout(400)
        self.title(OFFERS[3][0])

        # Offline: the verdict waits in the queue, the deck ends, and back online it is sent.
        self.context.set_offline(True)
        page.keyboard.press("ArrowLeft")
        expect(page.locator("[data-sort-end]")).to_be_visible()
        expect(page.locator("[data-sort-recap]")).to_have_text("2 qui vous intéressent, 1 peut-être, 1 pas pour vous")
        expect(page.locator("[data-sort-notice]")).to_contain_text("pas encore enregistrés")
        queue = json.loads(page.evaluate(f"localStorage.getItem('tri.queue.{self.user.pk}')"))
        self.assertEqual([(e["offer"], e["verdict"]) for e in queue], [(self.offers[3].pk, "no")])
        self.assertEqual(set(queue[0]), {"offer", "verdict", "date"})
        self.assertIsNone(self.verdict_of(self.offers[3]))
        self.check("fin-hors-ligne")
        self.context.set_offline(False)
        self.eventually(lambda: self.verdict_of(self.offers[3]) == NO, "the queued verdict was not sent")
        expect(page.locator("[data-sort-notice]")).to_be_hidden()
        self.assertEqual(page.evaluate(f"localStorage.getItem('tri.queue.{self.user.pk}')"), "[]")
        self.check("fin")

        page.keyboard.press("Backspace")  # the last one comes back from the end screen
        self.title(OFFERS[3][0])
        self.eventually(lambda: self.verdict_of(self.offers[3]) is None, "undo from the end was not sent")
        self.report()

    def report(self):
        for screen, rule, impact in self.violations:
            print(f"axe {screen}: {impact} {rule}")
        print(f"axe {self.width}px: {len(self.violations)} violation(s), none serious or critical.")

    def test_phone(self):
        self.journey(PHONE)

    def test_desktop(self):
        self.journey(DESKTOP)

    # The queue is read with suspicion.

    def test_a_broken_queue_is_emptied_and_a_gone_offer_leaves_the_queue(self):
        pk = self.user.pk
        page = self.open(PHONE)
        page.add_init_script(f"""if (!sessionStorage.getItem('seeded')) {{
            sessionStorage.setItem('seeded', '1');
            localStorage.setItem('tri.queue.{pk}', 'not json{{');
            localStorage.setItem('tri.queue.{pk + 1}',
                JSON.stringify([{{offer: 1, verdict: 'yes', date: Date.now()}}]));
        }}""")
        page.goto(self.live_server_url + "/tri/")
        self.assertIsNone(page.evaluate(f"localStorage.getItem('tri.queue.{pk}')"))
        self.assertEqual(Feedback.objects.count(), 0)  # another account's queue is never replayed here
        page.evaluate(f"""localStorage.setItem('tri.queue.{pk}', JSON.stringify([
            {{offer: 999999, verdict: 'yes', date: Date.now()}},
            {{offer: {self.offers[0].pk}, verdict: 'maybe', date: Date.now()}},
            {{offer: {self.offers[1].pk}, verdict: 'yes', date: Date.now() - 31 * 24 * 3600 * 1000}}]))""")
        page.reload()
        # The queued offer is out of the deck at once; the 404 is dropped, the maybe is sent.
        self.title(OFFERS[1][0])
        self.eventually(lambda: self.verdict_of(self.offers[0]) == MAYBE, "the queue was not replayed")
        self.assertEqual(page.evaluate(f"localStorage.getItem('tri.queue.{pk}')"), "[]")
        self.assertIsNone(self.verdict_of(self.offers[1]))  # too old: never sent
        self.assertEqual(page.evaluate(f"localStorage.getItem('tri.queue.{pk + 1}') !== null"), True)

    # « Ce que j’ai appris de vos choix »: the green card, « Corriger » and « Effacer ».

    def learned_journey(self, viewport):
        profile = self.user.profile
        profile.learned_summary = ("Vous choisissez surtout des postes de médiation en CDI. "
                                   "Vous écartez les contrats courts, même dans les lieux que vous citez.")
        profile.save()
        page = self.open(viewport)
        page.goto(self.live_server_url + "/tri/")
        card = page.locator(".sort__learned")
        expect(card).to_be_visible()
        expect(card.get_by_role("heading", name="Ce que j’ai appris de vos choix")).to_be_visible()
        expect(card).to_contain_text("Rédigé par IA")
        expect(card).to_contain_text("contrats courts")
        self.assertLess(card.bounding_box()["y"], page.locator(".sort-card--top").bounding_box()["y"])
        self.title(OFFERS[0][0])
        self.check("carte-apprise")

        card.get_by_role("link", name="Corriger").click()
        expect(page).to_have_url(re.compile(r"/tri/appris/$"))
        expect(page.get_by_label("Votre version")).to_have_value(re.compile("^Vous choisissez surtout"))
        self.check("corriger")
        expect(page.get_by_label("Votre version")).to_have_attribute("maxlength", "800")
        page.get_by_label("Votre version").fill("Je veux la médiation <b>seulement</b>, pas la billetterie.")
        page.get_by_role("button", name="Enregistrer").click()
        expect(page).to_have_url(re.compile(r"/tri/$"))
        expect(card).to_contain_text("Je veux la médiation <b>seulement</b>, pas la billetterie.")
        expect(card).not_to_contain_text("par IA")
        self.assertEqual(self.user.profile.__class__.objects.get(user=self.user).learned_summary_own, True)
        self.check("carte-corrigee")

        card.get_by_role("button", name="Effacer").click()
        expect(page.locator(".sort__learned")).to_have_count(0)
        self.title(OFFERS[0][0])
        self.assertEqual(self.user.profile.__class__.objects.get(user=self.user).learned_summary, "")
        self.check("carte-effacee")

    def test_learned_phone(self):
        self.learned_journey(PHONE)

    def test_learned_desktop(self):
        self.learned_journey(DESKTOP)
