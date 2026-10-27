"""Alert mails and what the night command now does around a slot (sub-step 9.2). Mail goes to locmem, pings to a
replaced opener: nothing leaves the machine."""
import datetime as dt
import urllib.error
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core import mail as django_mail
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

import mail
from accounts.testing import make_profile
from config.testing import assert_french_copy
from jobs.models import Source
from jobs.testing import make_offer, make_source
from mail.tests import fake_opener
from supervision import alerts
from supervision.models import AlertSent, TaskRun
from supervision.test_nuit import MONDAY, SATURDAY, SUNDAY, NuitBase, Recorder

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
KEY = "fictive-AbCdEfGh1234567890"
PING_URL = f"https://hc-ping.com/{KEY}"
PROXY = "http://vpn-user:S3cr3t-pw@10.0.0.5:8888"
LOGGER = "supervision.alerts"


def step(name, status="ok", error=""):
    return {"name": name, "status": status, "seconds": 1.0, "error": error}


def make_run(slot="nuit", day=MONDAY, status="ok", steps=(), started_at=None, **kwargs):
    return TaskRun.objects.create(slot=slot, day=day, status=status, steps=list(steps), finished_at=timezone.now(),
                                  started_at=started_at or timezone.now() - timedelta(minutes=30), **kwargs)


def failing_steps():
    return [step("collecte"), step("notation", "failed", "CommandError: Notation incomplète (1 en échec)"),
            step("purge_offres")]


@override_settings(EMAIL_BACKEND=LOCMEM, CLAUDE_ENGINE="", DEV_EMAILS=[], ALERT_EMAIL="alerte@example.fr",
                   HEALTHCHECKS_PING_URL="")
class AlertCase(TestCase):
    def sent(self):
        return django_mail.outbox


