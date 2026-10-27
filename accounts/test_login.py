"""Passwordless sign-in by code and link.

Contract: the design plan, sections 1 to 8 as amended by section 9. Every test listed in
sections 8 and 9.3 lives here (mail.send's DEV_EMAILS guard is in mail/tests.py).
"""
import re
import urllib.error
from datetime import timedelta
from io import BytesIO, StringIO
from unittest import mock

from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.models import Session
from django.core import mail as django_mail
from django.core.management import call_command
from django.db import connection
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

import mail
from accounts import auth, events
from accounts.models import LoginCode, OnboardingEvent, Profile, User, WaitlistEntry
from accounts.testing import make_finished_profile, make_login_code
from accounts.views import after_login_url
from config.testing import PLAIN_STATIC, assert_french_copy, assert_unique_ids

SITE = "https://tri.example.fr"
HOME, CODE_URL, RESEND_URL = "/", "/connexion/code/", "/connexion/renvoyer/"
LOGOUT_URL, START, SORT = "/deconnexion/", "/demarrer/", "/tri/"

RATE_LIMITED = ("Trop de demandes pour cette adresse. Réessayez dans quelques minutes, "
                "ou utilisez le dernier code reçu.")
EXPIRED = "Ce code a expiré. Demandez-en un nouveau."
TOO_MANY = "Trop d’essais. Demandez un nouveau code."
SESSION_CAP = "Trop d’essais. Réessayez dans une heure, ou utilisez le lien reçu par mail."
BETA_FULL = ("La bêta est complète. Si vous avez déjà un compte, votre code vient de partir. "
             "Sinon, votre adresse est sur la liste d’attente : nous vous écrirons quand une place se libère.")
LINK_EXPIRED = "Ce lien a expiré"
E2_HEADERS = {"Referrer-Policy": "strict-origin", "Cache-Control": "no-store", "X-Robots-Tag": "noindex"}


def wrong(remaining):
    return f"Ce code ne correspond pas. Il vous reste {remaining} essai{'s' if remaining > 1 else ''}."


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", CLAUDE_ENGINE="", DEV_EMAILS=[],
    BETA_CAPACITY=80, LOGIN_MAIL_DAILY_CAP=60, TRUST_CF_CONNECTING_IP=False, SITE_URL=SITE,
    STORAGES=PLAIN_STATIC, ALLOWED_HOSTS=["testserver", "autre.example.fr"])
class LoginTestCase(TestCase):
    def request_code(self, email="nora@example.fr", client=None, **extra):
        return (client or self.client).post(HOME, {"email": email}, **extra)

    def enter_code(self, code, client=None, path=CODE_URL, **data):
        return (client or self.client).post(path, {"code": code, **data})

    @staticmethod
    def code_in(message):
        return re.search(r"\b(\d{3}) (\d{3})\b", message.subject).group(0).replace(" ", "")

    @staticmethod
    def link_in(message):
        return re.search(re.escape(SITE) + r"(/connexion/lien/[A-Za-z0-9_-]+/)", message.body).group(1)

    @staticmethod
    def not_the(code):
        return "000000" if code != "000000" else "000001"

    @staticmethod
    def age(seconds, **filters):
        """Move rows back in time, as if they had been requested `seconds` earlier."""
        for row in LoginCode.objects.filter(**filters):
            row.created_at -= timedelta(seconds=seconds)
            row.expires_at -= timedelta(seconds=seconds)
            row.save()

    @staticmethod
    def with_login_email(client, email):
        session = client.session
        session[auth.SESSION_EMAIL] = email
        session.save()

    def signed_in_user(self, client=None):
        user_id = (client or self.client).session.get(SESSION_KEY)
        return User.objects.get(pk=user_id) if user_id else None

    @staticmethod
    def text_of(response):
        """The page source with runs of whitespace collapsed, so wrapped template lines read as one."""
        return " ".join(response.content.decode().split())

    def normalized(self, response, email):
        """A page with what legitimately differs between two requests (address, CSRF token) blanked out."""
        html = self.text_of(response).replace(email, "ADDRESS")
        return re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', 'name="csrfmiddlewaretoken"', html)


class EndToEndTests(LoginTestCase):
    def test_sign_in_by_code_read_in_the_mail(self):
        response = self.request_code("  Nora@Example.FR ")
        self.assertRedirects(response, CODE_URL)
        self.assertEqual(len(django_mail.outbox), 1)
        message = django_mail.outbox[0]
        self.assertEqual(message.to, ["nora@example.fr"])

        response = self.enter_code(self.code_in(message))

        self.assertRedirects(response, START)
        user = User.objects.get(email="nora@example.fr")
        self.assertEqual(self.signed_in_user(), user)
        self.assertTrue(Profile.objects.filter(user=user).exists())
        self.assertEqual(self.client.get(START).status_code, 200)

    def test_sign_in_by_link_get_then_post(self):
        self.request_code()
        link = self.link_in(django_mail.outbox[0])

        response = self.client.get(link)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "nora@example.fr")
        self.assertIsNone(self.signed_in_user())

        response = self.client.post(link)
        self.assertRedirects(response, START)
        self.assertEqual(self.signed_in_user().email, "nora@example.fr")

    def test_link_opened_on_another_device_shows_the_code_and_signs_in_there(self):
        self.request_code()
        message = django_mail.outbox[0]
        code = self.code_in(message)
        phone = Client()
        response = phone.get(self.link_in(message))
        self.assertContains(response, f"{code[:3]} {code[3:]}")
        self.assertRedirects(phone.post(self.link_in(message)), START)
        self.assertEqual(self.signed_in_user(phone).email, "nora@example.fr")
        self.assertIsNone(self.signed_in_user())

    def test_code_typed_with_spaces_is_accepted(self):
        self.request_code()
        code = self.code_in(django_mail.outbox[0])
        self.assertRedirects(self.enter_code(f" {code[:3]} {code[3:]} "), START)

    def test_events_record_the_funnel_without_address(self):
        self.request_code()
        self.enter_code(self.code_in(django_mail.outbox[0]))
        requested = OnboardingEvent.objects.get(name=events.CODE_REQUESTED)
        self.assertIsNone(requested.user)
        self.assertEqual(requested.details, {})
        self.assertEqual(OnboardingEvent.objects.get(name=events.CODE_ENTERED).user.email, "nora@example.fr")

    def test_link_use_is_recorded(self):
        self.request_code()
        self.client.post(self.link_in(django_mail.outbox[0]))
        self.assertTrue(OnboardingEvent.objects.filter(name=events.LINK_USED, user__email="nora@example.fr").exists())

    def test_existing_user_signs_in_to_the_same_account(self):
        user = User.objects.create_user("nora@example.fr")
        self.request_code("NORA@example.fr")
        self.enter_code(self.code_in(django_mail.outbox[0]))
        self.assertEqual(self.signed_in_user(), user)
        self.assertEqual(User.objects.count(), 1)


