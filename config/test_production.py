"""Production settings (sub-step 10.2): the HTTPS settings as pure functions, `check --deploy`, gunicorn without an
access log, the error log's redaction, the compose files and the container's probe."""
import io
import logging
import runpy
import urllib.error
from unittest import mock

from django.conf import settings
from django.core.checks import Tags, run_checks
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, SimpleTestCase, TestCase, override_settings

from accounts.models import User
from accounts.unsubscribe import make_token
from config.env import env_proxy_url, security_settings
from config.testing import PLAIN_STATIC

SITE = "https://tri.example.org"
BASE = settings.BASE_DIR


class SecuritySettingsTests(SimpleTestCase):
    def https(self, trust=False, origins=(), **environ):
        return security_settings(SITE, trust, list(origins), environ=environ)

    def test_http_keeps_the_local_defaults(self):
        result = security_settings("http://localhost:8000", False, [], environ={})
        self.assertFalse(result["SESSION_COOKIE_SECURE"])
        self.assertFalse(result["CSRF_COOKIE_SECURE"])
        self.assertEqual((result["SESSION_COOKIE_NAME"], result["CSRF_COOKIE_NAME"]), ("sessionid", "csrftoken"))
        self.assertEqual(result["CSRF_TRUSTED_ORIGINS"], [])
        self.assertEqual(result["SECURE_HSTS_SECONDS"], 0)
        self.assertFalse(result["SECURE_HSTS_INCLUDE_SUBDOMAINS"])
        self.assertIsNone(result["SECURE_PROXY_SSL_HEADER"])

    def test_http_never_trusts_the_proxy_header_even_behind_the_tunnel_flag(self):
        self.assertIsNone(security_settings("http://localhost:8000", True, [], environ={})["SECURE_PROXY_SSL_HEADER"])

    def test_https_cookies_are_secure_and_host_prefixed(self):
        result = self.https()
        self.assertTrue(result["SESSION_COOKIE_SECURE"])
        self.assertTrue(result["CSRF_COOKIE_SECURE"])
        self.assertEqual(result["SESSION_COOKIE_NAME"], "__Host-sessionid")
        self.assertEqual(result["CSRF_COOKIE_NAME"], "__Host-csrftoken")

    def test_https_site_url_joins_the_trusted_origins_once(self):
        self.assertEqual(self.https()["CSRF_TRUSTED_ORIGINS"], [SITE])
        self.assertEqual(self.https(origins=["https://other.example.org"])["CSRF_TRUSTED_ORIGINS"],
                         ["https://other.example.org", SITE])
        self.assertEqual(self.https(origins=[SITE])["CSRF_TRUSTED_ORIGINS"], [SITE])

    def test_trusted_origin_is_the_origin_of_a_site_url_with_a_path(self):
        result = security_settings(f"{SITE}/app", False, [], environ={})
        self.assertEqual(result["CSRF_TRUSTED_ORIGINS"], [SITE])

    def test_hsts_defaults_to_an_hour_in_https_and_is_off_in_http(self):
        self.assertEqual(self.https()["SECURE_HSTS_SECONDS"], 3600)
        self.assertEqual(security_settings("http://localhost:8000", False, [], environ={})["SECURE_HSTS_SECONDS"], 0)

    def test_hsts_seconds_variable_and_subdomains_but_never_preload(self):
        result = self.https(HSTS_SECONDS="31536000")
        self.assertEqual(result["SECURE_HSTS_SECONDS"], 31536000)
        self.assertTrue(result["SECURE_HSTS_INCLUDE_SUBDOMAINS"])
        self.assertFalse(result["SECURE_HSTS_PRELOAD"])
        self.assertFalse(self.https(HSTS_SECONDS="0")["SECURE_HSTS_INCLUDE_SUBDOMAINS"])
        self.assertFalse(self.https(HSTS_SECONDS="")["SECURE_HSTS_PRELOAD"])
        self.assertEqual(self.https(HSTS_SECONDS="")["SECURE_HSTS_SECONDS"], 3600)

    def test_hsts_seconds_refuses_garbage_and_negatives(self):
        for value in ("-1", "un an", "1.5"):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured):
                self.https(HSTS_SECONDS=value)

    def test_proxy_header_only_behind_the_tunnel_in_https(self):
        self.assertIsNone(self.https(trust=False)["SECURE_PROXY_SSL_HEADER"])
        self.assertEqual(self.https(trust=True)["SECURE_PROXY_SSL_HEADER"], ("HTTP_X_FORWARDED_PROTO", "https"))

    def test_the_site_never_redirects_to_https_itself(self):
        # The tunnel terminates TLS and the container's probe speaks http: a redirect would break the healthcheck.
        for result in (self.https(trust=True), security_settings("http://localhost:8000", False, [], environ={})):
            self.assertFalse(result["SECURE_SSL_REDIRECT"])

    def test_settings_of_this_run_follow_the_function(self):
        expected = security_settings(settings.SITE_URL, settings.TRUST_CF_CONNECTING_IP,
                                     [o for o in settings.CSRF_TRUSTED_ORIGINS if o != settings.SITE_URL])
        for name in ("SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE", "SESSION_COOKIE_NAME", "CSRF_COOKIE_NAME",
                     "SECURE_HSTS_SECONDS", "SECURE_HSTS_PRELOAD", "SECURE_SSL_REDIRECT"):
            self.assertEqual(getattr(settings, name), expected[name], name)


