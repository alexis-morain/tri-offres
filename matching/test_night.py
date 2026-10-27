"""The night (sub-step 5.4): matching/night.py, the two phases, the pool, the retry and the caps.

Claude is always the fake engine (llm.fake); the retry wait is a recorder, never a real sleep.
"""
import threading
from datetime import timedelta
from unittest import mock

from django.db.models.signals import post_save
from django.test import TestCase, override_settings
from django.utils import timezone

import llm
from accounts.models import Profile, User
from accounts.testing import make_profile
from jobs import summaries
from jobs.models import JobOffer
from jobs.testing import OfferMaker, make_source
from llm import fake
from matching import night, scoring
from matching.models import Score, ScoringRun
from matching.testing import SUMMARY, USAGE, Failing, scores_answer, size, summaries_answer


class NightTestCase(OfferMaker, TestCase):
    def setUp(self):
        fake.reset()
        self.addCleanup(fake.reset)
        self.now = timezone.now()
        self.source = make_source(domains=["E"])
        self.sleeps = []
        fake.set_response(summaries.TASK, summaries_answer)
        fake.set_response(scoring.TASK, scores_answer)

    def offer_defaults(self, number):
        return {"title": f"Offre {number}", "full_text": f"Texte {number}."}

    def run_night(self, **kwargs):
        return night.run_night(self.now, sleep=self.sleeps.append, **kwargs)

    def calls(self, task):
        return [call for call in fake.calls if call.task == task]


class SummariesPhaseTests(NightTestCase):
    def setUp(self):
        super().setUp()
        make_profile("nora@example.fr")

    def run_summaries(self, **kwargs):
        return self.run_night(phases=["resumes"], **kwargs)

    def test_batches_of_20_and_run(self):
        self.offers(45)
        fake.set_usage(summaries.TASK, USAGE)
        report = self.run_summaries()
        self.assertEqual(sorted(size(call) for call in self.calls(summaries.TASK)), [5, 20, 20])
        self.assertFalse(JobOffer.objects.filter(summary_written_at__isnull=True).exists())
        self.assertEqual(JobOffer.objects.filter(summary=SUMMARY).count(), 45)
        run = ScoringRun.objects.get()
        self.assertEqual((run.kind, run.user, run.state, run.error), (ScoringRun.Kind.SUMMARIES, None,
                                                                       ScoringRun.State.DONE, ""))
        self.assertEqual((run.offers_requested, run.offers_scored), (45, 45))
        self.assertEqual((run.input_tokens, run.output_tokens, run.cache_read_tokens, run.cache_creation_tokens),
                         (300, 30, 3, 6))
        self.assertIsNotNone(run.finished_at)
        self.assertEqual((report.summaries.requested, report.summaries.written, report.summaries.batches),
                         (45, 45, 3))
        self.assertEqual(report.failures(), [])
        self.assertEqual(self.calls(scoring.TASK), [])

    @override_settings(PREPASS_NIGHT_CAP=25)
    def test_cap_cuts_and_logs_once(self):
        old = self.offers(5, ago=timedelta(hours=30))
        self.offers(25, ago=timedelta(hours=2))
        with self.assertLogs("matching.night", "ERROR") as logs:
            report = self.run_summaries()
        self.assertEqual(len(logs.records), 1)
        self.assertIn("5", logs.output[0])
        self.assertEqual(sorted(size(call) for call in self.calls(summaries.TASK)), [5, 20])
        self.assertEqual(report.summaries_cut, 5)
        self.assertEqual(ScoringRun.objects.get().offers_requested, 25)
        self.assertEqual(JobOffer.objects.filter(pk__in=[offer.pk for offer in old],
                                                 summary_written_at__isnull=True).count(), 5)

    def test_failed_batch_retried_once_then_partial(self):
        self.offers(25)
        error = llm.EngineError("échec", usage=USAGE)
        fake.set_response(summaries.TASK, Failing(summaries_answer, error, lambda call, attempt: size(call) == 5))
        with self.assertNoLogs("matching.night", "ERROR"):
            report = self.run_summaries()
        self.assertEqual(sorted(size(call) for call in self.calls(summaries.TASK)), [5, 5, 20])
        self.assertEqual(self.sleeps, [night.RETRY_DELAY])
        self.assertEqual(night.RETRY_DELAY, 120)
        run = ScoringRun.objects.get()
        self.assertEqual((run.state, run.error), (ScoringRun.State.PARTIAL, ScoringRun.Error.ENGINE))
        self.assertEqual(run.offers_scored, 20)
        self.assertEqual(run.input_tokens, 200)  # the two failed calls; the fake usage of the good one is zero
        self.assertEqual(JobOffer.objects.filter(summary_written_at__isnull=True).count(), 5)
        self.assertEqual(report.summaries.failed_batches, 1)
        self.assertEqual(len(report.failures()), 1)

    def test_retry_that_succeeds(self):
        self.offers(3)
        error = llm.EngineError("échec")
        fake.set_response(summaries.TASK, Failing(summaries_answer, error, lambda call, attempt: attempt == 1))
        report = self.run_summaries()
        self.assertEqual(len(self.calls(summaries.TASK)), 2)
        self.assertEqual(self.sleeps, [120])
        self.assertEqual(ScoringRun.objects.get().state, ScoringRun.State.DONE)
        self.assertFalse(JobOffer.objects.filter(summary_written_at__isnull=True).exists())
        self.assertEqual(report.failures(), [])

    def test_every_batch_failed(self):
        self.offers(25)
        fake.set_response(summaries.TASK, llm.EngineError("échec"))
        self.run_summaries()
        run = ScoringRun.objects.get()
        self.assertEqual((run.state, run.error, run.offers_scored),
                         (ScoringRun.State.FAILED, ScoringRun.Error.ENGINE, 0))
        self.assertEqual(len(self.calls(summaries.TASK)), 4)

    def test_refused_never_retried(self):
        self.offers(25)
        fake.set_response(summaries.TASK, llm.EngineRefused("refus"))
        report = self.run_summaries()
        self.assertEqual(len(self.calls(summaries.TASK)), 2)
        self.assertEqual(self.sleeps, [])
        run = ScoringRun.objects.get()
        self.assertEqual((run.state, run.error), (ScoringRun.State.FAILED, ScoringRun.Error.REFUSED))
        self.assertTrue(report.summaries.engine_refused)
        self.assertEqual(len(report.failures()), 1)

    def test_nothing_to_summarize_makes_no_run(self):
        report = self.run_summaries()
        self.assertEqual(fake.calls, [])
        self.assertFalse(ScoringRun.objects.exists())
        self.assertEqual(report.summaries.requested, 0)

    def test_idempotent(self):
        self.offers(3)
        self.run_summaries()
        fake.calls.clear()
        self.run_summaries()
        self.assertEqual(fake.calls, [])
        self.assertEqual(ScoringRun.objects.count(), 1)