class StorageTests(LoginTestCase):
    def test_code_and_token_never_stored_in_clear(self):
        token = "Qm9uam91ciBOb3JhLCBjZWNpIGVzdCB1biBqZXRvbg"
        with mock.patch("accounts.auth.secrets.token_urlsafe", return_value=token):
            self.request_code()
        code = auth.code_for_token(token)
        self.assertIn(code[:3] + " " + code[3:], django_mail.outbox[0].subject)
        dump = []
        with connection.cursor() as cursor:
            for table in connection.introspection.table_names(cursor):
                cursor.execute(f'SELECT * FROM "{table}"')
                dump.append(repr(cursor.fetchall()))
        dump = "\n".join(dump)
        self.assertNotIn(token, dump)
        # Timestamps carry six-digit microseconds, which could match the code by chance.
        dump = re.sub(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\.\d{6}", "", dump)
        self.assertNotIn(code, dump)

    def test_sent_mail_is_stamped(self):
        self.request_code()
        self.assertIsNotNone(LoginCode.objects.get().sent_at)

    def test_hashes_are_64_hex_characters(self):
        self.request_code()
        row = LoginCode.objects.get()
        for value in (row.code_hash, row.token_hash):
            self.assertRegex(value, r"^[0-9a-f]{64}$")

    def test_code_hash_depends_on_the_address(self):
        self.assertNotEqual(auth.hash_code("a@example.fr", "123456"), auth.hash_code("b@example.fr", "123456"))

    def test_code_is_six_digits_derived_from_the_token(self):
        token = auth.new_token()
        self.assertEqual(len(token), 43)
        self.assertRegex(auth.code_for_token(token), r"^\d{6}$")
        self.assertEqual(auth.code_for_token(token), auth.code_for_token(token))

    def test_codes_and_links_refused_after_secret_key_change(self):
        self.request_code()
        message = django_mail.outbox[0]
        old_key = settings.SECRET_KEY
        # The fallback keeps the session readable, so only the code and link hashes are affected.
        with override_settings(SECRET_KEY="another-secret-key-for-this-test-only-0123456789",
                               SECRET_KEY_FALLBACKS=[old_key]):
            response = self.enter_code(self.code_in(message))
            self.assertContains(response, wrong(4))
            self.assertIsNone(self.signed_in_user())
            self.assertContains(self.client.get(self.link_in(message)), LINK_EXPIRED)
            self.client.post(self.link_in(message))
            self.assertIsNone(self.signed_in_user())


class LinkPageTests(LoginTestCase):
    def test_get_changes_nothing(self):
        self.request_code()
        link = self.link_in(django_mail.outbox[0])
        rows = list(LoginCode.objects.values())
        sessions = list(Session.objects.values())
        other = Client()
        with CaptureQueriesContext(connection) as queries:
            response = other.get(link)
        self.assertEqual(response.status_code, 200)
        writes = [q["sql"] for q in queries.captured_queries
                  if q["sql"].split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}]
        self.assertEqual(writes, [])
        self.assertEqual(list(LoginCode.objects.values()), rows)
        self.assertEqual(list(Session.objects.values()), sessions)
        self.assertEqual(User.objects.count(), 0)
        self.assertIsNone(self.signed_in_user(other))

    def test_valid_link_page_copy_and_headers(self):
        self.request_code()
        message = django_mail.outbox[0]
        response = self.client.get(self.link_in(message))
        self.assertContains(response, "Connexion à tri-offres")
        self.assertContains(response, "en tant que <strong>nora@example.fr</strong>", html=False)
        self.assertContains(response, "Me connecter")
        code = self.code_in(message)
        self.assertIn("Vous aviez commencé sur un autre appareil ? Tapez-y plutôt le code "
                      f'<span class="typed">{code[:3]} {code[3:]}</span>.', self.text_of(response))
        for name, value in E2_HEADERS.items():
            self.assertEqual(response[name], value)
        html = response.content.decode()
        assert_french_copy(self, html)
        assert_unique_ids(self, html)

    def test_invalid_tokens_answer_200_with_the_three_headers(self):
        for token in ("x" * 43, "nope", "a" * 300, "%00"):
            with self.subTest(token=token):
                for method in ("get", "post"):
                    response = getattr(self.client, method)(f"/connexion/lien/{token}/")
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, LINK_EXPIRED)
                    self.assertContains(response, "Demandez un nouveau code : il arrive en moins d’une minute.")
                    for name, value in E2_HEADERS.items():
                        self.assertEqual(response[name], value)
        self.assertIsNone(self.signed_in_user())

    def test_successful_post_carries_the_headers_too(self):
        self.request_code()
        response = self.client.post(self.link_in(django_mail.outbox[0]))
        for name, value in E2_HEADERS.items():
            self.assertEqual(response[name], value)

    def test_used_link_is_expired(self):
        self.request_code()
        link = self.link_in(django_mail.outbox[0])
        self.client.post(link)
        self.client.post(LOGOUT_URL)
        self.assertContains(self.client.get(link), LINK_EXPIRED)
        self.client.post(link)
        self.assertIsNone(self.signed_in_user())

    def test_expired_link(self):
        self.request_code()
        self.age(16 * 60)
        link = self.link_in(django_mail.outbox[0])
        self.assertContains(self.client.get(link), LINK_EXPIRED)
        self.client.post(link)
        self.assertIsNone(self.signed_in_user())

    def test_link_post_clears_the_pending_address(self):
        self.request_code()
        link = self.link_in(django_mail.outbox[0])
        self.client.post(link)
        self.assertNotIn(auth.SESSION_EMAIL, self.client.session)

    def test_link_post_while_signed_in_as_someone_else_switches_cleanly(self):
        bob = User.objects.create_user("bob@example.fr")
        self.client.force_login(bob)
        session = self.client.session
        session["marker"] = "bob was here"
        session.save()
        bob_key = session.session_key

        self.request_code("nora@example.fr", client=Client())
        response = self.client.post(self.link_in(django_mail.outbox[0]))

        self.assertRedirects(response, START)
        self.assertEqual(self.signed_in_user().email, "nora@example.fr")
        self.assertNotIn("marker", self.client.session)
        self.assertNotEqual(self.client.session.session_key, bob_key)
        self.assertFalse(Session.objects.filter(session_key=bob_key).exists())


