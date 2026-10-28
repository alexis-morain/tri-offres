from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from accounts.testing import make_profile
from jobs.models import JobOffer
from jobs.testing import OfferMaker, make_offer, make_source
from matching.models import Score
from matching.selection import (NIGHT_PER_PERSON, NIGHT_WINDOW, active_profiles, candidates, offers_to_summarize,
                                profile_criteria)

CDI_ONLY = {"version": 1, "contracts": {"values": ["cdi"], "strict": True}}
CDD_ONLY = {"version": 1, "contracts": {"values": ["cdd"], "strict": True}}


def set_user(profile, **fields):
    User.objects.filter(pk=profile.user_id).update(**fields)
    return profile


class SelectionTestCase(OfferMaker, TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.source = make_source(domains=["E", "K", "L"])

    def offer_defaults(self, number):
        return {"full_text": "Texte de l’offre."}

class ActiveProfilesTests(SelectionTestCase):
    def test_completed_profiles_of_mailable_accounts(self):
        active = make_profile("nora@example.fr")
        make_profile("lea@example.fr", onboarding_completed_at=None)
        set_user(make_profile("paul@example.fr"), status=User.Status.PAUSED)
        set_user(make_profile("zoe@example.fr"), status=User.Status.UNSUBSCRIBED)
        set_user(make_profile("max@example.fr"), is_active=False)
        self.assertEqual(list(active_profiles()), [active])


class OffersToSummarizeTests(SelectionTestCase):
    def test_no_active_profile_selects_nothing(self):
        self.offer()
        make_profile("lea@example.fr", onboarding_completed_at=None)
        self.assertEqual(list(offers_to_summarize(self.now)), [])

    def test_offer_passing_one_profile_of_two_selected_once(self):
        make_profile("nora@example.fr", hard_criteria=CDI_ONLY)
        make_profile("paul@example.fr", hard_criteria=CDD_ONLY)
        cdi = self.offer(contract_type=JobOffer.Contract.CDI)
        unknown = self.offer(contract_type="")  # passes both: selected once
        self.offer(contract_type=JobOffer.Contract.STAGE)  # passes no one
        self.assertCountEqual(list(offers_to_summarize(self.now)), [cdi, unknown])

    def test_offer_outside_every_domain_excluded(self):
        make_profile("nora@example.fr", domains=["J"])
        self.offer()
        self.assertEqual(list(offers_to_summarize(self.now)), [])

    def test_inactive_profiles_ignored(self):
        make_profile("nora@example.fr", onboarding_completed_at=None)
        set_user(make_profile("paul@example.fr"), status=User.Status.PAUSED)
        self.offer()
        self.assertEqual(list(offers_to_summarize(self.now)), [])

    def test_done_textless_expired_and_old_offers_excluded(self):
        make_profile("nora@example.fr")
        kept = self.offer(ago=timedelta(hours=24 * 6), expires_at=self.now + timedelta(days=1))
        self.offer(summary="Déjà résumée.", summary_written_at=self.now)
        self.offer(summary="", summary_written_at=self.now)  # summary refused: not sent again every night
        self.offer(full_text="")
        self.offer(expires_at=self.now - timedelta(minutes=1))
        self.offer(ago=timedelta(hours=24 * 7 + 1))
        self.assertEqual(list(offers_to_summarize(self.now)), [kept])

    def test_newest_first(self):
        make_profile("nora@example.fr")
        old = self.offer(ago=timedelta(hours=30))
        new = self.offer(ago=timedelta(hours=2))
        same_time_first = self.offer(ago=timedelta(hours=10))
        same_time_second = self.offer(ago=timedelta(hours=10))
        self.assertEqual(list(offers_to_summarize(self.now)), [new, same_time_second, same_time_first, old])

    def test_profiles_with_broken_criteria_ignored_and_logged(self):
        broken = make_profile("nora@example.fr", hard_criteria={"version": 1, "location": {"strict": True}})
        unknown = make_profile("lea@example.fr", hard_criteria={"version": 1, "inconnu": True})
        make_profile("paul@example.fr", hard_criteria=CDI_ONLY)
        cdi = self.offer(contract_type=JobOffer.Contract.CDI)
        self.offer(contract_type=JobOffer.Contract.STAGE)
        with self.assertLogs("matching.selection", "ERROR") as logs:
            offers = list(offers_to_summarize(self.now))
        self.assertEqual(offers, [cdi])
        self.assertEqual(len(logs.records), 2)
        for profile, line in zip((broken, unknown), logs.output):
            self.assertIn(f"account {profile.user_id}", line)
            self.assertIn("ValueError", line)

    def test_profile_criteria_normalized(self):
        profile = make_profile("nora@example.fr", hard_criteria={"contracts": {"values": ["cdi"], "strict": True}})
        self.assertEqual(profile_criteria(profile)["version"], 1)
        profile.hard_criteria = {"version": 1, "location": {"strict": True}}
        with self.assertRaises(ValueError):
            profile_criteria(profile)

    def test_given_profiles(self):
        nora = make_profile("nora@example.fr", domains=["J"])
        make_profile("paul@example.fr")
        self.offer()
        self.assertEqual(list(offers_to_summarize(self.now, profiles=[nora])), [])


class NightCandidatesTests(SelectionTestCase):
    def setUp(self):
        super().setUp()
        self.profile = make_profile("nora@example.fr")

    def score(self, offer, profile=None):
        return Score.objects.create(user=(profile or self.profile).user, offer=offer, score=7, reason="Bien.",
                                    category=Score.Category.TO_REVIEW)

    def candidates(self, window=NIGHT_WINDOW, limit=NIGHT_PER_PERSON):
        return candidates(self.profile, self.now, window, limit)

    def test_window_of_48_hours(self):
        kept = self.offer(ago=timedelta(hours=47))
        self.offer(ago=timedelta(hours=49))
        self.assertEqual(self.candidates(), [kept])

    def test_window_given_by_the_caller(self):
        kept = self.offer(ago=timedelta(hours=24 * 29))
        self.offer(ago=timedelta(hours=24 * 31))
        self.assertEqual(self.candidates(window=timedelta(days=30)), [kept])

    def test_scored_offer_excluded_for_this_person_only(self):
        scored = self.offer()
        other = make_profile("paul@example.fr")
        self.score(scored, other)
        self.assertEqual(self.candidates(), [scored])
        self.score(scored)
        self.assertEqual(self.candidates(), [])

    def test_expired_offer_excluded(self):
        kept = self.offer(expires_at=self.now + timedelta(hours=1))
        self.offer(expires_at=self.now - timedelta(minutes=1))
        self.assertEqual(self.candidates(), [kept])

    def test_criteria_and_domains_applied(self):
        self.profile.hard_criteria = CDI_ONLY
        self.profile.save()
        cdi = self.offer(contract_type=JobOffer.Contract.CDI)
        self.offer(contract_type=JobOffer.Contract.STAGE)
        elsewhere = make_source(name="Autre", domains=["J"])
        make_offer(elsewhere, external_id="j1", contract_type=JobOffer.Contract.CDI)
        self.assertEqual(self.candidates(), [cdi])

    def test_one_offer_per_match_key_the_newest(self):
        self.offer(ago=timedelta(hours=5), match_key="cle-a")
        newest = self.offer(ago=timedelta(hours=2), match_key="cle-a")
        no_key_old = self.offer(ago=timedelta(hours=6), match_key="")
        no_key_new = self.offer(ago=timedelta(hours=3), match_key="")
        self.assertEqual(self.candidates(), [newest, no_key_new, no_key_old])

    def test_match_key_already_scored_on_another_offer(self):
        scored = self.offer(ago=timedelta(hours=30), match_key="cle-a")
        self.score(scored)
        self.offer(ago=timedelta(hours=2), match_key="cle-a")
        kept = self.offer(ago=timedelta(hours=3), match_key="cle-b")
        self.assertEqual(self.candidates(), [kept])

    def test_cap_and_order(self):
        for index in range(NIGHT_PER_PERSON + 5):
            self.offer(ago=timedelta(hours=1 + index % 3))
        picked = self.candidates()
        self.assertEqual(len(picked), NIGHT_PER_PERSON)
        keys = [(-JobOffer.objects.get(pk=offer.pk).collected_at.timestamp(), -offer.pk) for offer in picked]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(self.candidates(limit=3)), 3)
        self.assertEqual(NIGHT_PER_PERSON, 40)


