"""The on-demand scoring (sub-step 5.5): matching/on_demand.py, its caps, its run and the onboarding views calling it.

Claude is always the fake engine (llm.fake). The run is executed without a thread: RUNNER is execute() itself, called
by the on_commit callbacks the tests capture; one test starts a real thread with a stand-in for execute().
"""
import threading
from datetime import timedelta
from unittest import mock

from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

import llm
from accounts.models import Profile, SummaryStatus, User
from accounts.testing import make_profile
from jobs import summaries
from jobs.testing import OfferMaker, make_offer, make_source
from llm import fake
from matching import on_demand, scoring
from matching.models import Feedback, Score, ScoringRun
from matching.testing import REASON, USAGE, Failing, scores_answer, size, summaries_answer
from onboarding import summary
from onboarding.testing import CORRECT, TEXT, VALIDATE, WRITE, SummaryTestCase, block_url, prefixed
from onboarding.tests import CULTURE, END, url

ON_DEMAND = ScoringRun.Kind.ON_DEMAND
State, Error = ScoringRun.State, ScoringRun.Error


def on_demand_runs(user):
    return ScoringRun.objects.filter(user=user, kind=ON_DEMAND).order_by("started_at", "pk")


class OnDemandTestCase(OfferMaker, TestCase):
    def setUp(self):
        fake.reset()
        self.addCleanup(fake.reset)
        patcher = mock.patch.object(on_demand, "RUNNER", on_demand.execute)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = timezone.now()
        self.source = make_source(domains=["E"])
        self.profile = make_profile("nora@example.fr", seek_text="Un poste de régie dans un festival.")
        self.user = self.profile.user
        fake.set_response(summaries.TASK, summaries_answer)
        fake.set_response(scoring.TASK, scores_answer)

    def offer_defaults(self, number):
        return {"title": f"Régie {number}", "full_text": f"Texte {number}."}

    def request(self, user=None, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return on_demand.request_run(user or self.user, **kwargs)

    def make_run(self, state, user=None, ago=timedelta(hours=1), kind=ON_DEMAND, **fields):
        return ScoringRun.objects.create(user=user or self.user, kind=kind, state=state,
                                         started_at=timezone.now() - ago, **fields)

    def calls(self, task):
        return [call for call in fake.calls if call.task == task]

    def other_user(self, email):
        return make_profile(email, seek_text="Un poste de médiation.").user


class RequestRunTests(OnDemandTestCase):
    def test_first_run_scores_and_is_done(self):
        self.offers(3)
        self.assertEqual(self.request(), "started")
        run = on_demand_runs(self.user).get()
        self.assertEqual((run.state, run.error, run.offers_requested, run.offers_scored), (State.DONE, "", 3, 3))
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(Score.objects.filter(user=self.user, reason=REASON).count(), 3)

    def test_second_without_change_hits_the_account_cap(self):
        self.request()
        self.assertEqual(self.request(), "account_cap")
        self.assertEqual(on_demand_runs(self.user).count(), 1)

    def test_second_after_change_drops_the_scores_neither_sent_nor_judged(self):
        self.request()
        plain, sent, judged = self.offers(3)
        for offer in (plain, sent, judged):
            Score.objects.create(user=self.user, offer=offer, score=2, reason="Ancienne raison.",
                                 category=Score.Category.DISCARDED)
        Score.objects.filter(offer=sent).update(sent_on=self.now.date())
        Feedback.objects.create(user=self.user, offer=judged, verdict=Feedback.Verdict.NO)
        other = self.other_user("lea@example.fr")
        Score.objects.create(user=other, offer=plain, score=3, reason="Autre personne.",
                             category=Score.Category.DISCARDED)
        self.assertEqual(self.request(after_change=True), "started")
        self.assertEqual(on_demand_runs(self.user).count(), 2)
        reasons = dict(Score.objects.filter(user=self.user).values_list("offer_id", "reason"))
        self.assertEqual(reasons, {plain.pk: REASON, sent.pk: "Ancienne raison.", judged.pk: "Ancienne raison."})
        self.assertTrue(Score.objects.filter(user=other, offer=plain).exists())

    def test_third_hits_the_account_cap_even_after_a_change(self):
        self.request()
        self.request(after_change=True)
        Score.objects.create(user=self.user, offer=self.offer(), score=2, reason="Gardée.",
                             category=Score.Category.DISCARDED)
        self.assertEqual(self.request(after_change=True), "account_cap")
        self.assertEqual(on_demand_runs(self.user).count(), 2)
        self.assertTrue(Score.objects.filter(reason="Gardée.").exists())

    def test_partial_counts_failed_and_refused_do_not(self):
        self.make_run(State.FAILED, error=Error.ENGINE)
        self.make_run(State.REFUSED, error=Error.CAP)
        self.assertEqual(self.request(), "started")
        ScoringRun.objects.all().delete()
        self.make_run(State.PARTIAL, error=Error.ENGINE)
        self.assertEqual(self.request(), "account_cap")

    def test_runs_older_than_24_hours_do_not_count(self):
        self.make_run(State.DONE, ago=timedelta(hours=25))
        self.make_run(State.DONE, ago=timedelta(hours=26))
        self.assertEqual(self.request(), "started")

    def test_a_recent_running_run_is_not_doubled(self):
        self.make_run(State.RUNNING, ago=timedelta(minutes=3))
        with self.captureOnCommitCallbacks() as callbacks:
            self.assertEqual(on_demand.request_run(self.user, after_change=True), "running")
        self.assertEqual(callbacks, [])
        self.assertEqual(on_demand_runs(self.user).count(), 1)

    def test_a_run_running_for_11_minutes_is_stale_and_a_new_one_starts(self):
        old = self.make_run(State.RUNNING, ago=timedelta(minutes=11))
        self.assertEqual(self.request(), "started")
        old.refresh_from_db()
        self.assertEqual((old.state, old.error), (State.FAILED, Error.STALE))
        self.assertIsNotNone(old.finished_at)
        self.assertEqual(on_demand_runs(self.user).last().state, State.DONE)

    @override_settings(ON_DEMAND_DAILY_CAP=2)
    def test_site_cap_records_a_refused_run_and_logs_without_address(self):
        for email in ("lea@example.fr", "noe@example.fr"):
            self.make_run(State.DONE, user=self.other_user(email))
        with self.assertLogs("matching.on_demand", "ERROR") as logs:
            self.assertEqual(self.request(), "site_cap")
        self.assertNotIn("@", "".join(logs.output))
        self.assertNotIn("nora", "".join(logs.output))
        run = on_demand_runs(self.user).get()
        self.assertEqual((run.state, run.error), (State.REFUSED, Error.CAP))
        self.assertIsNotNone(run.finished_at)
        late = self.other_user("zoe@example.fr")
        with self.assertLogs("matching.on_demand", "ERROR"):
            self.assertEqual(self.request(late), "site_cap")
        self.assertEqual(on_demand_runs(late).get().state, State.REFUSED)
        self.assertEqual(self.calls(scoring.TASK), [])

    @override_settings(ON_DEMAND_DAILY_CAP=2)
    def test_refused_runs_do_not_count_for_the_site(self):
        other = self.other_user("lea@example.fr")
        self.make_run(State.DONE, user=other)
        for _ in range(3):
            self.make_run(State.REFUSED, user=other, error=Error.CAP)
        self.make_run(State.DONE, user=other, kind=ScoringRun.Kind.NIGHT)
        self.assertEqual(self.request(), "started")

    def test_a_profile_without_summary_preferences_nor_texts_is_not_ready(self):
        self.profile.seek_text = ""
        self.profile.save()
        self.assertEqual(self.request(), "not_ready")
        for fields in ({"refuse_text": "Le travail le dimanche."},
                       {"weighted_preferences": {"version": 1, "values": ["team"]}},
                       {"summary_text": "Je cherche la régie."},
                       {"summary_sentences": [{"text": "Vous cherchez la régie.", "sources": ["jobs"]}]}):
            with self.subTest(fields=fields):
                Profile.objects.filter(pk=self.profile.pk).update(**fields)
                ScoringRun.objects.all().delete()
                self.assertEqual(self.request(), "started")
                Profile.objects.filter(pk=self.profile.pk).update(
                    refuse_text="", weighted_preferences={}, summary_text="", summary_sentences=[])
        self.assertEqual(self.request(User.objects.create_user("sans@example.fr")), "not_ready")

    def test_damaged_criteria_are_not_ready_never_an_error(self):
        Profile.objects.filter(pk=self.profile.pk).update(hard_criteria={"version": 99})
        self.assertEqual(self.request(), "not_ready")
        self.assertFalse(ScoringRun.objects.exists())

    def test_nothing_is_scheduled_when_the_transaction_rolls_back(self):
        with self.captureOnCommitCallbacks() as callbacks:
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    self.assertEqual(on_demand.request_run(self.user), "started")
                    raise RuntimeError
        self.assertEqual(callbacks, [])
        self.assertFalse(ScoringRun.objects.exists())


class LatestRunTests(OnDemandTestCase):
    def test_none_then_the_latest_on_demand_run(self):
        self.assertIsNone(on_demand.latest_run(self.user))
        self.make_run(State.DONE, ago=timedelta(hours=3))
        latest = self.make_run(State.PARTIAL, ago=timedelta(hours=2))
        self.make_run(State.DONE, ago=timedelta(hours=1), kind=ScoringRun.Kind.NIGHT)
        self.make_run(State.DONE, user=self.other_user("lea@example.fr"))
        self.assertEqual(on_demand.latest_run(self.user), latest)

    def test_a_stale_running_run_is_failed_first(self):
        run = self.make_run(State.RUNNING, ago=timedelta(minutes=10, seconds=5))
        latest = on_demand.latest_run(self.user)
        self.assertEqual(latest, run)
        self.assertEqual((latest.state, latest.error), (State.FAILED, Error.STALE))
        run.refresh_from_db()
        self.assertEqual(run.state, State.FAILED)
        recent = self.make_run(State.RUNNING, ago=timedelta(minutes=9))
        self.assertEqual(on_demand.latest_run(self.user).state, State.RUNNING)
        self.assertEqual(on_demand.latest_run(self.user), recent)


class ExecuteTests(OnDemandTestCase):
    def execute(self):
        run = ScoringRun.objects.create(user=self.user, kind=ON_DEMAND)
        on_demand.execute(run.pk)
        run.refresh_from_db()
        return run

    def test_window_of_30_days(self):
        inside = self.offer(ago=timedelta(days=29))
        self.offer(ago=timedelta(days=31))
        run = self.execute()
        self.assertEqual(run.offers_requested, 1)
        self.assertEqual(list(Score.objects.values_list("offer_id", flat=True)), [inside.pk])

    def test_20_newest_of_30_candidates_in_two_batches_with_usage(self):
        offers = self.offers(35)  # collected together: the last made come first
        fake.set_usage(scoring.TASK, USAGE)
        run = self.execute()
        self.assertEqual(sorted(size(call) for call in self.calls(summaries.TASK)), [10, 20])
        self.assertEqual([size(call) for call in self.calls(scoring.TASK)], [10, 10])
        for call in self.calls(scoring.TASK):
            self.assertEqual(call.timeouts, llm.BATCH)
        self.assertEqual((run.state, run.error, run.offers_requested, run.offers_scored), (State.DONE, "", 20, 20))
        self.assertEqual((run.input_tokens, run.output_tokens, run.cache_read_tokens, run.cache_creation_tokens),
                         (200, 20, 2, 4))
        self.assertEqual(set(Score.objects.values_list("offer_id", flat=True)), {offer.pk for offer in offers[-20:]})
        summaries_run = ScoringRun.objects.get(kind=ScoringRun.Kind.SUMMARIES)
        self.assertEqual((summaries_run.user, summaries_run.state, summaries_run.offers_requested),
                         (None, State.DONE, 30))

    def test_prepass_then_refilter_drops_an_offer_coded_for_another_domain(self):
        general = make_source(name="Généraliste", domains=[])
        nurse = self.offer(source=general, title="Infirmière de festival")
        kept = self.offers(2, summary="Déjà résumée.", summary_written_at=self.now)
        no_text = self.offer(full_text="")
        fake.set_usage(summaries.TASK, USAGE)
        fake.set_response(summaries.TASK, lambda call: {"offres": [
            {"id": 1, "summary": "Soins dans un service de médecine.", "appellation": "Infirmière en soins généraux",
             "rome_code": ""}]})
        run = self.execute()
        self.assertEqual([size(call) for call in self.calls(summaries.TASK)], [1])
        nurse.refresh_from_db()
        self.assertTrue(nurse.rome_code.startswith("J"))
        self.assertEqual(run.offers_requested, 3)
        self.assertEqual(set(Score.objects.values_list("offer_id", flat=True)),
                         {kept[0].pk, kept[1].pk, no_text.pk})
        summaries_run = ScoringRun.objects.get(kind=ScoringRun.Kind.SUMMARIES)
        self.assertEqual((summaries_run.state, summaries_run.input_tokens), (State.DONE, 100))

    def test_scored_expired_and_duplicate_offers_are_left_out(self):
        scored = self.offer()
        Score.objects.create(user=self.user, offer=scored, score=5, reason="Déjà notée.", category=Score.Category.MAYBE)
        self.offer(expires_at=self.now - timedelta(days=1))
        newest = self.offer(match_key="regie-lyon", ago=timedelta(days=0))
        self.offer(match_key="regie-lyon", ago=timedelta(days=2))
        run = self.execute()
        self.assertEqual(run.offers_requested, 1)
        self.assertTrue(Score.objects.filter(offer=newest, reason=REASON).exists())

    def test_one_failed_batch_is_partial_without_retry(self):
        self.offers(15)
        fake.set_response(scoring.TASK, Failing(scores_answer, llm.EngineError("échec", usage=USAGE),
                                                lambda call, attempt: size(call) == 5))
        run = self.execute()
        self.assertEqual([size(call) for call in self.calls(scoring.TASK)], [10, 5])
        self.assertEqual((run.state, run.error, run.offers_scored), (State.PARTIAL, Error.ENGINE, 10))
        self.assertEqual(run.input_tokens, 100)

    def test_every_batch_failed(self):
        for error, code in ((llm.EngineError("échec"), Error.ENGINE), (llm.EngineRefused("refus"), Error.REFUSED)):
            with self.subTest(code=code):
                Score.objects.all().delete()
                self.offers(12, summary="Résumée.", summary_written_at=self.now)
                fake.set_response(scoring.TASK, error)
                run = self.execute()
                self.assertEqual((run.state, run.error, run.offers_scored), (State.FAILED, code, 0))
                self.assertIsNotNone(run.finished_at)

    def test_refused_prepass_still_scores(self):
        self.offers(3)
        fake.set_response(summaries.TASK, llm.EngineRefused("refus"))
        run = self.execute()
        self.assertEqual((run.state, run.offers_scored), (State.DONE, 3))
        summaries_run = ScoringRun.objects.get(kind=ScoringRun.Kind.SUMMARIES)
        self.assertEqual((summaries_run.state, summaries_run.error), (State.FAILED, Error.REFUSED))

    def test_unexpected_error_fails_the_run_as_crash_and_logs_no_content(self):
        self.offers(3)
        with mock.patch.object(scoring, "apply_batch", side_effect=RuntimeError("Texte secret de l’offre")), \
                self.assertLogs("matching.on_demand", "ERROR") as logs:
            run = self.execute()
        self.assertEqual((run.state, run.error), (State.FAILED, Error.CRASH))
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("secret", "".join(logs.output))
        self.assertNotIn("nora", "".join(logs.output))
        self.assertFalse(ScoringRun.objects.filter(state=State.RUNNING).exists())

    def test_unexpected_error_in_the_prepass_leaves_no_run_running(self):
        self.offers(3)
        with mock.patch.object(summaries, "apply_batch", side_effect=RuntimeError("Texte secret")), \
                self.assertLogs("matching.on_demand", "ERROR"):
            run = self.execute()
        self.assertEqual((run.state, run.error), (State.FAILED, Error.CRASH))
        self.assertFalse(ScoringRun.objects.filter(state=State.RUNNING).exists())

    def test_no_candidate_is_done_without_a_call(self):
        run = self.execute()
        self.assertEqual((run.state, run.offers_requested, run.offers_scored), (State.DONE, 0, 0))
        self.assertEqual(fake.calls, [])
        self.assertFalse(ScoringRun.objects.filter(kind=ScoringRun.Kind.SUMMARIES).exists())


class SearchChangedTests(OnDemandTestCase):
    def test_nothing_before_a_first_run(self):
        before = on_demand.search_snapshot(self.profile)
        Profile.objects.filter(pk=self.profile.pk).update(seek_text="Autre chose.")
        on_demand.search_changed(self.user, before)
        self.assertFalse(ScoringRun.objects.exists())

    def test_unchanged_search_starts_nothing(self):
        self.request()
        Score.objects.create(user=self.user, offer=self.offer(), score=2, reason="Gardée.",
                             category=Score.Category.DISCARDED)
        profile = Profile.objects.get(pk=self.profile.pk)
        before = on_demand.search_snapshot(profile)
        profile.cv_profile = {"skills": ["Régie"]}
        profile.save()
        with self.captureOnCommitCallbacks(execute=True):
            on_demand.search_changed(self.user, before)
        self.assertEqual(on_demand_runs(self.user).count(), 1)
        self.assertTrue(Score.objects.filter(reason="Gardée.").exists())

    def test_nothing_while_the_summary_is_stale(self):
        self.request()
        Score.objects.create(user=self.user, offer=self.offer(), score=2, reason="Gardée.",
                             category=Score.Category.DISCARDED)
        before = on_demand.search_snapshot(Profile.objects.get(pk=self.profile.pk))
        Profile.objects.filter(pk=self.profile.pk).update(seek_text="Autre chose.",
                                                          summary_status=SummaryStatus.STALE)
        with self.captureOnCommitCallbacks(execute=True):
            on_demand.search_changed(self.user, before)
        self.assertEqual(on_demand_runs(self.user).count(), 1)
        self.assertTrue(Score.objects.filter(reason="Gardée.").exists())

    def test_watch_takes_the_snapshot_then_asks_for_the_bonus_run(self):
        self.request()
        profile = Profile.objects.get(pk=self.profile.pk)
        with self.captureOnCommitCallbacks(execute=True):
            with on_demand.watch(self.user, profile):
                profile.seek_text = "Autre chose."
                profile.save()
        self.assertEqual(on_demand_runs(self.user).count(), 2)

    def test_watch_never_fails_and_logs_without_address(self):
        profile = Profile.objects.get(pk=self.profile.pk)
        with mock.patch.object(on_demand, "search_changed", side_effect=RuntimeError("nora@example.fr")), \
                self.assertLogs("matching.on_demand", "ERROR") as logs:
            with on_demand.watch(self.user, profile):
                pass
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("nora", "".join(logs.output))

    def test_watch_survives_a_snapshot_that_fails(self):
        profile = Profile.objects.get(pk=self.profile.pk)
        profile.summary_sentences = ["abc", "def"]  # mistyped: final_text() raises TypeError
        ran = []
        with mock.patch.object(on_demand, "search_changed") as changed, \
                self.assertLogs("matching.on_demand", "ERROR") as logs:
            with on_demand.watch(self.user, profile):
                ran.append(True)
        self.assertEqual(ran, [True])
        changed.assert_not_called()
        self.assertIn("TypeError", logs.output[0])

    def test_watch_lets_an_error_of_its_block_through_without_a_run(self):
        self.request()
        profile = Profile.objects.get(pk=self.profile.pk)
        with self.assertRaises(KeyError), mock.patch.object(on_demand, "search_changed") as changed:
            with on_demand.watch(self.user, profile):
                raise KeyError("x")
        changed.assert_not_called()

    def test_each_part_of_the_search_counts(self):
        changes = ({"domains": ["E", "K"]}, {"hard_criteria": {"version": 1, "contracts": {"values": ["cdi"],
                                                                                          "strict": True}}},
                   {"weighted_preferences": {"version": 1, "values": ["team"]}}, {"seek_text": "Autre chose."},
                   {"refuse_text": "Le dimanche."}, {"summary_text": "Mon texte."})
        for change in changes:
            with self.subTest(change=change):
                ScoringRun.objects.all().delete()
                self.make_run(State.DONE)
                profile = Profile.objects.get(pk=self.profile.pk)
                before = on_demand.search_snapshot(profile)
                Profile.objects.filter(pk=profile.pk).update(**change)
                with self.captureOnCommitCallbacks(execute=True):
                    on_demand.search_changed(self.user, before)
                self.assertEqual(on_demand_runs(self.user).count(), 2)
                Profile.objects.filter(pk=profile.pk).update(
                    domains=["E"], hard_criteria={"version": 1}, weighted_preferences={},
                    seek_text="Un poste de régie dans un festival.", refuse_text="", summary_text="")

    def test_a_normalized_value_is_not_a_change(self):
        self.make_run(State.DONE)
        Profile.objects.filter(pk=self.profile.pk).update(domains=["K", "E", "E"])
        profile = Profile.objects.get(pk=self.profile.pk)
        profile.domains = ["E", "K"]  # as the questionnaire reads it
        before = on_demand.search_snapshot(profile)
        on_demand.search_changed(self.user, before)
        self.assertEqual(on_demand_runs(self.user).count(), 1)


class RealThreadTests(TestCase):
    def test_start_runs_execute_in_another_thread_and_closes_its_connections(self):
        seen = {}

        def fake_execute(run_id):
            seen["run"] = run_id
            seen["thread"] = threading.get_ident()

        real_close_all = on_demand.connections.close_all

        def close_all():
            seen["closed_in"] = threading.get_ident()
            real_close_all()

        with mock.patch.object(on_demand, "execute", fake_execute), \
                mock.patch.object(on_demand.connections, "close_all", close_all):
            thread = on_demand.start(42)
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(thread.daemon)
        self.assertEqual(seen["run"], 42)
        self.assertNotEqual(seen["thread"], threading.get_ident())
        self.assertEqual(seen["closed_in"], seen["thread"])

    def test_runner_is_start(self):
        self.assertIs(on_demand.RUNNER, on_demand.start)


class ViewTests(SummaryTestCase):
    """The onboarding views ask for a run; RUNNER executes it in place."""

    def setUp(self):
        super().setUp()
        fake.set_response(scoring.TASK, scores_answer)
        patcher = mock.patch.object(on_demand, "RUNNER", on_demand.execute)
        patcher.start()
        self.addCleanup(patcher.stop)

    def send(self, path, data=None):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(path, data or {})
        self.assertEqual(response.status_code, 302, response.content.decode()[:2000])
        return response

    def runs(self):
        return list(on_demand_runs(self.user).values_list("state", flat=True))

    def finish(self):
        """The summary written (first run), « Ça me va » and the end screen: « Ma recherche » opens."""
        self.send(WRITE)
        self.send(VALIDATE)
        self.assertEqual(self.client.get(END).status_code, 200)

    def old_score(self):
        """A score of the first run's time, neither sent nor judged: dropped when the bonus run starts."""
        source = make_source(domains=["E"])
        offer = make_offer(source, full_text="Texte fictif.", summary_written_at=timezone.now())
        return Score.objects.create(user=self.user, offer=offer, score=2, reason="Ancienne raison.",
                                    category=Score.Category.DISCARDED)

    def test_text_view_survives_mistyped_summary_sentences(self):
        self.send(WRITE)
        Profile.objects.filter(pk=self.profile.pk).update(summary_sentences=["abc", "def"])
        self.send(TEXT, {"text": "Une synthèse écrite à la main."})

    def test_the_written_summary_starts_one_run_even_when_reloaded(self):
        self.send(WRITE)
        self.assertEqual(self.profile.summary_status, SummaryStatus.READY)
        self.assertEqual(self.runs(), [State.DONE])
        self.send(WRITE)
        self.assertEqual(self.runs(), [State.DONE])

    def test_a_failed_summary_starts_nothing_then_ca_me_va_does(self):
        fake.set_response(summary.TASK, llm.EngineError("échec"))
        self.send(WRITE)
        self.assertEqual(self.profile.summary_status, SummaryStatus.PENDING)
        self.assertEqual(self.runs(), [])
        self.send(VALIDATE)
        self.assertEqual(self.runs(), [State.DONE])

    def test_ca_me_va_after_a_run_starts_no_other(self):
        self.send(WRITE)
        self.send(VALIDATE)
        self.assertEqual(self.runs(), [State.DONE])

    def test_a_correction_after_the_first_run_starts_the_bonus_run(self):
        self.send(WRITE)
        fake.set_response(summary.TASK, {"sentences": [
            {"text": "J’ai compris que vous cherchez la médiation.", "sources": ["seek_text"]},
            {"text": "Vous visez Lyon.", "sources": ["location"]}], "questions": []})
        self.send(CORRECT, {"correction": "Plutôt la médiation."})
        self.assertEqual(self.runs(), [State.DONE, State.DONE])

    def test_a_failed_correction_starts_nothing(self):
        self.send(WRITE)
        fake.set_response(summary.TASK, llm.EngineError("échec"))
        self.send(CORRECT, {"correction": "Plutôt la médiation.", "answer_0": "Oui."})
        self.assertEqual(self.runs(), [State.DONE])

    def test_own_text_after_the_first_run_starts_the_bonus_run(self):
        self.send(WRITE)
        self.send(TEXT, {"text": "Je cherche la médiation à Lyon."})
        self.assertEqual(self.runs(), [State.DONE, State.DONE])

    def test_block_1_unchanged_starts_nothing_changed_starts_the_bonus_run_at_once(self):
        self.finish()
        same = {}
        for slug in ("lieu", "contrats", "mots-cles", "remuneration", "temps", "experience"):
            same.update(prefixed(slug, CULTURE[slug]))
        self.send(block_url("exclude"), same)
        self.assertEqual(self.runs(), [State.DONE])
        old = self.old_score()
        self.send(block_url("exclude"), {**same, "lieu-distance": "60"})
        self.assertEqual(self.profile.hard_criteria["location"]["distance"], "60")
        self.assertEqual(self.profile.summary_status, SummaryStatus.READY)  # block 1 is not in the summary
        self.assertEqual(self.runs(), [State.DONE, State.DONE])
        self.assertFalse(Score.objects.filter(pk=old.pk).exists())

    def test_a_change_that_stales_the_summary_waits_for_its_rewriting(self):
        self.finish()
        old = self.old_score()
        self.send(url("mots") + "?retour=ma-recherche", {"seek_text": "La régie son.", "refuse_text": ""})
        self.assertEqual(self.profile.summary_status, SummaryStatus.STALE)
        self.assertEqual(self.runs(), [State.DONE])
        self.assertTrue(Score.objects.filter(pk=old.pk).exists())
        self.send(WRITE + "?retour=ma-recherche")
        self.assertEqual(self.profile.summary_status, SummaryStatus.READY)
        self.assertEqual(self.runs(), [State.DONE, State.DONE])
        self.assertFalse(Score.objects.filter(pk=old.pk).exists())

    def test_a_block_page_that_stales_the_summary_waits_too(self):
        self.finish()
        words = {**prefixed("mots", {"seek_text": "La régie son.", "refuse_text": ""}), "cv-action": "continue"}
        self.send(block_url("words"), words)
        self.assertEqual(self.profile.summary_status, SummaryStatus.STALE)
        self.assertEqual(self.runs(), [State.DONE])

    def test_a_view_never_fails_because_of_the_scoring(self):
        with mock.patch.object(on_demand, "request_run", side_effect=RuntimeError("base verrouillée")), \
                self.assertLogs("onboarding.views", "ERROR") as logs:
            self.send(WRITE)
        self.assertEqual(self.profile.summary_status, SummaryStatus.READY)
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("nora", "".join(logs.output))
