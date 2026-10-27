"""The step B gate (plan 2026-10-27, decisions 3.1 and 3.8): under the api engine a person without an OpenRouter key
goes to the connection screen before anything else, and is neither scored nor mailed; the other engines are untouched."""
import datetime as dt
import io
from unittest import mock

from django.core import mail as django_mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import events
from accounts.models import OnboardingEvent, OpenRouterKey, Profile, User
from accounts.testing import make_finished_profile, make_profile
from accounts.views import after_login_url
from config.testing import PLAIN_STATIC, assert_french_copy
from onboarding import reminder, schedule

CONNECT = "/openrouter/"
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
GATED = ["/tri/", "/mes-offres/", "/ma-recherche/", "/demarrer/", "/questionnaire/recherche/", "/fin/", "/synthese/"]
FREE = ["/mes-donnees/", "/mes-donnees/supprimer/", "/confidentialite/", "/mentions-legales/", "/openrouter/",
        "/openrouter/tuto/", "/openrouter/connecte/", "/openrouter/annulee/", "/interne/style/"]


def give_key(user, **fields):
    return OpenRouterKey.objects.create(user=user, encrypted_key="x", fingerprint="f" * 64, **fields)


@override_settings(CLAUDE_ENGINE="api", STORAGES=PLAIN_STATIC)
class MailableTests(TestCase):
    def test_only_an_account_with_a_working_key_is_mailable(self):
        keyless = make_profile("keyless@example.fr").user
        working = make_profile("working@example.fr").user
        refused = make_profile("refused@example.fr").user
        give_key(working)
        give_key(refused, refused_since=timezone.now())
        self.assertEqual(set(User.objects.mailable()), {working})
        self.assertEqual(set(User.objects.reachable()), {keyless, working, refused})

    def test_an_unsubscribed_account_with_a_key_is_not_mailable(self):
        user = make_profile().user
        give_key(user)
        User.objects.filter(pk=user.pk).update(status=User.Status.UNSUBSCRIBED)
        self.assertFalse(User.objects.mailable().exists())

    def test_the_other_engines_do_not_read_the_key(self):
        user = make_profile().user
        for engine in ("fake", "oauth", ""):
            with self.subTest(engine=engine), override_settings(CLAUDE_ENGINE=engine):
                self.assertEqual(list(User.objects.mailable()), [user])