class CodeTests(LoginTestCase):
    def test_five_wrong_attempts_then_the_code_is_blocked(self):
        self.request_code()
        code = self.code_in(django_mail.outbox[0])
        for remaining in (4, 3, 2, 1):
            self.assertContains(self.enter_code(self.not_the(code)), wrong(remaining))
        self.assertContains(self.enter_code(self.not_the(code)), TOO_MANY)
        self.assertEqual(LoginCode.objects.get().attempts, 5)
        self.assertContains(self.enter_code(code), TOO_MANY)
        self.assertIsNone(self.signed_in_user())
        self.assertEqual(LoginCode.objects.get().attempts, 5)

    def test_right_code_refused_after_five_wrong_ones_but_the_link_still_works(self):
        self.request_code()
        message = django_mail.outbox[0]
        for _ in range(5):
            self.enter_code(self.not_the(self.code_in(message)))
        self.assertContains(self.enter_code(self.code_in(message)), TOO_MANY)
        self.assertIsNone(self.signed_in_user())
        self.assertRedirects(self.client.post(self.link_in(message)), START)
        self.assertEqual(self.signed_in_user().email, "nora@example.fr")

    def test_expired_code(self):
        self.request_code()
        self.age(16 * 60)
        self.assertContains(self.enter_code(self.code_in(django_mail.outbox[0])), EXPIRED)
        self.assertIsNone(self.signed_in_user())

    def test_used_code(self):
        self.request_code()
        code = self.code_in(django_mail.outbox[0])
        self.enter_code(code)
        other = Client()
        self.with_login_email(other, "nora@example.fr")
        self.assertContains(self.enter_code(code, client=other), EXPIRED)
        self.assertIsNone(self.signed_in_user(other))

    def test_new_code_invalidates_the_previous_one(self):
        self.request_code()
        first = django_mail.outbox[0]
        self.age(61)
        self.request_code()
        second = django_mail.outbox[1]
        self.assertEqual(LoginCode.objects.filter(invalidated_at=None).count(), 1)
        self.assertContains(self.client.get(self.link_in(first)), LINK_EXPIRED)
        if self.code_in(first) != self.code_in(second):
            self.assertContains(self.enter_code(self.code_in(first)), wrong(4))
        self.assertIsNone(self.signed_in_user())
        self.assertRedirects(self.enter_code(self.code_in(second)), START)

    def test_posted_email_does_not_change_the_checked_address(self):
        self.request_code("nora@example.fr")
        self.request_code("bob@example.fr", client=Client())
        nora_code, bob_code = (self.code_in(m) for m in django_mail.outbox)
        if bob_code != nora_code:
            self.assertContains(self.enter_code(bob_code, email="bob@example.fr", login_email="bob@example.fr"),
                                wrong(4))
        self.assertIsNone(self.signed_in_user())
        self.enter_code(nora_code, email="bob@example.fr")
        self.assertEqual(self.signed_in_user().email, "nora@example.fr")
        self.assertFalse(User.objects.filter(email="bob@example.fr").exists())

    def test_malformed_code_does_not_count_as_an_attempt(self):
        self.request_code()
        for value in ("", "12", "12a456", "1234567"):
            with self.subTest(value=value):
                response = self.enter_code(value)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "field-error")
        self.assertEqual(LoginCode.objects.get().attempts, 0)

    def test_session_cap_of_twenty_failures_an_hour(self):
        for _ in range(4):
            _, code = make_login_code("nora@example.fr")
            self.with_login_email(self.client, "nora@example.fr")
            for _ in range(5):
                self.enter_code(self.not_the(code))
        token, code = make_login_code("nora@example.fr")
        response = self.enter_code(code)
        self.assertContains(response, SESSION_CAP)
        self.assertIsNone(self.signed_in_user())
        # The cap belongs to the session: another browser with the same address is not blocked.
        other = Client()
        self.with_login_email(other, "nora@example.fr")
        self.assertRedirects(self.enter_code(code, client=other), START)

    def test_code_page_without_pending_address_goes_home(self):
        self.assertRedirects(self.client.get(CODE_URL), HOME)
        self.assertRedirects(self.enter_code("123456"), HOME)
        self.assertRedirects(self.client.post(RESEND_URL), HOME)

    def test_no_next_parameter_is_followed(self):
        self.request_code()
        message = django_mail.outbox[0]
        response = self.enter_code(self.code_in(message), path=CODE_URL + "?next=https://evil.example/",
                                   next="https://evil.example/")
        self.assertRedirects(response, START)
        self.client.post(LOGOUT_URL)
        self.age(61)
        self.request_code()
        self.assertEqual(len(django_mail.outbox), 2)
        response = self.client.post(self.link_in(django_mail.outbox[-1]) + "?next=//evil.example/",
                                    {"next": "//evil.example/"})
        self.assertRedirects(response, START)

    def test_a_finished_questionnaire_signs_in_to_the_sort_whatever_next_says(self):
        make_finished_profile("nora@example.fr")
        self.request_code()
        response = self.enter_code(self.code_in(django_mail.outbox[0]), path=CODE_URL + "?next=https://evil.example/",
                                   next="https://evil.example/")
        self.assertRedirects(response, SORT, fetch_redirect_response=False)
        self.client.post(LOGOUT_URL)
        self.age(61)
        self.request_code()
        response = self.client.post(self.link_in(django_mail.outbox[-1]) + "?next=//evil.example/",
                                    {"next": "//evil.example/"})
        self.assertRedirects(response, SORT, fetch_redirect_response=False)

    def test_inactive_user_is_refused(self):
        User.objects.create_user("nora@example.fr", is_active=False)
        self.request_code()
        message = django_mail.outbox[0]
        self.assertContains(self.enter_code(self.code_in(message)), EXPIRED)
        self.assertIsNone(self.signed_in_user())
        response = self.client.post(self.link_in(message))
        self.assertContains(response, LINK_EXPIRED)
        self.assertIsNone(self.signed_in_user())


