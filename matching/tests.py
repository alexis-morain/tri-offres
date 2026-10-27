import datetime

from django.db import IntegrityError
from django.test import TestCase

from accounts.models import User
from jobs.testing import make_offer, make_source
from matching.models import Delivery, Feedback, Score


class MatchingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("nora@example.fr")
        self.offer = make_offer(make_source())


class ScoreTests(MatchingTestCase):
    def make_score(self, **kwargs):
        return Score.objects.create(user=self.user, offer=self.offer, **{"score": 7, "reason": "Bon métier",
                                                                         "category": Score.Category.TO_REVIEW,
                                                                         **kwargs})

    def test_bounds_accepted(self):
        self.make_score(score=0).delete()
        self.make_score(score=10)

    def test_score_above_ten_rejected(self):
        with self.assertRaises(IntegrityError):
            self.make_score(score=11)

    def test_negative_score_rejected(self):
        with self.assertRaises(IntegrityError):
            self.make_score(score=-1)

    def test_one_score_per_user_and_offer(self):
        self.make_score()
        with self.assertRaises(IntegrityError):
            self.make_score(score=3)


class FeedbackTests(MatchingTestCase):
    def give(self, verdict, cancelled=False):
        return Feedback.objects.create(user=self.user, offer=self.offer, verdict=verdict, cancelled=cancelled)

    def test_no_feedback_means_no_verdict(self):
        self.assertIsNone(Feedback.objects.current_verdict(self.user, self.offer))

    def test_latest_verdict_wins(self):
        self.give(Feedback.Verdict.NO)
        self.give(Feedback.Verdict.YES)
        self.assertEqual(Feedback.objects.current_verdict(self.user, self.offer), Feedback.Verdict.YES)

    def test_cancelled_rows_are_ignored(self):
        self.give(Feedback.Verdict.MAYBE)
        self.give(Feedback.Verdict.YES, cancelled=True)
        self.assertEqual(Feedback.objects.current_verdict(self.user, self.offer), Feedback.Verdict.MAYBE)

    def test_everything_cancelled_means_no_verdict(self):
        self.give(Feedback.Verdict.YES, cancelled=True)
        self.assertIsNone(Feedback.objects.current_verdict(self.user, self.offer))

    def test_other_users_feedback_is_ignored(self):
        other = User.objects.create_user("other@example.fr")
        Feedback.objects.create(user=other, offer=self.offer, verdict=Feedback.Verdict.NO)
        self.assertIsNone(Feedback.objects.current_verdict(self.user, self.offer))

    def test_unknown_verdict_rejected(self):
        with self.assertRaises(IntegrityError):
            self.give("perhaps")


class DeliveryTests(MatchingTestCase):
    def test_one_delivery_per_user_and_day(self):
        day = datetime.date(2026, 10, 9)
        Delivery.objects.create(user=self.user, date=day, offer_count=5)
        Delivery.objects.create(user=self.user, date=day + datetime.timedelta(days=1), offer_count=3)
        with self.assertRaises(IntegrityError):
            Delivery.objects.create(user=self.user, date=day, offer_count=2)
