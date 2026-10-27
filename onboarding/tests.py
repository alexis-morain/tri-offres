import json
import re

from django.test import Client, TestCase, override_settings

from accounts import events
from accounts.models import OnboardingEvent, Profile, User
from config.testing import PLAIN_STATIC, assert_french_copy, assert_unique_ids
from jobs import rome_titles
from jobs.testing import make_offer, make_source
from onboarding import criteria, flow

START = "/demarrer/"
COUNTER = "/questionnaire/compteur/"
SUMMARY = "/synthese/"
END = "/fin/"


def url(slug):
    return f"/questionnaire/{slug}/"


# Three people: in the culture niche (E and K), in the growth niche (D and M17), and in a domain outside both (M18).
# Their domains come from the postes typed (ROME titles: K, E, M17, M18), and D from the families of the fallback.
CULTURE = {
    "recherche": {"job_1": "Médiatrice culturelle", "job_2": "Chargée de communication",
                  "job_3": "  Régisseuse   son ", "offer_kind": "job", "career_change": "no"},
    "lieu": {"city_1": "Lyon", "city_1_insee": "69123", "city_1_department": "69", "city_1_region": "84",
             "distance": "30", "remote": "hybrid"},
    "contrats": {"contracts": ["cdi", "cdd"]},
    "mots-cles": {"include": "", "exclude": "stage,  Stage "},
    "remuneration": {"floor": "24000", "unit": "year"},
    "temps": {"hours": "full"},
    "experience": {"experience": "1_2", "preference": "on"},
}
CULTURE_CRITERIA = {
    "version": 1,
    "offer_kind": "job",
    "location": {"cities": [{"name": "Lyon", "insee": "69123", "department": "69", "region": "84"}],
                 "distance": "30", "remote": "hybrid", "strict": True},
    "contracts": {"values": ["cdi", "cdd"], "strict": True},
    "keywords": {"include": [], "exclude": ["stage"], "strict": True},
    "pay": {"floor": 24000, "unit": "year", "strict": True},
    "hours": {"value": "full", "strict": True},
    "experience": {"value": "1_2", "strict": False},
}
GROWTH = {
    "recherche": {"job_1": "Responsable marketing", "job_2": "Business developer", "domains": ["D"],
                  "offer_kind": "mission", "career_change": "yes", "previous_job": "Professeure de lettres"},
    "lieu": {"city_1": "", "distance": "city", "remote": "full_remote", "preference": "on"},
    "contrats": {"contracts": ["freelance"]},
    "mots-cles": {"include": "growth, marketing", "exclude": ""},
    "remuneration": {"floor": "450", "unit": "day", "preference": "on"},
    "temps": {"hours": "part_ok"},
    "experience": {"experience": "3_4"},
}
GROWTH_CRITERIA = {
    "version": 1,
    "offer_kind": "mission",
    "location": {"cities": [], "distance": "city", "remote": "full_remote", "strict": False},
    "contracts": {"values": ["freelance"], "strict": True},
    "keywords": {"include": ["growth", "marketing"], "exclude": [], "strict": True},
    "pay": {"floor": 450, "unit": "day", "strict": False},
    "hours": {"value": "part_ok", "strict": True},
    "experience": {"value": "3_4", "strict": True},
}
# Blocks 2 and 3, after the block 1 answers above.
CULTURE_REST = {
    "valeurs": {"values": ["team", "meaning"]},
    "structures": {"structures": ["culture", "association_ess"], "sectors": ["culture_media"]},
    "mots": {"seek_text": "Un poste où je parle aux publics.", "refuse_text": ""},
    "cv": {"action": "continue"},  # "Passer cette étape"
}
CULTURE_JOBS = [{"custom": "Médiatrice culturelle"}, {"custom": "Chargée de communication"},
                {"custom": "Régisseuse son"}]
CULTURE_PREFERENCES = {"version": 1, "jobs": CULTURE_JOBS, "values": ["meaning", "team"],
                       "structures": ["association_ess", "culture"], "sectors": ["culture_media"]}
GROWTH_REST = {
    "valeurs": {"values": ["autonomy"]},
    "structures": {},
    "mots": {"seek_text": "", "refuse_text": "Les postes payés uniquement à la commission."},
    "cv": {"action": "continue"},
}
GROWTH_PREFERENCES = {"version": 1, "values": ["autonomy"],
                      "jobs": [{"custom": "Responsable marketing"}, {"custom": "Business developer"}], "structures": [],
                      "sectors": []}
OTHER_FULL = {
    "recherche": {"job_1": "Développeuse web", "offer_kind": "both", "career_change": "no"},
    "lieu": {"city_1": "Nantes", "distance": "60", "remote": "any"},
    "contrats": {"contracts": ["cdi", "freelance"]},
    "mots-cles": {},
    "remuneration": {"floor": "500", "unit": "day"},
    "temps": {"hours": "full"},
    "experience": {"experience": "5_9"},
    "valeurs": {"values": ["autonomy", "learning"]},
    "structures": {"structures": ["company", "startup"], "sectors": ["consulting", "digital"]},
    "mots": {"seek_text": "Une équipe produit, du code en production chaque semaine.", "refuse_text": ""},
    "cv": {"action": "continue"},
}
OTHER_CRITERIA = {
    "version": 1,
    "offer_kind": "both",
    "location": {"cities": [{"name": "Nantes", "insee": "", "department": "", "region": ""}],
                 "distance": "60", "remote": "any", "strict": True},
    "contracts": {"values": ["cdi", "freelance"], "strict": True},
    "keywords": {"include": [], "exclude": [], "strict": True},
    "pay": {"floor": 500, "unit": "day", "strict": True},
    "hours": {"value": "full", "strict": True},
    "experience": {"value": "5_9", "strict": True},
}
OTHER_PREFERENCES = {"version": 1, "values": ["learning", "autonomy"], "jobs": [{"custom": "Développeuse web"}],
                     "structures": ["company", "startup"], "sectors": ["digital", "consulting"]}