class PeoplePhaseTests(NightTestCase):
    def setUp(self):
        super().setUp()
        self.nora = make_profile("nora@example.fr")
        self.paul = make_profile("paul@example.fr")

    def run_people(self, **kwargs):
        return self.run_night(phases=["personnes"], **kwargs)

    def calls_for(self, profile):
        return [call for call in self.calls(scoring.TASK) if call.user == profile.user]

    def run_of(self, profile):
        return ScoringRun.objects.get(user=profile.user, kind=ScoringRun.Kind.NIGHT)

    def test_two_people_three_batches_each(self):
        self.offers(25)
        report = self.run_people()
        for profile in (self.nora, self.paul):
            with self.subTest(profile=profile.user.email):
                self.assertEqual(sorted(size(call) for call in self.calls_for(profile)), [5, 10, 10])
                self.assertEqual(fake.calls[0].timeouts, llm.BATCH)
                self.assertEqual(Score.objects.filter(user=profile.user).count(), 25)
                run = self.run_of(profile)
                self.assertEqual((run.state, run.offers_requested, run.offers_scored),
                                 (ScoringRun.State.DONE, 25, 25))
        self.assertEqual([person.label for person in report.people],
                         [f"compte {self.nora.user_id}", f"compte {self.paul.user_id}"])
        self.assertEqual([person.written for person in report.people], [25, 25])
        self.assertEqual(self.calls(summaries.TASK), [])
        self.assertEqual(report.failures(), [])

    @override_settings(NIGHT_SCORE_CAP=30)
    def test_site_budget_cuts_the_second_person_and_logs(self):
        self.offers(25)
        third = make_profile("zoe@example.fr")
        with self.assertLogs("matching.night", "ERROR") as logs:
            report = self.run_people()
        self.assertEqual(len(logs.records), 1)
        self.assertIn("45", logs.output[0])
        self.assertEqual(self.run_of(self.nora).offers_requested, 25)
        self.assertEqual(self.run_of(self.paul).offers_requested, 5)
        self.assertEqual(Score.objects.filter(user=self.paul.user).count(), 5)
        self.assertFalse(ScoringRun.objects.filter(user=third.user).exists())
        self.assertEqual((report.people_cut_offers, report.people_cut_persons), (45, 2))

    def past_run(self, hours, requested, kind=ScoringRun.Kind.NIGHT):
        run = ScoringRun.objects.create(user=self.paul.user, kind=kind, offers_requested=requested,
                                        state=ScoringRun.State.DONE)
        ScoringRun.objects.filter(pk=run.pk).update(started_at=self.now - timedelta(hours=hours))

    @override_settings(NIGHT_SCORE_CAP=60)
    def test_budget_counts_the_night_runs_of_the_last_20_hours(self):
        self.offers(25)
        self.past_run(19, 40)  # counted
        self.past_run(21, 1000)  # outside the sliding window
        self.past_run(1, 500, kind=ScoringRun.Kind.ON_DEMAND)  # not a night run
        with self.assertLogs("matching.night", "ERROR"):
            report = self.run_people()
        self.assertEqual(report.budget, 20)
        self.assertEqual(Score.objects.filter(user=self.nora.user).count(), 20)
        self.assertFalse(Score.objects.filter(user=self.paul.user).exists())

    @override_settings(NIGHT_SCORE_CAP=60)
    def test_run_again_18_hours_after_a_full_night(self):
        self.offers(5)
        self.past_run(18, 60)
        with self.assertLogs("matching.night", "ERROR"):
            report = self.run_people()
        self.assertEqual(report.budget, 0)
        self.assertEqual(fake.calls, [])
        self.assertFalse(Score.objects.exists())

    def test_unexpected_error_in_a_call_fails_that_person_only(self):
        self.offers(5)
        crash = Failing(scores_answer, RuntimeError("SECRET-OFFRE"),
                        lambda call, attempt: call.user.email == "paul@example.fr")
        fake.set_response(scoring.TASK, crash)
        with self.assertLogs("matching.night", "ERROR") as logs:
            report = self.run_people()
        self.assertEqual(len(self.calls_for(self.paul)), 1)  # never retried
        run = self.run_of(self.paul)
        self.assertEqual((run.state, run.error), (ScoringRun.State.FAILED, ScoringRun.Error.CRASH))
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(self.run_of(self.nora).state, ScoringRun.State.DONE)
        self.assertEqual(Score.objects.filter(user=self.nora.user).count(), 5)
        self.assertIn("RuntimeError", logs.output[0])
        self.assertIn(str(run.pk), logs.output[0])
        self.assertNotIn("SECRET-OFFRE", "\n".join(logs.output))
        self.assertEqual(report.failures(), [f"compte {self.paul.user_id} : 1 lot en échec (erreur imprévue)"])

    def test_unexpected_error_in_apply_fails_that_person_only(self):
        self.offers(15)
        apply_batch = scoring.apply_batch

        def crashing(prepared, output):
            if prepared.user == self.paul.user and len(prepared.offers) == 5:
                apply_batch(prepared, output)  # written, then rolled back
                raise KeyError("SECRET-OFFRE")
            return apply_batch(prepared, output)

        with (mock.patch.object(scoring, "apply_batch", side_effect=crashing),
              self.assertLogs("matching.night", "ERROR") as logs):
            report = self.run_people()
        run = self.run_of(self.paul)
        self.assertEqual((run.state, run.error, run.offers_scored),
                         (ScoringRun.State.PARTIAL, ScoringRun.Error.CRASH, 10))
        self.assertEqual(Score.objects.filter(user=self.paul.user).count(), 10)
        self.assertEqual(self.run_of(self.nora).state, ScoringRun.State.DONE)
        self.assertEqual(Score.objects.filter(user=self.nora.user).count(), 15)
        self.assertIn("KeyError", logs.output[0])
        self.assertNotIn("SECRET-OFFRE", "\n".join(logs.output))
        self.assertEqual(len(report.failures()), 1)

    def test_profiles_with_broken_criteria_skipped(self):
        self.offers(3)
        self.nora.hard_criteria = {"version": 1, "location": {"strict": True}}  # KeyError: 'remote' unnormalized
        self.nora.save()
        third = make_profile("zoe@example.fr", hard_criteria={"version": 1, "inconnu": True})
        with self.assertLogs("matching", "ERROR") as logs:
            report = self.run_night()
        self.assertEqual(self.calls(summaries.TASK)[0].user, llm.SHARED)
        self.assertEqual(JobOffer.objects.filter(summary=SUMMARY).count(), 3)
        self.assertEqual([call.user for call in self.calls(scoring.TASK)], [self.paul.user])
        self.assertFalse(ScoringRun.objects.filter(user__in=[self.nora.user, third.user]).exists())
        self.assertEqual(Score.objects.filter(user=self.paul.user).count(), 3)
        self.assertEqual(report.skipped, [night.Skipped(self.nora.user_id, night.CRITERIA),
                                          night.Skipped(third.user_id, night.CRITERIA)])
        text = "\n".join(logs.output)
        self.assertIn(str(self.nora.user_id), text)
        self.assertIn("ValueError", text)
        self.assertEqual(report.failures(), [f"compte {self.nora.user_id} : critères illisibles, personne sautée",
                                             f"compte {third.user_id} : critères illisibles, personne sautée"])
        self.assertFalse(ScoringRun.objects.filter(state=ScoringRun.State.RUNNING).exists())

    def test_preparation_error_skips_that_person_only(self):
        self.offers(3)
        zoe = make_profile("zoe@example.fr")
        make_profile("max@example.fr")
        prepare_batch = scoring.prepare_batch

        def crashing(user, offers, timeouts):
            if user == zoe.user:
                raise TypeError("SECRET-PROFIL")
            return prepare_batch(user, offers, timeouts)

        with (mock.patch.object(scoring, "prepare_batch", side_effect=crashing),
              self.assertLogs("matching.night", "ERROR") as logs):
            report = self.run_people()
        self.assertEqual(Score.objects.exclude(user=zoe.user).count(), 9)
        self.assertFalse(Score.objects.filter(user=zoe.user).exists())
        self.assertFalse(ScoringRun.objects.filter(user=zoe.user).exists())
        self.assertFalse(ScoringRun.objects.filter(state=ScoringRun.State.RUNNING).exists())
        self.assertEqual(ScoringRun.objects.filter(state=ScoringRun.State.DONE).count(), 3)
        self.assertEqual(report.skipped, [night.Skipped(zoe.user_id, night.CRASH)])
        self.assertEqual(report.failures(), [f"compte {zoe.user_id} : erreur imprévue, personne sautée"])
        self.assertIn("TypeError", logs.output[0])
        self.assertIn(str(zoe.user_id), logs.output[0])
        self.assertNotIn("SECRET-PROFIL", "\n".join(logs.output))

    def test_unexpected_preferences_type_still_scored(self):
        self.offers(3)
        self.nora.weighted_preferences = 5
        self.nora.save()
        report = self.run_people()
        self.assertEqual(Score.objects.filter(user=self.nora.user).count(), 3)
        self.assertEqual(report.failures(), [])

    def test_no_address_in_failures_nor_logs(self):
        self.offers(5)
        third = make_profile("zoe@example.fr", hard_criteria={"version": 1, "inconnu": True})

        def respond(call):
            if call.user.email == "paul@example.fr":
                raise llm.EngineRefused("refus")
            raise RuntimeError("panne")

        fake.set_response(scoring.TASK, respond)
        with self.assertLogs("matching", "DEBUG") as logs:
            report = self.run_people()
        text = "\n".join(logs.output + report.failures())
        self.assertEqual(len(report.failures()), 3)
        for profile in (self.nora, self.paul, third):
            self.assertNotIn(profile.user.email, text)
        self.assertNotIn("example.fr", text)

    def test_refused_person_fails_the_other_is_scored(self):
        self.offers(25)
        refused = Failing(scores_answer, llm.EngineRefused("refus"),
                          lambda call, attempt: call.user.email == "paul@example.fr")
        fake.set_response(scoring.TASK, refused)
        report = self.run_people()
        self.assertEqual(len(self.calls_for(self.paul)), 3)  # one per batch, never retried
        self.assertEqual(self.sleeps, [])
        run = self.run_of(self.paul)
        self.assertEqual((run.state, run.error, run.offers_scored),
                         (ScoringRun.State.FAILED, ScoringRun.Error.REFUSED, 0))
        self.assertEqual(self.run_of(self.nora).state, ScoringRun.State.DONE)
        self.assertEqual(Score.objects.filter(user=self.nora.user).count(), 25)
        self.assertEqual(report.failures(), [f"compte {self.paul.user_id} : refusé par le moteur"])

    def test_retry_that_succeeds(self):
        self.paul.delete()
        self.offers(5)
        error = llm.EngineError("échec", usage=USAGE)
        fake.set_response(scoring.TASK, Failing(scores_answer, error, lambda call, attempt: attempt == 1))
        report = self.run_people()
        self.assertEqual(len(self.calls(scoring.TASK)), 2)
        self.assertEqual(self.sleeps, [120])
        run = self.run_of(self.nora)
        self.assertEqual((run.state, run.offers_scored, run.input_tokens), (ScoringRun.State.DONE, 5, 100))
        self.assertEqual(report.failures(), [])

    def test_failed_batch_after_retry_partial(self):
        self.paul.delete()
        self.offers(15)
        error = llm.EngineError("échec")
        fake.set_response(scoring.TASK, Failing(scores_answer, error, lambda call, attempt: size(call) == 5))
        report = self.run_people()
        run = self.run_of(self.nora)
        self.assertEqual((run.state, run.error, run.offers_scored),
                         (ScoringRun.State.PARTIAL, ScoringRun.Error.ENGINE, 10))
        self.assertEqual(report.people[0].failed_batches, 1)
        self.assertEqual(len(report.failures()), 1)

    def test_idempotent(self):
        self.offers(12)
        self.run_night()
        fake.calls.clear()
        runs = ScoringRun.objects.count()
        report = self.run_night()
        self.assertEqual(fake.calls, [])
        self.assertEqual(ScoringRun.objects.count(), runs)
        self.assertEqual(report.people, [])

    def test_one_person_by_email(self):
        self.offers(3)
        report = self.run_people(email="Paul@example.fr")
        self.assertEqual([call.user for call in fake.calls], [self.paul.user])
        self.assertEqual([person.label for person in report.people], [f"compte {self.paul.user_id}"])
        self.assertIsNone(report.summaries)


