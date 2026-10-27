"""The OpenRouter PKCE flow (contract 5.9 to 5.12, 5.14): start, return, exchange, disconnection. OpenRouter itself is
never reached: the exchange is a mocked httpx2 call."""
import base64
import hashlib
import logging
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import httpx2
from cryptography.fernet import Fernet
from django.db import connection
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import openrouter, pkce
from accounts.log_filters import RedactLoginTokens, redact
from accounts.models import OpenRouterKey, User
from accounts.testing import JournalTestMixin
from config.testing import PLAIN_STATIC, assert_french_copy

SITE = "https://jobs.example.org"
KEY = "sk-or-v1-CANARY0123456789abcdefABCDEF"  # fictive
OLD_KEY = "sk-or-v1-OLDKEY0123456789abcdefABCDEF"  # fictive
FERNET = Fernet.generate_key().decode()
CODE = "code-de-retour-0123456789"


class Capture(logging.Handler):
    """Every record of every logger, at DEBUG, formatted as a handler would write it."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())
        if record.exc_info:
            self.lines.append(logging.Formatter().formatException(record.exc_info))


class FlowCase(TestCase):
    def setUp(self):
        super().setUp()
        override = override_settings(OPENROUTER_KEYS_FERNET=[FERNET], SITE_URL=SITE, STORAGES=PLAIN_STATIC,
                                     EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
        override.enable()
        self.addCleanup(override.disable)
        self.user = User.objects.create_user("nora@example.fr")
        self.client.force_login(self.user)
        self.logs = Capture()
        root = logging.getLogger()
        self.addCleanup(setattr, root, "level", root.level)
        root.setLevel(logging.DEBUG)
        root.addHandler(self.logs)
        self.addCleanup(root.removeHandler, self.logs)

    def start(self, client=None):
        response = (client or self.client).post(reverse("accounts:openrouter_start"))
        self.assertEqual(response.status_code, 302)
        return response, parse_qs(urlsplit(response["Location"]).query)

    def finish(self, client=None, **params):
        return (client or self.client).get(reverse("accounts:openrouter_return"), params)

    def exchange(self, key=KEY):
        return mock.patch("accounts.views.pkce.exchange", return_value=key)

    def assertNoCanary(self, *texts):
        for text in (*self.logs.lines, *texts):
            self.assertNotIn("CANARY", str(text))
            self.assertNotIn("sk-or-v1", str(text))

    def slot(self):
        return self.client.session.get(pkce.SLOT)


class StartTests(FlowCase):
    def test_start_goes_to_openrouter_with_a_s256_challenge_and_the_state(self):
        response, query = self.start()
        self.assertTrue(response["Location"].startswith("https://openrouter.ai/auth?"))
        slot = self.slot()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(slot["verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["state"], [slot["state"]])
        self.assertEqual(query["key_label"], ["tri-offres"])
        self.assertEqual(query["callback_url"], [f"{SITE}/openrouter/retour/"])
        self.assertGreaterEqual(len(slot["verifier"]), 86)  # 64 random bytes, urlsafe
        self.assertGreaterEqual(len(slot["state"]), 43)  # 32 random bytes
        self.assertEqual(slot["user_pk"], self.user.pk)
        self.assertIn("issued_at", slot)
        self.assertNotIn("verifier", response["Location"])

    @override_settings(ALLOWED_HOSTS=["evil.example.org"])
    def test_the_callback_never_comes_from_the_host_header(self):
        response = self.client.post(reverse("accounts:openrouter_start"), HTTP_HOST="evil.example.org")
        self.assertIn("callback_url=https%3A%2F%2Fjobs.example.org%2Fopenrouter%2Fretour%2F", response["Location"])

    def test_a_new_start_overwrites_the_slot(self):
        self.start()
        first = self.slot()
        self.start()
        second = self.slot()
        self.assertNotEqual(first["state"], second["state"])
        self.assertNotEqual(first["verifier"], second["verifier"])

    def test_start_is_post_only(self):
        self.assertEqual(self.client.get(reverse("accounts:openrouter_start")).status_code, 405)

    def test_start_needs_the_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(reverse("accounts:openrouter_start"))
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(client.session.get(pkce.SLOT))

    def test_start_needs_a_signed_in_person(self):
        client = Client()
        response = client.post(reverse("accounts:openrouter_start"))
        self.assertRedirects(response, reverse("accounts:home"), fetch_redirect_response=False)
        self.assertIsNone(client.session.get(pkce.SLOT))


class ReturnTests(FlowCase):
    def test_the_good_return_stores_the_key_and_goes_to_a_constant_page(self):
        _, query = self.start()
        with self.exchange() as exchange:
            response = self.finish(code=CODE, state=query["state"][0])
        self.assertRedirects(response, reverse("accounts:openrouter_done"), fetch_redirect_response=False)
        exchange.assert_called_once()
        self.assertEqual(exchange.call_args.args[0], CODE)
        self.assertEqual(openrouter.read(self.user.pk).key, KEY)
        self.assertIsNone(self.slot())
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "strict-origin")
        self.assertNoCanary(response.content.decode(), str(dict(self.client.session)))

    def test_the_verifier_of_the_start_is_what_the_exchange_gets(self):
        _, query = self.start()
        verifier = self.slot()["verifier"]
        with self.exchange() as exchange:
            self.finish(code=CODE, state=query["state"][0])
        self.assertEqual(exchange.call_args.args[1], verifier)

    def test_without_a_code_it_is_a_cancellation_with_no_effect_and_the_slot_stays(self):
        _, query = self.start()
        before = self.slot()
        with self.exchange() as exchange:
            response = self.finish(error="access_denied", state=query["state"][0])
            forged = self.finish()
        for answer in (response, forged):
            self.assertRedirects(answer, reverse("accounts:openrouter_cancelled"), fetch_redirect_response=False)
        exchange.assert_not_called()
        self.assertEqual(self.slot(), before)
        self.assertFalse(OpenRouterKey.objects.exists())

    def test_next_is_ignored(self):
        _, query = self.start()
        with self.exchange():
            response = self.finish(code=CODE, state=query["state"][0], next="https://evil.example.org/")
        self.assertEqual(response["Location"], reverse("accounts:openrouter_done"))
        response = self.finish(next="https://evil.example.org/")
        self.assertEqual(response["Location"], reverse("accounts:openrouter_cancelled"))

    def test_head_is_refused(self):
        self.assertEqual(self.client.head(reverse("accounts:openrouter_return"), {"code": CODE}).status_code, 405)

    def test_a_wrong_state_is_refused_and_burns_the_slot(self):
        self.start()
        with self.exchange() as exchange:
            response = self.finish(code=CODE, state="not-the-state")
        self.assertRedirects(response, reverse("accounts:openrouter_cancelled"), fetch_redirect_response=False)
        exchange.assert_not_called()
        self.assertIsNone(self.slot())
        self.assertFalse(OpenRouterKey.objects.exists())

    def test_a_replay_is_refused(self):
        _, query = self.start()
        with self.exchange() as exchange:
            self.finish(code=CODE, state=query["state"][0])
            response = self.finish(code=CODE, state=query["state"][0])
        self.assertRedirects(response, reverse("accounts:openrouter_cancelled"), fetch_redirect_response=False)
        self.assertEqual(exchange.call_count, 1)

    def test_a_return_with_no_start_is_refused(self):
        with self.exchange() as exchange:
            response = self.finish(code=CODE, state="x")
        self.assertRedirects(response, reverse("accounts:openrouter_cancelled"), fetch_redirect_response=False)
        exchange.assert_not_called()

    def test_a_return_after_ten_minutes_is_refused(self):
        _, query = self.start()
        later = timezone.now() + timedelta(minutes=11)
        with self.exchange() as exchange, mock.patch("accounts.pkce.timezone.now", return_value=later):
            response = self.finish(code=CODE, state=query["state"][0])
        self.assertRedirects(response, reverse("accounts:openrouter_cancelled"), fetch_redirect_response=False)
        exchange.assert_not_called()
        self.assertIsNone(self.slot())

    def test_a_return_just_inside_ten_minutes_goes_through(self):
        _, query = self.start()
        later = timezone.now() + timedelta(minutes=9)
        with self.exchange(), mock.patch("accounts.pkce.timezone.now", return_value=later):
            response = self.finish(code=CODE, state=query["state"][0])
        self.assertEqual(response["Location"], reverse("accounts:openrouter_done"))

    def test_a_starts_b_finishes_is_refused_and_b_gets_no_key(self):
        _, query = self.start()
        bob = User.objects.create_user("bob@example.fr")
        self.client.force_login(bob)  # the browser now belongs to Bob; the slot below is Nora's start
        session = self.client.session
        session[pkce.SLOT] = {"verifier": "v" * 86, "state": query["state"][0], "user_pk": self.user.pk,
                              "issued_at": int(timezone.now().timestamp())}
        session.save()
        with self.exchange() as exchange:
            response = self.finish(code=CODE, state=query["state"][0])
        self.assertRedirects(response, reverse("accounts:openrouter_cancelled"), fetch_redirect_response=False)
        exchange.assert_not_called()
        self.assertFalse(OpenRouterKey.objects.exists())

    def test_a_signed_out_return_is_refused(self):
        _, query = self.start()
        self.client.post(reverse("accounts:logout"))
        with self.exchange() as exchange:
            response = self.finish(code=CODE, state=query["state"][0])
        self.assertRedirects(response, reverse("accounts:openrouter_cancelled"), fetch_redirect_response=False)
        exchange.assert_not_called()

    def test_a_malformed_slot_or_code_is_refused(self):
        _, query = self.start()
        for slot, code in (("pas un dictionnaire", CODE), ({"state": query["state"][0]}, CODE), (None, CODE),
                           (self.slot(), "x" * 600)):
            session = self.client.session
            session[pkce.SLOT] = slot
            session.save()
            with self.subTest(slot=slot), self.exchange() as exchange:
                response = self.finish(code=code, state=query["state"][0])
                self.assertRedirects(response, reverse("accounts:openrouter_cancelled"),
                                     fetch_redirect_response=False)
                exchange.assert_not_called()

    def test_a_failed_exchange_never_erases_a_valid_key(self):
        openrouter.store(self.user.pk, OLD_KEY)
        _, query = self.start()
        with self.exchange(key=None):
            response = self.finish(code=CODE, state=query["state"][0])
        self.assertRedirects(response, reverse("accounts:openrouter_cancelled"), fetch_redirect_response=False)
        self.assertEqual(openrouter.read(self.user.pk).key, OLD_KEY)
        self.assertIn("reste active", self.client.get(reverse("accounts:openrouter_cancelled")).content.decode())

    def test_the_exchange_runs_outside_any_transaction_of_its_own(self):
        _, query = self.start()
        depth = len(connection.savepoint_ids)  # TestCase wraps the test in a transaction: compare with its depth
        seen = []
        with mock.patch("accounts.views.pkce.exchange",
                        side_effect=lambda *args: seen.append(len(connection.savepoint_ids)) or KEY):
            self.finish(code=CODE, state=query["state"][0])
        self.assertEqual(seen, [depth])

    def test_the_pages_hold_no_key_and_are_in_house_style(self):
        _, query = self.start()
        with self.exchange():
            self.finish(code=CODE, state=query["state"][0])
        for name in ("openrouter_connect", "openrouter_done", "openrouter_cancelled"):
            page = self.client.get(reverse(f"accounts:{name}")).content.decode()
            with self.subTest(page=name):
                self.assertNoCanary(page)
                assert_french_copy(self, page)


class ConnectPageTests(FlowCase):
    def test_it_offers_the_button_when_not_connected(self):
        page = self.client.get(reverse("accounts:openrouter_connect")).content.decode()
        self.assertIn("Connecter OpenRouter", page)
        self.assertIn(reverse("accounts:openrouter_start"), page)
        self.assertNotIn(reverse("accounts:openrouter_disconnect"), page)

    def test_it_shows_the_status_and_the_disconnect_form_when_connected(self):
        openrouter.store(self.user.pk, KEY)
        page = self.client.get(reverse("accounts:openrouter_connect")).content.decode()
        self.assertIn(reverse("accounts:openrouter_disconnect"), page)
        self.assertIn(openrouter.key_url(hashlib.sha256(KEY.encode()).hexdigest()), page)
        self.assertNoCanary(page)

    def test_the_pages_need_a_signed_in_person(self):
        client = Client()
        for name in ("openrouter_connect", "openrouter_done", "openrouter_cancelled"):
            with self.subTest(page=name):
                self.assertRedirects(client.get(reverse(f"accounts:{name}")), reverse("accounts:home"),
                                     fetch_redirect_response=False)

    def test_is_connected_for_the_next_step(self):
        self.assertFalse(openrouter.is_connected(self.user))
        openrouter.store(self.user.pk, KEY)
        self.assertTrue(openrouter.is_connected(self.user))


class DisconnectViewTests(JournalTestMixin, FlowCase):
    def test_post_erases_the_key_and_writes_the_journal(self):
        openrouter.store(self.user.pk, KEY)
        response = self.client.post(reverse("accounts:openrouter_disconnect"))
        self.assertRedirects(response, reverse("accounts:openrouter_connect"), fetch_redirect_response=False)
        self.assertFalse(OpenRouterKey.objects.exists())
        self.assertEqual(len(self.journal_lines()), 1)
        self.assertIn("deconnexion_openrouter", self.journal_lines()[0])

    def test_get_is_refused_and_does_nothing(self):
        openrouter.store(self.user.pk, KEY)
        self.assertEqual(self.client.get(reverse("accounts:openrouter_disconnect")).status_code, 405)
        self.assertTrue(OpenRouterKey.objects.exists())

    def test_the_csrf_token_is_required(self):
        openrouter.store(self.user.pk, KEY)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse("accounts:openrouter_disconnect")).status_code, 403)
        self.assertTrue(OpenRouterKey.objects.exists())

    def test_it_needs_a_signed_in_person(self):
        openrouter.store(self.user.pk, KEY)
        response = Client().post(reverse("accounts:openrouter_disconnect"))
        self.assertRedirects(response, reverse("accounts:home"), fetch_redirect_response=False)
        self.assertTrue(OpenRouterKey.objects.exists())


def json_response(status=200, payload=None, content=None):
    request = httpx2.Request("POST", pkce.EXCHANGE_URL)
    if content is not None:
        return httpx2.Response(status, request=request, content=content)
    return httpx2.Response(status, request=request, json={"key": KEY, "user_id": "user_x"} if payload is None
                           else payload)


class ExchangeTests(SimpleTestCase):
    def run_exchange(self, response=None, side_effect=None):
        with mock.patch("accounts.pkce.httpx2.post", return_value=response, side_effect=side_effect) as post:
            result = pkce.exchange(CODE, "verifier-" + "v" * 80)
        self.post = post
        return result

    def test_the_good_answer_gives_the_key_alone(self):
        self.assertEqual(self.run_exchange(json_response()), KEY)

    def test_the_request_is_pinned_bounded_and_does_not_follow_redirects(self):
        self.run_exchange(json_response())
        args, kwargs = self.post.call_args
        self.assertEqual(args, ("https://openrouter.ai/api/v1/auth/keys",))
        self.assertEqual(kwargs["json"], {"code": CODE, "code_verifier": "verifier-" + "v" * 80,
                                          "code_challenge_method": "S256"})
        self.assertLessEqual(kwargs["timeout"], 10)
        self.assertIs(kwargs["follow_redirects"], False)

    def test_a_bad_answer_gives_none_without_logging_anything_from_it(self):
        bad = [json_response(403), json_response(500), json_response(302), json_response(payload={"key": 12}),
               json_response(payload={"key": "pas-openrouter-0123456789abcdef"}),
               json_response(payload={"key": "sk-or-" + "a" * 400}), json_response(payload={"key": "sk-or-v1"}),
               json_response(payload={"key": "sk-or-v1-abc def ghijklmnopqrstuvwxyz"}),
               json_response(payload=["key"]), json_response(payload={}), json_response(content=b"<html>CANARY"),
               json_response(content=b"x" * 10_000)]
        for response in bad:
            with self.subTest(status=response.status_code), self.assertLogs("accounts.pkce", "WARNING") as logs:
                self.assertIsNone(self.run_exchange(response))
            self.assertNotIn("CANARY", "\n".join(logs.output))

    def test_a_network_error_gives_none_and_logs_the_class_only(self):
        with self.assertLogs("accounts.pkce", "WARNING") as logs:
            self.assertIsNone(self.run_exchange(side_effect=httpx2.ConnectTimeout("boom " + KEY)))
        self.assertIn("ConnectTimeout", "\n".join(logs.output))
        self.assertNotIn("CANARY", "\n".join(logs.output))

    def test_the_http_client_loggers_are_clamped(self):
        self.assertEqual(logging.getLogger("httpx2").level, logging.WARNING)


class LogRedactionTests(SimpleTestCase):
    def test_the_filter_masks_an_openrouter_key_in_message_arguments_and_traceback(self):
        self.assertEqual(redact("clé sk-or-v1-CANARY_01-ab fin"), "clé [redacted] fin")
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "key %s", ("sk-or-v1-CANARY",), None)
        try:
            raise ValueError("sk-or-v1-CANARY")
        except ValueError:
            import sys

            record.exc_info = sys.exc_info()
        RedactLoginTokens().filter(record)
        text = record.getMessage() + (record.exc_text or "")
        self.assertNotIn("CANARY", text)

    def test_the_login_token_masking_still_works(self):
        self.assertEqual(redact("/connexion/lien/abc123/"), "/connexion/lien/[redacted]/")