CULTURE_FULL = {**CULTURE, **CULTURE_REST}
GROWTH_FULL = {**GROWTH, **GROWTH_REST}


@override_settings(STORAGES=PLAIN_STATIC)
class QuestionnaireTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("nora@example.fr")
        self.client.force_login(self.user)

    @property
    def profile(self):
        return Profile.objects.get(user=self.user)

    def post(self, slug, data):
        return self.client.post(url(slug), data)

    def walk(self, answers, until=None):
        for slug, data in answers.items():
            response = self.post(slug, data)
            self.assertEqual(response.status_code, 302, (slug, response.content.decode()[:3000]))
            if slug == until:
                break

    def html(self, slug):
        response = self.client.get(url(slug))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def resumes_at(self, slug):
        response = self.client.get(START)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["step"].slug, slug)
        self.assertContains(response, f'action="{url(slug)}"')

    def errors(self, slug, data):
        response = self.post(slug, data)
        self.assertEqual(response.status_code, 200)
        return response.context["form"].errors


class StartTests(QuestionnaireTestCase):
    def test_anonymous_visitors_are_sent_to_sign_in(self):
        self.client.logout()
        for path in (START, url("recherche"), COUNTER + "?step=lieu", SUMMARY, END):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith("/?"), response["Location"])

    def test_a_new_account_starts_with_the_search_screen(self):
        self.resumes_at("recherche")

    def test_profile_is_created_when_missing(self):
        Profile.objects.filter(user=self.user).delete()
        self.assertEqual(self.client.get(url("recherche")).status_code, 200)
        self.assertTrue(Profile.objects.filter(user=self.user).exists())

    def test_resume_after_signing_out_and_in_again(self):
        self.walk(CULTURE, until="contrats")
        self.client.logout()
        self.client.force_login(self.user)
        self.resumes_at("mots-cles")

    def test_unknown_screen_is_404(self):
        self.assertEqual(self.client.get(url("salaire")).status_code, 404)


class WalkThroughTests(QuestionnaireTestCase):
    def test_culture_screens_chain_and_save_criteria(self):
        response = self.post("recherche", CULTURE["recherche"])
        self.assertRedirects(response, url("lieu"), fetch_redirect_response=False)
        self.walk(CULTURE)
        profile = self.profile
        self.assertEqual(profile.hard_criteria, CULTURE_CRITERIA)
        self.assertEqual(criteria.normalize(profile.hard_criteria), profile.hard_criteria)
        self.assertEqual(profile.onboarding_step, "experience")
        self.assertEqual(profile.domains, ["E", "K"])
        self.assertFalse(profile.career_change)

    def test_growth_screens_save_criteria(self):
        self.walk(GROWTH)
        profile = self.profile
        self.assertEqual(profile.hard_criteria, GROWTH_CRITERIA)
        self.assertEqual(profile.domains, ["D", "M17"])
        self.assertTrue(profile.career_change)
        self.assertEqual(profile.previous_job, "Professeure de lettres")

    def test_each_screen_redirects_to_the_next_and_the_last_to_the_summary(self):
        slugs = list(CULTURE_FULL)
        for slug, following in zip(slugs, slugs[1:] + [None]):
            response = self.post(slug, CULTURE_FULL[slug])
            self.assertEqual(response["Location"], url(following) if following else SUMMARY)

    def test_each_validated_screen_is_logged(self):
        self.walk(CULTURE)
        names = list(OnboardingEvent.objects.filter(user=self.user).values_list("name", flat=True))
        self.assertEqual(names, [events.step(slug) for slug in CULTURE])
        self.assertEqual(events.step("lieu"), "step:lieu")

    def test_the_summary_follows_the_words_block(self):
        self.walk(CULTURE_FULL)
        for path in (START, SUMMARY):
            self.assertContains(self.client.get(path), "Je rédige ce que j’ai compris")

    def test_summary_and_end_before_their_turn_redirect_to_resume(self):
        self.walk(CULTURE, until="lieu")
        self.assertRedirects(self.client.get(SUMMARY), url("contrats"))
        self.assertRedirects(self.client.get(END), url("contrats"))