class RateLimitTests(LoginTestCase):
    def test_resend_within_sixty_seconds_is_refused_without_mail(self):
        self.request_code()
        response = self.client.post(RESEND_URL)
        self.assertRedirects(response, CODE_URL, fetch_redirect_response=False)
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertEqual(LoginCode.objects.count(), 1)
        self.assertContains(self.client.get(CODE_URL), RATE_LIMITED)

    def test_resend_after_sixty_seconds_sends_a_new_code(self):
        self.request_code()
        self.age(61)
        self.assertRedirects(self.client.post(RESEND_URL), CODE_URL)
        self.assertEqual(len(django_mail.outbox), 2)
        self.assertNotContains(self.client.get(CODE_URL), RATE_LIMITED)
        self.assertRedirects(self.enter_code(self.code_in(django_mail.outbox[1])), START)

    def test_fourth_request_in_ten_minutes_is_refused_without_mail(self):
        for _ in range(3):
            self.request_code()
            self.age(61)
        self.assertEqual(len(django_mail.outbox), 3)
        response = self.request_code()
        self.assertRedirects(response, CODE_URL, fetch_redirect_response=False)
        self.assertEqual(len(django_mail.outbox), 3)
        self.assertEqual(LoginCode.objects.count(), 3)
        self.assertContains(self.client.get(CODE_URL), RATE_LIMITED)
        self.age(10 * 60)
        self.request_code()
        self.assertEqual(len(django_mail.outbox), 4)

    def test_rate_limited_message_is_shown_once(self):
        self.request_code()
        self.request_code()
        self.assertContains(self.client.get(CODE_URL), RATE_LIMITED)
        self.assertNotContains(self.client.get(CODE_URL), RATE_LIMITED)

    def test_ten_requests_a_day_per_address(self):
        for hours in range(1, 10):
            make_login_code("nora@example.fr", ago=hours * 3600)
        self.request_code()
        self.assertEqual(len(django_mail.outbox), 1)
        self.age(11 * 60)
        self.request_code()
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertContains(self.client.get(CODE_URL), RATE_LIMITED)
        self.age(15 * 3600, created_at__lt=timezone.now() - timedelta(hours=8, minutes=30))
        self.request_code()
        self.assertEqual(len(django_mail.outbox), 2)

    def test_ten_requests_an_hour_per_ip(self):
        for n in range(10):
            self.request_code(f"person{n}@example.fr", client=Client(), REMOTE_ADDR="203.0.113.5")
        self.assertEqual(len(django_mail.outbox), 10)
        refused = Client()
        self.request_code("late@example.fr", client=refused, REMOTE_ADDR="203.0.113.5")
        self.assertEqual(len(django_mail.outbox), 10)
        self.assertContains(refused.get(CODE_URL), RATE_LIMITED)
        self.request_code("late@example.fr", client=Client(), REMOTE_ADDR="203.0.113.6")
        self.assertEqual(len(django_mail.outbox), 11)

    def test_ipv6_is_counted_per_64(self):
        for n in range(10):
            self.request_code(f"person{n}@example.fr", client=Client(), REMOTE_ADDR=f"2001:db8:1:2::{n + 1:x}")
        self.request_code("late@example.fr", client=Client(), REMOTE_ADDR="2001:db8:1:2:ffff:ffff:ffff:1")
        self.assertEqual(len(django_mail.outbox), 10)
        self.request_code("late@example.fr", client=Client(), REMOTE_ADDR="2001:db8:1:3::1")
        self.assertEqual(len(django_mail.outbox), 11)

    @override_settings(LOGIN_MAIL_DAILY_CAP=2)
    def test_site_cap_counts_mails_actually_sent(self):
        for n in range(3):
            make_login_code(f"unsent{n}@example.fr", sent=False)
        make_login_code("sent@example.fr", ago=25 * 3600)
        self.request_code()
        self.assertEqual(len(django_mail.outbox), 1)

    @override_settings(LOGIN_MAIL_DAILY_CAP=2)
    def test_site_daily_cap_stops_sending_and_logs_for_alexis(self):
        self.request_code("a@example.fr", client=Client(), REMOTE_ADDR="203.0.113.1")
        self.request_code("b@example.fr", client=Client(), REMOTE_ADDR="203.0.113.2")
        third = Client()
        with self.assertLogs("accounts", level="ERROR"):
            response = self.request_code("c@example.fr", client=third, REMOTE_ADDR="203.0.113.3")
        self.assertRedirects(response, CODE_URL, fetch_redirect_response=False)
        self.assertEqual(len(django_mail.outbox), 2)
        page = third.get(CODE_URL)
        self.assertContains(page, "Vérifiez vos mails")
        self.assertNotContains(page, RATE_LIMITED)


class SameAnswerTests(LoginTestCase):
    """Whether an address has an account, is new or goes to the waiting list, the answer looks the same."""

    def request_from_fresh_client(self, email):
        """A request from its own browser and IP, so that only the address limits apply."""
        client = Client()
        before = len(django_mail.outbox)
        self.request_code(email, client=client, REMOTE_ADDR=f"203.0.113.{LoginCode.objects.count() + 1}")
        return client, len(django_mail.outbox) - before

    @staticmethod
    def wrong_code_for(email):
        row = LoginCode.objects.filter(email=email).latest("created_at")
        return next(c for c in ("000000", "000001") if auth.hash_code(email, c) != row.code_hash)

    def test_existing_and_new_addresses(self):
        User.objects.create_user("known@example.fr")
        pages, mails = [], []
        for email in ("known@example.fr", "new@example.fr"):
            client, sent = self.request_from_fresh_client(email)
            mails.append(sent)
            pages.append(self.normalized(client.get(CODE_URL), email))
        self.assertEqual(mails, [1, 1])
        self.assertEqual(pages[0], pages[1])

    @override_settings(BETA_CAPACITY=1)
    def test_beta_full_known_address_gets_its_code_unknown_gets_the_waiting_list(self):
        User.objects.create_user("known@example.fr")
        User.objects.create_user("staff@example.fr", is_staff=True)
        pages = []
        known, sent = self.request_from_fresh_client("known@example.fr")
        self.assertEqual(sent, 1)
        self.assertIn("est votre code tri-offres", django_mail.outbox[-1].subject)
        pages.append(self.normalized(known.get(CODE_URL), "known@example.fr"))
        stranger, sent = self.request_from_fresh_client("stranger@example.fr")
        self.assertEqual(sent, 1)
        self.assertEqual(django_mail.outbox[-1].subject, "Vous êtes sur la liste d’attente de tri-offres")
        self.assertTrue(WaitlistEntry.objects.filter(email="stranger@example.fr").exists())
        self.assertTrue(LoginCode.objects.filter(email="stranger@example.fr").exists())
        pages.append(self.normalized(stranger.get(CODE_URL), "stranger@example.fr"))
        self.assertEqual(pages[0], pages[1])
        self.assertIn(BETA_FULL, pages[0])

    @override_settings(BETA_CAPACITY=1)
    def test_waiting_list_entry_is_idempotent(self):
        User.objects.create_user("known@example.fr")
        self.request_code("stranger@example.fr")
        self.age(61)
        self.request_code("stranger@example.fr")
        self.assertEqual(WaitlistEntry.objects.filter(email="stranger@example.fr").count(), 1)

    def test_same_wrong_code_and_rate_limit_messages_and_mail_counts(self):
        User.objects.create_user("known@example.fr")
        wrong_pages, mail_counts = {}, []
        for email, capacity in (("known@example.fr", 80), ("new@example.fr", 80), ("stranger@example.fr", 1)):
            with self.subTest(email=email), override_settings(BETA_CAPACITY=capacity):
                client, sent = self.request_from_fresh_client(email)
                mail_counts.append(sent)
                response = self.enter_code(self.wrong_code_for(email), client=client)
                self.assertContains(response, wrong(4))
                wrong_pages[email] = self.normalized(response, email)
                before = len(django_mail.outbox)
                client.post(RESEND_URL)
                mail_counts.append(len(django_mail.outbox) - before)
                self.assertContains(client.get(CODE_URL), RATE_LIMITED)
        self.assertEqual(mail_counts, [1, 0, 1, 0, 1, 0])
        self.assertEqual(wrong_pages["known@example.fr"], wrong_pages["new@example.fr"])

    @override_settings(BETA_CAPACITY=1)
    def test_staff_accounts_do_not_fill_the_beta(self):
        User.objects.create_user("staff@example.fr", is_staff=True)
        self.request_code("new@example.fr")
        self.assertIn("est votre code tri-offres", django_mail.outbox[0].subject)

    def test_beta_filled_between_request_and_sign_in_puts_the_address_on_the_list(self):
        self.request_code("new@example.fr")
        code = self.code_in(django_mail.outbox[0])
        with override_settings(BETA_CAPACITY=0):
            response = self.enter_code(code)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La bêta est complète")
        self.assertIsNone(self.signed_in_user())
        self.assertFalse(User.objects.filter(email="new@example.fr").exists())
        self.assertTrue(WaitlistEntry.objects.filter(email="new@example.fr").exists())
        self.assertIsNotNone(LoginCode.objects.get().used_at)
        assert_french_copy(self, response.content.decode())


