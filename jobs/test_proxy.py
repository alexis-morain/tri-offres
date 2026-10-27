"""Step 10.2 (D10, S18): the collection never leaves by the box's address when the proxy is required, and nothing the
actor writes (failure reasons, read error, report, log file) carries the proxy credentials. The fake actor is run by
the real subprocess: no network."""
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from jobs import collect
from jobs.test_collecte_command import ITEMS, night_record
from jobs.test_scraper import FakeActorMixin, run_stats_record, stats_line

PROXY = "http://proxyuser:s3cretpass@vpn-proxy.internal:8888"
LEAK = "s3cretpass"
REQUIRED_MESSAGE = ("SCRAPER_PROXY_URL est vide alors que SCRAPER_PROXY_REQUIRED est posé : la collecte ne sort "
                    "jamais par l’IP de la box.")


class ProxyRequiredTests(FakeActorMixin, TestCase):
    def collect(self, *args):
        out = StringIO()
        error = None
        try:
            call_command("collecte", *args, stdout=out)
        except CommandError as raised:
            error = raised
        return out.getvalue(), error

    @override_settings(SCRAPER_PROXY_REQUIRED=True, SCRAPER_PROXY_URL="")
    def test_required_and_empty_stops_before_the_actor_runs(self):
        self.behave(items=ITEMS, run_stats=night_record())
        output, error = self.collect()
        self.assertEqual(str(error), REQUIRED_MESSAGE)
        self.assertEqual(output, "")
        self.assertFalse((self.storage / "fake-input.json").exists())  # the actor never ran
        self.assertFalse((self.storage / "actor.log").exists())

    @override_settings(SCRAPER_PROXY_REQUIRED=True, SCRAPER_PROXY_URL="")
    def test_required_and_empty_stops_a_dry_run_too(self):
        self.behave(items=ITEMS, run_stats=night_record())
        _, error = self.collect("--dry-run")
        self.assertEqual(str(error), REQUIRED_MESSAGE)
        self.assertFalse((self.storage / "fake-input.json").exists())

    @override_settings(SCRAPER_PROXY_REQUIRED=True, SCRAPER_PROXY_URL=PROXY)
    def test_required_and_set_runs_the_actor_through_the_proxy(self):
        self.behave(items=ITEMS, run_stats=night_record())
        _, error = self.collect()
        self.assertIsNone(error)
        env = self.seen_by_actor("env")
        self.assertEqual((env["HTTPS_PROXY"], env["HTTP_PROXY"]), (PROXY, PROXY))

    @override_settings(SCRAPER_PROXY_REQUIRED=False, SCRAPER_PROXY_URL="")
    def test_not_required_runs_without_proxy(self):
        self.behave(items=ITEMS, run_stats=night_record())
        _, error = self.collect()
        self.assertIsNone(error)
        self.assertNotIn("HTTPS_PROXY", self.seen_by_actor("env"))


class ActorOutputIsRedactedTests(FakeActorMixin, TestCase):
    def collect(self, *args):
        out = StringIO()
        error = None
        try:
            call_command("collecte", *args, stdout=out)
        except CommandError as raised:
            error = raised
        return out.getvalue(), error

    def failing_record(self, reason):
        record = night_record()
        record["sources"]["meteojob"] = stats_line(failed=reason, pushed=0, filtered=0, dupes=0, cross_dupes=0,
                                                   already_seen=0)
        return record

    def check_nothing_leaks(self, proxy_setting):
        reason = f"ProxyError via {PROXY} refused"
        with override_settings(SCRAPER_PROXY_URL=proxy_setting):
            self.behave(items=ITEMS, run_stats=self.failing_record(reason), exit=1,
                        log_text=f"connecting through {PROXY}\nHTTP 407 for {PROXY}\n")
            output, error = self.collect()
        log = (self.storage / "actor.log").read_text()
        self.assertIsNotNone(error)  # exit code 1
        for text in (output, str(error), log):
            self.assertNotIn(LEAK, text)
            self.assertNotIn("proxyuser", text)
        self.assertIn("ProxyError via", output)  # the reason is still there, only the credentials are not
        self.assertIn("connecting through", log)

    def test_report_error_and_log_hide_the_credentials_of_the_configured_proxy(self):
        self.check_nothing_leaks(PROXY)

    def test_report_error_and_log_hide_credentials_of_a_url_that_is_not_configured(self):
        self.check_nothing_leaks("")

    @override_settings(SCRAPER_PROXY_URL=PROXY)
    def test_a_bare_password_is_hidden_too(self):
        reason = f"auth failed with password {LEAK}"
        self.behave(items=ITEMS, run_stats=self.failing_record(reason), log_text=f"password was {LEAK}")
        output, _ = self.collect()
        self.assertNotIn(LEAK, output)
        self.assertNotIn(LEAK, (self.storage / "actor.log").read_text())

    def test_log_is_redacted_in_the_part_that_is_kept(self):
        self.behave(items=ITEMS, run_stats=night_record(), log_text=f"through {PROXY}")
        self.collect()
        log = (self.storage / "actor.log").read_text()
        self.assertNotIn(LEAK, log)
        self.assertIn("fake actor: started", log)

    def test_read_error_and_the_report_hide_the_credentials(self):
        class Cut(Exception):
            pass

        def items():
            raise Cut(f"stream cut on {PROXY}")
            yield  # pragma: no cover

        sources = collect.actor_sources()
        result = collect.import_items(items(), sources, dry_run=True, stop_on=Cut)
        self.assertNotIn(LEAK, result.read_error)
        self.assertNotIn("proxyuser", result.read_error)
        self.assertIn("stream cut on", result.read_error)
        errors = collect.failures(result, None)
        self.assertNotIn(LEAK, " ".join(errors))

    @override_settings(SCRAPER_PROXY_URL="")
    def test_failure_reason_is_redacted_before_it_is_cut(self):
        # 190 characters of filler, then the credentials: the cut at 200 must not keep half of the secret.
        reason = "e" * 150 + " " + PROXY
        lines = collect.actor_lines({"meteojob"}, run_stats_record(meteojob=stats_line(failed=reason)))
        failed = lines["meteojob"].failed
        self.assertNotIn(LEAK, failed)
        self.assertNotIn("proxyuser", failed)
        self.assertNotIn("s3cret", failed)