class ProxyUrlTests(SimpleTestCase):
    def test_empty_by_default_and_kept_when_valid(self):
        self.assertEqual(env_proxy_url("X", environ={}), "")
        self.assertEqual(env_proxy_url("X", environ={"X": "  "}), "")
        for value in ("http://vpn-proxy:8888", "https://proxy.example.org:3128", "http://user:p%40ss@host:8888"):
            self.assertEqual(env_proxy_url("X", environ={"X": f" {value} "}), value)

    def test_anything_else_is_refused_without_repeating_the_value(self):
        for value in ("socks5://u:secretpw@host:1080", "vpn-proxy:8888", "http://", "http://u:secretpw@",
                      "http://u:secretpw@host:notaport", "http://u:secretpw@host:99999", "ftp://u:secretpw@h:1",
                      "http://u:secretpw@host :8888", "http://u:secretpw@h\nost:8888", "//u:secretpw@host:8888"):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured) as caught:
                env_proxy_url("X", environ={"X": value})
            self.assertEqual(str(caught.exception), "X must be an http:// or https:// URL with a host.")
            self.assertNotIn("secretpw", str(caught.exception))


class CheckDeployTests(SimpleTestCase):
    """`manage.py check --deploy` with the production values: only the warnings listed here may remain."""

    # W008: no SECURE_SSL_REDIRECT, on purpose (S14): the tunnel ends TLS and the probe speaks http.
    # W021: no SECURE_HSTS_PRELOAD, on purpose (S13): preload is irreversible, the site never asks for it.
    TOLERATED = {"security.W008", "security.W021"}

    def production(self, **extra):
        values = {"DEBUG": False, "ALLOWED_HOSTS": ["tri.example.org"], "SITE_URL": SITE,
                  "SECRET_KEY": "k7#Qz9" * 9, "TRUST_CF_CONNECTING_IP": True,
                  **security_settings(SITE, True, [], environ={}), **extra}
        return override_settings(**values)

    def issues(self):
        return {message.id for message in run_checks(tags=[Tags.security], include_deployment_checks=True)}

    def test_only_the_tolerated_warnings_remain(self):
        with self.production():
            self.assertEqual(self.issues() - self.TOLERATED, set())

    def test_the_check_does_catch_a_missing_setting(self):
        # Guards the test above: without the production values the same run is not clean.
        with self.production(SESSION_COOKIE_SECURE=False):
            self.assertIn("security.W012", self.issues())


class GunicornTests(SimpleTestCase):
    def test_conf_has_no_access_log_and_logs_errors_to_the_console(self):
        conf = runpy.run_path(str(BASE / "docker" / "gunicorn.conf.py"))
        self.assertIsNone(conf["accesslog"])
        self.assertEqual(conf["errorlog"], "-")
        self.assertFalse(conf["capture_output"])
        self.assertEqual((conf["workers"], conf["worker_class"], conf["threads"], conf["timeout"]),
                         (2, "gthread", 4, 90))
        self.assertEqual(conf["bind"], "0.0.0.0:8000")

    def test_conf_says_why_there_is_no_access_log(self):
        text = (BASE / "docker" / "gunicorn.conf.py").read_text()
        self.assertIn("desinscription", text)

    def test_entrypoint_starts_gunicorn_by_its_conf_and_has_no_access_log_option(self):
        script = (BASE / "docker" / "entrypoint.sh").read_text()
        self.assertIn("gunicorn config.wsgi:application -c docker/gunicorn.conf.py", script)
        for forbidden in ("--access-logfile", "--access-log", "GUNICORN_CMD_ARGS"):
            self.assertNotIn(forbidden, script)

    def test_no_compose_file_sets_gunicorn_options_in_the_environment(self):
        files = [*BASE.glob("docker-compose*.y*ml"), *BASE.glob("compose*.y*ml")]
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path.name):
                self.assertNotIn("GUNICORN_CMD_ARGS", path.read_text())
        self.assertNotIn("GUNICORN_CMD_ARGS", (BASE / "Dockerfile").read_text())

    def test_django_server_logger_is_left_to_runserver(self):
        self.assertNotIn("django.server", settings.LOGGING.get("loggers", {}))
        self.assertNotIn("django.server", settings.LOGGING.get("handlers", {}))