class NavigationTests(QuestionnaireTestCase):
    def test_jumping_ahead_is_refused(self):
        self.assertRedirects(self.client.get(url("contrats")), url("recherche"))
        self.walk(CULTURE, until="recherche")
        self.assertRedirects(self.client.get(url("remuneration")), url("lieu"))
        response = self.post("temps", CULTURE["temps"])
        self.assertRedirects(response, url("lieu"))
        self.assertNotIn("hours", self.profile.hard_criteria)

    def test_back_link_on_every_screen_but_the_first(self):
        self.assertNotIn("link-back", self.html("recherche"))
        self.walk(CULTURE)
        for slug in list(CULTURE)[1:]:
            with self.subTest(slug=slug):
                previous = flow.previous_step(slug, self.profile).slug
                self.assertRegex(self.html(slug), rf'class="link-back" href="{url(previous)}"')

    def test_saved_answers_are_shown_again(self):
        self.walk(CULTURE)
        lieu = self.html("lieu")
        self.assertRegex(lieu, r'name="city_1"[^>]*value="Lyon"|value="Lyon"[^>]*name="city_1"')
        self.assertRegex(lieu, r'value="69123"')
        self.assertRegex(lieu, r'value="hybrid"[^>]*checked')
        self.assertRegex(self.html("contrats"), r'value="cdd"[^>]*checked')
        self.assertNotRegex(self.html("contrats"), r'value="cdd_projet"[^>]*checked')
        self.assertRegex(self.html("remuneration"), r'value="24000"')
        self.assertRegex(self.html("experience"), r'name="preference"[^>]*checked')
        recherche = self.html("recherche")
        self.assertRegex(recherche, r'name="job_2" value="Chargée de communication"')
        self.assertRegex(recherche, r'value="job"[^>]*checked')

    def test_editing_an_earlier_screen_keeps_the_progress(self):
        self.walk(CULTURE)
        response = self.post("lieu", {**CULTURE["lieu"], "city_1": "Nantes", "city_1_insee": "44109"})
        self.assertRedirects(response, url("contrats"), fetch_redirect_response=False)
        profile = self.profile
        self.assertEqual(profile.onboarding_step, "experience")
        self.assertEqual(profile.hard_criteria["location"]["cities"][0]["name"], "Nantes")
        self.assertEqual(profile.hard_criteria["pay"], CULTURE_CRITERIA["pay"])

    def test_first_visit_preselects_nothing_on_the_search_screen(self):
        html = self.html("recherche")
        self.assertNotRegex(html, r'type="radio"[^>]*checked')