class CandidatesOrderTests(SelectionTestCase):
    """Offers with a ROME code first, then the others by closeness to the person's words, then the newest."""

    def setUp(self):
        super().setUp()
        # Its offers without code need a shared word; its coded ones are E codes, the profile's domain.
        self.source = make_source(name="Généraliste", domains=[])
        self.profile = make_profile("nora@example.fr", previous_job="Programmatrice de festival",
                                    weighted_preferences={"version": 1, "jobs": [{"custom": "Régisseur lumière"}]})

    def candidates(self, limit=NIGHT_PER_PERSON):
        return candidates(self.profile, self.now, NIGHT_WINDOW, limit)

    def test_coded_first_then_by_closeness_then_newest(self):
        nothing = self.offer(ago=timedelta(hours=1), title="Comptable")
        summary_only = self.offer(ago=timedelta(hours=2), title="Chargée d’accueil", summary="Accueil d’un festival.")
        title = self.offer(ago=timedelta(hours=3), title="Régisseuse lumière")
        coded_old = self.offer(ago=timedelta(hours=40), title="Développeur", rome_code="E1205")
        coded_new = self.offer(ago=timedelta(hours=20), title="Comptable", rome_code="E1101")
        self.assertEqual(self.candidates(), [coded_new, coded_old, title, summary_only])
        self.assertNotIn(nothing, self.candidates())

    def test_title_weighs_three_summary_one(self):
        twice_in_summary = self.offer(ago=timedelta(hours=1), title="Poste",
                                      summary="Lumière d’un festival.")
        in_title = self.offer(ago=timedelta(hours=2), title="Technicien lumière")
        self.assertEqual(self.candidates(), [in_title, twice_in_summary])

    def test_words_of_rome_labels_plural_and_case_folded(self):
        self.profile.weighted_preferences = {"version": 1, "jobs": ["M18"]}
        self.profile.seek_text = ""
        self.offer(ago=timedelta(hours=1), title="Comptable")
        close = self.offer(ago=timedelta(hours=2), title="Administrateur SYSTÈME")
        self.assertEqual(self.candidates(), [close])

    def test_short_and_empty_words_do_not_count(self):
        self.profile.weighted_preferences = {"version": 1}
        self.profile.previous_job = "Un poste dans une équipe, pour moi."
        newest = self.offer(ago=timedelta(hours=1), title="Comptable")
        same_words = self.offer(ago=timedelta(hours=2), title="Un poste pour moi dans une équipe")
        self.assertEqual(self.candidates(), [newest, same_words])

    def test_place_words_do_not_count(self):
        """The place is filtered already: the person's cities and words of place weigh nothing."""
        self.profile.weighted_preferences = {"version": 1}
        self.profile.previous_job = "Un lieu près de Nantes ou dans la région de Saint-Nazaire."
        self.profile.hard_criteria = {"version": 1, "location": {
            "cities": [{"name": "Saint-Nazaire", "insee": "44184", "department": "44", "region": "52"},
                       {"name": "Nantes", "insee": "44109", "department": "44", "region": "52"}],
            "distance": "60", "remote": "any", "strict": False}}
        newest = self.offer(ago=timedelta(hours=1), title="Comptable")
        place = self.offer(ago=timedelta(hours=2), title="Commercial Nantes, région Saint-Nazaire, lieu proche")
        self.assertEqual(self.candidates(), [newest, place])

    def test_no_code_and_no_shared_word_is_dropped(self):
        self.offer(ago=timedelta(hours=1), title="Comptable")
        close = self.offer(ago=timedelta(hours=30), title="Régisseur")
        in_summary = self.offer(ago=timedelta(hours=2), title="Poste", summary="Un festival.")
        self.assertEqual(self.candidates(), [close, in_summary])
        self.assertEqual(self.candidates(limit=1), [close])

    def test_offer_of_a_source_of_the_domain_without_shared_word_stays_last(self):
        """A source serving the person's domain vouches for its offers without code (plan of 28/10, chantier 2)."""
        niche = make_source(name="Profilculture", domains=["E", "K", "L"])
        self.offer(ago=timedelta(hours=1), title="Comptable")
        niche_old = self.offer(ago=timedelta(hours=5), title="Administrateur", source=niche)
        niche_new = self.offer(ago=timedelta(hours=1), title="Assistant technique", source=niche)
        close = self.offer(ago=timedelta(hours=30), title="Régisseur")
        coded = self.offer(ago=timedelta(hours=40), title="Comptable", rome_code="E1107")
        self.assertEqual(self.candidates(), [coded, close, niche_new, niche_old])
        self.assertEqual(self.candidates(limit=3), [coded, close, niche_new])

    def test_feminine_and_masculine_share_a_word(self):
        self.profile.weighted_preferences = {"version": 1, "jobs": [{"custom": "Infirmière"}]}
        self.profile.previous_job = ""
        self.offer(ago=timedelta(hours=1), title="Comptable")
        kept = self.offer(ago=timedelta(hours=2), title="INFIRMIER (F/H) - CMP")
        self.assertEqual(self.candidates(), [kept])

    def test_coded_offer_without_shared_word_stays(self):
        coded = self.offer(title="Comptable", rome_code="E1101")
        self.assertEqual(self.candidates(), [coded])

    def test_words_of_seek_text_do_not_count(self):
        """Free text brings common words (« service », « public »): only chosen and previous jobs give words."""
        self.profile.previous_job = ""
        self.profile.seek_text = "Un service public, plutôt souvent au contact des gens."
        self.offer(ago=timedelta(hours=1), title="Technicien de service public", summary="Contact des gens.")
        kept = self.offer(ago=timedelta(hours=2), title="Régisseur lumière")
        self.assertEqual(self.candidates(), [kept])

    def test_keywords_to_include_give_words(self):
        self.profile.weighted_preferences = {"version": 1}
        self.profile.previous_job = ""
        self.profile.hard_criteria = {"version": 1, "keywords": {"include": ["Sonorisation"], "exclude": ["stage"],
                                                                 "strict": True}}
        self.offer(ago=timedelta(hours=1), title="Comptable")
        self.offer(ago=timedelta(hours=1), title="Stage de régie")
        kept = self.offer(ago=timedelta(hours=2), title="Technicien sonorisation")
        self.assertEqual(self.candidates(), [kept])

    def test_previous_job_gives_words(self):
        self.profile.weighted_preferences = {"version": 1}
        self.profile.seek_text = ""
        self.profile.previous_job = "Professeure de lettres"
        self.offer(ago=timedelta(hours=1), title="Comptable")
        kept = self.offer(ago=timedelta(hours=2), title="Professeure remplaçante")
        self.assertEqual(self.candidates(), [kept])

    def test_no_word_at_all_keeps_every_offer_as_before(self):
        self.profile.weighted_preferences = {"version": 1}
        self.profile.seek_text = ""
        self.profile.previous_job = ""
        newest = self.offer(ago=timedelta(hours=1), title="Comptable")
        older = self.offer(ago=timedelta(hours=2), title="Régisseur")
        self.assertEqual(self.candidates(), [newest, older])

    def test_a_code_that_is_not_rome_counts_as_none(self):
        newest = self.offer(ago=timedelta(hours=1), title="Festival", rome_code="X")
        close = self.offer(ago=timedelta(hours=2), title="Régisseur lumière")
        self.assertEqual(self.candidates(), [close, newest])

    def test_one_offer_per_match_key_across_both_groups(self):
        coded = self.offer(ago=timedelta(hours=5), title="Régisseur", rome_code="E1107", match_key="cle-a")
        self.offer(ago=timedelta(hours=1), title="Régisseur", match_key="cle-a")
        other = self.offer(ago=timedelta(hours=2), title="Festival", match_key="cle-b")
        self.assertEqual(self.candidates(), [coded, other])

    def test_preferences_that_do_not_read_give_no_job_words(self):
        self.offer(ago=timedelta(hours=1), title="Comptable")
        self.offer(ago=timedelta(hours=2), title="Technicien lumière")
        festival = self.offer(ago=timedelta(hours=3), title="Festival")
        for weighted in ({"version": 1, "jobs": "régie"}, 5, {"jobs": [3, {"custom": 4}, "inconnu"]}):
            with self.subTest(weighted=weighted):
                self.profile.weighted_preferences = weighted
                self.assertEqual(self.candidates(), [festival])

    def test_offers_come_with_their_source(self):
        self.offer(title="Régisseur")
        self.offer(title="Comptable", rome_code="E1101")
        picked = self.candidates()
        with self.assertNumQueries(0):
            sources = [offer.source.name for offer in picked]
        self.assertEqual(len(sources), 2)

