"""`manage.py essai_notation` (the design plan), with the fake engine: no network.

The database of the tests lives in memory: each test says which name the command reads, a throwaway one under /tmp
unless it checks the refusal.
"""
import re
import tempfile
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

import llm
from accounts.models import Profile, User
from accounts.testing import make_profile
from config.testing import assert_french_copy
from jobs import summaries
from jobs.filters import passing_offers
from jobs.models import JobOffer
from jobs.rome_titles import code_for_appellation
from jobs.testing import make_offer, make_source
from llm import fake
from matching import scoring
from matching.management.commands import essai_notation
from matching.models import Score, ScoringRun
from matching.testing import REASON, SUMMARY, scores_answer, size, summaries_answer
from onboarding import criteria, preferences, rome

EMAILS = ["essai+culture@example.org", "essai+info@example.org", "essai+sante@example.org"]
SCRATCH = "/tmp/tri-offres-essai.sqlite3"
NURSE = "infirmier en soins généraux"
CULTURE_TITLES = ["Médiatrice culturelle, note 6", "Chargée de production, note 9", "Agent d’accueil, note 3"]


def scratch(name=SCRATCH):
    """The database name the command reads, without touching the test connection's."""
    return mock.patch.dict(settings.DATABASES["default"], {"NAME": name})


def graded_answer(call):
    """A score read from each offer's title (« note 9 »), the offer noted 6 flagged as exploration."""
    notes = []
    for index, block in re.findall(r'<offre id="(\d+)">(.*?)</offre>', call.user_content, re.S):
        found = re.search(r"note (\d+)", block)
        score = int(found.group(1)) if found else 7
        notes.append({"id": int(index), "score": score, "reason": REASON, "exploration": score == 6})
    return {"notes": notes}


def nurse_answer(call):
    """Summaries; the appellation of a nurse for the offers of the health source."""
    answer = summaries_answer(call)
    for item, block in zip(answer["offres"], re.findall(r"<offre id=.*?</offre>", call.user_content, re.S)):
        if "Clinique" in block:
            item["appellation"] = NURSE
    return answer


@override_settings(DEV_EMAILS=["dev@example.org", *EMAILS])
class EssaiNotationTestCase(TestCase):
    def setUp(self):
        fake.reset()
        self.addCleanup(fake.reset)
        fake.set_response(summaries.TASK, summaries_answer)
        fake.set_response(scoring.TASK, scores_answer)
        self.now = timezone.now()
        self.count = 0

    def offer(self, source, **fields):
        self.count += 1
        return make_offer(source, external_id=str(self.count), collected_at=self.now - timedelta(hours=2),
                          full_text="Texte fictif de l’offre.", **fields)

    def offers_for_each_profile(self):
        """One offer for the culture profile in Lyon, one for the IT one in Paris, one for the health one in
        Nantes; each source serves one profile only."""
        culture = make_source(name="Profession Spectacle", domains=["E"])
        it = make_source(name="Welcome to the Jungle", domains=["M18"])
        health = make_source(name="Staffsanté", domains=["J"])
        return {
            "culture": self.offer(culture, title="Médiatrice culturelle", employer="Musée de l’Exemple",
                                  city="Lyon", insee="69123", contract_type="CDI"),
            "info": self.offer(it, title="Développeur web Python", employer="Atelier Logiciel", city="Paris",
                               insee="75056", contract_type="CDI"),
            "sante": self.offer(health, title="Infirmière en soins généraux", employer="Clinique du Parc",
                                city="Nantes", insee="44109", contract_type="CDD"),
        }

    def essai(self, *args, name=SCRATCH):
        """Run the command; (output, error or None). The output's no-break spaces read as spaces."""
        out = StringIO()
        error = None
        with scratch(name):
            try:
                call_command("essai_notation", *args, stdout=out)
            except CommandError as raised:
                error = raised
        return out.getvalue().replace(" ", " ").replace(" ", " "), error

    def nothing_written(self):
        self.assertFalse(User.objects.filter(email__in=EMAILS).exists())
        self.assertFalse(ScoringRun.objects.exists())
        self.assertFalse(Score.objects.exists())
        self.assertEqual(fake.calls, [])


