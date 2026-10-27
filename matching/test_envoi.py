"""`manage.py envoi` (sub-step 6.4), with the locmem backend and the fake engine: nothing leaves. Offers are
fictitious."""
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from unittest import mock

from django.core import mail as django_mail
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone as django_timezone

import mail
from accounts import trial, unsubscribe
from accounts.models import Profile, User
from accounts.testing import make_profile
from config.testing import PLAIN_STATIC, assert_french_copy, visible_text
from jobs.testing import OfferMaker, make_source
from matching import delivery
from matching.management.commands.envoi import Command
from matching.models import Delivery, Score
from onboarding import finish, schedule
from onboarding.test_trial_texts import plain

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
MONDAY, SATURDAY = "2026-10-19", "2026-10-17"


@override_settings(EMAIL_BACKEND=LOCMEM, CLAUDE_ENGINE="fake", DEV_EMAILS=[])
class EnvoiTests(OfferMaker, TestCase):
    def setUp(self):
        self.now = django_timezone.now()
        self.source = make_source()
        self.nora = make_profile("nora@example.fr").user
        self.paul = make_profile("paul@example.fr").user

    def scored(self, user, score=7):
        offer = self.offer()
        return Score.objects.create(user=user, offer=offer, score=score, category=Score.Category.TO_REVIEW)

    def envoi(self, *args, day=MONDAY):
        out = StringIO()
        call_command("envoi", "--date", day, *args, stdout=out)
        return out.getvalue()

    def test_sends_marks_scores_and_records_delivery(self):
        score = self.scored(self.nora)
        out = self.envoi("--user", "nora@example.fr")
        self.assertEqual([m.to for m in django_mail.outbox], [["nora@example.fr"]])
        self.assertIn("1 mails envoyés, 0 vides, 0 en échec", out)
        score.refresh_from_db()
        self.assertEqual(score.sent_on, date(2026, 10, 19))
        self.assertEqual(Delivery.objects.get(user=self.nora).offer_count, 1)
        self.assertEqual(Delivery.objects.filter(user=self.paul).count(), 0)

    def test_empty_day_still_sends_a_mail(self):
        out = self.envoi()
        self.assertEqual(len(django_mail.outbox), 2)
        self.assertIn("0 mails envoyés, 2 vides, 0 en échec", out)
        self.assertEqual(Delivery.objects.get(user=self.nora).offer_count, 0)

    def test_rerun_never_sends_twice(self):
        self.scored(self.nora)
        self.envoi()
        self.envoi()
        self.assertEqual(len(django_mail.outbox), 2)

    def test_next_day_skips_offers_already_sent(self):
        self.scored(self.nora)
        self.envoi("--user", "nora@example.fr")
        self.envoi("--user", "nora@example.fr", day="2026-10-20")
        self.assertEqual(Delivery.objects.get(user=self.nora, date=date(2026, 10, 20)).offer_count, 0)

    def test_weekend_does_nothing(self):
        self.scored(self.nora)
        out = self.envoi(day=SATURDAY)
        self.assertEqual(django_mail.outbox, [])
        self.assertEqual(Delivery.objects.count(), 0)
        self.assertIn("pas ouvré", out)

    def test_dry_run_writes_and_sends_nothing(self):
        score = self.scored(self.nora)
        out = self.envoi("--dry-run")
        self.assertEqual(django_mail.outbox, [])
        self.assertEqual(Delivery.objects.count(), 0)
        score.refresh_from_db()
        self.assertIsNone(score.sent_on)
        self.assertIn(f"compte {self.nora.pk}", out)
        self.assertNotIn("nora@example.fr", out)

    def test_failure_gives_the_line_back_and_logs_without_address(self):
        score = self.scored(self.nora)
        with mock.patch("mail.send", side_effect=mail.MailDeliveryFailed("boom nora@example.fr")), \
                self.assertLogs("matching.management.commands.envoi", level="ERROR") as logs, \
                self.assertRaises(CommandError):
            self.envoi("--user", "nora@example.fr")
        self.assertEqual(Delivery.objects.count(), 0)
        score.refresh_from_db()
        self.assertIsNone(score.sent_on)
        self.assertIn(str(self.nora.pk), logs.output[0])
        self.assertIn("MailDeliveryFailed", logs.output[0])
        self.assertNotIn("nora@example.fr", logs.output[0])
        self.assertNotIn("boom", logs.output[0])
        self.envoi("--user", "nora@example.fr")  # a rerun tries again
        self.assertEqual(len(django_mail.outbox), 1)

    def test_write_failing_after_the_mail_keeps_the_reservation_and_never_resends(self):
        score = self.scored(self.nora)
        with mock.patch("matching.delivery.all_picked", side_effect=RuntimeError("boom nora@example.fr")), \
                self.assertLogs("matching.management.commands.envoi", level="ERROR") as logs:
            self.envoi("--user", "nora@example.fr")
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertEqual(Delivery.objects.filter(user=self.nora).count(), 1)
        self.assertIn(f"account {self.nora.pk}", logs.output[0])
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("nora@example.fr", logs.output[0])
        self.envoi("--user", "nora@example.fr")
        self.assertEqual(len(django_mail.outbox), 1)
        score.refresh_from_db()
        self.assertIsNone(score.sent_on)

    def test_dry_run_announces_who_already_has_their_mail(self):
        self.scored(self.nora)
        self.envoi("--user", "nora@example.fr")
        out = self.envoi("--dry-run")
        self.assertIn(f"compte {self.nora.pk} : déjà envoyé", out)
        self.assertIn("0 mails à envoyer, 1 vides, 0 en échec", out)  # paul only (no offer); nora is not counted
        self.assertEqual(len(django_mail.outbox), 1)

    def test_backend_that_accepts_nothing_is_a_failure(self):
        with mock.patch("mail.send", return_value=0), self.assertLogs(level="ERROR"), \
                self.assertRaises(CommandError):
            self.envoi("--user", "nora@example.fr")
        self.assertEqual(Delivery.objects.count(), 0)

    def test_paused_or_unfinished_people_get_nothing(self):
        User.objects.filter(pk=self.nora.pk).update(status=User.Status.PAUSED)
        make_profile("zoe@example.fr", onboarding_completed_at=None)
        self.envoi()
        self.assertEqual([m.to for m in django_mail.outbox], [["paul@example.fr"]])

    def test_footer_and_unsubscribe_headers_are_the_persons_own_over_https(self):
        with override_settings(SITE_URL="https://tri.example.fr"):
            self.envoi()
            by_address = {m.to[0]: m for m in django_mail.outbox}
            for user in (self.nora, self.paul):
                message = by_address[user.email]
                self.assertEqual(message.extra_headers, unsubscribe.mail_headers(user))
                self.assertEqual(message.extra_headers["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
                for part in (message.body, message.alternatives[0][0]):
                    self.assertIn(unsubscribe.unsubscribe_url(user), part)
                    self.assertIn("Pour ne plus recevoir ces mails :", part)
                    self.assertNotIn(unsubscribe.unsubscribe_url(self.paul if user == self.nora else self.nora), part)

    def test_no_unsubscribe_headers_over_http_but_the_footer_stays(self):
        with override_settings(SITE_URL="http://localhost:8000"):
            self.envoi("--user", "nora@example.fr")
        message = django_mail.outbox[0]
        self.assertEqual(message.extra_headers, {})
        self.assertIn(unsubscribe.unsubscribe_url(self.nora), message.body)

    def test_unsubscribed_people_get_nothing(self):
        User.objects.filter(pk=self.nora.pk).update(status=User.Status.UNSUBSCRIBED)
        self.envoi()
        self.assertEqual([m.to for m in django_mail.outbox], [["paul@example.fr"]])
        with self.assertRaises(CommandError):
            self.envoi("--user", "nora@example.fr")

    def test_unsubscribed_between_the_reservation_and_the_send_gets_no_mail_and_gives_the_line_back(self):
        real = delivery.context

        def unsubscribe_then_build(*args):
            User.objects.filter(pk=self.nora.pk).update(status=User.Status.UNSUBSCRIBED)
            return real(*args)

        with mock.patch("matching.delivery.context", side_effect=unsubscribe_then_build), \
                self.assertLogs("matching.management.commands.envoi", level="WARNING") as logs:
            out = self.envoi("--user", "nora@example.fr")
        self.assertEqual(django_mail.outbox, [])
        self.assertEqual(Delivery.objects.count(), 0)
        self.assertIn("0 mails envoyés, 0 vides, 0 en échec", out)
        self.assertIn(f"account {self.nora.pk}", logs.output[0])
        self.assertNotIn("nora@example.fr", logs.output[0])

    def test_deleted_between_the_reservation_and_the_send_gets_no_mail_and_nothing_fails(self):
        real = delivery.context

        def delete_then_build(*args):
            User.objects.filter(pk=self.nora.pk).delete()
            return real(*args)

        with mock.patch("matching.delivery.context", side_effect=delete_then_build), self.assertLogs(level="WARNING"):
            out = self.envoi("--user", "nora@example.fr")
        self.assertEqual(django_mail.outbox, [])
        self.assertEqual(Delivery.objects.count(), 0)
        self.assertIn("0 en échec", out)

    def test_unknown_user_is_an_error(self):
        with self.assertRaises(CommandError):
            self.envoi("--user", "nobody@example.fr")

    @override_settings(CLAUDE_ENGINE="oauth", DEV_EMAILS=["paul@example.fr"])
    def test_oauth_guard_of_mail_send_still_applies(self):
        with self.assertLogs(level="ERROR") as logs, self.assertRaises(CommandError):
            self.envoi("--user", "nora@example.fr")
        self.assertEqual(django_mail.outbox, [])
        self.assertIn("MailRefused", logs.output[0])
        self.assertEqual(Delivery.objects.count(), 0)


@override_settings(EMAIL_BACKEND=LOCMEM, CLAUDE_ENGINE="fake", DEV_EMAILS=[])
class TrialGateTests(OfferMaker, TestCase):
    """Outside the beta nothing is written to an account whose trial is over; in the beta its date is not read (8.1)."""

    def setUp(self):
        self.now = django_timezone.now()
        self.source = make_source()
        self.nora = make_profile("nora@example.fr").user  # trial running
        self.paul = make_profile("paul@example.fr").user  # trial over
        User.objects.filter(pk=self.nora.pk).update(trial_ends_at=self.now + timedelta(days=1))
        User.objects.filter(pk=self.paul.pk).update(trial_ends_at=self.now - timedelta(minutes=1))

    def envoi(self, *args, day=MONDAY):
        out = StringIO()
        call_command("envoi", "--date", day, *args, stdout=out)
        return out.getvalue()

    @override_settings(BETA=False)
    def test_a_finished_trial_gets_no_mail_and_no_line(self):
        out = self.envoi()
        self.assertEqual([m.to for m in django_mail.outbox], [["nora@example.fr"]])
        self.assertIn("0 mails envoyés, 1 vides, 0 en échec", out)
        self.assertFalse(Delivery.objects.filter(user=self.paul).exists())

    @override_settings(BETA=False)
    def test_the_user_option_on_a_finished_trial_finds_nobody(self):
        with self.assertRaisesMessage(CommandError, "Aucune personne active"):
            self.envoi("--user", "paul@example.fr")
        self.assertEqual(django_mail.outbox, [])

    @override_settings(BETA=False)
    def test_the_dry_run_counts_only_the_running_trial(self):
        out = self.envoi("--dry-run")
        self.assertIn(f"compte {self.nora.pk}", out)
        self.assertNotIn(f"compte {self.paul.pk}", out)

    @override_settings(BETA=False)
    def test_the_gate_follows_the_real_time_not_the_requested_day(self):
        # Asking for a day before the end does not bring a finished trial back (S9).
        self.envoi(day="2020-01-06")
        self.assertEqual([m.to for m in django_mail.outbox], [["nora@example.fr"]])

    def test_the_date_option_says_the_gate_follows_the_real_time(self):
        self.assertIn("heure réelle", " ".join(Command().create_parser("manage.py", "envoi").format_help().split()))

    @override_settings(BETA=False)
    def test_the_last_look_skips_a_trial_that_ends_between_the_selection_and_the_send(self):
        picked = delivery.pick(self.nora, self.now)
        with self.assertLogs("matching.management.commands.envoi", level="WARNING") as logs:
            outcome = Command()._send(self.nora, picked, date(2026, 10, 19), self.now + timedelta(days=2))
        self.assertEqual(outcome, "skipped")
        self.assertEqual(django_mail.outbox, [])
        self.assertEqual(Delivery.objects.count(), 0)  # the reservation is given back
        self.assertEqual(logs.output, [f"WARNING:matching.management.commands.envoi:Morning mail of account "
                                       f"{self.nora.pk} skipped: no longer mailable."])

    @override_settings(BETA=False)
    def test_the_last_look_lets_a_running_trial_through(self):
        outcome = Command()._send(self.nora, delivery.pick(self.nora, self.now), date(2026, 10, 19), self.now)
        self.assertEqual(outcome, "empty")
        self.assertEqual([m.to for m in django_mail.outbox], [["nora@example.fr"]])

    @override_settings(BETA=True)
    def test_in_beta_everyone_is_served_and_the_last_look_lets_through(self):
        self.envoi()
        self.assertCountEqual([m.to[0] for m in django_mail.outbox], ["nora@example.fr", "paul@example.fr"])
        outcome = Command()._send(self.nora, delivery.pick(self.nora, self.now), date(2026, 10, 20),
                                  self.now + timedelta(days=2))
        self.assertEqual(outcome, "empty")

    @override_settings(BETA=False)
    def test_a_reactivated_finished_trial_is_still_not_mailed_and_the_beta_brings_it_back(self):
        User.objects.filter(pk=self.paul.pk).update(status=User.Status.UNSUBSCRIBED)
        User.objects.filter(pk=self.paul.pk).update(status=User.Status.ACTIVE)
        self.envoi()
        self.assertEqual([m.to for m in django_mail.outbox], [["nora@example.fr"]])
        with override_settings(BETA=True):
            self.envoi("--user", "paul@example.fr")
        self.assertEqual(django_mail.outbox[-1].to, ["paul@example.fr"])


@override_settings(EMAIL_BACKEND=LOCMEM, CLAUDE_ENGINE="fake", DEV_EMAILS=[], SITE_URL="https://tri.example.fr",
                   BETA=False)
class LastMailTests(OfferMaker, TestCase):
    """The line of the last morning mail before the end of the trial (sub-step 8.2). `_send` is called with a fixed
    `now`, so nothing here reads the real clock."""

    END = datetime(2026, 10, 26, tzinfo=schedule.PARIS)  # Monday, midnight: Friday 23 is the last business-day mail
    LINE = ("C’est votre dernier mail du matin : votre essai se termine le lundi 26 octobre. Vos réponses et vos "
            "avis restent dans « Mes données » jusqu’à ce que vous supprimiez votre compte :")
    LINK = "https://tri.example.fr/mes-donnees/"
    UNSUBSCRIBE = "Pour ne plus recevoir ces mails"

    def setUp(self):
        self.now = datetime(2026, 10, 23, 5, 0, tzinfo=timezone.utc)
        self.source = make_source()
        self.nora = make_profile("nora@example.fr").user
        User.objects.filter(pk=self.nora.pk).update(trial_ends_at=self.END)
        self.nora.refresh_from_db()

    def send(self, day, with_offer=False):
        """The mail of `day` at 07:00 in Paris, and its text and HTML parts."""
        now = datetime.combine(day, schedule.SEND_TIME, tzinfo=schedule.PARIS)
        if with_offer:
            Score.objects.create(user=self.nora, offer=self.offer(), score=7, category=Score.Category.TO_REVIEW)
        outcome = Command()._send(self.nora, delivery.pick(self.nora, now), day, now)
        self.assertIn(outcome, ("sent", "empty"))
        message = django_mail.outbox[-1]
        return message.body, message.alternatives[0][0]

    def assertLine(self, body, html):
        text_part, html_part = " ".join(body.split()), plain(html)
        self.assertIn(f"{self.LINE} {self.LINK}", text_part)
        self.assertLess(text_part.index(self.LINE), text_part.index(self.UNSUBSCRIBE))
        self.assertIn(self.LINE, html_part)
        self.assertLess(html_part.index(self.LINE), html_part.index(self.UNSUBSCRIBE))
        self.assertIn(f'href="{self.LINK}"', html)
        assert_french_copy(self, html_part)

    def test_the_last_business_day_before_the_end_carries_the_line_empty_mail_or_not(self):
        for with_offer in (False, True):
            with self.subTest(with_offer=with_offer):
                Delivery.objects.all().delete()
                self.assertLine(*self.send(date(2026, 10, 23), with_offer=with_offer))

    def test_the_day_before_has_no_line(self):
        body, html = self.send(date(2026, 10, 22), with_offer=True)
        for part in (body, plain(html)):
            self.assertNotIn("dernier mail", part)
            self.assertNotIn("essai", part)
        self.assertNotIn(self.LINK, body + html)

    def test_the_line_ends_the_mail_in_the_empty_one_too(self):
        body, html = self.send(date(2026, 10, 23))
        self.assertIn("Je cherche à nouveau demain.", body)
        self.assertLess(body.index("Je cherche à nouveau demain."), body.index("C’est votre dernier mail"))

    @override_settings(BETA=True)
    def test_in_beta_there_is_no_line(self):
        body, html = self.send(date(2026, 10, 23))
        for part in (body, plain(html)):
            self.assertNotIn("dernier mail", part)
            self.assertNotIn("essai", part)

    def test_without_a_date_there_is_no_line(self):
        User.objects.filter(pk=self.nora.pk).update(trial_ends_at=None)
        self.nora.refresh_from_db()
        body, html = self.send(date(2026, 10, 23))
        self.assertNotIn("dernier mail", body)
        self.assertNotIn("dernier mail", plain(html))

    def test_the_context_carries_the_day_and_the_link(self):
        with mock.patch("mail.send", return_value=1) as send:
            Command()._send(self.nora, delivery.pick(self.nora, self.now), date(2026, 10, 23), self.now)
        context = send.call_args.args[2]
        self.assertTrue(context["trial_last"])
        self.assertEqual(context["trial_end_day"], "lundi 26 octobre")
        self.assertEqual(context["my_data_url"], self.LINK)

    @override_settings(STORAGES=PLAIN_STATIC)
    def test_the_same_end_day_in_the_three_texts(self):
        label = trial.end_label(self.nora)
        self.assertEqual(label, "lundi 26 octobre")
        welcome_now = datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc)
        with mock.patch("onboarding.finish.timezone", **{"now.return_value": welcome_now}):
            finish.send_welcome(Profile.objects.get(user=self.nora))
        welcome = plain(django_mail.outbox[-1].alternatives[0][0])
        self.assertIn(f"le mail du matin s’arrête le {label}.", welcome)
        body, html = self.send(date(2026, 10, 23))
        self.assertIn(f"votre essai se termine le {label}.", " ".join(body.split()))
        self.client.force_login(self.nora)
        with mock.patch.object(django_timezone, "now", return_value=self.now):
            page = " ".join(visible_text(self.client.get("/mes-donnees/").content.decode()).split())
        self.assertIn("Jusqu’au dimanche 25 octobre.", page)  # the seventh day, the day before the one written above
