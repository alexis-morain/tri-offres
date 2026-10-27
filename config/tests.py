from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.test import SimpleTestCase, TestCase

from config.env import (email_backend, env_bool, env_https_url, env_int, env_list, env_strict_bool, publisher_identity,
                        secret_key)


class StrictBoolTests(SimpleTestCase):
    """BETA is read strictly (step 8 plan, D3): a typo stops the start instead of silently ending the beta."""

    def test_absent_or_empty_is_the_default(self):
        for default in (True, False):
            for environ in ({}, {"X": ""}, {"X": "  "}):
                self.assertIs(env_strict_bool("X", default, environ=environ), default, environ)

    def test_true_values(self):
        for value in ("1", "true", "True", "YES", "on", " On ", "TRUE\n"):
            self.assertIs(env_strict_bool("X", False, environ={"X": value}), True, value)

    def test_false_values(self):
        for value in ("0", "false", "False", "NO", "off", " Off ", "0\n"):
            self.assertIs(env_strict_bool("X", True, environ={"X": value}), False, value)

    def test_any_other_value_is_refused_without_repeating_it(self):
        for value in ("ture", "2", "-1", "oui", "non", "enabled", "1 0", "trüe"):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured) as caught:
                env_strict_bool("BETA", True, environ={"BETA": value})
            self.assertEqual(str(caught.exception), "BETA must be 1 or 0.")
            self.assertNotIn(value, str(caught.exception))

    def test_the_real_environment_is_read_by_default(self):
        with mock.patch.dict("os.environ", {"X_STRICT": "off"}):
            self.assertIs(env_strict_bool("X_STRICT", True), False)


class BetaSettingTests(SimpleTestCase):
    """manage.py test forces BETA=1 (S7), the compose passes it on and never uses env_file (S6)."""

    def test_the_test_run_is_in_beta(self):
        self.assertIs(settings.BETA, True)

    def test_manage_py_assigns_beta_for_the_test_command_only(self):
        source = (Path(settings.BASE_DIR) / "manage.py").read_text()
        self.assertIn('os.environ["BETA"] = "1"', source)  # assigned, not setdefault: an exported BETA=0 is ignored

    def test_the_compose_passes_beta_and_never_the_env_file(self):
        lines = [line.strip() for line in (Path(settings.BASE_DIR) / "docker-compose.yml").read_text().splitlines()]
        code = [line for line in lines if not line.startswith("#")]
        self.assertIn("BETA: ${BETA:-1}", code)
        self.assertFalse([line for line in code if "env_file" in line])


class EnvHelperTests(SimpleTestCase):
    def test_bool_true_values(self):
        for value in ("1", "true", "True", "yes", "on"):
            self.assertTrue(env_bool("X", environ={"X": value}), value)

    def test_bool_false_values_and_default(self):
        for value in ("0", "false", "no", "off", ""):
            self.assertFalse(env_bool("X", environ={"X": value}), value)
        self.assertFalse(env_bool("X", environ={}))
        self.assertTrue(env_bool("X", default=True, environ={}))

    def test_list_splits_on_commas_and_strips(self):
        self.assertEqual(env_list("X", environ={"X": " a.fr, b.fr ,,"}), ["a.fr", "b.fr"])

    def test_int_reads_integers_and_falls_back_to_default(self):
        self.assertEqual(env_int("X", 80, environ={"X": " 12 "}), 12)
        self.assertEqual(env_int("X", 80, environ={}), 80)
        self.assertEqual(env_int("X", 80, environ={"X": ""}), 80)

    def test_int_refuses_garbage(self):
        with self.assertRaises(ImproperlyConfigured):
            env_int("X", 80, environ={"X": "quatre-vingts"})

    def test_https_url_is_empty_by_default_and_kept_when_https(self):
        self.assertEqual(env_https_url("X", environ={}), "")
        self.assertEqual(env_https_url("X", environ={"X": "  "}), "")
        self.assertEqual(env_https_url("X", environ={"X": " https://hc-ping.com/abc "}), "https://hc-ping.com/abc")

    def test_https_url_refuses_anything_else_without_repeating_the_value(self):
        for value in ("http://hc-ping.com/secretkey", "hc-ping.com/secretkey", "ftp://secretkey", "https://",
                      "https://hc-ping.com/secret key", "https://hc-ping.com/secret\nkey"):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured) as caught:
                env_https_url("X", environ={"X": value})
            self.assertEqual(str(caught.exception), "X must be an https:// URL.")

    def test_list_default(self):
        self.assertEqual(env_list("X", default="localhost,127.0.0.1", environ={}), ["localhost", "127.0.0.1"])
        self.assertEqual(env_list("X", environ={}), [])

    def test_secret_key_from_environment_is_returned_unchanged(self):
        for debug in (False, True):
            self.assertEqual(secret_key(debug=debug, environ={"DJANGO_SECRET_KEY": "s3cret"}), "s3cret")

    def test_missing_secret_key_raises_when_not_debug(self):
        with self.assertRaises(ImproperlyConfigured):
            secret_key(debug=False, environ={})

    def test_empty_secret_key_raises_when_not_debug(self):
        with self.assertRaises(ImproperlyConfigured):
            secret_key(debug=False, environ={"DJANGO_SECRET_KEY": ""})

    def test_debug_fallback_is_random_per_call_not_a_known_constant(self):
        # Regression: DEBUG=1 without a key used to yield a constant published in the public repo.
        with self.assertWarns(RuntimeWarning):
            first = secret_key(debug=True, environ={})
        with self.assertWarns(RuntimeWarning):
            second = secret_key(debug=True, environ={"DJANGO_SECRET_KEY": ""})
        self.assertNotEqual(first, second)
        old_literal = "django-" + "insecure-dev-only-key"
        self.assertNotIn(old_literal, (first, second))
        for key in (first, second):
            self.assertGreaterEqual(len(key), 50)