class GuardTests(EssaiNotationTestCase):
    def test_project_database_refused(self):
        self.offers_for_each_profile()
        _, error = self.essai(name=settings.BASE_DIR / "data" / "tri-offres.sqlite3")
        self.assertIsNotNone(error)
        self.assertIn("/tmp", str(error))
        assert_french_copy(self, str(error))
        self.nothing_written()

    def test_paths_that_leave_the_temporary_folder_refused(self):
        for name in ("/tmp", "/tmp/../Users/base.sqlite3", ":memory:", "/var/lib/tri-offres.sqlite3"):
            with self.subTest(name=name):
                self.assertFalse(essai_notation.is_scratch_database(name))

    def test_temporary_folders_accepted(self):
        for name in (SCRATCH, f"{tempfile.gettempdir()}/essai.sqlite3"):
            with self.subTest(name=name):
                self.assertTrue(essai_notation.is_scratch_database(name))

    def test_alias_missing_from_dev_emails_refused(self):
        self.offers_for_each_profile()
        with override_settings(DEV_EMAILS=["dev@example.org", *EMAILS[:2]]):
            _, error = self.essai()
        self.assertIsNotNone(error)
        self.assertIn("essai+sante@example.org", str(error))
        assert_french_copy(self, str(error))
        self.nothing_written()

    def test_database_without_offer_refused(self):
        _, error = self.essai()
        self.assertIsNotNone(error)
        self.assertIn("collecte", str(error))
        assert_french_copy(self, str(error))
        self.nothing_written()


class AccountsTests(EssaiNotationTestCase):
    def test_three_finished_profiles_written_once(self):
        offers = self.offers_for_each_profile()
        self.essai()
        self.essai()
        self.assertEqual(sorted(User.objects.values_list("email", flat=True)), EMAILS)
        self.assertEqual(Profile.objects.count(), 3)
        for user in User.objects.all():
            with self.subTest(email=user.email):
                self.assertEqual(user.status, User.Status.ACTIVE)
                self.assertTrue(user.is_active)
                profile = user.profile
                self.assertIsNotNone(profile.onboarding_completed_at)
                self.assertIsNotNone(profile.summary_validated_at)
                self.assertTrue(profile.summary_text.startswith("Vous cherchez"))
                assert_french_copy(self, profile.summary_text + profile.seek_text + profile.refuse_text)
                self.assertEqual(criteria.normalize(profile.hard_criteria), profile.hard_criteria)
                self.assertEqual(preferences.normalize(profile.weighted_preferences), profile.weighted_preferences)
                self.assertEqual(rome.normalize_domains(profile.domains), profile.domains)
                key = user.email.split("+")[1].split("@")[0]
                passing = passing_offers(criteria.normalize(profile.hard_criteria), self.now, profile.domains,
                                         since=self.now - timedelta(hours=48))
                self.assertEqual(list(passing), [offers[key]])

    def test_profiles_of_the_spec(self):
        self.offers_for_each_profile()
        self.essai()
        profiles = {profile.user.email: profile for profile in Profile.objects.select_related("user")}
        culture, info, sante = (profiles[email] for email in EMAILS)
        self.assertEqual(culture.domains, ["E", "L"])
        self.assertEqual(culture.hard_criteria["contracts"]["values"], ["cdi", "cdd", "cdd_projet"])
        self.assertEqual((culture.hard_criteria["location"]["cities"][0]["name"],
                          culture.hard_criteria["location"]["distance"]), ("Lyon", "30"))
        self.assertEqual(info.domains, ["M18"])
        self.assertEqual(info.hard_criteria["pay"], {"floor": 45000, "unit": "year", "strict": True})
        self.assertEqual(info.hard_criteria["location"]["distance"], "60")
        self.assertEqual(sante.domains, ["J"])
        self.assertEqual(sante.hard_criteria["contracts"]["values"], ["cdi", "cdd"])
        self.assertEqual(sante.hard_criteria["location"]["cities"][0]["name"], "Nantes")

    def test_no_summary_call(self):
        self.offers_for_each_profile()
        self.essai()
        self.assertEqual({call.task for call in fake.calls}, {summaries.TASK, scoring.TASK})