class SearchScreenTests(QuestionnaireTestCase):
    def fallback(self, html):
        return html[html.index('<details class="fallback"'):html.index("</details>", html.index('class="fallback"'))]

    def test_title_three_postes_with_suggestions_and_the_families_folded(self):
        html = self.html("recherche")
        self.assertIn("<title>Quel poste cherchez-vous ?, tri-offres</title>", html)
        self.assertRegex(html, r'<h1[^>]*>Quel poste <span class="nowrap">cherchez-vous</span> \?</h1>')
        for name, label in (("job_1", "Poste"), ("job_2", "Deuxième poste (facultatif)"),
                            ("job_3", "Troisième poste (facultatif)")):
            self.assertIn(f'<label class="field-label" for="id_{name}">{label}</label>', html)
            self.assertRegex(html, rf'<input type="text" name="{name}"[^>]*'
                                   r'data-suggestions-url="/questionnaire/postes/"')
        fallback = self.fallback(html)
        self.assertNotIn("open", fallback[:fallback.index(">")])
        self.assertIn("<summary>Je ne trouve pas mon poste<svg", fallback)
        self.assertIn("<legend class=\"field-label\">Famille de métiers</legend>", fallback)
        self.assertIn("le marketing dans l’immobilier, c’est Marketing.", fallback)
        self.assertRegex(fallback, r'<div class="chips" data-tone="pink" id="id_domains" data-max-choices="2">')
        self.assertEqual(len(re.findall(r'type="checkbox" name="domains"', fallback)), 22)
        self.assertIn("<span>Informatique / Télécommunication</span>", fallback)
        self.assertIn("Les missions se font en freelance, payées à la journée.", html)

    def test_the_postes_give_the_domains(self):
        self.post("recherche", CULTURE["recherche"])
        profile = self.profile
        self.assertEqual(profile.domains, ["E", "K"])
        self.assertEqual(profile.weighted_preferences, {"version": 1, "jobs": CULTURE_JOBS})

    def test_accents_capitals_and_spaces_do_not_matter_and_the_poste_is_kept_as_typed(self):
        self.post("recherche", {**CULTURE["recherche"], "job_1": "  MEDIATRICE   culturelle ", "job_2": "",
                                "job_3": ""})
        self.assertEqual(self.profile.domains, ["K"])
        self.assertEqual(self.profile.weighted_preferences["jobs"], [{"custom": "MEDIATRICE culturelle"}])

    def test_a_poste_is_required_and_nothing_else_is_answered(self):
        errors = self.errors("recherche", {})
        self.assertEqual(errors["job_1"], ["Indiquez le poste que vous cherchez, par exemple comptable."])
        self.assertEqual(errors["offer_kind"], ["Choisissez un poste salarié, des missions, ou les deux."])
        self.assertIn("career_change", errors)
        self.assertNotIn("domains", errors)

    def test_a_second_poste_alone_is_enough(self):
        self.post("recherche", {**OTHER_FULL["recherche"], "job_1": "", "job_2": "Développeuse web"})
        self.assertEqual(self.profile.domains, ["M18"])

    def test_an_unknown_poste_asks_for_its_family_with_the_fallback_open(self):
        response = self.post("recherche", {**GROWTH["recherche"], "job_1": "Business developer", "job_2": "",
                                           "domains": []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].errors["domains"], [
            "Je ne reconnais pas ce poste. Choisissez la famille de votre métier ci-dessous."])
        html = response.content.decode()
        self.assertRegex(html, r'<details class="fallback" open>')
        self.post("recherche", {**GROWTH["recherche"], "job_1": "Business developer", "job_2": ""})
        self.assertEqual(self.profile.domains, ["D"])
        self.assertEqual(self.profile.weighted_preferences["jobs"], [{"custom": "Business developer"}])

    def test_the_families_add_up_with_the_postes(self):
        self.walk(GROWTH, until="recherche")
        self.assertEqual(self.profile.domains, ["D", "M17"])
        fallback = self.fallback(self.html("recherche"))
        self.assertRegex(fallback, r'<details class="fallback" open>')
        self.assertRegex(fallback, r'value="D"[^>]*checked')
        self.assertNotRegex(fallback, r'value="M17"[^>]*checked')  # given by the poste typed

    def test_two_families_at_most_and_three_domains_in_all(self):
        errors = self.errors("recherche", {**CULTURE["recherche"], "domains": ["A", "B", "C"]})
        self.assertEqual(errors["domains"], ["Choisissez-en 2 au plus : retirez-en 1."])
        self.assertIn("domains", self.errors("recherche", {**CULTURE["recherche"], "domains": ["E11"]}))
        errors = self.errors("recherche", {**CULTURE["recherche"], "job_3": "Comptable", "domains": ["A"]})
        self.assertEqual(errors["domains"], ["Vos postes et ces familles font plus de trois familles de métiers : "
                                             "retirez-en."])
        self.assertEqual(self.profile.domains, [])

    def test_postes_need_a_letter_and_fit_in_a_hundred_and_fifty_characters(self):
        errors = self.errors("recherche", {**CULTURE["recherche"], "job_2": "1234"})
        self.assertEqual(errors["job_2"], ["Écrivez un nom de poste en lettres, ou laissez le champ vide."])
        errors = self.errors("recherche", {**CULTURE["recherche"], "job_2": "x" * 151})
        self.assertEqual(errors["job_2"], ["Faites plus court : 150 caractères au plus."])

    def test_the_same_poste_twice_counts_once(self):
        self.post("recherche", {**CULTURE["recherche"], "job_2": "médiatrice  culturelle", "job_3": ""})
        self.assertEqual(self.profile.weighted_preferences["jobs"], [{"custom": "Médiatrice culturelle"}])

    def test_saved_postes_are_shown_again(self):
        self.walk(CULTURE, until="recherche")
        html = self.html("recherche")
        self.assertRegex(html, r'name="job_1" value="Médiatrice culturelle"')
        self.assertRegex(html, r'name="job_3" value="Régisseuse son"')
        self.assertRegex(html, r'<details class="fallback">')
        self.assertRegex(html, r'value="job"[^>]*checked')

    def test_an_account_from_before_the_postes_keeps_its_domains(self):
        """Migration (plan of 26/10, 3.4): domains without a poste still answer the screen; they show in the
        fallback, open, until a poste is typed."""
        self.walk(CULTURE, until="lieu")
        Profile.objects.filter(user=self.user).update(domains=["E", "K"], weighted_preferences={"version": 1})
        self.resumes_at("contrats")
        fallback = self.fallback(self.html("recherche"))
        self.assertRegex(fallback, r'<details class="fallback" open>')
        self.assertRegex(fallback, r'value="E"[^>]*checked')
        self.assertRegex(fallback, r'value="K"[^>]*checked')

    def test_a_migrated_account_with_three_domains_has_to_drop_one(self):
        self.walk(CULTURE, until="recherche")
        Profile.objects.filter(user=self.user).update(domains=["E", "K", "L"], weighted_preferences={"version": 1})
        html = self.html("recherche")
        self.assertEqual(len(re.findall(r'name="domains" value="[EKL]"[^>]*checked', html)), 3)
        errors = self.errors("recherche", {**CULTURE["recherche"], "domains": ["E", "K", "L"]})
        self.assertEqual(errors["domains"], ["Choisissez-en 2 au plus : retirez-en 1."])

    def test_answers_kept_are_shown_when_the_domains_are_asked_again(self):
        # Damaged domains read as empty send the person back to this screen, with nothing validated before it.
        self.walk(GROWTH, until="recherche")
        Profile.objects.filter(user=self.user).update(domains=[], onboarding_step="")
        html = self.html("recherche")
        self.assertRegex(html, r'name="offer_kind" value="mission"[^>]*checked')
        self.assertRegex(html, r'name="career_change" value="yes"[^>]*checked')
        self.assertRegex(html, r'name="job_1" value="Responsable marketing"')

    def test_the_offer_kind_is_saved_for_every_domain(self):
        self.post("recherche", {"job_1": "Infirmier en soins généraux", "offer_kind": "mission",
                                "career_change": "no"})
        self.assertEqual(self.profile.domains, ["J"])
        self.assertEqual(self.profile.hard_criteria, {"version": 1, "offer_kind": "mission"})

    def test_career_change_needs_the_previous_job(self):
        errors = self.errors("recherche", {**CULTURE["recherche"], "career_change": "yes"})
        self.assertIn("previous_job", errors)

    def test_no_career_change_clears_the_previous_job(self):
        self.walk(GROWTH, until="recherche")
        self.post("recherche", {**GROWTH["recherche"], "career_change": "no"})
        self.assertEqual(self.profile.previous_job, "")

    def test_only_the_previous_job_is_marked_for_the_script(self):
        html = self.html("recherche")
        self.assertEqual(re.findall(r'data-show-when="([^"]+)"', html), ["career_change=yes"])
        self.assertNotRegex(html, r'id="field-offer_kind"[^>]*hidden')

    def test_changing_domains_keeps_the_contracts(self):
        self.walk(CULTURE, until="contrats")
        self.post("contrats", {"contracts": ["cdi", "service_civique", "stage"]})
        self.post("recherche", {**OTHER_FULL["recherche"], "offer_kind": "job"})
        self.assertEqual(self.profile.hard_criteria["contracts"],
                         {"values": ["cdi", "service_civique", "stage"], "strict": True})

    def test_leaving_missions_drops_a_day_rate(self):
        self.walk(GROWTH, until="remuneration")
        self.post("recherche", {**GROWTH["recherche"], "offer_kind": "job"})
        self.assertNotIn("pay", self.profile.hard_criteria)

    def test_switch_from_missions_to_job_sends_back_to_the_pay(self):
        self.walk(GROWTH)
        response = self.post("recherche", {**CULTURE["recherche"]})
        self.assertRedirects(response, url("lieu"), fetch_redirect_response=False)
        profile = self.profile
        self.assertEqual(profile.hard_criteria["contracts"], GROWTH_CRITERIA["contracts"])
        self.assertNotIn("pay", profile.hard_criteria)
        self.assertEqual(profile.onboarding_step, "mots-cles")
        self.resumes_at("remuneration")
        self.assertRedirects(self.client.get(url("temps")), url("remuneration"), fetch_redirect_response=False)

    def test_switch_from_both_to_job_with_a_day_rate_sends_back_to_the_pay(self):
        answers = {**GROWTH, "recherche": {**GROWTH["recherche"], "offer_kind": "both"},
                   "contrats": {"contracts": ["cdi", "freelance"]}}
        self.walk(answers)
        self.post("recherche", {**answers["recherche"], "offer_kind": "job"})
        profile = self.profile
        self.assertEqual(profile.hard_criteria["contracts"]["values"], ["cdi", "freelance"])
        self.assertEqual(profile.onboarding_step, "mots-cles")
        self.resumes_at("remuneration")

    def test_switch_that_drops_nothing_keeps_the_progress(self):
        self.walk(CULTURE)
        self.post("recherche", {**OTHER_FULL["recherche"], "offer_kind": "job"})
        self.assertEqual(self.profile.onboarding_step, "experience")

    def test_invalid_offer_kind_is_reported_once(self):
        errors = self.errors("recherche", {**CULTURE["recherche"], "offer_kind": "bogus"})
        self.assertEqual(len(errors["offer_kind"]), 1)

    def test_too_long_previous_job_is_reported_once(self):
        errors = self.errors("recherche", {**CULTURE["recherche"], "career_change": "yes",
                                           "previous_job": "x" * 121})
        self.assertEqual(errors["previous_job"], ["Faites plus court : 120 caractères au plus."])


