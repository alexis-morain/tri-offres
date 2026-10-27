"""The night and a person's OpenRouter key (contract 5.6 to 5.8): a refused key is not retried nor called again the
same night, an unreadable one stops every person that night, and neither is a failure of the step for the operator
unless the key cannot be read."""
from unittest import mock

import llm
from accounts.testing import make_profile
from llm import fake
from matching import night, on_demand, scoring
from matching.models import Score, ScoringRun
from matching.test_night import NightTestCase
from matching.testing import Failing, scores_answer

Error = ScoringRun.Error


class NightKeyTests(NightTestCase):
    def setUp(self):
        super().setUp()
        # One batch at a time, in the order of the jobs: what a refusal stops is then deterministic.
        patcher = mock.patch.object(night, "PARALLEL_BATCHES", 1)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.nora = make_profile("nora@example.fr")
        self.paul = make_profile("paul@example.fr")
        self.offers(25)  # three batches each

    def fail_for(self, email, error):
        fake.set_response(scoring.TASK, Failing(scores_answer, error, lambda call, attempt: call.user.email == email))

    def calls_for(self, profile):
        return [call for call in self.calls(scoring.TASK) if call.user == profile.user]

    def run_of(self, profile):
        return ScoringRun.objects.get(user=profile.user, kind=ScoringRun.Kind.NIGHT)

    def test_a_refused_key_is_called_once_not_retried_and_the_others_are_scored(self):
        for error in (llm.EngineCreditExhausted("x"), llm.EngineKeyRejected("x"), llm.EngineKeyLimitReached("x")):
            with self.subTest(error=type(error).__name__):
                fake.calls.clear()
                ScoringRun.objects.all().delete()
                Score.objects.all().delete()
                self.fail_for("nora@example.fr", error)
                report = self.run_night(phases=["personnes"])
                self.assertEqual(len(self.calls_for(self.nora)), 1)
                self.assertEqual(self.sleeps, [])
                self.assertEqual(len(self.calls_for(self.paul)), 3)
                run = self.run_of(self.nora)
                self.assertEqual((run.state, run.error), (ScoringRun.State.FAILED, Error.KEY))
                self.assertEqual(self.run_of(self.paul).state, ScoringRun.State.DONE)
                self.assertEqual(report.failures(), [])  # an expected condition, not the operator's to fix

    def test_the_report_says_the_key_was_refused(self):
        self.fail_for("nora@example.fr", llm.EngineCreditExhausted("x"))
        report = self.run_night(phases=["personnes"])
        lines = "\n".join(night.format_report(report))
        self.assertIn(f"compte {self.nora.user_id}", lines)
        self.assertIn("clé OpenRouter", lines)

    def test_a_refusal_next_night_is_tried_again(self):
        self.fail_for("nora@example.fr", llm.EngineCreditExhausted("x"))
        self.run_night(phases=["personnes"])
        fake.calls.clear()
        fake.set_response(scoring.TASK, scores_answer)
        self.run_night(phases=["personnes"])
        self.assertEqual(len(self.calls_for(self.nora)), 3)

    def test_an_unreadable_key_stops_every_person_that_night_and_fails_the_step(self):
        self.fail_for("nora@example.fr", llm.EngineKeyUnreadable("x"))
        report = self.run_night(phases=["personnes"])
        self.assertEqual(len(self.calls_for(self.nora)), 1)
        self.assertEqual(self.calls_for(self.paul), [])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self.run_of(self.nora).error, Error.KEY_UNREADABLE)
        self.assertEqual(self.run_of(self.paul).error, Error.KEY_UNREADABLE)
        self.assertEqual(len(report.failures()), 2)

    def test_the_prepass_is_not_stopped_by_a_persons_key(self):
        from jobs import summaries
        from matching.testing import summaries_answer

        fake.set_response(summaries.TASK, summaries_answer)
        self.fail_for("nora@example.fr", llm.EngineKeyUnreadable("x"))
        report = self.run_night()
        self.assertEqual(report.summaries.written, 25)

    def test_an_ordinary_failure_is_still_retried_once(self):
        self.fail_for("nora@example.fr", llm.EngineError("x"))
        self.run_night(phases=["personnes"])
        self.assertEqual(len(self.sleeps), 3)  # three batches, one retry each
        self.assertEqual(self.run_of(self.nora).error, Error.ENGINE)

    def test_error_codes(self):
        self.assertEqual(Error.of(llm.EngineKeyRejected("x")), Error.KEY)
        self.assertEqual(Error.of(llm.EngineCreditExhausted("x")), Error.KEY)
        self.assertEqual(Error.of(llm.EngineKeyLimitReached("x")), Error.KEY)
        self.assertEqual(Error.of(llm.EngineKeyUnreadable("x")), Error.KEY_UNREADABLE)
        self.assertEqual(Error.of(llm.EngineUnavailable("x")), Error.ENGINE)
        self.assertEqual(Error.of(llm.EngineRefused("x")), Error.REFUSED)


class OnDemandKeyTests(NightTestCase):
    def test_a_refused_key_stops_the_rest_of_the_run(self):
        profile = make_profile("nora@example.fr")
        run = ScoringRun.objects.create(user=profile.user, kind=ScoringRun.Kind.ON_DEMAND)
        calls = []

        def call(batch):
            calls.append(batch)
            raise llm.EngineCreditExhausted("x")

        on_demand._run_batches(run, list(range(25)), 10, call)
        self.assertEqual(len(calls), 1)
        run.refresh_from_db()
        self.assertEqual((run.state, run.error), (ScoringRun.State.FAILED, Error.KEY))

    def test_an_ordinary_failure_still_lets_the_next_batches_run(self):
        profile = make_profile("nora@example.fr")
        run = ScoringRun.objects.create(user=profile.user, kind=ScoringRun.Kind.ON_DEMAND)
        calls = []

        def call(batch):
            calls.append(batch)
            raise llm.EngineError("x")

        on_demand._run_batches(run, list(range(25)), 10, call)
        self.assertEqual(len(calls), 3)
