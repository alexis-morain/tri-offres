"""After the questionnaire (sub-step 3.7): « Ma recherche », its block pages, and « Mes données »."""
from django.test import override_settings
from django.utils import timezone

from accounts import events
from accounts.models import SummaryStatus
from config.testing import assert_french_copy, assert_unique_ids
from onboarding.test_end import LOCMEM, MY_DATA, MY_SEARCH, EndTestCase, text
from onboarding.test_summary import CV_PROFILE
from onboarding.testing import block_url, prefixed
from onboarding.tests import CULTURE, CULTURE_FULL, CULTURE_JOBS, END, OTHER_FULL, url

SAVED = "Modifications enregistrées."


EXCLUDE_POST = {**prefixed("lieu", {**CULTURE["lieu"], "distance": "60"}),
                **prefixed("contrats", CULTURE["contrats"]),
                **prefixed("mots-cles", CULTURE["mots-cles"]),
                **prefixed("remuneration", {"floor": "26000", "unit": "year"}),
                **prefixed("temps", CULTURE["temps"]),
                **prefixed("experience", CULTURE["experience"])}
PREFER_POST = {**prefixed("valeurs", {"values": ["team"]}),
               **prefixed("structures", {"structures": ["culture"], "sectors": ["retail"]})}
# Precise the poste: the banner of the accounts from before the postes (plan of 26/10, 3.4).
NO_POSTE = "Précisez le poste que vous cherchez"


@override_settings(EMAIL_BACKEND=LOCMEM)
class FinishedTestCase(EndTestCase):
    def setUp(self):
        super().setUp()
        self.finish()


class NotFinishedTests(EndTestCase):
    def test_my_search_and_block_pages_resume_the_questionnaire(self):
        for path in (MY_SEARCH, block_url("exclude")):
            with self.subTest(path=path):
                self.assertRedirects(self.client.get(path), "/synthese/", fetch_redirect_response=False)
        self.client.post("/synthese/valider/")
        self.assertRedirects(self.client.get(MY_SEARCH), END, fetch_redirect_response=False)

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        self.client.logout()
        for path in (MY_SEARCH, block_url("prefer"), MY_DATA):
            with self.subTest(path=path):
                self.assertTrue(self.client.get(path)["Location"].startswith("/?"))

    def test_my_data_is_open_before_the_end(self):
        self.assertEqual(self.client.get(MY_DATA).status_code, 200)