class RunTests(EssaiNotationTestCase):
    def test_night_for_the_three_accounts_only(self):
        offers = self.offers_for_each_profile()
        other = make_profile("nora@example.fr", domains=["E"])
        self.offer(make_source(name="Carenews", domains=["K"]), title="Coordinatrice associative")
        self.essai()
        self.assertEqual({call.user.email for call in fake.calls if call.task == scoring.TASK}, set(EMAILS))
        self.assertFalse(Score.objects.filter(user=other.user).exists())
        self.assertEqual([size(call) for call in fake.calls if call.task == summaries.TASK], [3])
        self.assertEqual(set(JobOffer.objects.filter(summary_written_at__isnull=False)),
                         set(offers.values()))

    def test_run_again_scores_nothing_new(self):
        self.offers_for_each_profile()
        self.essai()
        fake.calls.clear()
        output, error = self.essai()
        self.assertIsNone(error)
        self.assertEqual(fake.calls, [])
        self.assertEqual(Score.objects.count(), 3)
        self.assertIn(REASON, output)  # the scores already there are still shown

    def test_full_report(self):
        offers = self.offers_for_each_profile()
        culture = offers["culture"].source
        for title in CULTURE_TITLES:
            self.offer(culture, title=title, employer="Théâtre des Célestins", city="Lyon", insee="69123")
        fake.set_response(scoring.TASK, graded_answer)
        fake.set_response(summaries.TASK, nurse_answer)
        output, error = self.essai()
        self.assertIsNone(error)
        assert_french_copy(self, output)
        for heading in ("Pré-passe", "Culture", "Informatique", "Santé", "Coût", "Runs en échec"):
            self.assertIn(heading, output)
        self.assertIn("Offres résumées : 6", output)
        self.assertIn("Résumés refusés : 0", output)
        code = code_for_appellation(NURSE)
        self.assertIsNotNone(code)
        self.assertIn("Codes ROME trouvés par Claude : 1", output)
        self.assertIn("Codes ROME proposés par Claude : 1 appellation exacte (code de fiche non lu) ; codes de "
                      "fiche : 5 vides, 0 inexistant, 0 refusé par le contrôle des mots, 0 accepté.", output)
        self.assertIn("Offres sans code ROME : 5 sur 6 (83 %)", output)
        self.assertEqual(output.count(SUMMARY), 5)  # five summaries drawn
        self.assertIn(f"code ROME {code}", output)
        self.assertIn("Candidats : 4", output)
        self.assertIn("Notes : 4 (À regarder : 2, Peut-être : 1, Écartée : 1)", output)
        culture_part = output[output.index("== Culture"):output.index("== Informatique")]
        ranked = [culture_part.index(title) for title in
                  ("Chargée de production, note 9", "Médiatrice culturelle,", "Médiatrice culturelle, note 6",
                   "Agent d’accueil, note 3")]
        self.assertEqual(ranked, sorted(ranked))
        self.assertIn("9/10, À regarder : Chargée de production, note 9", culture_part)
        self.assertIn("6/10, Peut-être, piste à côté : Médiatrice culturelle, note 6, Théâtre des Célestins, Lyon, "
                      "Profession Spectacle", culture_part)
        self.assertIn(REASON, culture_part)
        self.assertIn("Aucun run en échec.", output)

    def test_cost(self):
        self.offers_for_each_profile()
        fake.set_usage(summaries.TASK, llm.Usage(model=llm.HAIKU, input_tokens=1_000_000, output_tokens=1_000_000))
        fake.set_usage(scoring.TASK, llm.Usage(model=llm.SONNET, input_tokens=1_000_000, output_tokens=100_000,
                                               cache_read_input_tokens=500_000,
                                               cache_creation_input_tokens=200_000))
        output, _ = self.essai()
        cost = output[output.index("== Coût"):]
        # Haiku: 0,10 + 0,50. Sonnet, three calls: 3 × (2 + 1 + 0,10 + 2 × 0,2 × 1,25).
        self.assertIn("Haiku 5.5 : 1 000 000 en entrée, 1 000 000 en sortie, 0 lu en cache, 0 écrit en cache, "
                      "0,60 $", cost)
        self.assertIn("Sonnet 5.5 : 3 000 000 en entrée, 300 000 en sortie, 1 500 000 lus en cache, 600 000 écrits "
                      "en cache, 10,80 $", cost)
        self.assertIn("Total : 11,40 $", cost)
        self.assertIn("Par personne : 3,80 $", cost)
        self.assertIn("Par offre notée : 3,8000 $", cost)  # four places: an offer costs a fraction of a cent
        self.assertIn("Coût de la notation : 3,60 $", output)

    def test_prices(self):
        self.assertEqual(essai_notation.cost(llm.SONNET, {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                                                          "cache_read_tokens": 1_000_000,
                                                          "cache_creation_tokens": 1_000_000}),
                         essai_notation.Decimal("14.70"))
        self.assertEqual(essai_notation.cost(llm.HAIKU, {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                                                         "cache_read_tokens": 0, "cache_creation_tokens": 1_000_000}),
                         essai_notation.Decimal("0.725"))

    def test_failed_runs_and_their_code(self):
        self.offers_for_each_profile()
        fake.set_response(scoring.TASK, llm.EngineRefused("refus"))
        output, error = self.essai()
        self.assertIsNone(error)
        failed = output[output.index("== Runs en échec"):]
        for email in EMAILS:
            key = User.objects.get(email=email).pk
            self.assertIn(f"compte {key} : En échec, 1 lot en échec, code refused", failed)
        assert_french_copy(self, output)