class SessionTests(LoginTestCase):
    def test_session_key_rotates_at_sign_in(self):
        self.request_code()
        before = self.client.session.session_key
        self.assertTrue(before)
        self.enter_code(self.code_in(django_mail.outbox[0]))
        after = self.client.session.session_key
        self.assertNotEqual(before, after)
        self.assertFalse(Session.objects.filter(session_key=before).exists())

    def test_pending_address_is_removed_at_sign_in(self):
        self.request_code()
        self.enter_code(self.code_in(django_mail.outbox[0]))
        self.assertNotIn(auth.SESSION_EMAIL, self.client.session)

    def test_staff_session_lasts_twelve_hours(self):
        User.objects.create_user("alexis@example.fr", is_staff=True)
        self.request_code("alexis@example.fr")
        self.enter_code(self.code_in(django_mail.outbox[0]))
        self.assertAlmostEqual(self.client.session.get_expiry_age(), 12 * 3600, delta=60)

    def test_ordinary_session_lasts_thirty_days_from_sign_in(self):
        self.request_code()
        self.enter_code(self.code_in(django_mail.outbox[0]))
        session = self.client.session
        expiry = session.get_expiry_date()
        self.assertAlmostEqual(session.get_expiry_age(), 30 * 24 * 3600, delta=60)
        # A later write to the session does not push the date back.
        session["later"] = True
        session.save()
        self.assertEqual(self.client.session.get_expiry_date(), expiry)

    def test_admin_login_signal_applies_to_force_login_too(self):
        self.client.force_login(User.objects.create_user("alexis@example.fr", is_staff=True))
        self.assertAlmostEqual(self.client.session.get_expiry_age(), 12 * 3600, delta=60)


