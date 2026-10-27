"""Migration 0008: the segment of each account becomes ROME domains on its profile."""
from config.testing import MigrationTestCase

BEFORE = [("accounts", "0007_profile_summary")]
AFTER = [("accounts", "0008_profile_domains")]
LOCATION = {"cities": [{"name": "Lyon", "insee": "69123", "department": "69", "region": "84"}],
            "distance": "30", "remote": "hybrid", "strict": True}


class DomainsMigrationTests(MigrationTestCase):
    migrate_from = BEFORE
    migrate_to = AFTER

    def setUpBeforeMigration(self, apps):
        User, Profile = apps.get_model("accounts", "User"), apps.get_model("accounts", "Profile")

        def profile(email, segment, **fields):
            user = User.objects.create(email=email, segment=segment)
            return Profile.objects.create(user=user, **fields).pk

        self.culture = profile(
            "nora@example.fr", "culture", onboarding_step="cv",
            hard_criteria={"version": 1, "location": LOCATION},
            weighted_preferences={"version": 1, "values": ["meaning"], "jobs": ["mediation", {"custom": "Régisseuse"}],
                                  "structures": ["school", "publisher", "association", "museum", "local_authority"]},
            summary_sentences=[{"text": "Vous cherchez dans la culture.", "sources": ["segment", "values"]}],
            summary_questions=[{"text": "Plutôt l’édition ?", "sources": ["segment"], "answer": ""}])
        self.growth = profile(
            "lea@example.fr", "growth", onboarding_step="lieu", hard_criteria={"version": 1, "offer_kind": "mission"},
            weighted_preferences={"version": 1, "structures": ["agency", "early_startup", "saas", "scaleup"]})
        self.culture_both = profile("noa@example.fr", "culture", onboarding_step="recherche",
                                    hard_criteria={"version": 1, "offer_kind": "both"})
        self.not_started = profile("sam@example.fr", "growth")
        # Values the application reads as empty (views._profile): the migration leaves them as they are.
        self.odd = profile("odd@example.fr", "culture", onboarding_step="lieu", hard_criteria=["x"],
                           weighted_preferences="x", summary_sentences=[{"text": "Sans source."}, "x"],
                           summary_questions="x")

    def get(self, pk):
        return self.apps.get_model("accounts", "Profile").objects.get(pk=pk)

    def test_culture_accounts(self):
        profile = self.get(self.culture)
        self.assertEqual(profile.domains, ["E", "K", "L"])
        self.assertEqual(profile.hard_criteria, {"version": 1, "offer_kind": "job", "location": LOCATION})
        self.assertEqual(profile.weighted_preferences, {
            "version": 1, "values": ["meaning"], "jobs": ["mediation", {"custom": "Régisseuse"}],
            "structures": ["association_ess", "culture", "public_sector", "education"]})
        self.assertEqual(profile.summary_sentences,
                         [{"text": "Vous cherchez dans la culture.", "sources": ["domains", "values"]}])
        self.assertEqual(profile.summary_questions,
                         [{"text": "Plutôt l’édition ?", "sources": ["domains"], "answer": ""}])

    def test_a_culture_offer_kind_already_there_is_kept(self):
        self.assertEqual(self.get(self.culture_both).hard_criteria, {"version": 1, "offer_kind": "both"})

    def test_growth_accounts(self):
        profile = self.get(self.growth)
        self.assertEqual(profile.domains, ["D", "M17"])
        self.assertEqual(profile.hard_criteria, {"version": 1, "offer_kind": "mission"})
        self.assertEqual(profile.weighted_preferences, {"version": 1, "structures": ["company", "startup"]})

    def test_profiles_without_a_validated_screen_get_no_domain(self):
        profile = self.get(self.not_started)
        self.assertEqual(profile.domains, [])
        self.assertEqual(profile.hard_criteria, {})

    def test_odd_stored_values_do_not_stop_the_migration(self):
        profile = self.get(self.odd)
        self.assertEqual(profile.domains, ["E", "K", "L"])
        self.assertEqual(profile.hard_criteria, ["x"])
        self.assertEqual(profile.weighted_preferences, "x")
        self.assertEqual(profile.summary_sentences, [{"text": "Sans source."}, "x"])
        self.assertEqual(profile.summary_questions, "x")

    def test_the_segment_is_gone(self):
        fields = {field.name for field in self.apps.get_model("accounts", "User")._meta.get_fields()}
        self.assertNotIn("segment", fields)

    def test_backwards_rebuilds_the_segment(self):
        apps = self.migrate(BEFORE)
        segments = dict(apps.get_model("accounts", "User").objects.values_list("email", "segment"))
        self.assertEqual(segments, {"nora@example.fr": "culture", "lea@example.fr": "growth",
                                    "noa@example.fr": "culture", "sam@example.fr": "culture",
                                    "odd@example.fr": "culture"})