class BothPhasesTests(NightTestCase):
    def test_summaries_before_scoring(self):
        make_profile("nora@example.fr")
        self.offers(3)
        self.run_night()
        self.assertEqual([call.task for call in fake.calls], [summaries.TASK, scoring.TASK])
        self.assertIn(SUMMARY, fake.calls[1].user_content)

    def test_profiles_limit_both_phases(self):
        make_profile("nora@example.fr")
        paul = make_profile("paul@example.fr", domains=["K"])
        stopped = make_profile("lea@example.fr", domains=["K"])
        stopped.user.is_active = False
        stopped.user.save()
        self.offers(2)
        self.offers(3, source=make_source(name="Carenews", domains=["K"]))
        report = self.run_night(profiles=[paul, stopped])
        self.assertEqual([size(call) for call in self.calls(summaries.TASK)], [3])
        self.assertEqual(report.summaries.requested, 3)
        self.assertEqual({call.user for call in self.calls(scoring.TASK)}, {paul.user})
        self.assertEqual([person.label for person in report.people], [f"compte {paul.user_id}"])
        self.assertEqual(JobOffer.objects.filter(summary_written_at__isnull=True).count(), 2)

    def test_writes_in_the_main_thread_calls_in_the_pool(self):
        make_profile("nora@example.fr")
        make_profile("paul@example.fr")
        self.offers(25)
        writes, calls = set(), set()

        def record_write(sender, **kwargs):
            writes.add(threading.current_thread())

        def recording(answer):
            def respond(call):
                calls.add(threading.current_thread())
                return answer(call)
            return respond

        fake.set_response(summaries.TASK, recording(summaries_answer))
        fake.set_response(scoring.TASK, recording(scores_answer))
        post_save.connect(record_write, sender=ScoringRun)
        self.addCleanup(post_save.disconnect, record_write, sender=ScoringRun)
        def tracked(function):
            def wrapper(*args, **kwargs):
                writes.add(threading.current_thread())
                return function(*args, **kwargs)
            return wrapper

        with (mock.patch.object(scoring, "apply_batch", side_effect=tracked(scoring.apply_batch)) as score_apply,
              mock.patch.object(summaries, "apply_batch",
                                side_effect=tracked(summaries.apply_batch)) as summary_apply):
            self.run_night()
        self.assertEqual(score_apply.call_count, 6)
        self.assertEqual(summary_apply.call_count, 2)
        self.assertEqual(writes, {threading.main_thread()})
        self.assertTrue(calls)
        self.assertNotIn(threading.main_thread(), calls)
        self.assertEqual(night.PARALLEL_BATCHES, 4)


