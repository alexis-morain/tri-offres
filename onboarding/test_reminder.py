"""The single reminder (sub-step 3.7): onboarding/reminder.py and the relance_onboarding command."""
import datetime as dt
import io
import re
from unittest import mock

from django.core import mail as django_mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

import mail
from accounts import auth, events, unsubscribe
from accounts.models import LoginCode, OnboardingEvent, Profile, User
from accounts.testing import make_login_code
from config.testing import PLAIN_STATIC, assert_french_copy
from onboarding import flow, reminder, schedule

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
PARIS = schedule.PARIS
NOW = dt.datetime(2026, 10, 14, 18, 0, tzinfo=PARIS)  # a Wednesday, 18:00
YESTERDAY = dt.datetime(2026, 10, 13, 10, 0, tzinfo=PARIS)


@override_settings(EMAIL_BACKEND=LOCMEM, DEV_EMAILS=[], SITE_URL="https://tri.example.fr", STORAGES=PLAIN_STATIC)
class ReminderTestCase(TestCase):
    def person(self, email="nora@example.fr", step="lieu", last_event=YESTERDAY, **profile):
        user = User.objects.create_user(email)
        Profile.objects.create(user=user, onboarding_step=step, **profile)
        if last_event is not None:
            OnboardingEvent.objects.create(user=user, name=events.step(step or "recherche"), created_at=last_event)
        return user

    def due_users(self, now=NOW):
        return [profile.user.email for profile in reminder.due(now)]

    def run_command(self, *args):
        out = io.StringIO()
        call_command("relance_onboarding", *args, stdout=out)
        return out.getvalue()


class DueTests(ReminderTestCase):
    def test_started_yesterday_and_not_finished(self):
        self.person()
        self.assertEqual(self.due_users(), ["nora@example.fr"])

    def test_who_is_left_out(self):
        self.person("today@example.fr", last_event=NOW - dt.timedelta(hours=2))
        self.person("two-days@example.fr", last_event=YESTERDAY - dt.timedelta(days=1))
        self.person("not-started@example.fr", step="")
        self.person("finished@example.fr", step="synthese", onboarding_completed_at=YESTERDAY)
        self.person("all-validated@example.fr", step="synthese")  # « Ça me va » without the end screen
        inactive = self.person("inactive@example.fr")
        User.objects.filter(pk=inactive.pk).update(is_active=False)
        for status in (User.Status.UNSUBSCRIBED, User.Status.PAUSED):
            left = self.person(f"{status}@example.fr")
            User.objects.filter(pk=left.pk).update(status=status)
        reminded = self.person("reminded@example.fr")
        OnboardingEvent.objects.create(user=reminded, name=events.REMINDER_SENT,
                                       created_at=YESTERDAY - dt.timedelta(days=5))
        self.assertEqual(self.due_users(), [])

    def test_signed_in_without_validating_a_screen_counts_as_started(self):
        for email, event in (("code@example.fr", events.CODE_ENTERED), ("link@example.fr", events.LINK_USED)):
            user = self.person(email, step="", last_event=None)
            OnboardingEvent.objects.create(user=user, name=event, created_at=YESTERDAY)
        self.assertEqual(sorted(self.due_users()), ["code@example.fr", "link@example.fr"])

    def test_signed_in_without_a_screen_still_needs_yesterday(self):
        user = self.person("old@example.fr", step="", last_event=None)
        OnboardingEvent.objects.create(user=user, name=events.CODE_ENTERED, created_at=YESTERDAY - dt.timedelta(days=1))
        self.assertEqual(self.due_users(), [])

    def test_the_last_event_counts_not_the_first(self):
        user = self.person()
        OnboardingEvent.objects.create(user=user, name=events.step("contrats"), created_at=NOW - dt.timedelta(hours=1))
        self.assertEqual(self.due_users(), [])

    def test_the_day_before_is_counted_in_paris_time(self):
        self.person("start@example.fr", last_event=dt.datetime(2026, 10, 13, 0, 0, tzinfo=PARIS))
        self.person("end@example.fr", last_event=dt.datetime(2026, 10, 13, 23, 59, tzinfo=PARIS))
        # 22:30 UTC on the 12th is 00:30 on the 13th in Paris.
        self.person("utc@example.fr", last_event=dt.datetime(2026, 10, 12, 22, 30, tzinfo=dt.timezone.utc))
        self.person("before@example.fr", last_event=dt.datetime(2026, 10, 12, 23, 59, tzinfo=PARIS))
        self.person("after@example.fr", last_event=dt.datetime(2026, 10, 14, 0, 0, tzinfo=PARIS))
        self.assertEqual(sorted(self.due_users()), ["end@example.fr", "start@example.fr", "utc@example.fr"])

    def test_minutes_left_come_from_the_flow(self):
        user = self.person(step="lieu")
        self.assertEqual(reminder.minutes_left(user.profile), flow.minutes_left("contrats", user.profile))