class LocationScreenTests(QuestionnaireTestCase):
    def setUp(self):
        super().setUp()
        self.walk(CULTURE, until="recherche")

    def test_a_city_is_required_unless_fully_remote(self):
        errors = self.errors("lieu", {"distance": "30", "remote": "hybrid"})
        self.assertIn("city_1", errors)
        self.assertEqual(self.post("lieu", {"distance": "30", "remote": "full_remote"}).status_code, 302)

    def test_without_javascript_only_the_name_is_saved(self):
        self.post("lieu", {"city_1": " Lyon ", "city_2": "Villeurbanne", "distance": "city", "remote": "any"})
        self.assertEqual(self.profile.hard_criteria["location"]["cities"], [
            {"name": "Lyon", "insee": "", "department": "", "region": ""},
            {"name": "Villeurbanne", "insee": "", "department": "", "region": ""},
        ])

    def test_a_city_name_needs_a_letter(self):
        for name in ("%", "_", "--", "75"):
            with self.subTest(name=name):
                errors = self.errors("lieu", {"city_1": name, "distance": "city", "remote": "any"})
                self.assertEqual(errors["city_1"], ["Indiquez le nom d’une ville, par exemple Lyon."])
        errors = self.errors("lieu", {"city_1": "Lyon", "city_2": "%", "distance": "city", "remote": "any"})
        self.assertIn("city_2", errors)

    def test_malformed_codes_are_dropped(self):
        self.post("lieu", {"city_1": "Lyon", "city_1_insee": "<script>", "distance": "city", "remote": "any"})
        self.assertEqual(self.profile.hard_criteria["location"]["cities"][0]["insee"], "")

    def test_city_fields_are_left_to_the_autocomplete(self):
        html = self.html("lieu")
        inputs = re.findall(r"<input[^>]*>", html)
        self.assertEqual(len([i for i in inputs if "data-city" in i and 'autocomplete="off"' in i]), 3)
        for label in ("Ville", "Deuxième ville (facultatif)", "Troisième ville (facultatif)"):
            self.assertIn(f">{label}</label>", html)

    def test_consequence_under_the_distance(self):
        html = self.html("lieu")  # nothing saved yet: the preselected 30 km
        self.assertIn("Distance à vol d’oiseau. Les offres dont la commune n’est pas reconnue restent visibles.",
                      html)
        self.assertLess(html.index('name="distance"'), html.index("data-consequence"))
        self.assertLess(html.index("data-consequence"), html.index('name="remote"'))
        self.post("lieu", {"city_1": "Lyon", "distance": "region", "remote": "any"})
        html = self.html("lieu")
        self.assertIn(">Les offres dont la commune n’est pas reconnue restent visibles.<", html)

    def test_consequence_follows_the_screen(self):
        result = json.loads(self.client.post(f"{COUNTER}?step=lieu",
                                             {"city_1": "Lyon", "distance": "city", "remote": "any"}).content)
        self.assertEqual(result["consequence"], "Les offres dont la commune n’est pas reconnue restent visibles.")