class SqliteSettingsTests(SimpleTestCase):
    def test_file_database_opens_in_wal_mode(self):
        # The test database is in-memory, where WAL does not apply: open a file-backed copy of the settings.
        default = connections["default"]
        db_file = Path(self.enterContext(TemporaryDirectory())) / "t.sqlite3"
        wrapper = type(default)({**default.settings_dict, "NAME": db_file}, alias="wal_check")
        try:
            with wrapper.cursor() as cursor:
                self.assertEqual(cursor.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                self.assertEqual(cursor.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        finally:
            wrapper.close()

    def test_transactions_are_immediate(self):
        self.assertEqual(settings.DATABASES["default"]["OPTIONS"]["transaction_mode"], "IMMEDIATE")


class HealthzTests(TestCase):
    def test_returns_ok_and_queries_database(self):
        with self.assertNumQueries(1):
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")
        self.assertTrue(response["Content-Type"].startswith("text/plain"))


class LoginSettingsTests(SimpleTestCase):
    """Settings the passwordless sign-in relies on (the design plan, 9.1)."""

    def test_sessions_live_in_the_database_for_thirty_days_without_sliding(self):
        self.assertEqual(settings.SESSION_ENGINE, "django.contrib.sessions.backends.db")
        self.assertEqual(settings.SESSION_COOKIE_AGE, 30 * 24 * 3600)
        self.assertFalse(settings.SESSION_SAVE_EVERY_REQUEST)
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")

    def test_sender_and_reply_to_have_neutral_defaults_in_the_test_environment(self):
        self.assertEqual(settings.DEFAULT_FROM_EMAIL, "tri-offres <noreply@example.org>")
        self.assertEqual(settings.MAIL_REPLY_TO, "contact@example.org")
        self.assertEqual(settings.LEGAL_NAME, "Nom de l’éditeur")

    def test_defaults(self):
        self.assertIsInstance(settings.BETA_CAPACITY, int)
        self.assertIsInstance(settings.LOGIN_MAIL_DAILY_CAP, int)
        self.assertIsInstance(settings.DEV_EMAILS, list)
        self.assertIsInstance(settings.SECRET_KEY_FALLBACKS, list)
        self.assertFalse(settings.SITE_URL.endswith("/"))
        for email in settings.DEV_EMAILS:
            self.assertEqual(email, email.strip().lower())

    def test_mail_backend_follows_the_resend_key(self):
        # The test runner swaps EMAIL_BACKEND for locmem, so the choice itself is tested.
        self.assertEqual(email_backend("test-mail-key"), "mail.backends.ResendBackend")
        self.assertEqual(email_backend(""), "mail.backends.ConsoleBackend")


class PublisherIdentityTests(SimpleTestCase):
    """The sender, the reply-to and the publisher's name come from the environment, with no personal default."""

    SET = {"DEFAULT_FROM_EMAIL": " Site <hello@example.net> ", "MAIL_REPLY_TO": "help@example.net ",
           "LEGAL_NAME": " Jane Doe "}

    def test_values_are_read_and_stripped(self):
        for debug, key in ((True, ""), (False, ""), (False, "test-mail-key")):
            self.assertEqual(publisher_identity(debug, key, environ=self.SET),
                             ("Site <hello@example.net>", "help@example.net", "Jane Doe"))

    def test_development_and_mail_on_the_console_get_neutral_defaults(self):
        for debug, key in ((True, ""), (False, ""), (True, "test-mail-key")):
            self.assertEqual(publisher_identity(debug, key, environ={}),
                             ("tri-offres <noreply@example.org>", "contact@example.org", "Nom de l’éditeur"))

    def test_real_sending_in_production_refuses_a_missing_value(self):
        for name in self.SET:
            without = {key: value for key, value in self.SET.items() if key != name}
            for environ in (without, {**without, name: ""}, {**without, name: "  "}):
                with self.subTest(name=name, environ=environ), self.assertRaisesMessage(ImproperlyConfigured, name):
                    publisher_identity(False, "test-mail-key", environ=environ)