class DryRunTests(NightTestCase):
    @override_settings(PREPASS_NIGHT_CAP=30, NIGHT_SCORE_CAP=30)
    def test_counts_without_call_nor_write(self):
        make_profile("nora@example.fr")
        make_profile("paul@example.fr")
        self.offers(45)
        with self.assertNoLogs("matching.night", "ERROR"):
            report = self.run_night(dry_run=True)
        self.assertEqual(fake.calls, [])
        self.assertFalse(ScoringRun.objects.exists())
        self.assertFalse(Score.objects.exists())
        self.assertFalse(JobOffer.objects.filter(summary_written_at__isnull=False).exists())
        self.assertEqual((report.summaries.requested, report.summaries.batches, report.summaries_cut), (30, 2, 15))
        self.assertEqual([(person.requested, person.batches) for person in report.people], [(30, 3)])
        self.assertEqual((report.people_cut_offers, report.people_cut_persons), (50, 2))
        self.assertTrue(report.dry_run)


class TrialGateTests(NightTestCase):
    """Outside the beta the night skips a finished trial in both phases; in the beta the date is not read (8.1)."""

    def setUp(self):
        super().setUp()
        self.running = make_profile("nora@example.fr")
        self.over = make_profile("paul@example.fr")
        User.objects.filter(pk=self.running.user_id).update(trial_ends_at=self.now + timedelta(days=1))
        User.objects.filter(pk=self.over.user_id).update(trial_ends_at=self.now - timedelta(seconds=1))
        self.offers(3)

    def scored_users(self):
        return {call.user for call in self.calls(scoring.TASK)}

    @override_settings(BETA=False)
    def test_the_finished_trial_is_not_scored(self):
        report = self.run_night(phases=["personnes"])
        self.assertEqual(self.scored_users(), {self.running.user})
        self.assertEqual([person.label for person in report.people], [f"compte {self.running.user_id}"])
        self.assertFalse(ScoringRun.objects.filter(user=self.over.user).exists())
        self.assertFalse(Score.objects.filter(user=self.over.user).exists())

    @override_settings(BETA=False)
    def test_the_night_judges_the_trial_at_its_own_time(self):
        report = night.run_night(self.now + timedelta(days=2), sleep=self.sleeps.append, phases=["personnes"])
        self.assertEqual(report.people, [])
        self.assertEqual(fake.calls, [])

    def give_the_finished_trial_its_own_domain(self):
        Profile.objects.filter(pk=self.over.pk).update(domains=["K"])
        self.offers(2, source=make_source(name="Carenews", domains=["K"]))

    @override_settings(BETA=False)
    def test_the_prepass_does_not_read_the_offers_only_the_finished_trial_wants(self):
        self.give_the_finished_trial_its_own_domain()
        self.run_night(phases=["resumes"])
        self.assertEqual([size(call) for call in self.calls(summaries.TASK)], [3])
        self.assertEqual(JobOffer.objects.filter(summary_written_at__isnull=True).count(), 2)

    @override_settings(BETA=True)
    def test_in_beta_the_prepass_reads_them(self):
        self.give_the_finished_trial_its_own_domain()
        self.run_night(phases=["resumes"])
        self.assertEqual([size(call) for call in self.calls(summaries.TASK)], [5])

    @override_settings(BETA=False)
    def test_a_dry_run_counts_only_the_running_trial(self):
        report = self.run_night(phases=["personnes"], dry_run=True)
        self.assertEqual([person.label for person in report.people], [f"compte {self.running.user_id}"])

    @override_settings(BETA=True)
    def test_in_beta_the_date_is_not_read(self):
        self.run_night(phases=["personnes"])
        self.assertEqual(self.scored_users(), {self.running.user, self.over.user})
