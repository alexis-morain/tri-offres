"""Blocks 2 and 3 of the questionnaire: "Ce que vous préférez" and "En vos mots"."""
import re

from accounts.models import Profile
from onboarding import flow, forms
from onboarding.tests import (COUNTER, CULTURE, CULTURE_CRITERIA, CULTURE_FULL, CULTURE_JOBS, CULTURE_PREFERENCES,
                              CULTURE_REST, GROWTH_CRITERIA, GROWTH_FULL, GROWTH_PREFERENCES, OTHER_CRITERIA,
                              OTHER_FULL, OTHER_PREFERENCES, START, SUMMARY, QuestionnaireTestCase, url)

CULTURE_CHIPS = ["mediation", "production_admin", "communication", "editorial", "book_trade", "library"]


class EndToEndTests(QuestionnaireTestCase):
    def answer_everything(self, answers):
        """From the sign-in landing to the summary, following each redirect as a browser would."""
        response = self.client.get(START)
        for slug, data in answers.items():
            self.assertEqual(response.context["step"].slug, slug)
            response = self.client.post(url(slug), data, follow=True)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], SUMMARY)
        self.assertContains(response, "Je rédige ce que j’ai compris")

    def test_culture_profile_is_complete(self):
        self.answer_everything(CULTURE_FULL)
        profile = self.profile
        self.assertEqual(profile.hard_criteria, CULTURE_CRITERIA)
        self.assertEqual(profile.weighted_preferences, CULTURE_PREFERENCES)
        self.assertEqual(profile.seek_text, "Un poste où je parle aux publics.")
        self.assertEqual(profile.refuse_text, "")
        self.assertEqual(profile.onboarding_step, "cv")

    def test_growth_career_change_profile_is_complete(self):
        self.answer_everything(GROWTH_FULL)
        profile = self.profile
        self.assertEqual(profile.domains, ["D", "M17"])
        self.assertTrue(profile.career_change)
        self.assertEqual(profile.hard_criteria, GROWTH_CRITERIA)
        self.assertEqual(profile.weighted_preferences, GROWTH_PREFERENCES)
        self.assertEqual(profile.seek_text, "")
        self.assertEqual(profile.refuse_text, "Les postes payés uniquement à la commission.")

    def test_profile_outside_the_culture_and_growth_niches_is_complete(self):
        self.answer_everything(OTHER_FULL)
        profile = self.profile
        self.assertEqual(profile.domains, ["M18"])
        self.assertEqual(profile.hard_criteria, OTHER_CRITERIA)
        self.assertEqual(profile.weighted_preferences, OTHER_PREFERENCES)
        self.assertEqual(profile.onboarding_step, "cv")


class ValuesScreenTests(QuestionnaireTestCase):
    def setUp(self):
        super().setUp()
        self.walk(CULTURE)

    def test_blue_capped_chips_with_their_reason(self):
        html = self.html("valeurs")
        self.assertIn("Ce qui compte le plus dans votre prochain poste, tri-offres", html)
        self.assertIn("Règle le ton de la notation. Ne retire aucune offre.", html)
        self.assertRegex(html, r'class="chips" data-tone="blue"[^>]*data-max-choices="3"')
        self.assertIn("Jusqu’à 3.", html)
        self.assertIn("<span>Le sens du travail</span>", html)
        self.assertRegex(html, r'class="link-back" href="/questionnaire/experience/"')

    def test_at_least_one_and_three_at_most(self):
        self.assertIn("values", self.errors("valeurs", {}))
        errors = self.errors("valeurs", {"values": ["meaning", "team", "pay", "autonomy"]})
        self.assertEqual(errors["values"], ["Choisissez-en 3 au plus : retirez-en 1."])
        self.assertIn("values", self.errors("valeurs", {"values": ["bogus"]}))
        self.assertNotIn("values", self.profile.weighted_preferences)

    def test_saved_in_the_list_order_and_shown_again(self):
        self.post("valeurs", {"values": ["pay", "meaning"]})
        self.assertEqual(self.profile.weighted_preferences,
                         {"version": 1, "jobs": CULTURE_JOBS, "values": ["meaning", "pay"]})
        self.assertRegex(self.html("valeurs"), r'value="pay"[^>]*checked')


def give_chips(user, chips):
    """An account from before the postes: job chips picked on « Les métiers que vous visez », after its postes."""
    profile = Profile.objects.get(user=user)
    profile.weighted_preferences = {**profile.weighted_preferences, "jobs": CULTURE_JOBS + chips}
    profile.save()