class CsrfTests(LoginTestCase):
    def test_every_post_without_token_is_refused(self):
        client = Client(enforce_csrf_checks=True)
        for url, data in ((HOME, {"email": "nora@example.fr"}), (CODE_URL, {"code": "123456"}),
                          (RESEND_URL, {}), (f"/connexion/lien/{'x' * 43}/", {}), (LOGOUT_URL, {})):
            with self.subTest(url=url):
                self.assertEqual(client.post(url, data).status_code, 403)
        self.assertEqual(django_mail.outbox, [])

    def link_page_token(self, client):
        """A browser's view of the link page: the CSRF token from its form."""
        self.request_code()
        link = self.link_in(django_mail.outbox[0])
        page = client.get(link)
        return link, re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)

    def test_link_post_from_a_browser_passes_csrf(self):
        # Under Referrer-Policy: strict-origin, a browser sends the real Origin and an origin-only Referer.
        browser = Client(enforce_csrf_checks=True)
        link, token = self.link_page_token(browser)
        response = browser.post(link, {"csrfmiddlewaretoken": token},
                                HTTP_ORIGIN="http://testserver", HTTP_REFERER="http://testserver/")
        self.assertRedirects(response, START)
        self.assertEqual(self.signed_in_user(browser).email, "nora@example.fr")

    def test_null_origin_is_refused_which_is_why_e2_is_not_no_referrer(self):
        # Chromium sends Origin: null on a form POST from a no-referrer page; Django refuses it.
        browser = Client(enforce_csrf_checks=True)
        link, token = self.link_page_token(browser)
        response = browser.post(link, {"csrfmiddlewaretoken": token}, HTTP_ORIGIN="null")
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.signed_in_user(browser))
        self.assertIsNone(LoginCode.objects.get().used_at)

    def test_csrf_failure_page(self):
        response = Client(enforce_csrf_checks=True).post(HOME, {"email": "nora@example.fr"})
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "La page a expiré", status_code=403)
        self.assertContains(response, "Rechargez-la, puis réessayez. Si cela recommence, demandez un nouveau code.",
                            status_code=403)
        self.assertContains(response, 'href="/"', status_code=403)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "strict-origin")
        html = response.content.decode()
        assert_french_copy(self, html)
        assert_unique_ids(self, html)

    def test_post_with_token_is_accepted(self):
        client = Client(enforce_csrf_checks=True)
        client.get(HOME)
        token = client.cookies["csrftoken"].value
        response = client.post(HOME, {"email": "nora@example.fr", "csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(django_mail.outbox), 1)


class ClientIpTests(SimpleTestCase):
    def request(self, **meta):
        return RequestFactory().get("/", **meta)

    @override_settings(TRUST_CF_CONNECTING_IP=False)
    def test_cloudflare_header_ignored_when_setting_is_off(self):
        request = self.request(REMOTE_ADDR="192.0.2.1", HTTP_CF_CONNECTING_IP="198.51.100.7")
        self.assertEqual(auth.client_ip(request), "192.0.2.1")

    @override_settings(TRUST_CF_CONNECTING_IP=True)
    def test_cloudflare_header_used_when_setting_is_on(self):
        request = self.request(REMOTE_ADDR="172.18.0.2", HTTP_CF_CONNECTING_IP=" 198.51.100.7 ")
        self.assertEqual(auth.client_ip(request), "198.51.100.7")

    @override_settings(TRUST_CF_CONNECTING_IP=True)
    def test_malformed_cloudflare_header_falls_back_to_remote_addr(self):
        for value in ("not-an-ip", "198.51.100.7, 10.0.0.1", "", "999.1.1.1", "fe80::1%eth0", "x" * 5000):
            with self.subTest(value=value):
                request = self.request(REMOTE_ADDR="172.18.0.2", HTTP_CF_CONNECTING_IP=value)
                self.assertEqual(auth.client_ip(request), "172.18.0.2")

    def test_missing_or_bad_remote_addr_gives_none(self):
        self.assertIsNone(auth.client_ip(self.request(REMOTE_ADDR="")))
        self.assertIsNone(auth.client_ip(self.request(REMOTE_ADDR="garbage")))

    def test_bucket_keeps_ipv4_and_cuts_ipv6_to_its_64(self):
        self.assertEqual(auth.ip_bucket("192.0.2.1"), "192.0.2.1")
        self.assertEqual(auth.ip_bucket("2001:db8:1:2:aaaa:bbbb:cccc:dddd"), "2001:db8:1:2::")
        self.assertEqual(auth.ip_bucket("::ffff:192.0.2.1"), "192.0.2.1")
        self.assertIsNone(auth.ip_bucket(None))


class ClientIpViewTests(LoginTestCase):
    def test_cloudflare_header_not_trusted_by_default(self):
        for n in range(10):
            self.request_code(f"p{n}@example.fr", client=Client(), REMOTE_ADDR="203.0.113.9",
                              HTTP_CF_CONNECTING_IP=f"198.51.100.{n}")
        self.request_code("late@example.fr", client=Client(), REMOTE_ADDR="203.0.113.9",
                          HTTP_CF_CONNECTING_IP="198.51.100.200")
        self.assertEqual(len(django_mail.outbox), 10)

    @override_settings(TRUST_CF_CONNECTING_IP=True)
    def test_malformed_cloudflare_header_does_not_crash(self):
        response = self.request_code(HTTP_CF_CONNECTING_IP="<script>")
        self.assertRedirects(response, CODE_URL)
        self.assertEqual(LoginCode.objects.get().requested_ip, "127.0.0.1")


class MailLinkTests(LoginTestCase):
    def test_link_built_from_site_url_whatever_the_host(self):
        self.request_code(HTTP_HOST="autre.example.fr")
        message = django_mail.outbox[0]
        self.assertIn(SITE + "/connexion/lien/", message.body)
        self.assertIn(f'href="{SITE}/connexion/lien/', message.alternatives[0][0])
        self.assertNotIn("autre.example.fr", message.body + message.alternatives[0][0])


class MailFailureTests(LoginTestCase):
    def assert_normal_code_page(self, response):
        self.assertRedirects(response, CODE_URL, fetch_redirect_response=False)
        page = self.client.get(CODE_URL)
        self.assertContains(page, "Vérifiez vos mails")
        self.assertContains(page, "nora@example.fr")
        self.assertNotContains(page, RATE_LIMITED)
        self.assertEqual(LoginCode.objects.count(), 1)

    @override_settings(CLAUDE_ENGINE="oauth", DEV_EMAILS=["alexis@example.fr"])
    def test_refused_mail_gives_the_normal_page(self):
        with self.assertLogs("accounts", level="WARNING"):
            response = self.request_code()
        self.assertEqual(django_mail.outbox, [])
        self.assert_normal_code_page(response)

    @override_settings(EMAIL_BACKEND="mail.backends.ResendBackend", RESEND_API_KEY="test-mail-key")
    def test_resend_timeout_gives_the_normal_page(self):
        opener = mock.MagicMock()
        opener.open.side_effect = TimeoutError("timed out")
        with mock.patch("mail.backends.urllib.request.build_opener", return_value=opener), \
                self.assertLogs("accounts", level="ERROR"):
            response = self.request_code()
        opener.open.assert_called_once()
        self.assert_normal_code_page(response)

    @override_settings(EMAIL_BACKEND="mail.backends.ResendBackend", RESEND_API_KEY="test-mail-key")
    def test_resend_error_gives_the_normal_page(self):
        error = urllib.error.HTTPError("https://api.resend.com/emails", 500, "Oops", {}, BytesIO(b"{}"))
        opener = mock.MagicMock()
        opener.open.side_effect = error
        with mock.patch("mail.backends.urllib.request.build_opener", return_value=opener), \
                self.assertLogs("accounts", level="ERROR"):
            response = self.request_code()
        self.assert_normal_code_page(response)

    @override_settings(EMAIL_BACKEND="mail.backends.ResendBackend", RESEND_API_KEY="test-mail-key")
    def test_resend_redirect_is_refused_and_gives_the_normal_page(self):
        from mail.tests import redirecting_opener
        build, calls = redirecting_opener("https://elsewhere.example/steal")
        with mock.patch("mail.backends.urllib.request.build_opener", side_effect=build), \
                self.assertLogs("accounts", level="ERROR") as logs:
            response = self.request_code()
        self.assertEqual(calls, ["https://api.resend.com/emails"])
        self.assertNotIn("elsewhere.example", "\n".join(logs.output))
        self.assert_normal_code_page(response)


class LogoutTests(LoginTestCase):
    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(LOGOUT_URL).status_code, 405)

    def test_post_signs_out(self):
        self.client.force_login(User.objects.create_user("nora@example.fr"))
        self.assertRedirects(self.client.post(LOGOUT_URL), HOME)
        self.assertIsNone(self.signed_in_user())


class HomePageTests(LoginTestCase):
    def test_page_copy_and_form(self):
        response = self.client.get(HOME)
        self.assertEqual(response.status_code, 200)
        html = self.text_of(response)
        for text in ("Les offres du jour, déjà triées, dans votre boîte à 7 h",
                     "tri-offres lit chaque nuit des sites d’emploi et des pages carrières, dans tous les domaines.",
                     "Il ne postule jamais à votre place.", "Votre adresse mail",
                     "Pas de mot de passe : un code à six chiffres vous attend dans votre boîte.",
                     "Recevoir mon code", "Bêta gratuite, 80 places. Environ 4 minutes, 3 étapes.",
                     "Ce que nous gardons et pourquoi", "Mardi · 6 offres à regarder",
                     "Chargée de médiation culturelle", "Assistante d’édition jeunesse",
                     "Exemple avec des offres fictives. Notes générées par IA, la décision vous appartient."):
            with self.subTest(text=text):
                self.assertIn(text, html)
        self.assertNotIn("du growth", html)
        self.assertIn('type="email"', html)
        self.assertIn('autocomplete="email"', html)
        self.assertIn('href="/confidentialite/"', html)
        self.assertIn('class="card card--sheet', html)
        self.assertIn('class="gauge', html)
        assert_french_copy(self, html)
        assert_unique_ids(self, html)

    @override_settings(BETA_CAPACITY=1)
    def test_full_beta_note_keeps_the_same_form(self):
        User.objects.create_user("known@example.fr")
        User.objects.create_user("staff@example.fr", is_staff=True)
        response = self.client.get(HOME)
        self.assertIn("La bêta est complète. Vous avez déjà un compte ? Votre code arrive comme d’habitude. "
                      "Sinon, laissez votre adresse : nous vous écrirons quand une place se libère.",
                      self.text_of(response))
        self.assertNotContains(response, "Bêta gratuite")
        self.assertContains(response, 'type="email"')
        self.assertContains(response, "Recevoir mon code")
        assert_french_copy(self, response.content.decode())

    @override_settings(BETA_CAPACITY=2)
    def test_not_full_while_staff_fill_the_count(self):
        User.objects.create_user("known@example.fr")
        User.objects.create_user("staff@example.fr", is_staff=True)
        self.assertContains(self.client.get(HOME), "Bêta gratuite, 2 places.")

    @override_settings(BETA_CAPACITY=50)
    def test_capacity_comes_from_settings(self):
        self.assertContains(self.client.get(HOME), "Bêta gratuite, 50 places.")

    def test_signed_in_visitor_is_sent_on(self):
        user = User.objects.create_user("nora@example.fr")
        self.client.force_login(user)
        self.assertRedirects(self.client.get(HOME), after_login_url(user))
        self.assertRedirects(self.client.post(HOME, {"email": "x@example.fr"}), after_login_url(user))
        self.assertEqual(django_mail.outbox, [])

    def test_invalid_addresses_are_refused_without_mail(self):
        for value in ("", "nora@", '"nora"@example.fr', "nora(x)@example.fr", "nora@[192.0.2.1]",
                      "a" * 245 + "@example.fr", "nora@example.fr,eve@example.fr", "em;ma@example.fr"):
            with self.subTest(value=value):
                response = self.request_code(value)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "error-summary")
        self.assertEqual(django_mail.outbox, [])
        self.assertEqual(LoginCode.objects.count(), 0)

    def test_error_message_says_what_to_do(self):
        self.assertContains(self.request_code("nora@"),
                            "Vérifiez l’adresse : elle doit ressembler à nom@exemple.fr.")
        self.assertContains(self.request_code(""), "Indiquez votre adresse mail.")

    def test_change_address_prefills_from_the_session_not_the_url(self):
        self.request_code()
        response = self.client.get(HOME + "?modifier=1&email=eve@example.fr")
        self.assertContains(response, 'value="nora@example.fr"')
        self.assertNotContains(response, "eve@example.fr")
        self.assertNotContains(Client().get(HOME + "?modifier=1&email=eve@example.fr"), "eve@example.fr")

    def test_privacy_page(self):
        response = self.client.get("/confidentialite/")
        self.assertEqual(response.status_code, 200)
        page = self.text_of(response)
        for text in ("Ce que nous gardons et pourquoi", "serveur personnel en France", "Resend",
                     "aucun outil de mesure d’audience"):
            with self.subTest(text=text):
                self.assertIn(text, page)
        assert_french_copy(self, response.content.decode())


