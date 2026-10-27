"""The offers of the morning mail (sub-step 6.4): matching/delivery.py. Offers are fictitious."""
from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.testing import make_profile
from jobs.testing import OfferMaker, make_source
from matching import delivery
from matching.models import Feedback, Score, ScoringRun

TO_REVIEW, MAYBE = Score.Category.TO_REVIEW, Score.Category.MAYBE


class DeliveryTestCase(OfferMaker, TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.source = make_source()
        self.user = make_profile("nora@example.fr").user

    def scored(self, score=7, category=TO_REVIEW, user=None, exploration=False, **fields):
        offer = self.offer(**fields)
        Score.objects.create(user=user or self.user, offer=offer, score=score, category=category,
                             is_exploration=exploration)
        return offer

    def picked(self):
        found = delivery.pick(self.user, self.now)
        return ([score.offer_id for score in found["to_review"]], [score.offer_id for score in found["maybe"]],
                found["side_track"] and found["side_track"].offer_id)


class PickTests(DeliveryTestCase):
    def test_sections_follow_the_category(self):
        a, b = self.scored(8), self.scored(5, MAYBE)
        self.assertEqual(self.picked(), ([a.pk], [b.pk], None))

    def test_threshold_is_the_deck_one(self):
        kept = self.scored(5)
        self.scored(4, MAYBE)
        self.assertEqual(self.picked(), ([kept.pk], [], None))

    def test_sent_judged_and_expired_are_left_out(self):
        sent = self.scored()
        Score.objects.filter(offer=sent).update(sent_on=date(2026, 10, 1))
        judged = self.scored()
        Feedback.objects.create(user=self.user, offer=judged, verdict=Feedback.Verdict.NO)
        self.scored(expires_at=self.now - timedelta(minutes=1))
        undone = self.scored()
        Feedback.objects.create(user=self.user, offer=undone, verdict=Feedback.Verdict.NO, cancelled=True)
        self.assertEqual(self.picked(), ([undone.pk], [], None))

    def test_other_people_scores_are_left_out(self):
        self.scored(user=make_profile("paul@example.fr").user)
        self.assertEqual(self.picked(), ([], [], None))

    def test_best_exploration_goes_aside_and_alone(self):
        self.scored(6, exploration=True)
        best = self.scored(9, exploration=True)
        self.assertEqual(self.picked()[2], best.pk)
        self.assertEqual(len(self.picked()[0]), 1)
        self.assertNotIn(best.pk, self.picked()[0])

    def test_cap_keeps_the_best(self):
        top = self.scored(9)
        self.scored(5, MAYBE)
        for _ in range(delivery.MAIL_MAX - 1):
            self.scored(6)
        to_review, maybe, _ = self.picked()
        self.assertEqual(len(to_review) + len(maybe), delivery.MAIL_MAX)
        self.assertEqual(to_review[0], top.pk)
        self.assertEqual(maybe, [])

    def test_offer_older_than_the_sort_page_window_is_left_out(self):
        self.scored(ago=delivery.deck.WINDOW + timedelta(days=1))
        recent = self.scored(ago=delivery.deck.WINDOW - timedelta(days=1))
        self.assertEqual(self.picked(), ([recent.pk], [], None))

    def test_equal_scores_most_recent_offer_first(self):
        old = self.scored(7, ago=timedelta(hours=30))
        recent = self.scored(7, ago=timedelta(hours=2))
        self.assertEqual(self.picked()[0], [recent.pk, old.pk])


class ReadTonightTests(DeliveryTestCase):
    def test_none_without_run(self):
        self.assertEqual(delivery.read_tonight(self.user), 0)

    def test_latest_night_of_the_person(self):
        other = make_profile("paul@example.fr").user
        ScoringRun.objects.create(user=self.user, kind=ScoringRun.Kind.NIGHT, offers_scored=3,
                                  started_at=self.now - timedelta(days=1))
        ScoringRun.objects.create(user=self.user, kind=ScoringRun.Kind.NIGHT, offers_scored=12,
                                  started_at=self.now - timedelta(hours=5))
        ScoringRun.objects.create(user=self.user, kind=ScoringRun.Kind.ON_DEMAND, offers_scored=99)
        ScoringRun.objects.create(user=other, kind=ScoringRun.Kind.NIGHT, offers_scored=50)
        self.assertEqual(delivery.read_tonight(self.user), 12)


class CardTests(DeliveryTestCase):
    def test_card_carries_the_source_and_a_checked_link(self):
        self.scored(7, url="javascript:alert(1)")
        score = delivery.pick(self.user, self.now)["to_review"][0]
        self.assertEqual(delivery.card(score)["link"], "")
        self.assertEqual(delivery.card(score)["source"], "Profession Spectacle")
        score.offer.url = "https://example.fr/offre"
        self.assertEqual(delivery.card(score)["link"], "https://example.fr/offre")
