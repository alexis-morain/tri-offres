"""`manage.py notation` (the design plan), with the fake engine: no network."""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

import llm
from accounts.testing import make_profile
from config.testing import assert_french_copy
from jobs import summaries
from jobs.models import JobOffer
from jobs.testing import make_offer, make_source
from llm import fake
from matching import night, scoring
from matching.models import Score, ScoringRun
from matching.testing import REASON, SUMMARY, scores_answer, summaries_answer


class NotationCommandTests(TestCase):
    def setUp(self):
        fake.reset()
        self.addCleanup(fake.reset)
        fake.set_response(summaries.TASK, summaries_answer)
        fake.set_response(scoring.TASK, scores_answer)
        retry_patch = mock.patch.object(night, "RETRY_DELAY", 0)
        retry_patch.start()
        self.addCleanup(retry_patch.stop)
        self.nora = make_profile("nora@example.fr")
        self.paul = make_profile("paul@example.fr")
        source = make_source(domains=["E"])
        for index in range(1, 13):
            offer = make_offer(source, external_id=str(index), title=f"Offre {index}", full_text="Texte fictif.")
            JobOffer.objects.filter(pk=offer.pk).update(collected_at=timezone.now() - timedelta(hours=2))

    def notation(self, *args):
        """Run the command; (output, error or None)."""
        out = StringIO()
        try:
            call_command("notation", *args, stdout=out)
        except CommandError as error:
            return out.getvalue(), error
        return out.getvalue(), None

    def test_dry_run_writes_nothing_and_calls_no_one(self):
        output, error = self.notation("--dry-run")
        self.assertIsNone(error)
        self.assertEqual(fake.calls, [])
        self.assertFalse(ScoringRun.objects.exists())
        self.assertFalse(Score.objects.exists())
        self.assertFalse(JobOffer.objects.filter(summary_written_at__isnull=False).exists())
        self.assertIn("essai", output)
        self.assertIn("12 offres à résumer", output)
        self.assertIn(f"compte {self.nora.user_id}", output)
        self.assertIn(f"compte {self.paul.user_id}", output)
        assert_french_copy(self, output)

    def orphans(self):
        """An on-demand run and a night run whose thread died 11 minutes ago, and one started 5 minutes ago."""
        now = timezone.now()
        runs = [ScoringRun.objects.create(user=self.nora.user, kind=kind, started_at=now - timedelta(minutes=age))
                for kind, age in ((ScoringRun.Kind.ON_DEMAND, 11), (ScoringRun.Kind.NIGHT, 30),
                                  (ScoringRun.Kind.ON_DEMAND, 5))]
        return runs

    def test_orphan_runs_failed_as_stale_and_counted(self):
        old_demand, old_night, recent = self.orphans()
        output, error = self.notation()
        self.assertIsNone(error)
        for run in (old_demand, old_night):
            run.refresh_from_db()
            self.assertEqual((run.state, run.error), (ScoringRun.State.FAILED, ScoringRun.Error.STALE))
        recent.refresh_from_db()
        self.assertEqual(recent.state, ScoringRun.State.RUNNING)
        self.assertIn("2 runs restés en cours depuis plus de 10 minutes, passés en échec.", output)
        assert_french_copy(self, output)

    def test_dry_run_leaves_orphan_runs_alone(self):
        runs = self.orphans()
        output, error = self.notation("--dry-run")
        self.assertIsNone(error)
        self.assertEqual(ScoringRun.objects.filter(state=ScoringRun.State.RUNNING).count(), len(runs))
        self.assertNotIn("passés en échec", output)

    def test_full_night(self):
        output, error = self.notation()
        self.assertIsNone(error)
        self.assertEqual(Score.objects.count(), 24)
        self.assertEqual(ScoringRun.objects.count(), 3)
        self.assertIn("Résumés", output)
        self.assertIn("12 résumés écrits", output)
        self.assertIn("Codes ROME proposés par Claude : 0 appellation exacte (code de fiche non lu) ; codes de "
                      "fiche : 12 vides, 0 inexistant, 0 refusé par le contrôle des mots, 0 accepté.", output)
        self.assertIn("12 notes écrites", output)
        self.assertNotIn("Texte fictif", output)
        self.assertNotIn(REASON, output)
        self.assertNotIn(SUMMARY, output)
        assert_french_copy(self, output)

    def test_one_user(self):
        output, error = self.notation("--user", "paul@example.fr")
        self.assertIsNone(error)
        self.assertEqual({call.task for call in fake.calls}, {scoring.TASK})
        self.assertEqual({call.user for call in fake.calls}, {self.paul.user})
        self.assertNotIn(f"compte {self.nora.user_id}", output)

    def test_unknown_user(self):
        _, error = self.notation("--user", "inconnu@example.fr")
        self.assertIsNotNone(error)
        self.assertEqual(fake.calls, [])

    def test_user_with_summaries_phase_refused(self):
        _, error = self.notation("--user", "paul@example.fr", "--phase", "resumes")
        self.assertIsNotNone(error)
        self.assertEqual(fake.calls, [])

    def test_phase(self):
        self.notation("--phase", "resumes")
        self.assertEqual({call.task for call in fake.calls}, {summaries.TASK})
        fake.calls.clear()
        self.notation("--phase", "personnes")
        self.assertEqual({call.task for call in fake.calls}, {scoring.TASK})

    def test_unknown_phase(self):
        with self.assertRaises(CommandError):
            call_command("notation", "--phase", "fiches", stdout=StringIO(), stderr=StringIO())

    def test_failure_raises_after_the_report(self):
        fake.set_response(scoring.TASK, llm.EngineRefused("refus"))
        output, error = self.notation("--phase", "personnes")
        self.assertIsNotNone(error)
        self.assertEqual(str(error), f"Notation incomplète (compte {self.nora.user_id} : refusé par le moteur ; "
                                     f"compte {self.paul.user_id} : refusé par le moteur)")
        self.assertIn(f"compte {self.nora.user_id}", output)  # the report is written first
        assert_french_copy(self, str(error))

    def test_failed_batch_raises(self):
        fake.set_response(summaries.TASK, llm.EngineError("échec"))
        output, error = self.notation("--phase", "resumes")
        self.assertIsNotNone(error)
        self.assertIn("lot en échec", output)
        self.assertEqual(str(error), "Notation incomplète (résumés : 1 lot en échec)")

    def test_unexpected_error_raises_after_the_report(self):
        fake.set_response(scoring.TASK, RuntimeError("panne"))
        with self.assertLogs("matching.night", "ERROR"):
            output, error = self.notation("--phase", "personnes")
        self.assertIsNotNone(error)
        self.assertIn("erreur imprévue", str(error))
        self.assertIn(f"compte {self.paul.user_id}", output)
        self.assertFalse(ScoringRun.objects.filter(state=ScoringRun.State.RUNNING).exists())

    def broken_night(self):
        """One damaged profile of each kind next to the two sound ones; a preparation error for the last one."""
        make_profile("lieu@example.fr", hard_criteria={"version": 1, "location": {"strict": True}})
        make_profile("inconnu@example.fr", hard_criteria={"version": 1, "inconnu": True})
        make_profile("prefs@example.fr", weighted_preferences=5)
        crash = make_profile("crash@example.fr")
        prepare_batch = scoring.prepare_batch

        def crashing(user, offers, timeouts):
            if user == crash.user:
                raise TypeError("panne")
            return prepare_batch(user, offers, timeouts)

        return mock.patch.object(scoring, "prepare_batch", side_effect=crashing)

    def test_damaged_profiles_people_phase(self):
        with self.broken_night(), self.assertLogs("matching", "ERROR"):
            output, error = self.notation("--phase", "personnes")
        self.assertIsNotNone(error)
        self.assertIn(f"compte {self.paul.user_id}", output)
        self.assertEqual(Score.objects.values("user").distinct().count(), 3)  # nora, paul, prefs
        self.assertFalse(ScoringRun.objects.filter(state=ScoringRun.State.RUNNING).exists())

    def test_damaged_profiles_full_night_leaves_no_run_running(self):
        with self.broken_night(), self.assertLogs("matching", "ERROR"):
            output, error = self.notation()
        self.assertIsNotNone(error)
        self.assertTrue(str(error).startswith("Notation incomplète ("))
        self.assertIn("1. Résumés", output)
        self.assertEqual(ScoringRun.objects.get(kind=ScoringRun.Kind.SUMMARIES).state, ScoringRun.State.DONE)
        self.assertEqual(Score.objects.values("user").distinct().count(), 3)
        self.assertFalse(ScoringRun.objects.filter(state=ScoringRun.State.RUNNING).exists())
        self.assertNotIn("example.fr", str(error))
