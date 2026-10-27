"""healthchecks.io pings (sub-step 9.2): the opener is replaced, nothing leaves the machine."""
import urllib.error
import urllib.request
from io import BytesIO
from unittest import mock

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from config.http import RefuseRedirects
from mail.tests import fake_opener, fake_response, redirecting_opener
from supervision import healthchecks, monitor
from supervision.models import TaskRun

KEY = "fictive-AbCdEfGh1234567890"
URL = f"https://hc-ping.com/{KEY}"
LOGGER = "supervision.healthchecks"


def run_of(slot="nuit", status="ok", steps=()):
    return TaskRun(slot=slot, day="2026-10-19", started_at=timezone.now(), status=status, steps=list(steps))


def http_error(code):
    return urllib.error.HTTPError(f"{URL}/tri-offres-nuit", code, "x", {}, BytesIO(b""))


@override_settings(HEALTHCHECKS_PING_URL=URL)
class PingTests(SimpleTestCase):
    def patched(self, opener):
        return mock.patch("supervision.healthchecks.urllib.request.build_opener", return_value=opener)

    def test_no_url_no_call(self):
        with override_settings(HEALTHCHECKS_PING_URL=""), \
                mock.patch("supervision.healthchecks.urllib.request.build_opener") as build:
            self.assertFalse(healthchecks.ping("nuit", "start"))
        build.assert_not_called()

    def test_each_kind_posts_to_the_slug_of_the_slot(self):
        expected = {("nuit", "start"): f"{URL}/tri-offres-nuit/start", ("nuit", "success"): f"{URL}/tri-offres-nuit",
                    ("matin", "fail"): f"{URL}/tri-offres-matin/fail", ("soir", "success"): f"{URL}/tri-offres-soir"}
        for (slot, kind), url in expected.items():
            opener = fake_opener()
            with self.subTest(slot=slot, kind=kind), self.patched(opener):
                self.assertTrue(healthchecks.ping(slot, kind, "corps"))
            request = opener.open.call_args.args[0]
            self.assertEqual((request.full_url, request.get_method(), request.data), (url, "POST", b"corps"))
            self.assertEqual(opener.open.call_args.kwargs["timeout"], 10)

    def test_a_trailing_slash_in_the_setting_is_harmless(self):
        opener = fake_opener()
        with override_settings(HEALTHCHECKS_PING_URL=URL + "/"), self.patched(opener):
            healthchecks.ping("nuit", "success")
        self.assertEqual(opener.open.call_args.args[0].full_url, f"{URL}/tri-offres-nuit")

    def test_unknown_slot_or_kind_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            healthchecks.ping("midi", "start")
        with self.assertRaises(ValueError):
            healthchecks.ping("nuit", "boom")

    def test_the_body_is_cut_to_its_last_10000_bytes(self):
        opener = fake_opener()
        with self.patched(opener):
            healthchecks.ping("nuit", "success", "a" * 5000 + "b" * 10000)
        self.assertEqual(opener.open.call_args.args[0].data, b"b" * 10000)

    def test_the_opener_has_no_proxy_and_refuses_redirects(self):
        with self.patched(fake_opener()) as build:
            healthchecks.ping("nuit", "start")
        handlers = build.call_args.args
        proxies = [h for h in handlers if isinstance(h, urllib.request.ProxyHandler)]
        self.assertEqual(len(proxies), 1)
        self.assertEqual(proxies[0].proxies, {})
        self.assertTrue(any(isinstance(h, RefuseRedirects) for h in handlers))

    def test_a_redirect_is_not_followed_and_not_retried(self):
        build, calls = redirecting_opener("https://elsewhere.example/steal")
        with mock.patch("supervision.healthchecks.urllib.request.build_opener", side_effect=build), \
                mock.patch("supervision.healthchecks.time.sleep") as sleep, self.assertLogs(LOGGER) as logs:
            self.assertFalse(healthchecks.ping("nuit", "success"))
        self.assertEqual(calls, [f"{URL}/tri-offres-nuit"])
        sleep.assert_not_called()
        self.assertNotIn("elsewhere.example", "\n".join(logs.output))
        self.assertNotIn(KEY, "\n".join(logs.output))

    def test_network_error_is_retried_once_after_two_seconds_then_swallowed(self):
        for error in (urllib.error.URLError(f"no route {URL}"), TimeoutError(URL), ConnectionResetError(URL)):
            opener = fake_opener(error=error)
            with self.subTest(error=type(error).__name__), self.patched(opener), \
                    mock.patch("supervision.healthchecks.time.sleep") as sleep, self.assertLogs(LOGGER) as logs:
                self.assertFalse(healthchecks.ping("nuit", "success"))
            self.assertEqual(opener.open.call_count, 2)
            sleep.assert_called_once_with(2)
            self.assertNotIn(KEY, "\n".join(logs.output))
            self.assertIn(type(error).__name__, "\n".join(logs.output))

    def test_a_5xx_is_retried_and_a_second_try_can_succeed(self):
        opener = mock.MagicMock()
        opener.open.side_effect = [http_error(500), fake_response()]
        with self.patched(opener), mock.patch("supervision.healthchecks.time.sleep") as sleep, \
                self.assertLogs(LOGGER, "WARNING") as logs:
            self.assertTrue(healthchecks.ping("nuit", "success"))
        self.assertEqual(opener.open.call_count, 2)
        sleep.assert_called_once_with(2)
        self.assertNotIn(KEY, "\n".join(logs.output))

    def test_a_persistent_500_is_swallowed(self):
        opener = fake_opener(error=http_error(500))
        with self.patched(opener), mock.patch("supervision.healthchecks.time.sleep"), \
                self.assertLogs(LOGGER) as logs:
            self.assertFalse(healthchecks.ping("nuit", "fail"))
        self.assertEqual(opener.open.call_count, 2)
        self.assertNotIn(KEY, "\n".join(logs.output))

    def test_a_4xx_is_not_retried(self):
        opener = fake_opener(error=http_error(404))
        with self.patched(opener), mock.patch("supervision.healthchecks.time.sleep") as sleep, \
                self.assertLogs(LOGGER) as logs:
            self.assertFalse(healthchecks.ping("nuit", "fail"))
        self.assertEqual(opener.open.call_count, 1)
        sleep.assert_not_called()
        self.assertNotIn(KEY, "\n".join(logs.output))

    def test_any_exception_is_swallowed_and_its_text_never_logged(self):
        opener = fake_opener(error=ValueError(f"boom {URL} nora@example.fr"))
        with self.patched(opener), mock.patch("supervision.healthchecks.time.sleep"), \
                self.assertLogs(LOGGER) as logs:
            self.assertFalse(healthchecks.ping("nuit", "start"))
        text = "\n".join(logs.output)
        self.assertNotIn(KEY, text)
        self.assertNotIn("nora@example.fr", text)
        self.assertIn("ValueError", text)

    def test_a_good_ping_logs_nothing(self):
        with self.patched(fake_opener()), self.assertNoLogs(LOGGER):
            healthchecks.ping("nuit", "success")