class FailedStepTests(AlertCase):
    def test_a_failed_step_mails_the_step_its_error_and_the_last_40_lines(self):
        output = "\n".join(f"ligne {n}" for n in range(1, 61)) + "\n"
        run = make_run(status="failed", steps=failing_steps())
        alerts.notify(run, {"notation": output, "collecte": "collecte fine\n"})
        self.assertEqual(len(self.sent()), 1)
        message = self.sent()[0]
        self.assertEqual(message.to, ["alerte@example.fr"])
        self.assertEqual(message.subject, "tri-offres · alerte (nuit du 19/10)")
        self.assertIn("notation", message.body)
        self.assertIn("CommandError: Notation incomplète (1 en échec)", message.body)
        self.assertIn("ligne 60", message.body)
        self.assertIn("ligne 21\n", message.body)
        self.assertNotIn("ligne 20\n", message.body)
        self.assertNotIn("collecte fine", message.body)
        self.assertIn("ligne 60", message.alternatives[0][0])

    def test_the_mail_is_technical_without_unsubscribe(self):
        alerts.notify(make_run(status="failed", steps=failing_steps()), {"notation": "x\n"})
        message = self.sent()[0]
        self.assertEqual(message.extra_headers, {})
        html = message.alternatives[0][0]
        for part in (message.body, html):
            self.assertNotIn("Pour ne plus recevoir", part)
            self.assertNotIn("desinscription", part)
        assert_french_copy(self, html)
        assert_french_copy(self, message.body)

    def test_links_in_third_party_text_are_neutralised(self):
        run = make_run(status="failed", steps=[step("collecte", "failed", "RuntimeError: voir https://evil.example/x")])
        alerts.notify(run, {"collecte": "échec sur http://evil.example/a et ftp://autre.example/b\n"})
        message = self.sent()[0]
        for part in (message.body, message.alternatives[0][0], message.subject):
            self.assertNotIn("://", part)
        self.assertIn("https[:]//evil.example/x", message.body)
        self.assertIn("http[:]//evil.example/a", message.body)

    def test_html_in_the_output_is_escaped_in_the_pre_block(self):
        run = make_run(status="failed", steps=failing_steps())
        alerts.notify(run, {"notation": "<script>alert(1)</script> & <b>gras</b>\n"})
        html = self.sent()[0].alternatives[0][0]
        self.assertIn("<pre", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;gras&lt;/b&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>gras", html)

    def test_the_error_and_the_output_are_masked(self):
        error = "CommandError: échec pour nora@example.fr via " + "k" * 50
        run = make_run(status="failed", steps=[step("notation", "failed", error)])
        alerts.notify(run, {"notation": "adresse nora@example.fr jeton " + "t" * 50 + "\n"})
        message = self.sent()[0]
        for part in (message.body, message.alternatives[0][0]):
            self.assertNotIn("nora@example.fr", part)
            self.assertNotIn("kkkkkkkkkk", part)
            self.assertNotIn("tttttttttt", part)

    @override_settings(SCRAPER_PROXY_URL=PROXY)
    def test_the_proxy_url_is_masked_even_if_the_output_was_not(self):
        run = make_run(status="failed", steps=[step("collecte", "failed", f"OSError: proxy {PROXY} refusé")])
        alerts.notify(run, {"collecte": f"HTTPS_PROXY={PROXY}\nmot de passe S3cr3t-pw\n"})
        message = self.sent()[0]
        for part in (message.body, message.alternatives[0][0]):
            self.assertNotIn("S3cr3t-pw", part)
            self.assertNotIn("vpn-user", part)
            self.assertNotIn("10.0.0.5", part)

    def test_a_huge_output_is_cut(self):
        run = make_run(status="failed", steps=failing_steps())
        alerts.notify(run, {"notation": ("y " * 5000 + "\n") * 40})
        self.assertLess(len(self.sent()[0].body), 20000)

    def test_a_missing_output_still_mails(self):
        alerts.notify(make_run(status="failed", steps=failing_steps()), {})
        self.assertEqual(len(self.sent()), 1)


class SourceAndOfferTests(AlertCase):
    def setUp(self):
        self.started = timezone.now() - timedelta(minutes=30)

    def source(self, key, **kwargs):
        fields = {"name": f"Source {key}", "kind": Source.Kind.ACTOR, "actor_source": key,
                  "state": Source.State.FAILING, "empty_runs": 2, "last_run_at": timezone.now()}
        return make_source(**{**fields, **kwargs})

    def night_run(self, **kwargs):
        return make_run(started_at=self.started, steps=[step("collecte"), step("notation")], **kwargs)

    def test_a_source_that_failed_tonight_mails_its_name_and_key_with_the_total(self):
        self.source("essai-a", name="Welcome to the Jungle")
        self.source("essai-b", empty_runs=5, last_run_at=self.started - timedelta(days=1))
        make_offer(make_source(name="autre"), collected_at=timezone.now())
        alerts.notify(self.night_run(), {})
        self.assertEqual(len(self.sent()), 1)
        body = self.sent()[0].body
        self.assertIn("Welcome to the Jungle", body)
        self.assertIn("essai-a", body)
        self.assertNotIn("essai-b", body)
        self.assertIn("2 sources", body)

    def test_a_source_already_failing_yesterday_does_not_mail(self):
        self.source("essai-a", empty_runs=3, last_run_at=timezone.now())
        self.source("essai-b", last_run_at=self.started - timedelta(hours=20))
        make_offer(make_source(name="autre"), collected_at=timezone.now())
        alerts.notify(self.night_run(), {})
        self.assertEqual(self.sent(), [])

    def test_only_the_night_slot_looks_at_sources(self):
        self.source("essai-a")
        alerts.notify(make_run(slot="soir", started_at=self.started, steps=[step("relance_onboarding")]), {})
        self.assertEqual(self.sent(), [])

    def test_a_business_night_collection_without_new_offer_mails(self):
        make_offer(make_source(name="vieille"), collected_at=self.started - timedelta(hours=2))
        alerts.notify(self.night_run(), {})
        self.assertEqual(len(self.sent()), 1)
        self.assertIn("aucune offre neuve", self.sent()[0].body)

    def test_a_new_offer_since_the_start_means_no_mail(self):
        make_offer(make_source(name="s"), collected_at=self.started + timedelta(minutes=5))
        alerts.notify(self.night_run(), {})
        self.assertEqual(self.sent(), [])

    def test_no_offer_alert_when_collection_was_skipped_or_failed_or_on_a_day_without_mail(self):
        for steps, day in (([step("collecte", "skipped"), step("notation", "skipped")], SATURDAY),
                           ([step("collecte", "failed", "CommandError: x")], MONDAY)):
            run = make_run(day=day, started_at=self.started, steps=steps)
            with self.subTest(day=day):
                alerts.notify(run, {})
                for message in self.sent():
                    self.assertNotIn("aucune offre neuve", message.body)
        # a collection that reports "ok" on a Sunday (forced by hand) is not an alert either
        django_mail.outbox.clear()
        alerts.notify(make_run(day=SUNDAY, started_at=self.started, steps=[step("collecte")]), {})
        self.assertEqual(self.sent(), [])

    def test_several_reasons_make_one_mail(self):
        self.source("essai-a")
        run = make_run(status="failed", started_at=self.started,
                       steps=[step("collecte"), step("notation", "failed", "CommandError: x")])
        alerts.notify(run, {"notation": "sortie\n"})
        self.assertEqual(len(self.sent()), 1)
        body = self.sent()[0].body
        for part in ("notation", "essai-a", "aucune offre neuve"):
            self.assertIn(part, body)

    def test_nothing_wrong_nothing_sent(self):
        make_offer(make_source(name="s"), collected_at=timezone.now())
        alerts.notify(self.night_run(), {})
        self.assertEqual(self.sent(), [])
        self.assertFalse(AlertSent.objects.exists())

    def test_other_slots_do_not_alert_without_a_failure(self):
        alerts.notify(make_run(slot="matin", steps=[step("envoi")]), {})
        alerts.notify(make_run(slot="soir", steps=[step("relance_onboarding")]), {})
        self.assertEqual(self.sent(), [])


class SendFailureTests(AlertCase):
    def test_a_failing_send_changes_nothing_and_logs_the_class_only(self):
        run = make_run(status="failed", steps=failing_steps())
        for error in (mail.MailDeliveryFailed("Resend unreachable nora@example.fr"), RuntimeError("alerte@example.fr")):
            with self.subTest(error=type(error).__name__), mock.patch("supervision.alerts.mail.send",
                                                                      side_effect=error), \
                    self.assertLogs(LOGGER) as logs:
                alerts.notify(run, {})
            self.assertNotIn("@", "\n".join(logs.output))
            self.assertIn(type(error).__name__, "\n".join(logs.output))
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertFalse(AlertSent.objects.exists())

    def test_a_mail_the_backend_did_not_send_is_not_recorded(self):
        with mock.patch("supervision.alerts.mail.send", return_value=0), self.assertLogs(LOGGER):
            alerts.notify(make_run(status="failed", steps=failing_steps()), {})
        self.assertFalse(AlertSent.objects.exists())

    @override_settings(ALERT_EMAIL="pas une adresse")
    def test_an_invalid_alert_address_is_logged_not_raised(self):
        with self.assertLogs(LOGGER):
            alerts.notify(make_run(status="failed", steps=failing_steps()), {})
        self.assertEqual(self.sent(), [])


class DedupeAndCapTests(AlertCase):
    def test_the_same_slot_day_and_reason_mails_once(self):
        run = make_run(status="failed", steps=failing_steps())
        alerts.notify(run, {})
        alerts.notify(make_run(status="failed", steps=failing_steps(), forced=True), {})
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(AlertSent.objects.filter(slot="nuit", day=MONDAY, reason="step:notation").count(), 1)

    def test_another_day_or_slot_mails_again(self):
        alerts.notify(make_run(status="failed", steps=failing_steps()), {})
        alerts.notify(make_run(day=SUNDAY, status="failed", steps=failing_steps()), {})
        alerts.notify(make_run(slot="matin", status="failed", steps=[step("envoi", "failed", "CommandError: x")]), {})
        self.assertEqual(len(self.sent()), 3)

    def test_a_new_reason_the_same_day_mails_only_the_new_one(self):
        alerts.notify(make_run(status="failed", steps=failing_steps()), {})
        steps = failing_steps() + [step("clearsessions", "failed", "OperationalError: verrouillée")]
        alerts.notify(make_run(status="failed", steps=steps, forced=True), {})
        self.assertEqual(len(self.sent()), 2)
        self.assertIn("clearsessions", self.sent()[1].body)
        self.assertNotIn("Notation incomplète", self.sent()[1].body)

    def record(self, count, ago=timedelta(hours=1)):
        """`count` mails already sent `ago` before now, the first one carrying three reasons."""
        for number in range(count):
            sent_at = timezone.now() - ago - timedelta(minutes=number)
            for reason in (["a", "b", "c"] if number == 0 else ["a"]):
                AlertSent.objects.create(slot="soir", day=dt.date(2026, 9, 1) + timedelta(days=number),
                                         reason=reason, sent_at=sent_at)

    def test_six_mails_in_24_hours_stop_the_seventh(self):
        self.record(6)
        with self.assertLogs(LOGGER, "ERROR") as logs:
            alerts.notify(make_run(status="failed", steps=failing_steps()), {})
        self.assertEqual(self.sent(), [])
        self.assertNotIn("@", "\n".join(logs.output))

    def test_five_mails_leave_room_for_one_more_and_a_multi_reason_mail_counts_once(self):
        self.record(5)
        make_offer(make_source(name="s"), collected_at=timezone.now())
        alerts.notify(make_run(status="failed", steps=failing_steps()), {})
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(AlertSent.objects.filter(day=MONDAY).count(), 1)

    def test_older_mails_do_not_count(self):
        self.record(6, ago=timedelta(hours=25))
        alerts.notify(make_run(status="failed", steps=failing_steps()), {})
        self.assertEqual(len(self.sent()), 1)

    def test_a_capped_alert_is_not_recorded_so_it_can_still_go_later(self):
        self.record(6)
        with self.assertLogs(LOGGER):
            alerts.notify(make_run(status="failed", steps=failing_steps()), {})
        self.assertFalse(AlertSent.objects.filter(day=MONDAY).exists())

    def test_old_alert_rows_are_purged_after_90_days_with_the_task_runs(self):
        now = timezone.now()
        AlertSent.objects.create(slot="nuit", day=MONDAY, reason="old", sent_at=now - timedelta(days=91))
        AlertSent.objects.create(slot="nuit", day=MONDAY, reason="recent", sent_at=now - timedelta(days=89))
        out = StringIO()
        call_command("purge_taskruns", stdout=out)
        self.assertEqual(list(AlertSent.objects.values_list("reason", flat=True)), ["recent"])
        self.assertIn("1 alerte(s)", out.getvalue())


class KilledTests(AlertCase):
    def test_send_killed_mails_the_reason(self):
        self.assertTrue(alerts.send_killed("nuit", dt.date(2026, 10, 19), "durée maximale dépassée (2 h 50)"))
        message = self.sent()[0]
        self.assertEqual(message.subject, "tri-offres · alerte (nuit du 19/10)")
        self.assertIn("durée maximale dépassée (2 h 50)", message.body)
        self.assertIn("interrompu", message.body)

    def test_send_killed_is_deduplicated_capped_and_masked(self):
        day = dt.date(2026, 10, 19)
        alerts.send_killed("nuit", day, "tué pour nora@example.fr https://evil.example")
        self.assertFalse(alerts.send_killed("nuit", day, "encore"))
        self.assertEqual(len(self.sent()), 1)
        self.assertNotIn("nora@example.fr", self.sent()[0].body)
        self.assertNotIn("://", self.sent()[0].body)
        self.assertTrue(alerts.send_killed("nuit", day + timedelta(days=1), "autre jour"))

    def test_send_killed_never_raises(self):
        with mock.patch("supervision.alerts.mail.send", side_effect=RuntimeError("x")), self.assertLogs(LOGGER):
            self.assertFalse(alerts.send_killed("soir", dt.date(2026, 10, 19), "tué"))

    def test_send_not_started_mails_once_per_day(self):
        day = dt.date(2026, 10, 19)
        self.assertTrue(alerts.send_not_started("matin", day))
        message = self.sent()[0]
        self.assertEqual(message.subject, "tri-offres · alerte (matin du 19/10)")
        self.assertIn("Créneau matin non lancé", message.body)
        self.assertFalse(alerts.send_not_started("matin", day))
        self.assertEqual(len(self.sent()), 1)


@override_settings(EMAIL_BACKEND=LOCMEM, CLAUDE_ENGINE="", DEV_EMAILS=[], ALERT_EMAIL="alerte@example.fr",
                   HEALTHCHECKS_PING_URL=PING_URL, SCRAPER_PROXY_URL=PROXY)
class NightIntegrationTests(NuitBase):
    """The real monitor around the night command, commands and HTTP replaced by doubles."""

    def setUp(self):
        super().setUp()
        sleep = mock.patch("supervision.healthchecks.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        self.opener = fake_opener()
        build = mock.patch("supervision.healthchecks.urllib.request.build_opener", return_value=self.opener)
        build.start()
        self.addCleanup(build.stop)

    def pings(self):
        return [(call.args[0].full_url, call.args[0].data.decode()) for call in self.opener.open.call_args_list]

    def attempt(self, slot, *options, recorder=None):
        """(output, CommandError or None): the output of a run that may end in failure."""
        out, error = StringIO(), None
        with mock.patch("supervision.management.commands.nuit.call_command", recorder or Recorder()):
            try:
                call_command("nuit", slot, *options, stdout=out)
            except CommandError as exc:
                error = exc
        return out.getvalue(), error

    def test_a_good_slot_pings_start_then_success_and_mails_nothing(self):
        self.nuit("soir", "--date", MONDAY)
        self.assertEqual([url for url, _ in self.pings()],
                         [f"{PING_URL}/tri-offres-soir/start", f"{PING_URL}/tri-offres-soir"])
        self.assertRegex(self.pings()[1][1], r"^relance_onboarding : ok \(\d+(,\d)? s\)\nCréneau : ok$")
        self.assertEqual(django_mail.outbox, [])

    def test_a_failed_slot_pings_fail_and_mails_once(self):
        _, error = self.attempt("soir", "--date", MONDAY, recorder=Recorder(failing={"relance_onboarding":
                                                                                    RuntimeError("boum")}))
        self.assertIsNotNone(error)
        self.assertEqual([url for url, _ in self.pings()],
                         [f"{PING_URL}/tri-offres-soir/start", f"{PING_URL}/tri-offres-soir/fail"])
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertEqual(TaskRun.objects.get().status, "failed")

    def test_dead_ping_and_dead_mail_change_neither_the_slot_nor_the_exit_code(self):
        self.opener.open.side_effect = urllib.error.URLError("no route")
        recorder = Recorder(failing={"relance_onboarding": RuntimeError("boum")})
        with mock.patch("supervision.alerts.mail.send", side_effect=mail.MailDeliveryFailed("x")), \
                self.assertLogs(level="WARNING"):
            _, error = self.attempt("soir", "--date", MONDAY, recorder=recorder)
        self.assertEqual(str(error), "Créneau soir en échec : relance_onboarding.")
        self.assertEqual(TaskRun.objects.get().status, "failed")
        # a good slot with a dead ping is still ok
        with self.assertLogs(level="WARNING"):
            self.nuit("soir", "--date", SUNDAY)
        self.assertEqual(TaskRun.objects.get(day=SUNDAY).status, "ok")

    def test_dry_run_neither_pings_nor_mails_and_lists_the_reasons(self):
        recorder = Recorder(failing={"notation": RuntimeError("boum nora@example.fr")})
        out, error = self.attempt("nuit", "--dry-run", "--date", MONDAY, recorder=recorder)
        self.assertIsNotNone(error)
        self.assertEqual(self.opener.open.call_count, 0)
        self.assertEqual(django_mail.outbox, [])
        self.assertFalse(AlertSent.objects.exists())
        self.assertIn("Alerte qui partirait : étape notation en échec", out)
        self.assertNotIn("nora@example.fr", out)
        self.assertIn("Résumé", out.splitlines()[-1])

    def test_dry_run_without_a_reason_says_so(self):
        out, _ = self.attempt("soir", "--dry-run", "--date", MONDAY)
        self.assertIn("Aucune alerte.", out)

    def test_the_address_and_secrets_reach_neither_the_ping_nor_the_mail_nor_the_journal_nor_the_output(self):
        make_profile("repere-secret@example.fr")
        boom = RuntimeError(f"échec pour repere-secret@example.fr via {PROXY} clé {KEY} {'t' * 50}\nsuite")
        recorder = Recorder(failing={"notation": boom, "collecte": boom},
                            say={"notation": f"compte repere-secret@example.fr proxy {PROXY} {KEY} S3cr3t-pw\n",
                                 "collecte": f"vu https://hc-ping.com/{KEY}\n"})
        out, error = self.attempt("nuit", "--date", MONDAY, recorder=recorder)
        self.assertIsNotNone(error)
        message = django_mail.outbox[0]
        everything = "\n".join([out, message.subject, message.body, message.alternatives[0][0],
                                *(f"{url}\n{body}" for url, body in self.pings()),
                                *(str(s) for run in TaskRun.objects.all() for s in run.steps)])
        # the ping URLs hold the key by design: only their bodies are searched for it
        everything = everything.replace(PING_URL, "")
        for secret in ("repere-secret", "S3cr3t-pw", "vpn-user", "10.0.0.5", KEY, "ttttttttt"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, everything)

    def test_error_and_output_are_masked_before_the_journal_and_the_console(self):
        recorder = Recorder(failing={"relance_onboarding": RuntimeError("pour nora@example.fr")},
                            say={"relance_onboarding": "adresse nora@example.fr\n"})
        out, _ = self.attempt("soir", "--date", MONDAY, recorder=recorder)
        error = TaskRun.objects.get().steps[0]["error"]
        self.assertNotIn("nora@example.fr", error)
        self.assertTrue(error.startswith("RuntimeError: "))
        self.assertNotIn("nora@example.fr", out)
