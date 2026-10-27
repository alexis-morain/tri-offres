"""What the person reads about the end of the trial (sub-step 8.2): the welcome mail and « Mes données ». Outside the
beta only; with the beta on (the default of the test run) every mail and page is the one of before. Fixed times."""
import datetime as dt
from html import unescape
from unittest import mock

from django.test import override_settings
from django.utils import timezone

import mail
from accounts import events, trial
from accounts.models import User
from config.testing import assert_french_copy, assert_unique_ids, visible_text
from onboarding import schedule
from onboarding.test_end import MY_DATA, EndTestCase, at, text
from onboarding.test_my_search import FinishedTestCase
from onboarding.testing import MY_SEARCH

PARIS = schedule.PARIS
NOW = dt.datetime(2026, 10, 20, 13, 0, tzinfo=dt.timezone.utc)  # Tue 20 Oct, 15:00 in Paris
END = dt.datetime(2026, 10, 27, tzinfo=PARIS)  # the trial ends at midnight: Tue 27 Oct is the first day without mail
SENTENCE = "Votre essai dure 7 jours : le mail du matin s’arrête le mardi 27 octobre."
FIRST_SORT = "Votre premier tri arrive demain, mercredi 21 octobre, vers 7 h, depuis noreply@example.org."
DOWN = mail.MailDeliveryFailed("down")


def plain(value):
    """A mail part as a person reads it: tags and entities gone, non-breaking spaces and line breaks flattened."""
    return " ".join(unescape(visible_text(value)).replace("\xa0", " ").replace(" ", " ").split())


def frozen(value):
    return mock.patch.object(timezone, "now", return_value=value)


class WelcomeTrialTests(EndTestCase):
    def parts(self, message):
        return {"text": plain(message.body), "html": plain(message.alternatives[0][0])}

    def finish_at(self, now=NOW):
        with at(now):
            return self.finish()

    def late_welcome(self, days):
        """The first welcome mail fails, a visit of « Ma recherche » `days` later sends it."""
        with mock.patch("onboarding.finish.mail.send", side_effect=DOWN):
            self.finish_at()
        with at(NOW + dt.timedelta(days=days)):
            self.page(MY_SEARCH)
        return self.welcome_mails()[0]

    @override_settings(BETA=False)
    def test_one_sentence_after_the_first_sort_in_text_and_html(self):
        self.finish_at()
        self.assertEqual(User.objects.get(pk=self.user.pk).trial_ends_at, END)
        for kind, part in self.parts(self.welcome_mails()[0]).items():
            with self.subTest(kind=kind):
                self.assertIn(f"{FIRST_SORT} {SENTENCE} Pour qu’il arrive dans votre boîte principale", part)
                self.assertEqual(part.count("essai"), 1)
                assert_french_copy(self, part)

    @override_settings(BETA=False)
    def test_the_number_of_days_is_read_from_the_constant(self):
        with mock.patch.object(trial, "TRIAL_DAYS", 5):
            self.finish_at()
        for part in self.parts(self.welcome_mails()[0]).values():
            self.assertIn("Votre essai dure 5 jours : le mail du matin s’arrête le dimanche 25 octobre.", part)

    @override_settings(BETA=False)
    def test_the_same_end_day_in_the_welcome_mail_and_in_my_data(self):
        self.finish_at()
        self.assertIn(f"le {trial.end_label(User.objects.get(pk=self.user.pk))}.", plain(self.welcome_mails()[0].body))
        with frozen(NOW):
            self.assertIn("Jusqu’au lundi 26 octobre.", text(self.page(MY_DATA)))
        with frozen(END):
            self.assertIn("Terminé le mardi 27 octobre.", text(self.page(MY_DATA)))

    @override_settings(BETA=True)
    def test_in_beta_the_mail_is_the_one_of_before(self):
        self.finish_at()
        message = self.welcome_mails()[0]
        for kind, part in self.parts(message).items():
            with self.subTest(kind=kind):
                self.assertIn(f"{FIRST_SORT} Pour qu’il arrive dans votre boîte principale", part)
                self.assertNotIn("essai", part)
        self.assertEqual(message.subject, "Bienvenue : votre premier tri arrive demain, mercredi 21 octobre")

    @override_settings(BETA=False)
    def test_a_late_retry_inside_the_trial_keeps_the_sentence(self):
        message = self.late_welcome(days=3)
        for part in self.parts(message).values():
            self.assertIn(SENTENCE, part)
            self.assertIn("Votre premier tri arrive lundi 26 octobre, vers 7 h", part)  # counted from the retry

    @override_settings(BETA=False)
    def test_a_late_retry_after_the_end_announces_no_first_sort_and_no_trial(self):
        message = self.late_welcome(days=10)
        for kind, part in self.parts(message).items():
            with self.subTest(kind=kind):
                self.assertNotIn("premier tri arrive", part)
                self.assertNotIn("essai", part)
                self.assertNotIn("vers 7 h", part)
                # The rest of the mail is the same.
                # No morning mail will come: neither the inbox tips nor the question about the first mail.
                self.assertNotIn("Pour qu’il arrive dans votre boîte principale", part)
                self.assertNotIn("Gmail : Si le mail arrive", part)
                self.assertNotIn("Une question, si vous avez trente secondes", part)
                self.assertIn("ouvrez Ma recherche" if kind == "html" else "ouvrez « Ma recherche »", part)
        self.assertNotIn("premier tri arrive", message.subject)
        self.assertEqual(message.subject, "Bienvenue sur tri-offres")
        self.assertIn("Bienvenue sur tri-offres", self.parts(message)["text"])
        self.assertEqual(self.event_names().count(events.WELCOME_SENT), 1)

    @override_settings(BETA=False)
    def test_the_exact_end_second_is_over(self):
        with mock.patch("onboarding.finish.mail.send", side_effect=DOWN):
            self.finish_at()
        with at(END):
            self.page(MY_SEARCH)
        self.assertNotIn("premier tri arrive", self.parts(self.welcome_mails()[0])["text"])

    @override_settings(BETA=False)
    def test_without_a_date_the_mail_is_the_one_of_before(self):
        User.objects.filter(pk=self.user.pk).update(is_staff=True)  # the team gets no date
        self.finish_at()
        self.assertIsNone(User.objects.get(pk=self.user.pk).trial_ends_at)
        message = self.welcome_mails()[0]
        for part in self.parts(message).values():
            self.assertIn(f"{FIRST_SORT} Pour qu’il arrive dans votre boîte principale", part)
            self.assertNotIn("essai", part)
        self.assertEqual(message.subject, "Bienvenue : votre premier tri arrive demain, mercredi 21 octobre")

    @override_settings(BETA=True)
    def test_in_beta_a_date_set_by_hand_is_not_read(self):
        User.objects.filter(pk=self.user.pk).update(trial_ends_at=dt.datetime(2026, 10, 1, tzinfo=PARIS))
        with mock.patch("onboarding.finish.mail.send", side_effect=DOWN):
            self.finish_at()
        with at(NOW + dt.timedelta(days=10)):
            self.page(MY_SEARCH)
        for part in self.parts(self.welcome_mails()[0]).values():
            self.assertIn("Votre premier tri arrive", part)
            self.assertNotIn("essai", part)