class MySearchTests(FinishedTestCase):
    def test_cards_rows_and_their_edit_links(self):
        html = self.page(MY_SEARCH)
        self.assertIn("<title>Ma recherche, tri-offres</title>", html)
        pink, blue, yellow = (self.card(html, tone) for tone in ("pink", "blue", "yellow"))
        self.assertIn("Lyon, jusqu’à 30 km, télétravail : hybride", pink)
        self.assertIn(f'href="{url("lieu")}?retour=ma-recherche"', pink)
        self.assertIn(f'href="{url("recherche")}?retour=ma-recherche"', pink)
        self.assertIn("Le sens du travail, l’équipe", blue)
        self.assertIn(f'href="{url("valeurs")}?retour=ma-recherche"', blue)
        self.assertIn("Un poste où je parle aux publics.", yellow)
        self.assertIn(f'href="{url("cv")}?retour=ma-recherche"', yellow)
        for tone, slug in (("pink", "exclude"), ("blue", "prefer"), ("yellow", "words")):
            self.assertIn(f'href="{block_url(slug)}"', self.card(html, tone))
            self.assertIn(">Modifier tout le bloc<", self.card(html, tone))

    def test_green_card_ready(self):
        green = self.card(self.page(MY_SEARCH), "green")
        self.assertIn("Synthèse rédigée par IA", green)
        self.assertIn("J’ai compris que vous cherchez un poste où vous parlez aux publics.", green)
        self.assertIn('href="/synthese/"', green)
        self.assertNotIn("Vos réponses ont changé depuis.", green)

    def test_green_card_pending(self):
        self.update(summary_status=SummaryStatus.PENDING, summary_sentences=[])
        green = text(self.card(self.page(MY_SEARCH), "green"))
        self.assertIn("Je rédige cette synthèse cette nuit.", green)

    def test_green_card_stale_and_rewriting_comes_back_here(self):
        self.update(summary_status=SummaryStatus.STALE)
        green = self.card(self.page(MY_SEARCH), "green")
        self.assertIn("Vos réponses ont changé depuis.", green)
        self.assertIn('action="/synthese/rediger/?retour=ma-recherche"', green)
        self.assertRedirects(self.client.post("/synthese/rediger/?retour=ma-recherche"), MY_SEARCH,
                             fetch_redirect_response=False)
        self.assertEqual(self.profile.summary_status, SummaryStatus.READY)

    def test_a_screen_opened_from_here_comes_back_with_a_message(self):
        html = self.page(url("lieu") + "?retour=ma-recherche")
        self.assertIn('action="/questionnaire/lieu/?retour=ma-recherche"', html)
        self.assertIn(f'<a class="link-back" href="{MY_SEARCH}">', html)
        self.assertIn("Retour à «\u00a0Ma recherche\u00a0»</a>", html)
        response = self.client.post(url("lieu") + "?retour=ma-recherche", {**CULTURE["lieu"], "distance": "60"})
        self.assertRedirects(response, MY_SEARCH, fetch_redirect_response=False)
        html = self.page(MY_SEARCH)
        self.assertIn(SAVED, html)
        self.assertIn("Lyon, jusqu’à 60 km", self.card(html, "pink"))
        self.assertNotIn(SAVED, self.page(MY_SEARCH))  # once

    def test_correcting_the_summary_comes_back_here(self):
        html = self.page("/synthese/")
        self.assertIn(f'href="{url("lieu")}?retour=ma-recherche"', self.card(html, "pink"))
        self.assertNotIn("retour=synthese", html)
        response = self.client.post("/synthese/valider/")
        self.assertRedirects(response, MY_SEARCH, fetch_redirect_response=False)
        self.assertIn(SAVED, self.page(MY_SEARCH))

    def test_no_time_left_once_the_questionnaire_is_over(self):
        self.assertNotIn("progress__time", self.page(url("lieu") + "?retour=ma-recherche"))

    def test_the_cv_screen_comes_back_here(self):
        html = self.page(url("cv") + "?retour=ma-recherche")
        self.assertIn("Retour à «\u00a0Ma recherche\u00a0»</a>", html)
        response = self.client.post(url("cv") + "?retour=ma-recherche", {"action": "continue"})
        self.assertRedirects(response, MY_SEARCH, fetch_redirect_response=False)

    def test_part_b_edit_from_here_makes_the_summary_stale(self):
        self.client.post(url("mots") + "?retour=ma-recherche", {"seek_text": "Autre chose.", "refuse_text": ""})
        self.assertEqual(self.profile.summary_status, SummaryStatus.STALE)

    def test_new_postes_from_here_come_back_here(self):
        response = self.client.post(url("recherche") + "?retour=ma-recherche",
                                    {**OTHER_FULL["recherche"], "offer_kind": "job"})
        self.assertRedirects(response, MY_SEARCH, fetch_redirect_response=False)
        html = self.page(MY_SEARCH)
        self.assertIn("Développeuse web", self.card(html, "pink"))
        self.assertIn("Informatique / Télécommunication", self.card(html, "pink"))

    def test_no_banner_once_a_poste_is_typed(self):
        html = self.page(MY_SEARCH)
        self.assertNotIn(NO_POSTE, html)
        self.assertIn("Médiatrice culturelle\u202f; Chargée de communication\u202f; Régisseuse son",
                      self.card(html, "pink"))

    def test_an_account_from_before_the_postes_is_asked_to_type_one(self):
        self.update(weighted_preferences={**self.profile.weighted_preferences, "jobs": ["mediation"]})
        html = self.page(MY_SEARCH)
        self.assertIn(NO_POSTE, html)
        banner = html[html.index(NO_POSTE) - 300:html.index(NO_POSTE) + 300]
        self.assertIn(f'href="{url("recherche")}?retour=ma-recherche"', banner)
        self.assertIn("Médiation", self.card(html, "blue"))  # its chips stay, with their own screen
        self.assertIn(f'href="{url("metiers")}?retour=ma-recherche"', self.card(html, "blue"))
        assert_french_copy(self, html)

    def test_house_style(self):
        html = self.page(MY_SEARCH)
        assert_french_copy(self, html)
        assert_unique_ids(self, html)
        self.assertIn('aria-current="page">Ma recherche</a>', html)