class CodePageTests(LoginTestCase):
    def test_page_copy_and_field(self):
        self.request_code()
        response = self.client.get(CODE_URL)
        html = self.text_of(response)
        for text in ("Vérifiez vos mails",
                     "Un code à six chiffres vient de partir vers <strong>nora@example.fr</strong>.",
                     "Ce n’est pas la bonne adresse ?", "Code reçu", "Me connecter",
                     "Rien reçu ? Regardez dans les indésirables : l’expéditeur est "
                     "tri-offres &lt;noreply@example.org&gt;. Le code reste valable 15 minutes.",
                     "Renvoyer le code"):
            with self.subTest(text=text):
                self.assertIn(text, html)
        for attribute in ('inputmode="numeric"', 'autocomplete="one-time-code"', "data-code-input",
                          'class="input input--code"', 'data-countdown="60"', 'href="/?modifier=1"'):
            with self.subTest(attribute=attribute):
                self.assertIn(attribute, html)
        self.assertNotIn("Ouvrir Gmail", html)
        self.assertNotIn("Ouvrir Outlook", html)
        assert_french_copy(self, html)
        assert_unique_ids(self, html)

    def test_webmail_shortcuts_follow_the_domain(self):
        cases = {"nora@gmail.com": "https://mail.google.com/", "nora@googlemail.com": "https://mail.google.com/",
                 "nora@outlook.fr": "https://outlook.live.com/mail/",
                 "nora@hotmail.com": "https://outlook.live.com/mail/",
                 "nora@live.fr": "https://outlook.live.com/mail/"}
        for n, (email, url) in enumerate(cases.items()):
            with self.subTest(email=email):
                client = Client()
                self.request_code(email, client=client, REMOTE_ADDR=f"203.0.113.{n + 1}")
                self.assertContains(client.get(CODE_URL), f'href="{url}"')
        client = Client()
        self.with_login_email(client, "nora@notgmail.com")
        self.assertNotContains(client.get(CODE_URL), "mail.google.com")

    def test_wrong_code_error_is_linked_to_the_field(self):
        self.request_code()
        response = self.enter_code(self.not_the(self.code_in(django_mail.outbox[0])))
        html = response.content.decode()
        self.assertIn("error-summary", html)
        self.assertIn("Vérifiez le code", html)
        self.assertIn('aria-invalid="true"', html)
        assert_unique_ids(self, html)


class OnboardingStubTests(LoginTestCase):
    def test_start_requires_sign_in(self):
        response = self.client.get(START)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(HOME))

    def test_start_shows_the_questionnaire(self):
        self.client.force_login(User.objects.create_user("nora@example.fr"))
        response = self.client.get(START)
        self.assertContains(response, "Quel poste cherchez-vous ?")
        assert_french_copy(self, response.content.decode())

    def test_after_login_url(self):
        self.assertEqual(after_login_url(User(email="nora@example.fr")), START)
        self.assertEqual(after_login_url(make_finished_profile("lea@example.fr").user), SORT)


class PurgeTests(LoginTestCase):
    def test_purge_removes_rows_older_than_seven_days(self):
        make_login_code("old@example.fr", ago=7 * 24 * 3600 + 60)
        make_login_code("recent@example.fr", ago=6 * 24 * 3600)
        out = StringIO()
        call_command("purge_login_codes", stdout=out)
        self.assertEqual(list(LoginCode.objects.values_list("email", flat=True)), ["recent@example.fr"])
        self.assertIn("1", out.getvalue())


class ResendFailureOpeners:
    """Openers standing in for urllib's, one per way Resend can fail."""

    @staticmethod
    def failing(error):
        opener = mock.MagicMock()
        opener.open.side_effect = error
        return mock.patch("mail.backends.urllib.request.build_opener", return_value=opener)