class JobsScreenTests(QuestionnaireTestCase):
    """« Les métiers que vous visez », kept for the accounts that picked job chips (plan of 26/10, 3.1)."""

    def setUp(self):
        super().setUp()
        self.walk(CULTURE_FULL, until="valeurs")
        give_chips(self.user, ["mediation"])

    def test_a_new_account_never_sees_it(self):
        give_chips(self.user, [])
        self.assertRedirects(self.client.get(url("metiers")), url("structures"))
        self.assertRedirects(self.post("valeurs", CULTURE_REST["valeurs"]), url("structures"),
                             fetch_redirect_response=False)

    def test_chips_grouped_by_domain_then_the_niche_list(self):
        html = self.html("metiers")
        self.assertIn("Les métiers que vous visez", html)
        self.assertIn("Les offres de ces métiers remontent en tête.", html)
        self.assertRegex(html, r'data-tone="blue"[^>]*data-max-choices="5"[^>]*data-grouped')
        legends = re.findall(r'<p class="chips__legend" id="id_jobs_group_\d+">([^<]+)</p>', html)
        self.assertEqual(legends, ["Communication / Multimédia", "Services à la personne / à la collectivité",
                                   "Plus précis, pour la culture et l’ESS"])
        self.assertEqual(html.count('role="group" aria-labelledby="id_jobs_group_'), 3)
        self.assertIn("<span>Édition et communication</span>", html)
        self.assertNotIn("SDR ou BDR", html)
        self.assertNotIn("Artistes - interprètes du spectacle", html)  # L is not chosen
        self.assertNotIn("custom_job", html)  # the postes are typed on the search screen

    def test_chips_saved_after_the_postes_in_the_list_order(self):
        self.post("metiers", {"jobs": ["mediation", "K16", "E11"]})
        self.assertEqual(self.profile.weighted_preferences["jobs"], CULTURE_JOBS + ["E11", "K16", "mediation"])

    def test_dropping_every_chip_keeps_the_postes_and_the_screen_goes(self):
        self.assertRedirects(self.post("metiers", {}), url("structures"), fetch_redirect_response=False)
        self.assertEqual(self.profile.weighted_preferences["jobs"], CULTURE_JOBS)
        self.assertRedirects(self.client.get(url("metiers")), url("structures"))

    def test_a_chip_needed_without_a_poste(self):
        Profile.objects.filter(user=self.user).update(weighted_preferences={"version": 1, "jobs": ["mediation"]})
        self.assertEqual(self.errors("metiers", {})["jobs"], ["Choisissez au moins un métier."])

    def test_five_chips_at_most(self):
        errors = self.errors("metiers", {"jobs": CULTURE_CHIPS})
        self.assertEqual(errors["jobs"], ["Choisissez-en 5 au plus : retirez-en 1."])
        self.assertEqual(self.post("metiers", {"jobs": CULTURE_CHIPS[:5]}).status_code, 302)
        self.assertEqual(len(self.profile.weighted_preferences["jobs"]), 8)

    def test_a_job_outside_the_domains_is_refused(self):
        for job in ("sdr_bdr", "L12", "M18"):
            with self.subTest(job=job):
                self.assertIn("jobs", self.errors("metiers", {"jobs": [job]}))

    def test_saved_answers_are_shown_again(self):
        self.post("metiers", {"jobs": ["editorial"]})
        self.assertRegex(self.html("metiers"), r'value="editorial"[^>]*checked')

    def test_career_change_rewords_the_screen(self):
        self.post("recherche", {**CULTURE["recherche"], "career_change": "yes", "previous_job": "Comptable"})
        html = self.html("metiers")
        self.assertIn("<title>Les métiers qui vous attirent, même sans expérience directe, tri-offres</title>", html)
        self.assertRegex(html, r"<h1[^>]*>Les métiers qui vous attirent, même sans expérience directe</h1>")
        self.assertIn("Je garderai aussi une ou deux offres d’exploration par mail.", html)
        self.assertNotIn("Les offres de ces métiers remontent en tête.", html)


