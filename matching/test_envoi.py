"""`manage.py envoi` (sub-step 6.4), with the locmem backend and the fake engine: nothing leaves. Offers are
fictitious."""
from datetime import date
from io import StringIO
from unittest import mock

from django.core import mail as django_mail
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone as django_timezone

import mail
from accounts import unsubscribe
from accounts.models import User
from accounts.testing import make_profile
from jobs.testing import OfferMaker, make_source
from matching import delivery
from matching.models import Delivery, Score

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