class BlockPageTests(FinishedTestCase):
    def test_each_block_gathers_its_screens_with_prefixes(self):
        expected = {"exclude": ["lieu", "contrats", "mots-cles", "remuneration", "temps", "experience"],
                    "prefer": ["valeurs", "structures"], "words": ["mots"]}
        for slug, screens in expected.items():
            with self.subTest(block=slug):
                response = self.client.get(block_url(slug))
                self.assertEqual(response.status_code, 200)
                self.assertEqual([section["step"].slug for section in response.context["sections"]], screens)
                html = response.content.decode()
                self.assertEqual(html.count('type="submit">Enregistrer</button>'), 1)
                self.assertIn(f'name="{screens[0]}-', html)
                assert_french_copy(self, html)
                assert_unique_ids(self, html)

    def test_titles(self):
        html = self.page(block_url("exclude"))
        self.assertIn("<title>Ce que vous excluez, tri-offres</title>", html)
        self.assertIn('<h1 class="question__title">Ce que vous excluez</h1>', html)
        self.assertIn('>Où <span class="nowrap">voulez-vous</span> travailler ?</h2>', html)

    def test_saved_answers_are_prefilled(self):
        html = self.page(block_url("words"))
        self.assertIn("Un poste où je parle aux publics.</textarea>", html)
        self.assertIn('href="/questionnaire/cv/?retour=ma-recherche"', html)
        self.assertIn("Aucun CV envoyé.", html)

    def test_saving_the_exclusions(self):
        response = self.client.post(block_url("exclude"), EXCLUDE_POST)
        self.assertRedirects(response, MY_SEARCH, fetch_redirect_response=False)
        criteria = self.profile.hard_criteria
        self.assertEqual(criteria["location"]["distance"], "60")
        self.assertEqual(criteria["pay"]["floor"], 26000)
        self.assertEqual(criteria["contracts"]["values"], ["cdi", "cdd"])
        self.assertFalse(criteria["experience"]["strict"])
        html = self.page(MY_SEARCH)
        self.assertIn(SAVED, html)
        self.assertIn("26 000 € brut par an au moins", self.card(html, "pink"))
        self.assertEqual(self.profile.summary_status, SummaryStatus.READY)  # block 1 leaves part B alone
        for slug in ("lieu", "contrats", "mots-cles", "remuneration", "temps", "experience"):
            self.assertIn(events.step(slug), self.event_names())

    def test_saving_the_preferences_makes_the_summary_stale(self):
        self.assertRedirects(self.client.post(block_url("prefer"), PREFER_POST), MY_SEARCH,
                             fetch_redirect_response=False)
        preferences = self.profile.weighted_preferences
        self.assertEqual((preferences["values"], preferences["jobs"], preferences["structures"],
                          preferences["sectors"]), (["team"], CULTURE_JOBS, ["culture"], ["retail"]))

    def test_job_chips_have_their_screen_on_the_block_page(self):
        self.update(weighted_preferences={**self.profile.weighted_preferences, "jobs": CULTURE_JOBS + ["E11"]})
        response = self.client.get(block_url("prefer"))
        self.assertEqual([section["step"].slug for section in response.context["sections"]],
                         ["valeurs", "metiers", "structures"])
        self.client.post(block_url("prefer"), {**PREFER_POST, **prefixed("metiers", {"jobs": ["mediation"]})})
        self.assertEqual(self.profile.weighted_preferences["jobs"], CULTURE_JOBS + ["mediation"])
        self.assertEqual(self.profile.summary_status, SummaryStatus.STALE)

    def test_unchanged_answers_leave_the_summary_ready(self):
        self.client.post(block_url("words"), prefixed("mots", CULTURE_FULL["mots"]))
        self.assertEqual(self.profile.summary_status, SummaryStatus.READY)

    def test_one_invalid_screen_saves_nothing(self):
        before = self.profile.hard_criteria
        data = {**EXCLUDE_POST, "contrats-contracts": [], "remuneration-floor": "12"}
        response = self.client.post(block_url("exclude"), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.profile.hard_criteria, before)
        html = response.content.decode()
        self.assertIn('class="error-summary"', html)
        self.assertEqual(html.count('class="error-summary"'), 1)
        self.assertIn('href="#id_contrats-contracts_0"', html)
        self.assertIn('href="#id_remuneration-floor"', html)
        self.assertIn("Cochez au moins un contrat.", html)
        self.assertIn("Indiquez un salaire brut annuel entre", html)
        self.assertIn('value="60"', html)  # what was typed stays
        assert_unique_ids(self, html)

    def test_saving_a_block_rewinds_when_a_later_answer_is_gone(self):
        prefs = dict(self.profile.weighted_preferences)
        del prefs["values"]
        self.update(weighted_preferences=prefs)
        self.client.post(block_url("exclude"), EXCLUDE_POST)
        self.assertEqual(self.profile.onboarding_step, "experience")
        self.assertRedirects(self.client.get(MY_SEARCH), url("valeurs"), fetch_redirect_response=False)

    def test_unknown_block(self):
        self.assertEqual(self.client.get(block_url("cv")).status_code, 404)