class MailNotSentKeepsEarlierCodesTests(LoginTestCase):
    """A request whose mail does not go out leaves the earlier code and link of the address usable."""

    def setUp(self):
        self.request_code()
        self.first = django_mail.outbox[0]
        self.page = self.normalized(self.client.get(CODE_URL), "nora@example.fr")
        self.age(61)

    def assert_earlier_code_still_works(self):
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertEqual(self.normalized(self.client.get(CODE_URL), "nora@example.fr"), self.page)
        latest = LoginCode.objects.latest("pk")
        self.assertIsNone(latest.sent_at)
        self.assertIsNotNone(latest.invalidated_at)
        self.assertContains(self.client.get(self.link_in(self.first)), "Me connecter")
        self.assertRedirects(self.enter_code(self.code_in(self.first)), START)

    def test_site_cap(self):
        with override_settings(LOGIN_MAIL_DAILY_CAP=1), self.assertLogs("accounts", level="ERROR"):
            self.request_code()
        self.assert_earlier_code_still_works()

    def test_mail_refused(self):
        with override_settings(CLAUDE_ENGINE="oauth", DEV_EMAILS=["alexis@example.fr"]), \
                self.assertLogs("accounts", level="WARNING"):
            self.request_code()
        self.assert_earlier_code_still_works()

    def resend_failure(self, patcher):
        with override_settings(EMAIL_BACKEND="mail.backends.ResendBackend", RESEND_API_KEY="test-mail-key"), \
                patcher, self.assertLogs("accounts", level="ERROR"):
            self.request_code()
        self.assert_earlier_code_still_works()

    def test_resend_error(self):
        error = urllib.error.HTTPError("https://api.resend.com/emails", 500, "Oops", {}, BytesIO(b"{}"))
        self.resend_failure(ResendFailureOpeners.failing(error))

    def test_resend_timeout(self):
        self.resend_failure(ResendFailureOpeners.failing(TimeoutError("timed out")))

    def test_resend_redirect_refused(self):
        from mail.tests import redirecting_opener
        build, _ = redirecting_opener("https://elsewhere.example/")
        self.resend_failure(mock.patch("mail.backends.urllib.request.build_opener", side_effect=build))

    def test_sent_mail_replaces_the_earlier_code(self):
        self.request_code()
        self.assertEqual(len(django_mail.outbox), 2)
        self.assertContains(self.client.get(self.link_in(self.first)), LINK_EXPIRED)


class TokenRedactionTests(LoginTestCase):
    def link_and_token(self):
        self.request_code()
        link = self.link_in(django_mail.outbox[0])
        return link, link.rstrip("/").rsplit("/", 1)[1]

    def test_token_is_redacted_from_request_logs(self):
        link, token = self.link_and_token()
        with self.assertLogs("django.request", level="WARNING") as logs:
            response = self.client.head(link)
        self.assertEqual(response.status_code, 405)
        text = "\n".join(logs.output)
        self.assertIn("/connexion/lien/", text)
        self.assertNotIn(token, text)

    def test_token_is_redacted_from_csrf_logs(self):
        link, token = self.link_and_token()
        with self.assertLogs("django.security.csrf", level="WARNING") as logs:
            Client(enforce_csrf_checks=True).post(link)
        text = "\n".join(logs.output)
        self.assertIn("/connexion/lien/", text)
        self.assertNotIn(token, text)


class SingleSourceTests(LoginTestCase):
    """Durations and the sender come from the Python constants and settings, not from the templates."""

    def test_code_lifetime_and_resend_delay(self):
        with mock.patch("accounts.auth.CODE_TTL_MINUTES", 20), mock.patch("accounts.auth.RESEND_DELAY_SECONDS", 45):
            self.request_code()
            page = self.client.get(CODE_URL)
        self.assertIn("Le code reste valable 20 minutes.", self.text_of(page))
        self.assertContains(page, 'data-countdown="45"')
        message = django_mail.outbox[0]
        self.assertIn("Ce code et ce lien restent valables 20 minutes.", message.body)
        self.assertIn("Ce code et ce lien restent valables 20 minutes.", message.alternatives[0][0])

    @override_settings(DEFAULT_FROM_EMAIL="essai <essai@example.fr>")
    def test_sender_shown_on_the_code_page(self):
        self.request_code()
        self.assertIn("l’expéditeur est essai &lt;essai@example.fr&gt;.", self.text_of(self.client.get(CODE_URL)))


class IssueCodeTests(LoginTestCase):
    """auth.issue_code: the sign-in limits and the code life cycle, whatever mail carries the code."""

    def sender(self, result=1, error=None):
        calls = []

        def send(code, link_path):
            calls.append((code, link_path))
            if error is not None:
                raise error
            return result
        return send, calls

    def test_sends_once_then_stamps_and_replaces_earlier_codes(self):
        make_login_code("nora@example.fr", ago=600)
        send, calls = self.sender()
        outcome = auth.issue_code("nora@example.fr", "203.0.113.1", send)
        self.assertEqual((outcome.throttled, outcome.sent), (False, True))
        [(code, link_path)] = calls
        row = LoginCode.objects.latest("pk")
        token = re.fullmatch(r"/connexion/lien/([A-Za-z0-9_-]{43})/", link_path).group(1)
        self.assertEqual(row.token_hash, auth.hash_token(token))
        self.assertEqual(row.code_hash, auth.hash_code("nora@example.fr", code))
        self.assertEqual(row.requested_ip, "203.0.113.1")
        self.assertIsNotNone(row.sent_at)
        self.assertIsNotNone(LoginCode.objects.earliest("pk").invalidated_at)

    def test_throttled_request_creates_nothing_and_sends_nothing(self):
        make_login_code("nora@example.fr", ago=30)
        send, calls = self.sender()
        outcome = auth.issue_code("nora@example.fr", None, send)
        self.assertEqual((outcome.throttled, outcome.sent), (True, False))
        self.assertEqual(calls, [])
        self.assertEqual(LoginCode.objects.count(), 1)

    def assert_voided_and_earlier_kept(self, outcome):
        self.assertEqual((outcome.throttled, outcome.sent), (False, False))
        earlier, new = LoginCode.objects.order_by("pk")
        self.assertIsNone(earlier.invalidated_at)
        self.assertIsNotNone(new.invalidated_at)
        self.assertIsNone(new.sent_at)

    def test_mail_error_voids_the_new_code(self):
        make_login_code("nora@example.fr", ago=600)
        send, _ = self.sender(error=mail.MailDeliveryFailed("down"))
        with self.assertLogs("accounts", level="ERROR"):
            outcome = auth.issue_code("nora@example.fr", None, send)
        self.assert_voided_and_earlier_kept(outcome)

    def test_refused_mail_voids_the_new_code(self):
        make_login_code("nora@example.fr", ago=600)
        send, _ = self.sender(error=mail.MailRefused("not a dev address"))
        with self.assertLogs("accounts", level="WARNING"):
            outcome = auth.issue_code("nora@example.fr", None, send)
        self.assert_voided_and_earlier_kept(outcome)

    def test_nothing_accepted_by_the_backend_voids_the_new_code(self):
        make_login_code("nora@example.fr", ago=600)
        send, _ = self.sender(result=0)
        self.assert_voided_and_earlier_kept(auth.issue_code("nora@example.fr", None, send))

    @override_settings(LOGIN_MAIL_DAILY_CAP=1)
    def test_site_cap_skips_the_send(self):
        make_login_code("nora@example.fr", ago=600)
        send, calls = self.sender()
        with self.assertLogs("accounts", level="ERROR"):
            outcome = auth.issue_code("nora@example.fr", None, send)
        self.assertEqual(calls, [])
        self.assert_voided_and_earlier_kept(outcome)
