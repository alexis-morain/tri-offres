from django.test import SimpleTestCase

from accounts.models import Profile
from onboarding import flow

SLUGS = ["recherche", "lieu", "contrats", "mots-cles", "remuneration", "temps", "experience", "valeurs", "metiers",
         "structures", "mots", "cv", "synthese"]


def profile_at(slug, career_change=False, jobs=None):
    preferences = {"version": 1, "jobs": jobs} if jobs else {"version": 1}
    return Profile(onboarding_step=slug, career_change=career_change, weighted_preferences=preferences)


class FlowTests(SimpleTestCase):
    def test_screen_order_and_blocks(self):
        self.assertEqual([step.slug for step in flow.STEPS], SLUGS)
        self.assertIsNone(flow.STEPS[0].block)
        self.assertEqual([step.block.slug for step in flow.STEPS[1:-1]],
                         ["exclude"] * 6 + ["prefer"] * 3 + ["words"] * 2)
        self.assertIsNone(flow.STEPS[-1].block)  # the summary, outside the blocks

    def test_blocks_are_described_once(self):
        self.assertEqual([(b.slug, b.name, b.tone) for b in flow.BLOCKS], [
            ("exclude", "Ce que vous excluez", "pink"),
            ("prefer", "Ce que vous préférez", "blue"),
            ("words", "En vos mots", "yellow"),
        ])

    def test_only_the_exclusions_carry_the_counter(self):
        self.assertEqual([block.has_counter for block in flow.BLOCKS], [True, False, False])

    def test_next_step_follows_the_last_validated_screen(self):
        self.assertEqual(flow.next_step(profile_at("")).slug, "recherche")
        self.assertEqual(flow.next_step(profile_at("recherche")).slug, "lieu")
        self.assertEqual(flow.next_step(profile_at("temps")).slug, "experience")
        self.assertEqual(flow.next_step(profile_at("experience")).slug, "valeurs")
        self.assertEqual(flow.next_step(profile_at("mots")).slug, "cv")
        self.assertEqual(flow.next_step(profile_at("cv")).slug, "synthese")
        self.assertIsNone(flow.next_step(profile_at("synthese")))

    def test_unknown_saved_step_starts_over(self):
        self.assertEqual(flow.next_step(profile_at("ancien-ecran")).slug, "recherche")

    def test_previous_step(self):
        self.assertIsNone(flow.previous_step("recherche", profile_at("")))
        self.assertEqual(flow.previous_step("lieu", profile_at("")).slug, "recherche")
        self.assertEqual(flow.previous_step("experience", profile_at("")).slug, "temps")

    def test_the_jobs_screen_only_for_accounts_with_job_chips(self):
        """The chips of « Les métiers que vous visez » left the questionnaire (plan of 26/10, 3.1): the screen stays
        for the accounts that picked some before, until they remove them."""
        typed = profile_at("valeurs", jobs=[{"custom": "Comptable"}])
        self.assertEqual(flow.next_step(typed).slug, "structures")
        self.assertFalse(flow.can_open(typed, "metiers"))
        self.assertTrue(flow.can_open(typed, "structures"))
        self.assertEqual(flow.previous_step("structures", typed).slug, "valeurs")
        self.assertEqual(flow.following_step("valeurs", typed).slug, "structures")
        chips = profile_at("valeurs", jobs=["mediation", {"custom": "Comptable"}])
        self.assertEqual(flow.next_step(chips).slug, "metiers")
        self.assertFalse(flow.can_open(chips, "structures"))
        self.assertEqual(flow.previous_step("structures", chips).slug, "metiers")
        self.assertEqual(flow.following_step("valeurs", chips).slug, "metiers")
        self.assertEqual([step.slug for step in flow.block_steps(flow.PREFER, typed)], ["valeurs", "structures"])
        self.assertEqual([step.slug for step in flow.block_steps(flow.PREFER, chips)],
                         ["valeurs", "metiers", "structures"])

    def test_can_open_validated_screens_and_the_next_one(self):
        profile = profile_at("lieu")
        for slug in ("recherche", "lieu", "contrats"):
            self.assertTrue(flow.can_open(profile, slug), slug)
        for slug in ("remuneration", "experience"):
            self.assertFalse(flow.can_open(profile, slug), slug)
        self.assertTrue(flow.can_open(profile_at(""), "recherche"))
        self.assertFalse(flow.can_open(profile_at(""), "lieu"))

    def test_minutes_left_counts_the_current_screen_and_rounds_up(self):
        profile = profile_at("")
        total = sum(step.seconds for step in flow.STEPS if step.slug != "metiers")
        self.assertEqual(flow.minutes_left("recherche", profile), -(-total // 60))
        self.assertEqual(flow.minutes_left("recherche", profile_at("", jobs=["mediation"])),
                         -(-(total + 30) // 60))
        last = flow.STEPS[-1].seconds
        self.assertEqual(flow.minutes_left("synthese", profile), -(-last // 60))
        self.assertEqual(flow.minutes_left("cv", profile), 2)
        self.assertEqual(flow.minutes_left("mots", profile), 3)

    def test_time_of_the_new_screens(self):
        self.assertEqual({step.slug: step.seconds for step in flow.STEPS[7:]},
                         {"valeurs": 20, "metiers": 30, "structures": 30, "mots": 60, "cv": 60, "synthese": 30})

    def test_time_of_the_search_screen(self):
        self.assertEqual(flow.get_step("recherche").seconds, 45)

    def test_time_left_text(self):
        profile = profile_at("")
        self.assertEqual(flow.time_left_text("synthese", profile), "Moins d’une minute")
        self.assertEqual(flow.time_left_text("cv", profile), "Encore 2 minutes environ")
        self.assertEqual(flow.time_left_text("mots", profile), "Encore 3 minutes environ")
        self.assertEqual(flow.time_left_text("recherche", profile), "Encore 7 minutes environ")

    def test_blocks_end_where_the_next_begins(self):
        self.assertEqual([step.slug for step in flow.STEPS if flow.ends_block(step)],
                         ["experience", "structures", "cv"])

    def test_career_change_rewords_the_jobs_screen_only(self):
        metiers = flow.get_step("metiers")
        self.assertEqual(flow.wording(metiers, profile_at("")), (metiers.title, metiers.why))
        self.assertEqual(flow.wording(metiers, profile_at("", career_change=True)), (
            "Les métiers qui vous attirent, même sans expérience directe",
            "Je garderai aussi une ou deux offres d’exploration par mail."))
        valeurs = flow.get_step("valeurs")
        self.assertEqual(flow.wording(valeurs, profile_at("", career_change=True)), (valeurs.title, valeurs.why))

    def test_first_missing_reads_each_screen_in_its_own_store(self):
        criteria = {"version": 1, "offer_kind": "job",
                    **{key: {} for key in ("location", "contracts", "keywords", "pay", "hours", "experience")}}
        profile = Profile(onboarding_step="mots", hard_criteria=criteria, domains=["E"],
                          weighted_preferences={"version": 1, "jobs": ["mediation"], "structures": []})
        self.assertEqual(flow.first_missing(profile).slug, "valeurs")
        profile.weighted_preferences["values"] = ["team"]
        self.assertIsNone(flow.first_missing(profile))

    def test_the_search_screen_is_missing_without_domains_or_offer_kind(self):
        for domains, criteria in (([], {"version": 1, "offer_kind": "job"}), (["E"], {"version": 1})):
            with self.subTest(domains=domains, criteria=criteria):
                profile = Profile(onboarding_step="lieu", domains=domains, hard_criteria=criteria)
                self.assertEqual(flow.first_missing(profile).slug, "recherche")
                self.assertTrue(flow.rewind(profile))
                self.assertEqual(profile.onboarding_step, "")