class MyDataTests(FinishedTestCase):
    def test_what_is_kept(self):
        html = self.page(MY_DATA)
        page = text(html)
        self.assertIn("<title>Mes données, tri-offres</title>", html)
        self.assertIn("nora@example.fr", page)
        self.assertIn(f"Le {timezone.localtime(self.user.date_joined).day} ", page)
        self.assertIn(f'href="{MY_SEARCH}"', html)
        self.assertIn("Aucun CV envoyé.", page)
        self.assertIn("J’ai compris que vous cherchez un poste où vous parlez aux publics.", page)
        self.assertIn('href="/confidentialite/"', html)
        self.assertNotIn("Supprimer le résumé de mon CV", html)
        danger = text(html[html.index('<div class="danger-zone">'):])
        self.assertIn("Supprimer mon compte", danger)
        self.assertNotIn("mailto:", html)
        assert_french_copy(self, html)

    def test_the_page_has_unique_ids(self):
        assert_unique_ids(self, self.page(MY_DATA))

    def test_cv_kept_and_deleted(self):
        read = timezone.now()
        self.update(cv_profile=CV_PROFILE, cv_processed_at=read)
        html = self.page(MY_DATA)
        page = text(html)
        self.assertIn(f"Lu le {timezone.localtime(read).day} ", page)
        self.assertIn("Le fichier a été supprimé à la lecture ; seul le résumé est gardé.", page)
        self.assertIn("CV lu : 1 poste, 1 compétence", page)
        self.assertNotRegex(page, r"\.\.|compétence\.")
        danger = html[html.index('<div class="danger-zone">'):]
        self.assertIn('action="/mes-donnees/supprimer-cv/"', danger)
        self.assertIn(">Supprimer le résumé de mon CV</button>", danger)
        response = self.client.post("/mes-donnees/supprimer-cv/")
        self.assertRedirects(response, MY_DATA, fetch_redirect_response=False)
        profile = self.profile
        self.assertEqual((profile.cv_profile, profile.cv_processed_at), ({}, None))
        self.assertEqual(profile.summary_status, SummaryStatus.STALE)
        self.assertIn("Aucun CV envoyé.", self.page(MY_DATA))

    def test_delete_is_post_only(self):
        self.assertEqual(self.client.get("/mes-donnees/supprimer-cv/").status_code, 405)


class MyDataLearnedTests(FinishedTestCase):
    LABEL = "Ce que j’ai appris de vos choix"

    def test_nothing_yet(self):
        page = text(self.page(MY_DATA))
        self.assertIn(self.LABEL, page)
        self.assertIn("Rien pour l’instant", page)

    def test_the_text_with_the_ai_label_when_claude_wrote_it(self):
        self.update(learned_summary="Vous choisissez surtout la médiation.")
        html = self.page(MY_DATA)
        row = html[html.index(self.LABEL):]
        self.assertIn("Vous choisissez surtout la médiation.", text(row))
        self.assertIn("rédigé par IA", text(row[:row.index("</dt>")]))
        self.assertNotIn("Rien pour l’instant", html)
        assert_french_copy(self, html)

    def test_no_ai_label_for_the_persons_own_text_and_the_text_is_escaped(self):
        self.update(learned_summary="Mon avis <script>alert(1)</script>", learned_summary_own=True)
        html = self.page(MY_DATA)
        row = html[html.index(self.LABEL):html.index("</dt>", html.index(self.LABEL))]
        self.assertNotIn("par IA", row)
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("&lt;script&gt;alert(1)", html)