class BodyTests(SimpleTestCase):
    STEPS = [{"name": "collecte", "status": "ok", "seconds": 12.34, "error": ""},
             {"name": "notation", "status": "failed", "seconds": 3.0, "error": "CommandError: boom"},
             {"name": "purge_offres", "status": "skipped", "seconds": 0, "error": ""}]

    def test_one_line_per_step_then_the_state_of_the_slot(self):
        body = healthchecks.ping_body(run_of(status="failed", steps=self.STEPS))
        self.assertEqual(body.splitlines(), [
            "collecte : ok (12,3 s)", "notation : échec (3 s)", "purge_offres : sautée (0 s)", "Créneau : échec"])

    def test_the_state_words(self):
        words = (("ok", "ok"), ("failed", "échec"), ("interrupted", "interrompu"), ("running", "en cours"))
        for status, word in words:
            with self.subTest(status=status):
                self.assertEqual(healthchecks.ping_body(run_of(status=status)).splitlines(), [f"Créneau : {word}"])

    def test_nothing_but_table_names_states_and_durations_gets_through(self):
        steps = [{"name": "notation", "status": "failed", "seconds": 1.5,
                  "error": "CommandError: nora@example.fr token=" + "x" * 50},
                 {"name": "nora@example.fr", "status": "ok", "seconds": 1, "error": ""},
                 {"name": "purge_offres", "status": "nora@example.fr", "seconds": "nora@example.fr", "error": ""},
                 {"name": "clearsessions", "status": "ok", "seconds": 2, "error": ""}]
        body = healthchecks.ping_body(run_of(status="failed", steps=steps))
        self.assertNotIn("nora", body)
        self.assertNotIn("CommandError", body)
        self.assertNotIn("xxxx", body)
        self.assertIn("notation : échec (1,5 s)", body)
        self.assertIn("clearsessions : ok (2 s)", body)
        self.assertNotIn("purge_offres", body)  # unknown state word: the line is dropped


@override_settings(HEALTHCHECKS_PING_URL=URL, ALERT_EMAIL="alerte@example.fr")
class MonitorHooksTests(SimpleTestCase):
    def test_started_pings_start_and_finished_pings_by_status(self):
        for status, suffix in (("ok", ""), ("failed", "/fail"), ("interrupted", "/fail")):
            opener = fake_opener()
            run = run_of(slot="matin", status=status)
            with self.subTest(status=status), mock.patch("supervision.healthchecks.urllib.request.build_opener",
                                                         return_value=opener), \
                    mock.patch("supervision.alerts.notify"):
                monitor.started(run)
                monitor.finished(run, {})
            urls = [call.args[0].full_url for call in opener.open.call_args_list]
            self.assertEqual(urls, [f"{URL}/tri-offres-matin/start", f"{URL}/tri-offres-matin{suffix}"])
