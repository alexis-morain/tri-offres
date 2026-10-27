"""The deck of the sort page (sub-step 6.1): matching/deck.py. Offers are fictitious."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.testing import make_profile
from jobs.testing import OfferMaker, make_source
from matching import deck
from matching.models import Feedback, Score

YES, NO, MAYBE = Feedback.Verdict.YES, Feedback.Verdict.NO, Feedback.Verdict.MAYBE


class DeckTestCase(OfferMaker, TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.source = make_source()
        self.user = make_profile("nora@example.fr").user

    def scored(self, score=7, user=None, **fields):
        offer = self.offer(**fields)
        Score.objects.create(user=user or self.user, offer=offer, score=score, category=Score.Category.TO_REVIEW)
        return offer

    def swipe(self, offer, verdict=YES, user=None, **fields):
        return Feedback.objects.create(user=user or self.user, offer=offer, verdict=verdict, **fields)

    def deck_ids(self):
        return [score.offer_id for score in deck.deck(self.user, self.now)]

    def rows(self, user=None):
        return Feedback.objects.filter(user=user or self.user)


class DeckTests(DeckTestCase):
    def test_threshold_is_the_maybe_minimum(self):
        self.assertEqual(deck.SHOW_MIN, 5)
        kept = [self.scored(5), self.scored(10)]
        self.scored(4)
        self.assertEqual(sorted(self.deck_ids()), sorted(offer.pk for offer in kept))

    def test_expired_offer_is_left_out(self):
        self.scored(expires_at=self.now - timedelta(minutes=1))
        live = self.scored(expires_at=self.now + timedelta(days=1))
        open_ended = self.scored()
        self.assertEqual(sorted(self.deck_ids()), sorted([live.pk, open_ended.pk]))

    def test_offer_outside_the_window_is_left_out(self):
        self.scored(ago=deck.WINDOW + timedelta(hours=1))
        recent = self.scored(ago=deck.WINDOW - timedelta(hours=1))
        self.assertEqual(self.deck_ids(), [recent.pk])

    def test_judged_offer_is_left_out_whatever_the_verdict(self):
        for verdict in (YES, NO, MAYBE):
            self.swipe(self.scored(), verdict)
        todo = self.scored()
        self.assertEqual(self.deck_ids(), [todo.pk])

    def test_cancelled_verdict_puts_the_offer_back(self):
        offer = self.scored()
        self.swipe(offer, YES, cancelled=True)
        self.assertEqual(self.deck_ids(), [offer.pk])

    def test_cancelling_the_latest_of_two_verdicts_keeps_the_earlier_one(self):
        offer = self.scored()
        self.swipe(offer, YES)
        self.swipe(offer, NO, cancelled=True)
        self.assertEqual(self.deck_ids(), [])

    def test_order_is_score_then_recency_then_key(self):
        low = self.scored(6, ago=timedelta(hours=1))
        old_high = self.scored(9, ago=timedelta(days=2))
        new_high = self.scored(9, ago=timedelta(hours=2))
        # Same score, same collection time: the later key first.
        first = self.scored(7, ago=timedelta(hours=5))
        second = self.scored(7, ago=timedelta(hours=5))
        self.assertEqual(self.deck_ids(), [new_high.pk, old_high.pk, second.pk, first.pk, low.pk])

    def test_deck_is_cut_to_its_size_and_count_gives_the_total(self):
        for _ in range(deck.DECK_SIZE + 3):
            self.scored()
        self.assertEqual(len(self.deck_ids()), deck.DECK_SIZE)
        self.assertEqual(deck.deck_count(self.user, self.now), deck.DECK_SIZE + 3)

    def test_another_persons_scores_and_judgments_do_not_count(self):
        other = make_profile("lea@example.fr").user
        offer = self.scored()
        self.scored(user=other)
        self.swipe(offer, YES, user=other)
        self.assertEqual(self.deck_ids(), [offer.pk])
        self.assertEqual([score.user_id for score in deck.deck(self.user, self.now)], [self.user.pk])
        self.assertEqual(deck.deck_count(self.user, self.now), 1)

    def test_two_queries_at_most_whatever_the_history(self):
        for _ in range(4):
            offer = self.scored()
            self.swipe(offer, YES, cancelled=True)
            self.swipe(offer, NO)
            self.swipe(offer, MAYBE, cancelled=True)
        for _ in range(5):
            self.scored()
        with self.assertNumQueries(1):
            cards = deck.deck(self.user, self.now)
            self.assertEqual(len(cards), 5)
            # The source is read with the offer.
            self.assertTrue(all(card.offer.source.name for card in cards))
        with self.assertNumQueries(1):
            self.assertEqual(deck.deck_count(self.user, self.now), 5)

    def test_first_sort_threshold_is_gone(self):
        from matching import scoring
        self.assertFalse(hasattr(scoring, "FIRST_SORT_MIN"))


class GiveTests(DeckTestCase):
    def test_gives_a_row(self):
        offer = self.scored()
        deck.give(self.user, offer.pk, YES)
        self.assertEqual(list(self.rows().values_list("offer_id", "verdict", "cancelled")),
                         [(offer.pk, YES, False)])
        self.assertEqual(Feedback.objects.current_verdict(self.user, offer), YES)

    def test_no_score_for_the_person_raises_and_writes_nothing(self):
        other = make_profile("lea@example.fr").user
        theirs = self.scored(user=other)
        with self.assertRaises(Score.DoesNotExist):
            deck.give(self.user, theirs.pk, YES)
        with self.assertRaises(Score.DoesNotExist):
            deck.give(self.user, theirs.pk + 1000, YES)
        self.assertEqual(Feedback.objects.count(), 0)

    def test_unknown_verdict_raises_and_writes_nothing(self):
        offer = self.scored()
        for verdict in ("", "YES", "oui", None):
            with self.assertRaises(ValueError):
                deck.give(self.user, offer.pk, verdict)
        self.assertEqual(Feedback.objects.count(), 0)

    def test_a_second_verdict_adds_a_row_and_the_last_counts(self):
        offer = self.scored()
        deck.give(self.user, offer.pk, YES)
        deck.give(self.user, offer.pk, NO)
        self.assertEqual(self.rows().count(), 2)
        self.assertEqual(Feedback.objects.current_verdict(self.user, offer), NO)

    def test_the_same_verdict_twice_is_one_row(self):
        offer = self.scored()
        deck.give(self.user, offer.pk, YES)
        deck.give(self.user, offer.pk, YES)
        self.assertEqual(self.rows().count(), 1)

    def test_the_same_verdict_after_a_cancellation_is_a_new_row(self):
        offer = self.scored()
        deck.give(self.user, offer.pk, YES)
        deck.undo(self.user, offer.pk)
        deck.give(self.user, offer.pk, YES)
        self.assertEqual(list(self.rows().order_by("pk").values_list("cancelled", flat=True)), [True, False])

    def test_cap_refuses_the_next_row_and_writes_nothing(self):
        offers = [self.scored() for _ in range(deck.DAILY_CAP + 1)]
        for offer in offers[:deck.DAILY_CAP]:
            deck.give(self.user, offer.pk, YES)
        with self.assertRaises(deck.CapReached):
            deck.give(self.user, offers[-1].pk, YES)
        self.assertEqual(self.rows().count(), deck.DAILY_CAP)

    def test_cap_counts_the_last_24_hours_and_one_person_only(self):
        self.assertEqual(deck.DAILY_CAP, 500)
        offers = [self.scored() for _ in range(deck.DAILY_CAP)]
        Feedback.objects.bulk_create([Feedback(user=self.user, offer=offer, verdict=YES) for offer in offers])
        Feedback.objects.filter(user=self.user).update(created_at=timezone.now() - timedelta(hours=24, minutes=1))
        fresh = self.scored()
        deck.give(self.user, fresh.pk, YES)
        Feedback.objects.bulk_create([Feedback(user=self.user, offer=fresh, verdict=NO)
                                      for _ in range(deck.DAILY_CAP - 1)])
        with self.assertRaises(deck.CapReached):
            deck.give(self.user, fresh.pk, MAYBE)
        # The cap is the person's own.
        other = make_profile("lea@example.fr").user
        theirs = self.scored(user=other)
        deck.give(other, theirs.pk, YES)
        self.assertEqual(self.rows(other).count(), 1)


class UndoTests(DeckTestCase):
    def test_cancels_the_last_row_of_that_offer_only(self):
        first, second = self.scored(), self.scored()
        deck.give(self.user, first.pk, YES)
        deck.give(self.user, second.pk, NO)
        self.assertEqual(deck.undo(self.user, first.pk), first.pk)
        self.assertEqual(Feedback.objects.current_verdict(self.user, first), None)
        self.assertEqual(Feedback.objects.current_verdict(self.user, second), NO)
        self.assertEqual(self.rows().filter(cancelled=True).count(), 1)

    def test_a_second_call_changes_nothing(self):
        other = self.scored()
        offer = self.scored()
        deck.give(self.user, other.pk, MAYBE)
        deck.give(self.user, offer.pk, YES)
        self.assertEqual(deck.undo(self.user, offer.pk), offer.pk)
        before = list(self.rows().order_by("pk").values_list("cancelled", flat=True))
        self.assertIsNone(deck.undo(self.user, offer.pk))
        self.assertEqual(list(self.rows().order_by("pk").values_list("cancelled", flat=True)), before)
        self.assertEqual(Feedback.objects.current_verdict(self.user, other), MAYBE)

    def test_only_the_latest_row_of_the_offer_goes(self):
        offer = self.scored()
        deck.give(self.user, offer.pk, YES)
        deck.give(self.user, offer.pk, NO)
        deck.undo(self.user, offer.pk)
        self.assertEqual(Feedback.objects.current_verdict(self.user, offer), YES)

    def test_nothing_to_cancel_gives_none(self):
        offer = self.scored()
        self.assertIsNone(deck.undo(self.user, offer.pk))
        self.assertIsNone(deck.undo(self.user, offer.pk + 1000))

    def test_never_touches_another_persons_rows(self):
        other = make_profile("lea@example.fr").user
        offer = self.scored(user=other)
        self.swipe(offer, YES, user=other)
        self.assertIsNone(deck.undo(self.user, offer.pk))
        self.assertFalse(self.rows(other).get().cancelled)

    def test_cap_refuses_the_cancellation_too(self):
        offers = [self.scored() for _ in range(deck.DAILY_CAP)]
        Feedback.objects.bulk_create([Feedback(user=self.user, offer=offer, verdict=YES) for offer in offers])
        with self.assertRaises(deck.CapReached):
            deck.undo(self.user, offers[0].pk)
        self.assertFalse(self.rows().filter(cancelled=True).exists())


class LinkTests(TestCase):
    def test_only_http_and_https_links_are_shown(self):
        for url in ("javascript:alert(1)", "data:text/html,x", "//x.example/a", "https://exa mple.fr/a", "ftp://x.fr",
                    "https://example.fr/a b", "https://example.fr/\n", "", "/relative"):
            self.assertEqual(deck.safe_link(url), "", url)
        for url in ("https://example.fr/offre/1", "http://example.fr/offre?a=1"):
            self.assertEqual(deck.safe_link(url), url)