class ResetTests(EssaiNotationTestCase):
    def test_reset(self):
        offers = self.offers_for_each_profile()
        by_title = self.offer(offers["culture"].source, title="Régisseur, note 5", city="Lyon", insee="69123",
                              rome_code="L1508")
        fake.set_response(summaries.TASK, nurse_answer)
        self.essai()
        other = make_profile("nora@example.fr")
        Score.objects.create(user=other.user, offer=by_title, score=5, reason="Raison.", category="maybe")
        self.assertTrue(JobOffer.objects.filter(rome_by_claude=True).exists())
        fake.calls.clear()
        output, error = self.essai("--reset")
        self.assertIsNone(error)
        assert_french_copy(self, output)
        # Everything played again: the pre-pass and the three people.
        self.assertEqual([size(call) for call in fake.calls if call.task == summaries.TASK], [4])
        self.assertEqual(Score.objects.filter(user__email__in=EMAILS).count(), 4)
        self.assertTrue(Score.objects.filter(user=other.user).exists())

    def test_reset_clears_the_pre_pass_and_the_accounts_work(self):
        offers = self.offers_for_each_profile()
        by_title = self.offer(offers["culture"].source, title="Régisseur", city="Lyon", insee="69123",
                              rome_code="L1508")
        fake.set_response(summaries.TASK, nurse_answer)
        self.essai()
        other = make_profile("nora@example.fr")
        Score.objects.create(user=other.user, offer=by_title, score=5, reason="Raison.", category="maybe")
        with scratch():
            counts = essai_notation.reset(EMAILS)
        self.assertEqual(counts.scores, 4)
        self.assertFalse(Score.objects.filter(user__email__in=EMAILS).exists())
        self.assertFalse(ScoringRun.objects.filter(user__email__in=EMAILS).exists())
        self.assertTrue(Score.objects.filter(user=other.user).exists())
        self.assertFalse(JobOffer.objects.exclude(summary="").exists())
        self.assertFalse(JobOffer.objects.filter(summary_written_at__isnull=False).exists())
        self.assertFalse(JobOffer.objects.filter(rome_by_claude=True).exists())
        offers["sante"].refresh_from_db()
        self.assertEqual(offers["sante"].rome_code, "")
        by_title.refresh_from_db()
        self.assertEqual((by_title.rome_code, by_title.rome_by_claude), ("L1508", False))