class ContractsScreenTests(QuestionnaireTestCase):
    def checked(self, html):
        return set(re.findall(r'value="([a-z_]+)"[^>]*checked', html))

    def test_culture_prechecks(self):
        self.walk(CULTURE, until="lieu")
        self.assertEqual(self.checked(self.html("contrats")), {"cdi", "cdd", "cdd_projet", "fonction_publique"})

    def test_prechecks_follow_the_offer_kind(self):
        for search in (GROWTH["recherche"], OTHER_FULL["recherche"]):
            for kind, expected in (("job", {"cdi", "cdd"}), ("mission", {"freelance"}),
                                   ("both", {"cdi", "cdd", "freelance"})):
                with self.subTest(search=search, kind=kind):
                    self.post("recherche", {**search, "offer_kind": kind, "career_change": "no"})
                    self.post("lieu", CULTURE["lieu"])
                    self.assertEqual(self.checked(self.html("contrats")), expected)

    def test_at_least_one(self):
        self.walk(CULTURE, until="lieu")
        self.assertIn("contracts", self.errors("contrats", {}))

    def test_every_contract_is_offered_to_everyone(self):
        self.walk(CULTURE, until="lieu")
        html = self.html("contrats")
        for code in criteria.CONTRACTS:
            self.assertIn(f'value="{code}"', html)
        self.assertIn("contracts", self.errors("contrats", {"contracts": ["bogus"]}))
        self.post("contrats", {"contracts": ["stage", "freelance", "cdi"]})
        self.assertEqual(self.profile.hard_criteria["contracts"]["values"], ["cdi", "freelance", "stage"])

    def test_interim_is_offered_unchecked(self):
        self.walk(CULTURE, until="lieu")
        html = self.html("contrats")
        self.assertIn(">Intérim<", html)
        self.assertNotIn("interim", self.checked(html))
        self.post("contrats", {"contracts": ["interim", "cdd"]})
        self.assertEqual(self.profile.hard_criteria["contracts"]["values"], ["cdd", "interim"])


class KeywordsScreenTests(QuestionnaireTestCase):
    def setUp(self):
        super().setUp()
        self.walk(CULTURE, until="contrats")

    def test_empty_is_an_answer(self):
        self.assertRedirects(self.post("mots-cles", {}), url("remuneration"), fetch_redirect_response=False)
        self.assertEqual(self.profile.hard_criteria["keywords"], {"include": [], "exclude": [], "strict": True})
        self.assertEqual(self.profile.onboarding_step, "mots-cles")

    def test_words_are_split_on_commas_and_cleaned(self):
        self.post("mots-cles", {"include": " régie,  son , Régie ,,commercial   terrain", "exclude": "stage"})
        self.assertEqual(self.profile.hard_criteria["keywords"], {
            "include": ["régie", "son", "commercial terrain"], "exclude": ["stage"], "strict": True})

    def test_saved_words_come_back_in_the_fields(self):
        self.post("mots-cles", {"include": "régie, son", "exclude": "stage"})
        html = self.html("mots-cles")
        self.assertRegex(html, r'name="include"[^>]*value="régie, son"')
        self.assertRegex(html, r'name="exclude"[^>]*value="stage"')

    def test_the_screen_works_without_javascript(self):
        html = self.html("mots-cles")
        self.assertIn('<label class="field-label" for="id_include">Mots à exiger</label>', html)
        self.assertIn("écrivez aussi stagiaire si besoin", html)
        self.assertNotIn("<script", html.split("<main", 1)[1].split("</main>")[0])

    def test_refused_words(self):
        for field, value in (("include", "régie, 1234"), ("exclude", "a" * 41),
                             ("include", ",".join(f"mot{n}x" for n in range(11)))):
            with self.subTest(field=field, value=value[:20]):
                self.assertIn(field, self.errors("mots-cles", {field: value}))
        self.assertEqual(self.profile.onboarding_step, "contrats")

    def test_the_counter_previews_the_words(self):
        source = make_source()
        make_offer(source, "1", title="Stage de régie")
        make_offer(source, "2", title="Régisseur général")
        text = json.loads(self.client.post(f"{COUNTER}?step=mots-cles", {"exclude": "stage"}).content)["text"]
        self.assertTrue(text.startswith("Sur les 7 derniers jours, 1 offre passait ces filtres."), text)
        self.assertNotIn("keywords", self.profile.hard_criteria)


class PayScreenTests(QuestionnaireTestCase):
    def setUp(self):
        super().setUp()
        self.walk(CULTURE, until="mots-cles")

    def test_bounds_by_unit(self):
        for floor, unit in (("4000", "year"), ("250000", "year"), ("300", "month"), ("25000", "month")):
            with self.subTest(floor=floor, unit=unit):
                self.assertIn("floor", self.errors("remuneration", {"floor": floor, "unit": unit}))
        for floor, unit in (("5000", "year"), ("200000", "year"), ("500", "month"), ("20000", "month")):
            with self.subTest(floor=floor, unit=unit):
                self.assertEqual(self.post("remuneration", {"floor": floor, "unit": unit}).status_code, 302)

    def test_amount_required_unless_no_floor(self):
        self.assertIn("floor", self.errors("remuneration", {"unit": "year"}))
        self.post("remuneration", {"floor": "99", "unit": "year", "no_floor": "on"})
        self.assertEqual(self.profile.hard_criteria["pay"], {"floor": None, "unit": "year", "strict": True})

    def test_day_rate_only_for_missions(self):
        self.assertNotIn('value="day"', self.html("remuneration"))
        self.assertIn("unit", self.errors("remuneration", {"floor": "400", "unit": "day"}))

    def test_day_rate_bounds(self):
        self.post("recherche", {**GROWTH["recherche"], "offer_kind": "both", "career_change": "no"})
        self.post("contrats", {"contracts": ["cdi"]})
        self.assertIn('value="day"', self.html("remuneration"))
        self.assertIn("floor", self.errors("remuneration", {"floor": "40", "unit": "day"}))
        self.assertIn("floor", self.errors("remuneration", {"floor": "2500", "unit": "day"}))
        self.assertEqual(self.post("remuneration", {"floor": "400", "unit": "day"}).status_code, 302)

    def test_consequence_of_the_saved_floor(self):
        self.post("remuneration", {"floor": "24000", "unit": "year"})
        self.assertIn("Les offres sous 24\u202f000\u00a0€ brut par an ne vous seront pas montrées.",
                      self.html("remuneration"))
        self.post("remuneration", {"floor": "24000", "unit": "year", "preference": "on"})
        self.assertIn("seront moins bien notées.", self.html("remuneration"))
        self.post("remuneration", {"unit": "year", "no_floor": "on"})
        self.assertIn("Aucune offre n’est écartée pour son salaire.", self.html("remuneration"))

    def test_amount_field_is_a_short_number(self):
        html = self.html("remuneration")
        self.assertRegex(html, r'<input type="number" name="floor"[^>]*inputmode="numeric"')
        self.assertRegex(html, r'name="floor"[^>]*class="[^"]*input--short')