class SendTests(ReminderTestCase):
    def due_now(self):
        """The person is due whatever the real date: due() is checked at NOW, the sending happens now."""
        real = reminder.due
        return mock.patch("onboarding.reminder.due", side_effect=lambda now: real(NOW))

    def test_mail_with_a_new_code_and_link(self):
        user = self.person(step="valeurs")
        with self.due_now():
            output = self.run_command()
        self.assertIn("1 relance(s) envoyée(s) sur 1.", output)
        self.assertNotIn("nora@example.fr", output)
        self.assertEqual(len(django_mail.outbox), 1)
        message = django_mail.outbox[0]
        minutes = flow.minutes_left("structures", user.profile)
        self.assertRegex(message.subject, rf"^\d{{3}} \d{{3}} : il vous reste {minutes} minutes sur tri-offres$")
        code = message.subject[:7]
        self.assertEqual(message.to, ["nora@example.fr"])
        html = message.alternatives[0][0]
        for part in (message.body, html):
            with self.subTest(part=part[:20]):
                self.assertIn(code, part)
                self.assertIn(f"Il vous reste {minutes} minutes pour finir votre questionnaire", " ".join(part.split()))
                self.assertIn("Ce code et ce lien restent valables 15 minutes.", " ".join(part.split()))
                assert_french_copy(self, part)
        link = re.search(r"https://tri\.example\.fr/connexion/lien/([A-Za-z0-9_-]{43})/", message.body).group(1)
        row = LoginCode.objects.get(email="nora@example.fr")
        self.assertEqual(row.code_hash, auth.hash_code("nora@example.fr", code.replace(" ", "")))
        self.assertEqual(row.token_hash, auth.hash_token(link))
        self.assertIsNotNone(row.sent_at)
        self.assertEqual(row.expires_at - row.created_at, auth.CODE_TTL)
        self.assertTrue(OnboardingEvent.objects.filter(user=user, name=events.REMINDER_SENT).exists())

    def test_the_link_signs_in_and_resumes(self):
        self.person(step="valeurs")
        with self.due_now():
            self.run_command()
        link = re.search(r"https://tri\.example\.fr(/connexion/lien/[^/\s]+/)", django_mail.outbox[0].body).group(1)
        response = self.client.post(link)
        self.assertRedirects(response, "/demarrer/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/demarrer/").context["step"].slug, "structures")

    def test_codes_are_issued_through_the_sign_in_rules(self):
        self.person()
        with mock.patch("onboarding.reminder.auth.issue_code", wraps=auth.issue_code) as issue, self.due_now():
            self.run_command()
        issue.assert_called_once()
        self.assertEqual(issue.call_args.args[:2], ("nora@example.fr", None))

    def test_mail_says_how_to_stop_the_reminder(self):
        self.person()
        with self.due_now():
            self.run_command()
        message = django_mail.outbox[0]
        for part in (message.body, message.alternatives[0][0]):
            text = " ".join(part.split())
            self.assertIn("Vous ne souhaitez plus ce rappel ? Ignorez ce message : nous ne vous relancerons pas.", text)
            self.assertNotIn("Vous n’avez rien demandé", text)

    def test_footer_and_unsubscribe_headers_over_https(self):
        user = self.person()
        with self.due_now():
            self.run_command()
        message = django_mail.outbox[0]
        self.assertEqual(message.extra_headers, unsubscribe.mail_headers(user))
        for part in (message.body, message.alternatives[0][0]):
            self.assertIn("Pour ne plus recevoir ces mails :", " ".join(part.split()))
            self.assertIn(unsubscribe.unsubscribe_url(user), part)
            assert_french_copy(self, part)

    @override_settings(SITE_URL="http://localhost:8000")
    def test_no_unsubscribe_headers_over_http(self):
        self.person()
        with self.due_now():
            self.run_command()
        self.assertEqual(django_mail.outbox[0].extra_headers, {})

    def test_unsubscribed_between_due_and_send_gets_no_mail_and_no_reminder_event(self):
        user = self.person()

        def unsubscribe_first(profile):
            User.objects.filter(pk=user.pk).update(status=User.Status.UNSUBSCRIBED)
            return 5

        with mock.patch("onboarding.reminder.minutes_left", side_effect=unsubscribe_first), self.due_now():
            output = self.run_command()
        self.assertEqual(django_mail.outbox, [])
        self.assertIn("0 relance(s) envoyée(s) sur 1.", output)
        self.assertFalse(OnboardingEvent.objects.filter(user=user, name=events.REMINDER_SENT).exists())
        self.assertIsNotNone(LoginCode.objects.get(email="nora@example.fr").invalidated_at)  # the code is voided

    def test_deleted_between_due_and_send_gets_no_mail_and_nothing_fails(self):
        user = self.person()

        def delete_first(profile):
            User.objects.filter(pk=user.pk).delete()
            return 5

        with mock.patch("onboarding.reminder.minutes_left", side_effect=delete_first), self.due_now():
            output = self.run_command()
        self.assertEqual(django_mail.outbox, [])
        self.assertIn("0 relance(s) envoyée(s) sur 1.", output)

    def test_one_minute_is_singular(self):
        self.person()
        with mock.patch("onboarding.reminder.minutes_left", return_value=1), self.due_now():
            self.run_command()
        self.assertTrue(django_mail.outbox[0].subject.endswith(" : il vous reste 1 minute sur tri-offres"))

    def test_never_a_second_reminder(self):
        self.person()
        with self.due_now():
            self.run_command()
            self.run_command()
        self.assertEqual(len(django_mail.outbox), 1)
        # Even if the person comes back and leaves again: the day after, still nothing.
        user = User.objects.get(email="nora@example.fr")
        OnboardingEvent.objects.create(user=user, name=events.step("contrats"), created_at=NOW)
        self.assertEqual(self.due_users(NOW + dt.timedelta(days=1)), [])

    def test_sign_in_limits_refuse_the_code_and_the_mail(self):
        user = self.person()
        make_login_code("nora@example.fr", ago=30)  # a code asked for 30 seconds ago: the resend delay applies
        with self.due_now():
            output = self.run_command()
        self.assertIn("0 relance(s) envoyée(s) sur 1.", output)
        self.assertEqual(django_mail.outbox, [])
        self.assertEqual(LoginCode.objects.filter(email="nora@example.fr").count(), 1)
        self.assertFalse(OnboardingEvent.objects.filter(user=user, name=events.REMINDER_SENT).exists())

    def test_earlier_codes_are_replaced_only_once_the_mail_went_out(self):
        self.person()
        make_login_code("nora@example.fr", ago=600)
        with mock.patch("onboarding.reminder.mail.send", side_effect=mail.MailDeliveryFailed("down")), \
                self.due_now():
            self.run_command()
        rows = LoginCode.objects.filter(email="nora@example.fr").order_by("pk")
        self.assertIsNone(rows[0].invalidated_at)  # the earlier code still works
        self.assertIsNotNone(rows[1].invalidated_at)  # the reminder's code is voided
        self.assertFalse(OnboardingEvent.objects.filter(name=events.REMINDER_SENT).exists())
        # Two minutes later (past the resend delay), the mail goes out and replaces the earlier code.
        LoginCode.objects.filter(pk=rows[1].pk).update(created_at=timezone.now() - dt.timedelta(minutes=2))
        with self.due_now():
            self.run_command()
        rows = LoginCode.objects.filter(email="nora@example.fr").order_by("pk")
        self.assertIsNotNone(rows[0].invalidated_at)
        self.assertIsNone(rows[2].invalidated_at)

    @override_settings(LOGIN_MAIL_DAILY_CAP=0)
    def test_site_cap(self):
        self.person()
        with self.due_now():
            self.run_command()
        self.assertEqual(django_mail.outbox, [])
        self.assertFalse(OnboardingEvent.objects.filter(name=events.REMINDER_SENT).exists())

    @override_settings(CLAUDE_ENGINE="oauth", DEV_EMAILS=["alexis@example.fr"])
    def test_oauth_engine_mails_dev_emails_only(self):
        self.person()
        with self.due_now():
            self.run_command()
        self.assertEqual(django_mail.outbox, [])

    def test_dry_run_sends_nothing(self):
        user = self.person()
        with self.due_now():
            output = self.run_command("--dry-run")
        minutes = flow.minutes_left("contrats", user.profile)
        self.assertIn(f"Relance à envoyer : utilisateur {user.pk}, {minutes} min restantes", output)
        self.assertIn("1 relance(s) à envoyer, aucune envoyée (--dry-run).", output)
        self.assertEqual(django_mail.outbox, [])
        self.assertFalse(LoginCode.objects.exists())
        self.assertFalse(OnboardingEvent.objects.filter(name=events.REMINDER_SENT).exists())

    def test_command_uses_the_real_clock(self):
        self.person(last_event=timezone.now() - dt.timedelta(days=1))
        with mock.patch("onboarding.reminder.due", wraps=reminder.due) as due:
            self.run_command("--dry-run")
        self.assertLess(abs(due.call_args.args[0] - timezone.now()), dt.timedelta(minutes=1))