class StructuresScreenTests(QuestionnaireTestCase):
    def setUp(self):
        super().setUp()
        self.walk(GROWTH_FULL, until="valeurs")

    def test_optional(self):
        html = self.html("structures")
        self.assertIn("<title>Où aimeriez-vous travailler ?, tri-offres</title>", html)
        self.assertIn("Ces préférences font monter la note des offres qui y correspondent. Elles n’en écartent "
                      "aucune.", html)
        self.assertRedirects(self.post("structures", {}), url("mots"), fetch_redirect_response=False)
        self.assertEqual(self.profile.weighted_preferences["structures"], [])
        self.assertEqual(self.profile.weighted_preferences["sectors"], [])

    def test_sectors_are_a_second_optional_list_stored_beside_the_structures(self):
        html = self.html("structures")
        self.assertIn("Secteur de l’employeur", html)
        self.assertIn("Jusqu’à 3, ou aucun si cela vous est égal.", html)
        self.assertEqual(len(re.findall(r'name="sectors"', html)), 14)
        self.assertEqual(self.errors("structures", {"sectors": ["culture"]}).keys(), {"sectors"})
        self.assertEqual(self.errors("structures", {"sectors": ["industry", "retail", "digital", "consulting"]})
                         ["sectors"], ["Choisissez-en 3 au plus : retirez-en 1."])
        self.post("structures", {"structures": ["startup"], "sectors": ["digital", "real_estate"]})
        preferences = self.profile.weighted_preferences
        self.assertEqual((preferences["structures"], preferences["sectors"]), (["startup"], ["real_estate", "digital"]))
        self.assertRegex(self.html("structures"), r'value="digital"[^>]*checked')

    def test_an_account_from_before_the_sectors_is_not_sent_back(self):
        weighted = {key: value for key, value in self.profile.weighted_preferences.items() if key != "sectors"}
        Profile.objects.filter(user=self.user).update(
            weighted_preferences={**weighted, "structures": ["startup"]})
        self.assertNotIn("sectors", self.profile.weighted_preferences)
        self.assertTrue(forms.StructuresForm.answered(self.profile))
        self.assertIsNone(flow.first_missing(self.profile))

    def test_one_list_for_everyone(self):
        html = self.html("structures")
        chips = html[html.index('class="chips"'):]
        self.assertEqual(re.findall(r"<span>([^<]+)</span>", chips[:chips.index("</div>")]),
                         ["Entreprise", "Startup", "Association et ESS", "Culture", "Secteur public",
                          "Établissement de santé ou social", "Enseignement"])
        self.assertIn("structures", self.errors("structures", {"structures": ["publisher"]}))
        self.post("structures", {"structures": ["culture", "startup"]})
        self.assertEqual(self.profile.weighted_preferences["structures"], ["startup", "culture"])


class StructuresCapTests(QuestionnaireTestCase):
    def test_five_at_most(self):
        self.walk(CULTURE_FULL, until="valeurs")
        six = ["company", "startup", "association_ess", "culture", "public_sector", "education"]
        self.assertEqual(self.errors("structures", {"structures": six})["structures"],
                         ["Choisissez-en 5 au plus : retirez-en 1."])


class WordsScreenTests(QuestionnaireTestCase):
    def setUp(self):
        super().setUp()
        self.walk(CULTURE_FULL, until="structures")

    def test_two_optional_texts_with_the_examples(self):
        html = self.html("mots")
        self.assertIn("Facultatif, mais c’est ce qui affine le plus la notation.", html)
        self.assertEqual(len(re.findall(r'<textarea[^>]*rows="4"[^>]*maxlength="1000"', html)), 2)
        self.assertIn(">Ce que vous cherchez</label>", html)
        self.assertIn(">Ce que vous refusez</label>", html)
        self.assertIn("Par exemple : «\u00a0Une équipe à taille humaine, où je touche à plusieurs sujets.\u00a0» "
                      "ou «\u00a0Un poste où je vois le résultat concret de mon travail.\u00a0»", html)
        self.assertIn("Par exemple : «\u00a0Les horaires décalés et le travail du week-end.\u00a0» ou "
                      "«\u00a0Les postes payés surtout à la commission.\u00a0»", html)
        self.assertRedirects(self.post("mots", {}), url("cv"), fetch_redirect_response=False)
        self.assertEqual((self.profile.seek_text, self.profile.refuse_text), ("", ""))

    def test_thousand_characters_at_most_and_trimmed(self):
        errors = self.errors("mots", {"seek_text": "x" * 1001})
        self.assertEqual(errors["seek_text"], ["Faites plus court : 1 000 caractères au plus."])
        self.post("mots", {"seek_text": "  Un musée  ", "refuse_text": "x" * 1000})
        self.assertEqual(self.profile.seek_text, "Un musée")
        self.assertEqual(len(self.profile.refuse_text), 1000)
        self.assertIn("Un musée</textarea>", self.html("mots"))


