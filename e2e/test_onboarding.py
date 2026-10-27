"""End-to-end test of the onboarding (the design plan section 12), in a real browser.

Not part of `manage.py test`: e2e/ has no __init__.py, so the default discovery skips it. Run it with:

    .venv/bin/python manage.py test e2e.test_onboarding

and, to save a full-page screenshot of every screen at both widths for the design review:

    E2E_SCREENSHOTS=/tmp/e2e-screens .venv/bin/python manage.py test e2e.test_onboarding

Needs requirements-dev.txt (Playwright, whose Chromium must already sit in ~/Library/Caches/ms-playwright).
Claude is the fake engine (manage.py test forces it), mail the locmem backend; every request leaving the live
server (the communes autocomplete) is blocked. axe-core runs on every screen: no serious or critical violation.
"""
import json
import os
import re
from pathlib import Path
from unittest import mock

# Playwright's sync API runs an event loop in this thread; the test still talks to the database synchronously.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.contrib.staticfiles.testing import StaticLiveServerTestCase  # noqa: E402
from django.core import mail as django_mail  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.utils import timezone  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from accounts.models import Profile  # noqa: E402
from config.testing import PLAIN_STATIC  # noqa: E402
from jobs import summaries  # noqa: E402
from jobs.testing import make_offer, make_source  # noqa: E402
from llm import fake  # noqa: E402
from matching import on_demand, scoring  # noqa: E402
from matching.testing import scores_answer, summaries_answer  # noqa: E402
from onboarding import schedule, summary  # noqa: E402

AXE = Path(__file__).resolve().parent / "vendor" / "axe.min.js"
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}
BLOCKING = {"serious", "critical"}

SUMMARY = {
    "sentences": [
        {"text": "J’ai compris que vous cherchez avant tout une équipe et un travail qui a du sens.",
         "sources": ["values"]},
        {"text": "Vous visez des métiers précis, et le lieu compte pour vous.", "sources": ["jobs", "location"]},
        {"text": "Je n’ai pas bien saisi si la rémunération est un critère ferme.", "sources": ["pay"]},
    ],
    "questions": [],
}