class ProgressAndFileTests(QuestionnaireTestCase):
    def test_progress_names_the_blocks_without_numbers(self):
        self.walk(CULTURE, until="recherche")
        html = self.html("lieu")
        progress = html[html.index('class="progress"'):html.index("</nav>")]
        self.assertRegex(progress,
                         r'data-tone="pink" aria-current="step"><span class="progress__name">Ce que vous excluez')
        self.assertIn("Ce que vous préférez", progress)
        self.assertIn("En vos mots", progress)
        self.assertIn("Encore ", progress)
        blocks = progress[:progress.index("</ol>")]
        self.assertNotRegex(re.sub(r"<[^>]+>", "", blocks), r"[0-9]")

    def test_search_screen_has_no_current_block_and_no_file(self):
        html = self.html("recherche")
        self.assertNotIn('aria-current="step"', html)
        self.assertNotIn("data-drawer", html)
        self.assertIn("Encore 7 minutes environ", html)

    def test_last_screens_count_the_summary(self):
        self.walk(CULTURE_FULL, until="mots")
        self.assertIn("Encore 2 minutes environ", self.html("cv"))

    def test_file_shows_one_row_per_validated_screen(self):
        self.walk(CULTURE, until="mots-cles")
        html = self.html("remuneration")
        drawer = html[html.index("data-drawer"):]
        self.assertIn("Votre fichier", drawer)
        self.assertIn("1 fiche", drawer)
        self.assertIn("card card--pink", drawer)
        self.assertIn("Lyon, jusqu’à 30 km, télétravail : hybride", drawer)
        self.assertIn("CDI, CDD", drawer)
        self.assertNotIn("Rémunération", drawer)

    def test_block_done_gets_a_check_and_its_card_is_filed_once(self):
        self.walk(CULTURE)
        html = self.html("valeurs")
        self.assertIn("card card--pink card--filed", html)
        self.assertIn("progress__block--done", html)
        self.assertNotIn("card--filed", self.html("valeurs"))

    def test_card_is_not_filed_before_the_block_ends(self):
        self.walk(CULTURE, until="temps")
        self.assertNotIn("card--filed", self.html("experience"))