@override_settings(BETA=False)
class MyDataTrialTests(FinishedTestCase):
    """The « Essai » row and the wording of the « Mail du matin » row."""

    RESUBSCRIBE = "Recevoir de nouveau le mail du matin"

    def setUp(self):
        super().setUp()
        User.objects.filter(pk=self.user.pk).update(trial_ends_at=END)

    def my_data(self, now, **fields):
        User.objects.filter(pk=self.user.pk).update(**fields)
        with frozen(now):
            return self.page(MY_DATA)

    def test_during_the_trial_the_row_says_until_the_seventh_day(self):
        html = self.my_data(dt.datetime(2026, 10, 25, 10, 0, tzinfo=PARIS))
        self.assertIn("<dt>Essai</dt>", html)
        page = text(html)
        self.assertIn("Essai Jusqu’au lundi 26 octobre.", page)
        self.assertIn("Mail du matin Vous recevez le mail du matin.", page)
        self.assertNotIn("Terminé", page)

    def test_the_last_second_is_still_inside(self):
        page = text(self.my_data(END - dt.timedelta(seconds=1)))
        self.assertIn("Jusqu’au lundi 26 octobre.", page)
        self.assertIn("Vous recevez le mail du matin.", page)

    def test_after_the_end_the_row_says_finished_on_the_eighth_day(self):
        for now in (END, END + dt.timedelta(days=3)):
            with self.subTest(now=now):
                page = text(self.my_data(now))
                self.assertIn("Essai Terminé le mardi 27 octobre.", page)
                self.assertNotIn("Jusqu’au", page)

    def test_an_active_account_after_the_end_is_told_the_mail_stopped_and_gets_no_button(self):
        html = self.my_data(END + dt.timedelta(days=1))
        page = text(html)
        self.assertIn("Mail du matin Le mail du matin s’est arrêté à la fin de votre essai.", page)
        self.assertNotIn("Vous recevez le mail du matin", page)
        self.assertNotIn(self.RESUBSCRIBE, html)
        self.assertNotIn("/mes-donnees/reabonner/", html)

    def test_an_unsubscribed_account_after_the_end_keeps_its_sentence_and_gets_no_button(self):
        html = self.my_data(END + dt.timedelta(days=1), status=User.Status.UNSUBSCRIBED)
        page = text(html)
        self.assertIn("Mail du matin Vous ne recevez plus de mail.", page)
        self.assertNotIn("s’est arrêté", page)
        self.assertNotIn(self.RESUBSCRIBE, html)
        self.assertNotIn("/mes-donnees/reabonner/", html)

    def test_an_unsubscribed_account_during_the_trial_can_still_resubscribe(self):
        html = self.my_data(dt.datetime(2026, 10, 25, 10, 0, tzinfo=PARIS), status=User.Status.UNSUBSCRIBED)
        self.assertIn(self.RESUBSCRIBE, html)
        self.assertIn("Vous ne recevez plus de mail.", text(html))
        self.assertIn("Jusqu’au lundi 26 octobre.", text(html))

    def test_without_a_date_there_is_no_row(self):
        html = self.my_data(END + dt.timedelta(days=4), trial_ends_at=None)
        self.assertNotIn("<dt>Essai</dt>", html)
        self.assertIn("Vous recevez le mail du matin.", text(html))

    def test_the_page_stays_valid(self):
        html = self.my_data(END + dt.timedelta(days=1))
        assert_french_copy(self, html)
        assert_unique_ids(self, html)

    @override_settings(BETA=True)
    def test_in_beta_the_page_is_the_one_of_before(self):
        for status, wanted in ((User.Status.ACTIVE, "Vous recevez le mail du matin."),
                               (User.Status.UNSUBSCRIBED, "Vous ne recevez plus de mail.")):
            with self.subTest(status=status):
                html = self.my_data(END + dt.timedelta(days=4), status=status)
                page = text(html)
                self.assertNotIn("<dt>Essai</dt>", html)
                self.assertNotIn("essai", page.lower())
                self.assertNotIn("Jusqu’au", page)
                self.assertIn(wanted, page)
                self.assertEqual(self.RESUBSCRIBE in html, status == User.Status.UNSUBSCRIBED)