@override_settings(CLAUDE_ENGINE="api", STORAGES=PLAIN_STATIC)
class GateTests(TestCase):
    def setUp(self):
        self.user = make_finished_profile().user
        self.client.force_login(self.user)

    def test_a_person_without_a_key_is_sent_to_the_connection_screen(self):
        for path in GATED:
            with self.subTest(path=path):
                self.assertRedirects(self.client.get(path), CONNECT, fetch_redirect_response=False)

    def test_a_post_is_refused_the_same_way(self):
        self.assertRedirects(self.client.post("/tri/avis/", {"offer": "1", "verdict": "yes"}), CONNECT,
                             fetch_redirect_response=False)

    def test_what_a_person_may_always_reach_stays_open(self):
        for path in FREE:
            with self.subTest(path=path):
                self.assertNotEqual(self.client.get(path, follow=False).status_code, 302)

    def test_sign_out_and_the_unsubscribe_page_stay_open(self):
        self.assertRedirects(self.client.post("/deconnexion/"), "/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/desinscription/abc/").status_code, 200)

    def test_deleting_the_account_stays_possible(self):
        response = self.client.post(reverse("onboarding:delete_account"))
        self.assertEqual(response.status_code, 303)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())

    def test_with_a_key_the_pages_open(self):
        give_key(self.user)
        for path in GATED[:3]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_a_refused_key_is_not_sent_back_to_connect(self):
        give_key(self.user, refused_since=timezone.now())
        self.assertEqual(self.client.get("/mes-offres/").status_code, 200)

    def test_the_other_engines_never_redirect(self):
        for engine in ("fake", "oauth"):
            with self.subTest(engine=engine), override_settings(CLAUDE_ENGINE=engine):
                self.assertEqual(self.client.get("/mes-offres/").status_code, 200)

    def test_an_anonymous_visitor_keeps_the_usual_answer(self):
        self.client.logout()
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertNotEqual(self.client.get("/mes-offres/")["Location"], CONNECT)

    def test_sign_in_lands_on_the_connection_screen(self):
        self.assertEqual(after_login_url(self.user), CONNECT)
        give_key(self.user)
        self.assertEqual(after_login_url(self.user), "/tri/")

    def test_a_new_account_goes_to_the_connection_screen_before_the_questionnaire(self):
        user = User.objects.create_user("new@example.fr")
        Profile.objects.create(user=user)
        self.assertEqual(after_login_url(user), CONNECT)

    def test_the_admin_and_the_health_check_are_not_gated(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/admin/login/").status_code, 200)


@override_settings(CLAUDE_ENGINE="api", STORAGES=PLAIN_STATIC, EMAIL_BACKEND=LOCMEM, DEV_EMAILS=[],
                   SITE_URL="https://tri.example.fr")
class InviteCommandTests(TestCase):
    def run_command(self, *args):
        out = io.StringIO()
        call_command("inviter_openrouter", *args, stdout=out)
        return out.getvalue()

    def setUp(self):
        self.keyless = make_profile("keyless@example.fr").user
        self.connected = make_profile("connected@example.fr").user
        give_key(self.connected)
        self.unsubscribed = make_profile("gone@example.fr").user
        User.objects.filter(pk=self.unsubscribed.pk).update(status=User.Status.UNSUBSCRIBED)

    def test_every_active_account_without_a_key_gets_one_mail(self):
        self.run_command()
        self.assertEqual([m.to for m in django_mail.outbox], [["keyless@example.fr"]])
        message = django_mail.outbox[0]
        self.assertIn("https://tri.example.fr/openrouter/", message.body)
        self.assertIn("List-Unsubscribe", message.extra_headers)
        self.assertTrue(OnboardingEvent.objects.filter(user=self.keyless, name=events.OPENROUTER_INVITED).exists())

    def test_a_second_run_sends_nothing(self):
        self.run_command()
        self.run_command()
        self.assertEqual(len(django_mail.outbox), 1)

    def test_the_dry_run_sends_and_records_nothing(self):
        out = self.run_command("--dry-run")
        self.assertEqual(django_mail.outbox, [])
        self.assertFalse(OnboardingEvent.objects.filter(name=events.OPENROUTER_INVITED).exists())
        self.assertIn("1 ", out)

    def test_a_mail_that_fails_leaves_no_trace_so_the_next_run_retries(self):
        with mock.patch("accounts.management.commands.inviter_openrouter.mail.send", return_value=0):
            self.run_command()
        self.assertFalse(OnboardingEvent.objects.filter(name=events.OPENROUTER_INVITED).exists())
        self.run_command()
        self.assertEqual(len(django_mail.outbox), 1)

    def test_the_mail_is_written_in_french_without_the_forbidden_words(self):
        self.run_command()
        assert_french_copy(self, django_mail.outbox[0].alternatives[0][0])
        assert_french_copy(self, django_mail.outbox[0].body)


@override_settings(CLAUDE_ENGINE="api", STORAGES=PLAIN_STATIC, EMAIL_BACKEND=LOCMEM, DEV_EMAILS=[],
                   SITE_URL="https://tri.example.fr")
class ReminderTests(TestCase):
    """The single reminder also goes to the account that signed in and never connected OpenRouter."""

    NOW = dt.datetime(2026, 10, 14, 18, 0, tzinfo=schedule.PARIS)
    YESTERDAY = dt.datetime(2026, 10, 13, 10, 0, tzinfo=schedule.PARIS)

    def person(self, email="nora@example.fr"):
        user = User.objects.create_user(email)
        Profile.objects.create(user=user)
        OnboardingEvent.objects.create(user=user, name=events.CODE_ENTERED, created_at=self.YESTERDAY)
        return user

    def due(self):
        return [profile.user.email for profile in reminder.due(self.NOW)]

    def test_an_account_that_never_connected_is_reminded_once(self):
        user = self.person()
        self.assertEqual(self.due(), ["nora@example.fr"])
        OnboardingEvent.objects.create(user=user, name=events.REMINDER_SENT, created_at=self.YESTERDAY)
        self.assertEqual(self.due(), [])

    def test_an_unsubscribed_account_is_still_left_out(self):
        user = self.person()
        User.objects.filter(pk=user.pk).update(status=User.Status.UNSUBSCRIBED)
        self.assertEqual(self.due(), [])

    def test_the_mail_says_the_extra_step_only_to_those_without_a_key(self):
        keyless, connected = self.person(), self.person("paul@example.fr")
        give_key(connected)
        for user, expected in ((keyless, True), (connected, False)):
            with self.subTest(user=user.email):
                django_mail.outbox.clear()
                self.assertTrue(reminder.remind(Profile.objects.get(user=user)))
                body = " ".join(django_mail.outbox[0].body.split())
                self.assertEqual("connecter votre compte OpenRouter" in body, expected)
                assert_french_copy(self, django_mail.outbox[0].alternatives[0][0])