class CounterTests(QuestionnaireTestCase):
    def setUp(self):
        super().setUp()
        self.walk(CULTURE, until="recherche")

    def counter(self, step, **data):
        response = self.client.post(f"{COUNTER}?step={step}", data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        return json.loads(response.content)

    def test_hidden_without_offers(self):
        html = self.html("lieu")
        self.assertNotIn("data-counter ", html)
        self.assertNotIn("data-counter-summary", html)
        self.assertNotIn("card__counter", html)
        self.assertIsNone(self.counter(step="lieu")["text"])

    def test_shown_on_the_pink_card_with_offers(self):
        source = make_source()
        make_offer(source, "1", insee="69123")
        make_offer(source, "2", insee="75056")
        html = self.html("lieu")
        self.assertIn(">Sur les 7 derniers jours, 2 offres passaient ces filtres. C’est peu : élargir la zone ou "
                      "les contrats en trouvera davantage.<", html)
        self.assertIn('data-counter-url="/questionnaire/compteur/?step=lieu"', html)
        self.assertRegex(html, r'class="card__counter" data-counter aria-hidden="true">'
                               r'Sur les 7 derniers jours, 2 offres passaient')

    def test_json_merges_the_unsaved_screen_with_saved_criteria(self):
        source = make_source()
        make_offer(source, "1", insee="69123", contract_type="CDI")
        make_offer(source, "2", insee="69266", contract_type="CDI")
        make_offer(source, "3", insee="69123", contract_type="STAGE")
        make_offer(source, "4", insee="75056", contract_type="CDI")
        self.post("lieu", CULTURE["lieu"])
        self.post("contrats", {"contracts": ["cdi"]})
        self.assertTrue(self.counter(step="lieu")["text"].startswith(
            "Sur les 7 derniers jours, 2 offres passaient ces filtres."))
        result = self.counter(step="lieu", city_1="Lyon", distance="city", remote="any")
        self.assertTrue(result["text"].startswith("Sur les 7 derniers jours, 1 offre passait ces filtres."))
        self.assertEqual(self.profile.hard_criteria["location"]["distance"], "30")

    def test_invalid_screen_data_falls_back_to_saved_criteria(self):
        source = make_source()
        for number in range(5):
            make_offer(source, str(number))
        self.assertEqual(self.counter(step="lieu", remote="bogus")["text"], "Hier, 5 offres passaient ces filtres.")

    def test_pay_consequence_follows_the_screen(self):
        self.walk(CULTURE, until="mots-cles")
        result = self.counter(step="remuneration", floor="30000", unit="year")
        self.assertIn("30\u202f000", result["consequence"])

    def test_screen_that_cannot_be_opened_or_unknown(self):
        self.assertEqual(self.client.post(f"{COUNTER}?step=temps").status_code, 404)
        self.assertEqual(self.client.post(f"{COUNTER}?step=recherche").status_code, 404)
        self.assertEqual(self.client.post(COUNTER).status_code, 404)

    def test_no_counter_outside_the_exclusions(self):
        self.walk(CULTURE_FULL)
        for slug in CULTURE_REST:
            with self.subTest(slug=slug):
                self.assertEqual(self.client.post(f"{COUNTER}?step={slug}").status_code, 404)
                self.assertNotIn("data-counter-url", self.html(slug))

    def test_get_is_refused_so_answers_stay_out_of_access_logs(self):
        self.assertEqual(self.client.get(COUNTER, {"step": "lieu", "city_1": "Lyon"}).status_code, 405)

    def test_post_needs_the_csrf_token_header(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        client.get(url("lieu"))
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.post(f"{COUNTER}?step=lieu", {"distance": "30"}).status_code, 403)
        response = client.post(f"{COUNTER}?step=lieu", {"distance": "30"}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)

    def test_counter_counts_only_offers_for_the_domains(self):
        make_offer(make_source(domains=["E", "K", "L"]), "1")
        make_offer(make_source(name="Welcome", domains=["D", "M17"]), "2")
        make_offer(make_source(name="Toutes"), "3")
        make_offer(make_source(name="Toutes métiers"), "4", rome_code="M1805")
        self.assertTrue(self.counter(step="lieu")["text"].startswith(
            "Sur les 7 derniers jours, 2 offres passaient ces filtres."))

    def test_phones_get_the_counter_in_the_folded_band_and_only_one_live_region(self):
        make_offer(make_source(), "1")
        html = self.html("lieu")
        summary = html[html.index("<summary"):html.index("</summary>")]
        self.assertRegex(summary, r'data-counter-summary[^>]*>Sur les 7 derniers jours, 1 offre passait ces filtres\.')
        self.assertEqual(html.count("aria-live"), 1 + html.count("data-consequence"))
        self.assertIn('aria-live="polite"', summary)
        self.assertRegex(html, r'class="card__counter" data-counter aria-hidden="true"')

    def test_file_comes_before_the_question_in_the_page(self):
        self.walk(CULTURE, until="lieu")
        html = self.html("contrats")
        self.assertLess(html.index("data-drawer"), html.index('<form class="question"'))


@override_settings(STORAGES=PLAIN_STATIC)
class PrivacyTests(TestCase):
    def test_privacy_page_names_the_communes_service(self):
        response = self.client.get("/confidentialite/")
        self.assertContains(response, "Les villes que vous tapez sont envoyées au service public geo.api.gouv.fr "
                                      "pour l’autocomplétion.")


class HouseStyleTests(QuestionnaireTestCase):
    def test_titles_end_like_the_other_pages(self):
        self.walk(CULTURE_FULL)
        for path in [url(slug) for slug in CULTURE_FULL] + [SUMMARY]:
            title = re.search(r"<title>(.*?)</title>", self.client.get(path).content.decode()).group(1)
            self.assertTrue(title.endswith(", tri-offres"), title)

    def check(self, html):
        assert_french_copy(self, html)
        assert_unique_ids(self, html)

    def test_every_screen_for_every_kind_of_profile(self):
        for answers in (CULTURE_FULL, GROWTH_FULL, OTHER_FULL):
            for slug, data in answers.items():
                with self.subTest(search=answers["recherche"]["job_1"], slug=slug):
                    self.check(self.html(slug))
                    self.post(slug, data)
            self.check(self.client.get(SUMMARY).content.decode())

    def test_every_screen_with_errors(self):
        self.walk(GROWTH_FULL)
        for slug in GROWTH_FULL:
            with self.subTest(slug=slug):
                response = self.post(slug, {"job_1": "1234", "career_change": "yes", "city_1": "",
                                            "floor": "1", "unit": "year", "include": "1234", "structures": ["bogus"],
                                            "seek_text": "x" * 1001})
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                self.assertIn("error-summary", html)
                self.check(html)

    def test_every_group_is_a_fieldset_with_a_legend(self):
        self.walk(GROWTH_FULL)
        for slug in GROWTH_FULL:
            html = self.html(slug)
            groups = html.count('<div class="options"') + html.count('<div class="chips"')
            self.assertEqual(groups, html.count("<legend"), slug)


class JobSuggestionsTests(QuestionnaireTestCase):
    """GET /questionnaire/postes/?q=: the ROME job titles offered while a poste is typed."""

    URL = "/questionnaire/postes/"

    def suggestions(self, query):
        response = self.client.get(self.URL, {"q": query})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        return json.loads(response.content)["suggestions"]

    def test_signed_in_people_only(self):
        self.client.logout()
        response = self.client.get(self.URL, {"q": "comptable"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/?"), response["Location"])

    def test_suggestions_from_the_rome_titles(self):
        self.assertIn("Infirmier général", self.suggestions("infirm"))
        self.assertEqual(len(self.suggestions("marketing")), rome_titles.SUGGESTIONS)

    def test_the_query_is_cut_at_sixty_characters(self):
        # The word after the 60th character is never read: the query still finds the nurses.
        self.assertIn("Infirmier général", self.suggestions("infirm".ljust(60) + "zzz"))
        self.assertEqual(self.suggestions("x" * 10_000), [])

    def test_post_is_refused(self):
        self.assertEqual(self.client.post(self.URL, {"q": "comptable"}).status_code, 405)