@override_settings(STORAGES=PLAIN_STATIC, DEV_EMAILS=[], CLAUDE_ENGINE="fake")
class OnboardingEndToEnd(StaticLiveServerTestCase):
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
        fake.reset()
        self.addCleanup(fake.reset)
        fake.set_response(summary.TASK, SUMMARY)
        # No real thread during the browser tests: the on-demand run is asked for, and executed by score_first_offers().
        fake.set_response(summaries.TASK, summaries_answer)
        fake.set_response(scoring.TASK, scores_answer)
        self.runs = []
        runner = mock.patch.object(on_demand, "RUNNER", self.runs.append)
        runner.start()
        self.addCleanup(runner.stop)
        self.shots = Path(os.environ["E2E_SCREENSHOTS"]) if os.environ.get("E2E_SCREENSHOTS") else None
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)
        self.violations = []  # (screen, id, impact, help, nodes) of every width, every impact
        # Yesterday's offers, collected now: the live counter has something to count on block 1.
        # One source per kind of domains served, and one serving everyone.
        # (contract type, city, its INSEE code); an offer without a commune passes every place.
        lyon, villeurbanne, paris = ("Lyon", "69123"), ("Villeurbanne", "69266"), ("Paris", "75056")
        nowhere = ("", "")
        offers = {("E", "K", "L"): [("CDI", *lyon), ("CDD", *lyon), ("CDI", *villeurbanne), ("STAGE", *paris),
                                    ("SERVICE_CIVIQUE", "Marseille", "13055"), ("INTERIM", *lyon)],
                  ("D", "M17"): [("FREELANCE", *nowhere), ("FREELANCE", *paris), ("CDI", *lyon), ("AUTRE", *nowhere)],
                  ("M18",): [("FREELANCE", *nowhere), ("CDI", "Nantes", "44109")],
                  (): [("CDD", *lyon), ("FREELANCE", *nowhere)]}
        for domains, rows in offers.items():
            name = "-".join(domains) or "toutes"
            source = make_source(name=f"Source {name}", domains=list(domains))
            for number, (contract_type, city, insee) in enumerate(rows):
                # The title of the offers serving everyone shares words with both people's jobs (the first sort).
                title = "Médiation culturelle, développeuse web" if not domains else "Offre fictive"
                make_offer(source, external_id=str(number), title=title, contract_type=contract_type, city=city,
                           insee=insee, url=f"https://example.fr/{name}/{number}")

    def open(self, viewport):
        context = self.browser.new_context(viewport=viewport, locale="fr-FR")
        context.set_default_timeout(15000)
        self.addCleanup(context.close)
        # Only the live server: the communes autocomplete (geo.api.gouv.fr) never leaves the machine.
        context.route(re.compile(r"^(?!" + re.escape(self.live_server_url) + r")"), lambda route: route.abort())
        self.page = context.new_page()
        self.width = viewport["width"]
        self.screen_number = 0
        return self.page

    # What every screen goes through.

    def check(self, name, skip=()):
        """axe-core on the current screen (no serious or critical violation, but for the rules in `skip`), and its
        screenshot when asked."""
        page = self.page
        page.wait_for_load_state("networkidle")
        if not page.evaluate("typeof window.axe !== 'undefined'"):
            page.add_script_tag(path=str(AXE))
        result = page.evaluate(
            "async skip => await axe.run(document, {resultTypes: ['violations'],"
            " rules: Object.fromEntries(skip.map(rule => [rule, {enabled: false}]))})", list(skip))
        for violation in result["violations"]:
            self.violations.append((f"{self.width} {name}", violation["id"], violation["impact"], violation["help"],
                                    [node["target"] for node in violation["nodes"]]))
        blocking = [v for v in result["violations"] if v["impact"] in BLOCKING]
        self.assertEqual(blocking, [], f"{self.width}px {name}: " + json.dumps(
            [(v["id"], v["impact"], [n["target"] for n in v["nodes"]]) for v in blocking], ensure_ascii=False))
        if self.shots:
            self.screen_number += 1
            page.wait_for_timeout(400)  # the stamp and the filed card finish their 300 ms
            stem = self.shots / f"{self.width}-{self.screen_number:02d}-{name}"
            page.screenshot(path=f"{stem}.png", full_page=True)
            # The rendered page too, for the design detector, which cannot sign in.
            Path(f"{stem}.html").write_text(page.content(), encoding="utf-8")

    def pick(self, name, value):
        """Tick a radio button, a check box or a chip by clicking its label, as a person does."""
        self.page.locator(f'label:has(input[name="{name}"][value="{value}"])').click()
        expect(self.page.locator(f'input[name="{name}"][value="{value}"]')).to_be_checked()

    def counter_shows(self):
        """The live counter on a block 1 screen: in the folded band on phones, on the pink card from 60rem."""
        where = "[data-counter-summary]" if self.width < 960 else ".card--pink [data-counter]"
        expect(self.page.locator(where)).to_be_visible()
        expect(self.page.locator(where)).to_have_text(
            re.compile(r"^(Hier|Sur les 7 derniers jours), \d+ offres? passai"))

    def screen(self, title):
        expect(self.page.locator("h1")).to_have_text(title)

    def poste(self, label, typed, suggestion):
        """Type the start of a poste and pick one of the ROME titles suggested."""
        self.page.get_by_label(label, exact=True).fill(typed)
        self.page.get_by_role("option", name=suggestion, exact=True).click()
        expect(self.page.get_by_label(label, exact=True)).to_have_value(suggestion)

    def go_on(self, button="Continuer"):
        self.page.get_by_role("button", name=button, exact=True).click()

    # The journey.

    def sign_in(self, email):
        page = self.page
        page.goto(self.live_server_url + "/")
        self.check("accueil")
        page.get_by_label("Votre adresse mail").fill(email)
        self.go_on("Recevoir mon code")
        expect(page).to_have_url(re.compile(r"/connexion/code/$"))
        self.check("code")
        message = [m for m in django_mail.outbox if m.to == [email]][-1]
        code = re.search(r"\b(\d{3} \d{3})\b", message.subject).group(1)
        page.get_by_label("Code reçu").fill(code.replace(" ", ""))  # the page sends itself at six digits
        expect(page).to_have_url(re.compile(r"/demarrer/$"))

    def summary_and_end(self, email):
        page = self.page
        self.screen("Votre CV (facultatif)")
        self.check("cv")
        # The waiting screen sends itself on load. Its form is held back once, to look at the screen, then sent.
        page.add_init_script("""document.addEventListener("submit", function (event) {
            if (event.target.hasAttribute("data-auto-submit")) event.preventDefault();
        }, true);""")
        self.go_on("Passer cette étape")
        self.screen("Je rédige ce que j’ai compris")
        self.check("synthese-attente")
        page.evaluate("document.querySelector('form[data-auto-submit]').submit()")  # no submit event this way
        self.screen("Voici ce que j’ai compris")
        expect(page.locator(".summary__card--understood")).to_contain_text(SUMMARY["sentences"][0]["text"])
        self.check("synthese")
        self.go_on("Ça me va")
        self.screen("Votre premier tri arrive")
        profile = Profile.objects.get(user__email=email)
        delivery = schedule.first_delivery(profile.onboarding_completed_at)
        day = schedule.describe_delivery(delivery, timezone.now())
        expect(page.locator(".lede")).to_have_text(f"Il arrive {day}, vers 7 h, depuis noreply@example.org.")
        expect(page.locator(".stamp")).to_contain_text(schedule.stamp_text(delivery))
        # The first run is still going: the page says so and refreshes itself every ten seconds.
        expect(page.locator("[role=status]")).to_have_text(
            "Je note vos premières offres. Cette page se met à jour toute seule.")
        # The decided refresh (plan of step 5, decision 8) is a meta refresh, which axe flags (WCAG 2.2.1): this screen
        # only, and the link under the message does the same by hand.
        self.check("fin-calcul", skip=("meta-refresh",))
        self.assertEqual(len([m for m in django_mail.outbox if m.subject.startswith("Bienvenue")]), 1)
        self.first_sort()

    def first_sort(self):
        """The run ends (executed here, in place of its thread), the page shows the offers, the button leads to them."""
        page = self.page
        self.assertEqual(len(self.runs), 1)
        on_demand.execute(self.runs[0])
        page.get_by_role("link", name="Voir si elles sont prêtes").click()
        self.screen("Vos premières offres sont prêtes")
        button = page.get_by_role("link", name=re.compile(r"^Trier (mes \d+ premières offres|ma première offre)$"))
        expect(button).to_be_visible()
        expect(page.locator(".stamp")).to_be_visible()  # the appointment follows, unchanged
        self.check("fin-pret")
        button.click()
        expect(page).to_have_url(re.compile(r"/tri/$"))
        self.check("tri")
        page.go_back()

    def after(self):
        page = self.page
        page.get_by_role("link", name="Modifier ma recherche").click()
        self.screen("Ma recherche")
        self.check("ma-recherche")
        page.get_by_role("link", name="Modifier tout le bloc ce que vous excluez").click()
        self.screen("Ce que vous excluez")
        self.check("ma-recherche-exclusions")
        self.go_on("Enregistrer")
        expect(page.get_by_role("status")).to_have_text("Modifications enregistrées.")
        page.get_by_role("link", name="Mes données").first.click()
        self.screen("Mes données")
        self.check("mes-donnees")

    def report(self):
        """Every violation found, blocking or not, printed for the run's log."""
        for screen, rule, impact, help_text, nodes in self.violations:
            print(f"axe {screen}: {impact} {rule} ({help_text}) on {nodes}")
        print(f"axe {self.width}px: {len(self.violations)} violation(s), none serious or critical.")

    def test_phone_communication_and_health(self):
        page = self.open(PHONE)
        email = "nora@example.fr"
        self.sign_in(email)
        self.screen("Quel poste cherchez-vous ?")
        self.check("recherche")
        self.poste("Poste", "média cult", "Médiatrice culturelle")
        self.poste("Deuxième poste (facultatif)", "infirmier en soins", "Infirmier en soins généraux")
        expect(page.locator(".fallback")).not_to_have_attribute("open", "")
        self.pick("offer_kind", "job")
        self.pick("career_change", "no")
        self.go_on()
        self.screen("Où voulez-vous travailler ?")
        self.counter_shows()
        page.get_by_label("Ville", exact=True).fill("Lyon")
        self.pick("distance", "30")
        self.pick("remote", "hybrid")
        expect(page.locator("[data-consequence]")).to_have_text(
            "Distance à vol d’oiseau. Les offres dont la commune n’est pas reconnue restent visibles.")
        self.check("lieu")
        self.go_on()
        self.screen("Quels contrats acceptez-vous ?")
        expect(page.locator('input[name="contracts"][value="interim"]')).not_to_be_checked()
        self.pick("contracts", "interim")
        self.check("contrats")
        self.go_on()
        self.screen("Des mots à exiger ou à écarter dans le titre ?")
        self.counter_shows()
        page.get_by_label("Mots à exiger").fill("offre, médiation")
        page.get_by_label("Mots à écarter").fill("stagiaire")
        self.check("mots-cles")
        self.go_on()
        self.screen("Quelle rémunération minimale ?")
        page.get_by_label("Montant minimal").fill("24000")
        self.check("remuneration")
        self.go_on()
        self.screen("Quel temps de travail ?")
        self.pick("hours", "full")
        self.check("temps")
        self.go_on()
        self.screen("Combien d’années d’expérience avez-vous ?")
        self.pick("experience", "1_2")
        self.check("experience")
        self.go_on()
        self.screen("Ce qui compte le plus dans votre prochain poste")
        self.pick("values", "meaning")
        self.pick("values", "team")
        self.check("valeurs")
        self.go_on()
        self.screen("Où aimeriez-vous travailler ?")
        self.pick("structures", "association_ess")
        self.pick("sectors", "culture_media")
        self.check("structures")
        self.go_on()
        self.screen("En vos mots")
        page.get_by_label("Ce que vous cherchez").fill("Un poste où je parle aux publics, en médiathèque.")
        self.check("mots")
        self.go_on()
        self.summary_and_end(email)
        profile = Profile.objects.get(user__email=email)
        self.assertEqual(profile.domains, ["J", "K"])
        self.assertIn("interim", profile.hard_criteria["contracts"]["values"])
        self.assertEqual(profile.hard_criteria["keywords"],
                         {"include": ["offre", "médiation"], "exclude": ["stagiaire"], "strict": True})
        self.assertEqual(profile.weighted_preferences["jobs"],
                         [{"custom": "Médiatrice culturelle"}, {"custom": "Infirmier en soins généraux"}])
        self.after()
        self.report()

    def test_desktop_computing_in_career_change(self):
        page = self.open(DESKTOP)
        email = "lea@example.fr"
        self.sign_in(email)
        self.screen("Quel poste cherchez-vous ?")
        self.poste("Poste", "dévelop web", "Développeuse web")
        # A poste the ROME does not know: its family, from the fallback.
        page.get_by_label("Deuxième poste (facultatif)", exact=True).fill("Growth hacker")
        page.get_by_text("Je ne trouve pas mon poste").click()
        self.pick("domains", "M17")
        self.pick("domains", "D")
        expect(page.locator('input[name="domains"][value="A"]')).to_be_disabled()  # the cap of two, by the script
        self.pick("offer_kind", "mission")
        self.pick("career_change", "yes")
        page.get_by_label("Votre métier actuel ou le dernier").fill("Professeure de lettres")
        self.check("recherche")
        self.go_on()
        self.screen("Où voulez-vous travailler ?")
        self.counter_shows()
        self.pick("distance", "city")
        self.pick("remote", "full_remote")
        self.check("lieu")
        self.go_on()
        self.screen("Quels contrats acceptez-vous ?")
        self.check("contrats")
        self.go_on()
        self.screen("Des mots à exiger ou à écarter dans le titre ?")
        self.check("mots-cles")
        self.go_on()
        self.screen("Quelle rémunération minimale ?")
        page.get_by_label("Montant minimal").fill("450")
        self.check("remuneration")
        self.go_on()
        self.screen("Quel temps de travail ?")
        self.pick("hours", "part_ok")
        self.check("temps")
        self.go_on()
        self.screen("Combien d’années d’expérience avez-vous ?")
        self.pick("experience", "3_4")
        self.check("experience")
        self.go_on()
        self.screen("Ce qui compte le plus dans votre prochain poste")
        self.pick("values", "autonomy")
        self.check("valeurs")
        self.go_on()
        self.screen("Où aimeriez-vous travailler ?")
        self.check("structures")
        self.go_on()
        self.screen("En vos mots")
        page.get_by_label("Ce que vous refusez").fill("Les postes payés uniquement à la commission.")
        self.check("mots")
        self.go_on()
        self.summary_and_end(email)
        profile = Profile.objects.get(user__email=email)
        self.assertEqual(profile.domains, ["D", "M18", "M17"])
        self.assertEqual(profile.weighted_preferences["jobs"],
                         [{"custom": "Développeuse web"}, {"custom": "Growth hacker"}])
        self.after()
        self.report()

    @override_settings(DEBUG=True)
    def test_style_page(self):
        """The internal style page (DEBUG only) loads at both widths; with E2E_SCREENSHOTS, a capture of each."""
        for viewport in (DESKTOP, PHONE):
            page = self.open(viewport)
            response = page.goto(self.live_server_url + "/interne/style/")
            self.assertEqual(response.status, 200)
            if self.shots:
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(400)
                page.screenshot(path=str(self.shots / f"style-{viewport['width']}.png"), full_page=True)