class GrowthWordsTests(QuestionnaireTestCase):
    def test_the_same_examples_for_every_domain(self):
        self.walk(GROWTH_FULL, until="structures")
        html = self.html("mots")
        self.assertIn("«\u00a0Les postes payés surtout à la commission.\u00a0»", html)


class DomainSwitchTests(QuestionnaireTestCase):
    def test_switch_keeps_values_structures_and_the_chips_still_offered(self):
        self.walk(CULTURE_FULL)
        give_chips(self.user, ["E11", "K16", "mediation"])
        self.post("recherche", {**CULTURE["recherche"], "job_1": "Bibliothécaire", "job_2": "Développeuse web",
                                "job_3": ""})
        profile = self.profile
        self.assertEqual(profile.domains, ["M18", "K"])  # in the order of the list
        self.assertEqual(profile.weighted_preferences, {
            **CULTURE_PREFERENCES, "jobs": [{"custom": "Bibliothécaire"}, {"custom": "Développeuse web"}, "K16",
                                            "mediation"]})
        self.assertEqual(profile.onboarding_step, "cv")

    def test_switch_with_no_chip_left_keeps_the_progress(self):
        self.walk(CULTURE_FULL)
        give_chips(self.user, ["E11"])
        response = self.post("recherche", {**OTHER_FULL["recherche"], "offer_kind": "job"})
        self.assertRedirects(response, url("lieu"), fetch_redirect_response=False)
        profile = self.profile
        self.assertEqual(profile.weighted_preferences["jobs"], [{"custom": "Développeuse web"}])
        self.assertEqual(profile.onboarding_step, "cv")

    def test_no_structure_chosen_survives_a_switch(self):
        self.walk(GROWTH_FULL)
        self.post("recherche", {**CULTURE["recherche"]})
        self.assertEqual(self.profile.weighted_preferences["structures"], [])

    def test_same_domains_keep_every_preference(self):
        self.walk(CULTURE_FULL)
        self.post("recherche", {**CULTURE["recherche"], "career_change": "yes", "previous_job": "Comptable"})
        profile = self.profile
        self.assertEqual(profile.weighted_preferences, CULTURE_PREFERENCES)
        self.assertEqual(profile.onboarding_step, "cv")


class EditingTests(QuestionnaireTestCase):
    def test_editing_a_preference_keeps_the_progress(self):
        self.walk(CULTURE_FULL)
        response = self.post("valeurs", {"values": ["learning"]})
        self.assertRedirects(response, url("structures"), fetch_redirect_response=False)
        profile = self.profile
        self.assertEqual(profile.onboarding_step, "cv")
        self.assertEqual(profile.weighted_preferences["values"], ["learning"])
        self.assertEqual(profile.weighted_preferences["jobs"], CULTURE_PREFERENCES["jobs"])

    def test_jumping_ahead_is_refused(self):
        self.walk(CULTURE)
        self.assertRedirects(self.client.get(url("structures")), url("valeurs"))
        self.post("mots", {"seek_text": "Un musée"})
        self.assertEqual(self.profile.seek_text, "")


class FileTests(QuestionnaireTestCase):
    def drawer(self, html):
        return html[html.index("data-drawer"):html.index("</details>")]

    def card(self, html, tone):
        drawer = self.drawer(html)
        start = drawer.index(f"card card--{tone}")
        return drawer[start:drawer.index("</article>", start)]

    def test_blue_card_fills_screen_by_screen(self):
        self.walk(CULTURE)
        empty = self.card(self.html("valeurs"), "blue")
        self.assertIn("Ce que vous préférez", empty)
        self.assertIn("Chaque réponse validée s’inscrit ici.", empty)
        self.walk(CULTURE_REST, until="valeurs")
        card = self.card(self.html("structures"), "blue")
        self.assertIn("Le sens du travail, l’équipe", card)
        self.assertNotIn("Métiers", card)
        self.assertIn("card card--pink", self.drawer(self.html("structures")))

    def test_blue_card_rows_and_filed_once_the_block_ends(self):
        self.walk(CULTURE_FULL, until="structures")
        html = self.html("mots")
        card = self.card(html, "blue")
        self.assertIn("card--filed", card)
        self.assertNotIn("Métiers", card)  # the postes typed are not a preference of this block
        self.assertIn("Association et ESS, culture", card)
        self.assertNotIn("card--filed", self.card(self.html("mots"), "blue"))
        self.assertIn("Chaque réponse validée s’inscrit ici.", self.card(html, "yellow"))
        self.assertIn("3 fiches", self.drawer(html))

    def test_job_chips_of_an_account_from_before_the_postes(self):
        self.walk(CULTURE_FULL, until="structures")
        give_chips(self.user, ["mediation", "E11"])
        self.assertIn("Médiation\u202f; Édition et communication", self.card(self.html("mots"), "blue"))

    def test_no_structure_chosen_reads_plainly(self):
        self.walk(GROWTH_FULL, until="structures")
        self.assertIn("Aucune en particulier", self.card(self.html("mots"), "blue"))

    def test_yellow_card_after_the_words(self):
        long = "Une structure à taille humaine, où je touche à plusieurs projets et où je parle aux publics " * 3
        self.walk({**CULTURE_FULL, "mots": {"seek_text": long, "refuse_text": ""}}, until="mots")
        card = self.card(self.html("cv"), "yellow")
        self.assertIn("Pas encore rempli.", card)
        shown = re.search(r"<dt>Ce que vous cherchez</dt><dd>(.*?)</dd>", card).group(1)
        self.assertLessEqual(len(shown), 120)
        self.assertTrue(shown.endswith("…"))