class ErrorLogTests(TestCase):
    """S17: a console handler on django.request and django.security, with the token filter on the handler."""

    def handler(self):
        return next(h for h in logging.getLogger("django.request").handlers if h.get_name() == "console_redacted")

    def capture(self):
        stream = io.StringIO()
        previous = self.handler().setStream(stream)
        self.addCleanup(self.handler().setStream, previous)
        return stream

    def test_handler_is_on_both_loggers_with_the_filter_and_a_warning_level(self):
        handler = self.handler()
        self.assertEqual(handler.level, logging.WARNING)
        self.assertEqual([type(f).__name__ for f in handler.filters], ["RedactLoginTokens"])
        for name in ("django.request", "django.security"):
            with self.subTest(logger=name):
                logger = logging.getLogger(name)
                self.assertIn(handler, logger.handlers)
                self.assertFalse(logger.propagate)

    def test_django_request_keeps_its_logger_filter(self):
        self.assertEqual([type(f).__name__ for f in logging.getLogger("django.request").filters],
                         ["RedactLoginTokens"])

    @override_settings(STORAGES=PLAIN_STATIC)
    def test_a_500_on_the_unsubscribe_page_prints_the_token_nowhere(self):
        user = User.objects.create_user("nora@example.fr")
        token = make_token(user)
        path = f"/desinscription/{token}/"
        stream = self.capture()
        with mock.patch("accounts.views.unsubscribe.stop_mails", side_effect=RuntimeError(f"boom {path}")):
            response = Client(raise_request_exception=False).post(path)
        self.assertEqual(response.status_code, 500)
        output = stream.getvalue()
        self.assertIn("Internal Server Error: /desinscription/[redacted]/", output)
        self.assertIn("RuntimeError: boom /desinscription/[redacted]/", output)  # the trace is printed, masked
        self.assertIn("Traceback (most recent call last)", output)
        self.assertNotIn(token, output)
        self.assertNotIn(token.split(":")[0], output)

    @override_settings(STORAGES=PLAIN_STATIC)
    def test_a_refused_sign_in_link_post_prints_no_token(self):
        token = "A" * 43
        stream = self.capture()
        Client(enforce_csrf_checks=True).post(f"/connexion/lien/{token}/")
        output = stream.getvalue()
        self.assertIn("/connexion/lien/[redacted]/", output)
        self.assertNotIn(token, output)


class ProbeTests(SimpleTestCase):
    """S16: the container's probe sends the first allowed host, or ALLOWED_HOSTS would refuse it."""

    def load(self):
        return runpy.run_path(str(BASE / "docker" / "healthcheck.py"), run_name="not_main")

    def test_first_allowed_host(self):
        host = self.load()["probe_host"]
        self.assertEqual(host({"DJANGO_ALLOWED_HOSTS": "tri.example.org,localhost"}), "tri.example.org")
        self.assertEqual(host({"DJANGO_ALLOWED_HOSTS": " tri.example.org , localhost"}), "tri.example.org")

    def test_defaults_and_unusable_entries_fall_back_to_localhost(self):
        host = self.load()["probe_host"]
        self.assertEqual(host({}), "localhost")
        self.assertEqual(host({"DJANGO_ALLOWED_HOSTS": ""}), "localhost")
        self.assertEqual(host({"DJANGO_ALLOWED_HOSTS": "*"}), "localhost")
        self.assertEqual(host({"DJANGO_ALLOWED_HOSTS": ".example.org"}), "example.org")

    def test_probe_asks_healthz_on_the_local_port_with_that_host_header(self):
        namespace = self.load()
        with mock.patch("urllib.request.urlopen") as urlopen:
            namespace["probe"]({"DJANGO_ALLOWED_HOSTS": "tri.example.org"})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8000/healthz")
        self.assertEqual(request.get_header("Host"), "tri.example.org")
        self.assertLessEqual(urlopen.call_args.kwargs["timeout"], 5)

    def test_probe_fails_loudly_when_the_site_answers_an_error(self):
        namespace = self.load()
        error = urllib.error.HTTPError("http://127.0.0.1:8000/healthz", 400, "Bad Request", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=error), self.assertRaises(urllib.error.HTTPError):
            namespace["probe"]({})

    def test_compose_runs_the_probe_and_no_longer_hardcodes_the_host(self):
        text = (BASE / "docker-compose.yml").read_text()
        self.assertIn("docker/healthcheck.py", text)
        self.assertNotIn("urlopen('http://localhost", text)


class ComposeTests(SimpleTestCase):
    def test_no_env_file_and_the_new_variables_are_interpolated_and_empty_by_default(self):
        text = (BASE / "docker-compose.yml").read_text()
        self.assertNotIn("env_file", text.replace("`env_file: .env`", ""))
        for name in ("SCRAPER_PROXY_URL", "SCRAPER_PROXY_REQUIRED", "RESEND_API_KEY", "HSTS_SECONDS",
                     "ADMIN_ACCESS_TEAM_DOMAIN", "ADMIN_ACCESS_AUD"):
            self.assertRegex(text, rf"{name}: \$\{{{name}:-[^}}]*\}}", name)
        self.assertRegex(text, r"TRUST_CF_CONNECTING_IP: \$\{TRUST_CF_CONNECTING_IP:-0\}")
        self.assertRegex(text, r"SITE_URL: \$\{SITE_URL:-http://localhost:8000\}")