class DuplicatesAndSpacesTests(QuestionnaireTestCase):
    def setUp(self):
        super().setUp()
        self.walk(CULTURE_FULL, until="valeurs")

    def test_a_value_sent_four_times_counts_once(self):
        self.assertEqual(self.post("valeurs", {"values": ["meaning"] * 4}).status_code, 302)
        self.assertEqual(self.profile.weighted_preferences["values"], ["meaning"])

    def test_a_job_sent_several_times_counts_once(self):
        give_chips(self.user, ["mediation"])
        response = self.post("metiers", {"jobs": ["mediation"] * 6 + CULTURE_CHIPS[1:4]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.profile.weighted_preferences["jobs"],
                         CULTURE_JOBS + ["mediation", "production_admin", "communication", "editorial"])


class DamagedProfileTests(QuestionnaireTestCase):
    """A stored value that no longer fits its schema is read as empty, never a server error."""

    def damage(self, step="mots", **fields):
        fields = fields or {"hard_criteria": {"location": "Lyon"}, "weighted_preferences": {"values": ["bogus"]}}
        Profile.objects.filter(user=self.user).update(onboarding_step=step, **fields)

    def test_every_page_still_answers(self):
        self.walk(CULTURE_FULL)
        self.damage(domains={"E": True})
        with self.assertLogs("onboarding.views", "WARNING") as logs:
            for path in [START, SUMMARY, "/fin/"] + [url(slug) for slug in CULTURE_FULL]:
                with self.subTest(path=path):
                    self.assertLess(self.client.get(path, follow=True).status_code, 500)
            self.assertLess(self.client.post(f"{COUNTER}?step=lieu").status_code, 500)
        self.assertIn(f"user {self.user.pk}", logs.output[0])
        self.assertNotIn("nora@example.fr", "".join(logs.output))

    def test_domains_that_fit_are_read_normalized(self):
        self.walk(CULTURE_FULL)
        give_chips(self.user, ["mediation"])
        self.damage(domains=["K", "E", "E"])
        html = self.client.get(url("metiers")).content.decode()
        self.assertEqual(html.count(">Communication / Multimédia</p>"), 1)

    def test_the_person_resumes_at_the_first_screen_whose_answer_is_gone(self):
        # The offer kind went with the criteria, damaged domains read as none: the search screen comes first.
        for fields, slug in (({}, "recherche"), ({"domains": ["E", "Z"]}, "recherche"),
                             ({"weighted_preferences": {"values": ["bogus"]}}, "valeurs")):
            with self.subTest(slug=slug, fields=fields):
                self.walk(CULTURE_FULL)
                self.damage(**fields)
                with self.assertLogs("onboarding.views", "WARNING"):
                    self.resumes_at(slug)

    def test_answering_again_repairs_the_profile(self):
        self.walk(CULTURE_FULL)
        self.damage()
        with self.assertLogs("onboarding.views", "WARNING"):
            self.post("recherche", CULTURE["recherche"])
        self.post("lieu", CULTURE["lieu"])
        profile = self.profile
        self.assertEqual(profile.hard_criteria,
                         {"version": 1, "offer_kind": "job", "location": CULTURE_CRITERIA["location"]})
        self.assertEqual(profile.weighted_preferences, {"version": 1, "jobs": CULTURE_JOBS})
