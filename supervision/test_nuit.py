"""`manage.py nuit` (sub-step 9.1): the commands it chains are replaced by doubles, nothing real runs."""
import fcntl
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import LoginCode
from accounts.testing import make_login_code, make_profile
from supervision import locks, slots
from supervision.models import TaskRun

MONDAY, SATURDAY, SUNDAY = "2026-10-19", "2026-10-17", "2026-10-18"
HOLIDAY = "2026-11-11"  # a Wednesday
SAT_HOLIDAY = "2026-08-15"  # a Saturday, Assumption


class Recorder:
    """Stands for call_command: records (name, args), fails for the names in `failing`."""

    def __init__(self, failing=(), say=None):
        self.calls, self.failing, self.say = [], dict(failing), say or {}

    def __call__(self, name, *args, **kwargs):
        self.calls.append((name, args))
        kwargs["stdout"].write(self.say.get(name, f"{name} fait\n"))
        if name in self.failing:
            raise self.failing[name]

    @property
    def names(self):
        return [name for name, _ in self.calls]


class NuitBase(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        override = override_settings(TASK_LOCK_DIR=Path(directory.name))
        override.enable()
        self.addCleanup(override.disable)

    def nuit(self, slot, *options, recorder=None, **kwargs):
        recorder = recorder or Recorder()
        out = StringIO()
        with mock.patch("supervision.management.commands.nuit.call_command", recorder):
            call_command("nuit", slot, *options, stdout=out, **kwargs)
        return recorder, out.getvalue()


class StepTableTests(NuitBase):
    def names(self, slot, date):
        recorder, _ = self.nuit(slot, "--date", date)
        return recorder.names

    def test_business_monday(self):
        self.assertEqual(self.names("nuit", MONDAY), ["collecte", "notation", "purge_offres", "purge_login_codes",
                                                      "purge_journal", "clearsessions", "purge_taskruns"])

    def test_saturday_adds_the_learned_summary_and_skips_collection(self):
        self.assertEqual(self.names("nuit", SATURDAY), ["purge_offres", "purge_login_codes", "purge_journal",
                                                        "clearsessions", "purge_taskruns", "synthese_apprise"])
        run = TaskRun.objects.get()
        states = {s["name"]: s["status"] for s in run.steps}
        self.assertEqual(states["collecte"], "skipped")
        self.assertEqual(states["notation"], "skipped")

    def test_sunday_has_no_summary_step_at_all(self):
        self.names("nuit", SUNDAY)
        self.assertNotIn("synthese_apprise", [s["name"] for s in TaskRun.objects.get().steps])

    def test_holiday_skips_collection(self):
        self.assertEqual(self.names("nuit", HOLIDAY)[0], "purge_offres")

    def test_saturday_holiday(self):
        names = self.names("nuit", SAT_HOLIDAY)
        self.assertNotIn("collecte", names)
        self.assertIn("synthese_apprise", names)

    def test_morning_sends_on_a_business_day_only(self):
        self.assertEqual(self.names("matin", MONDAY), ["envoi"])
        self.assertEqual(self.names("matin", SUNDAY), [])
        self.assertEqual(TaskRun.objects.get(day=SUNDAY).steps[0]["status"], "skipped")

    def test_evening_runs_every_day(self):
        self.assertEqual(self.names("soir", SUNDAY), ["relance_onboarding"])

    def test_every_step_is_a_command(self):
        from django.core.management import get_commands
        known = get_commands()
        for steps in slots.SLOTS.values():
            for step in steps:
                self.assertIn(step.name, known)


class JournalTests(NuitBase):
    def test_night_goes_on_when_the_actor_is_not_installed(self):
        real_call_command = call_command

        def call(name, *args, **kwargs):
            if name == "collecte":
                return real_call_command(name, *args, **kwargs)
            kwargs["stdout"].write(f"{name} fait\n")

        with override_settings(SCRAPER_DIR=Path("/nonexistent/actor")), \
                mock.patch("supervision.management.commands.nuit.call_command", call), \
                self.assertRaises(CommandError):
            call_command("nuit", "nuit", "--date", MONDAY, stdout=StringIO())
        run = TaskRun.objects.get()
        states = {step["name"]: step for step in run.steps}
        self.assertEqual(run.status, "failed")
        self.assertEqual(states["collecte"]["status"], "failed")
        self.assertIn("n’est pas installé", states["collecte"]["error"])
        self.assertEqual([name for name, step in states.items() if step["status"] == "ok"],
                         ["notation", "purge_offres", "purge_login_codes", "purge_journal", "clearsessions",
                          "purge_taskruns"])

    def test_failure_does_not_stop_the_next_steps(self):
        recorder = Recorder(failing={"notation": CommandError("Notation incomplète (1 en échec)")})
        with self.assertRaises(CommandError):
            self.nuit("nuit", "--date", MONDAY, recorder=recorder)
        self.assertEqual(recorder.names[-1], "purge_taskruns")
        run = TaskRun.objects.get()
        self.assertEqual(run.status, "failed")
        self.assertIsNotNone(run.finished_at)
        states = {s["name"]: s for s in run.steps}
        self.assertEqual(states["notation"]["status"], "failed")
        self.assertEqual(states["notation"]["error"], "CommandError: Notation incomplète (1 en échec)")
        self.assertEqual(states["purge_offres"]["status"], "ok")

    def test_success_is_recorded_with_steps_and_durations(self):
        _, out = self.nuit("soir", "--date", MONDAY)
        run = TaskRun.objects.get()
        self.assertEqual((run.slot, str(run.day), run.status, run.forced), ("soir", MONDAY, "ok", False))
        self.assertEqual(run.steps[0]["name"], "relance_onboarding")
        self.assertIsInstance(run.steps[0]["seconds"], float)
        self.assertIn("== relance_onboarding ==", out)
        self.assertIn("relance_onboarding fait", out)
        self.assertIn("Résumé", out.splitlines()[-1])

    def test_error_is_cut_to_one_short_line_without_trace(self):
        boom = ValueError("première ligne\nTraceback (most recent call last):\n  File x" + "y" * 500)
        with self.assertRaises(CommandError):
            self.nuit("soir", "--date", MONDAY, recorder=Recorder(failing={"relance_onboarding": boom}))
        error = TaskRun.objects.get().steps[0]["error"]
        self.assertEqual(error, "ValueError: première ligne")
        long = ValueError("mot " * 400)
        with self.assertRaises(CommandError):
            self.nuit("soir", "--date", SUNDAY, recorder=Recorder(failing={"relance_onboarding": long}))
        self.assertEqual(len(TaskRun.objects.get(day=SUNDAY).steps[0]["error"]), 300)

    def test_hooks_are_called_with_outputs(self):
        with mock.patch("supervision.management.commands.nuit.monitor") as monitor:
            self.nuit("soir", "--date", MONDAY)
        run = TaskRun.objects.get()
        monitor.started.assert_called_once_with(run)
        monitor.finished.assert_called_once_with(run, {"relance_onboarding": "relance_onboarding fait\n"})

    def test_no_address_in_the_output(self):
        make_profile("repere-secret@example.fr")
        make_login_code("repere-secret@example.fr", ago=timedelta(days=30).total_seconds())
        out = StringIO()
        for slot in ("soir", "matin", "nuit"):
            with mock.patch("supervision.management.commands.nuit.call_command",
                            self._real_without_actor):
                call_command("nuit", slot, "--dry-run", "--date", MONDAY, stdout=out)
        self.assertNotIn("repere-secret", out.getvalue())

    @staticmethod
    def _real_without_actor(name, *args, **kwargs):
        if name in ("collecte", "notation", "synthese_apprise"):
            return  # the actor and the engine never run in tests
        return call_command(name, *args, **kwargs)


class DryRunTests(NuitBase):
    def test_dry_run_passes_dry_run_and_writes_nothing(self):
        recorder, _ = self.nuit("nuit", "--dry-run", "--date", MONDAY)
        # collecte is skipped: its --dry-run still runs the actor, which marks the offers as seen
        self.assertEqual(recorder.names, ["notation", "purge_offres", "purge_login_codes", "purge_journal"])
        for _, args in recorder.calls:
            self.assertEqual(args, ("--dry-run",))
        self.assertFalse(TaskRun.objects.exists())

    def test_dry_run_of_other_slots(self):
        recorder, _ = self.nuit("matin", "--dry-run", "--date", MONDAY)
        self.assertEqual(recorder.calls, [("envoi", ("--dry-run", f"--date={MONDAY}"))])
        recorder, _ = self.nuit("soir", "--dry-run", "--date", MONDAY)
        self.assertEqual(recorder.calls, [("relance_onboarding", ("--dry-run",))])

    def test_date_reaches_envoi_only_when_given(self):
        recorder, _ = self.nuit("matin", "--date", MONDAY)
        self.assertEqual(recorder.calls, [("envoi", (f"--date={MONDAY}",))])
        recorder, _ = self.nuit("matin", "--force")
        self.assertIn(recorder.calls, ([("envoi", ())], []))

    def test_dry_run_deletes_no_login_code(self):
        make_login_code("a@example.fr", ago=timedelta(days=30).total_seconds())
        call_command("purge_login_codes", "--dry-run", stdout=StringIO())
        self.assertEqual(LoginCode.objects.count(), 1)

    def test_dry_run_runs_even_when_the_slot_is_done(self):
        self.nuit("soir", "--date", MONDAY)
        recorder, _ = self.nuit("soir", "--dry-run", "--date", MONDAY)
        self.assertEqual(recorder.names, ["relance_onboarding"])


class LockTests(NuitBase):
    def test_lock_held_elsewhere(self):
        with open(locks._path("soir"), "a") as other:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(locks.is_locked("soir"))
            self.assertFalse(locks.is_locked("matin"))
            recorder = Recorder()
            with self.assertRaisesMessage(CommandError, "Créneau soir déjà en cours."):
                self.nuit("soir", "--date", MONDAY, recorder=recorder)
        self.assertEqual(recorder.calls, [])
        self.assertFalse(TaskRun.objects.exists())
        self.assertFalse(locks.is_locked("soir"))

    def test_lock_is_released_after_a_run(self):
        self.nuit("soir", "--date", MONDAY)
        self.assertFalse(locks.is_locked("soir"))

    def test_lock_dir_is_created(self):
        with tempfile.TemporaryDirectory() as parent, override_settings(TASK_LOCK_DIR=Path(parent) / "locks"):
            self.assertFalse(locks.is_locked("nuit"))
            self.assertTrue((Path(parent) / "locks").is_dir())

    def test_orphan_running_line_is_closed_as_interrupted(self):
        orphan = TaskRun.objects.create(slot="soir", day=SATURDAY, started_at=timezone.now(), status="running")
        other = TaskRun.objects.create(slot="matin", day=SATURDAY, started_at=timezone.now(), status="running")
        self.nuit("soir", "--date", MONDAY)
        orphan.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(orphan.status, "interrupted")
        self.assertIsNotNone(orphan.finished_at)
        self.assertEqual(other.status, "running")


class AlreadyDoneTests(NuitBase):
    def test_second_run_the_same_day_does_nothing(self):
        self.nuit("soir", "--date", MONDAY)
        recorder, out = self.nuit("soir", "--date", MONDAY)
        self.assertEqual(recorder.calls, [])
        self.assertIn("déjà fait", out)
        self.assertEqual(TaskRun.objects.count(), 1)

    def test_a_failed_slot_counts_as_done(self):
        with self.assertRaises(CommandError):
            self.nuit("soir", "--date", MONDAY, recorder=Recorder(failing={"relance_onboarding": ValueError("x")}))
        recorder, _ = self.nuit("soir", "--date", MONDAY)
        self.assertEqual(recorder.calls, [])

    def test_an_interrupted_slot_is_not_done(self):
        TaskRun.objects.create(slot="soir", day=MONDAY, started_at=timezone.now(), status="interrupted")
        recorder, _ = self.nuit("soir", "--date", MONDAY)
        self.assertEqual(recorder.names, ["relance_onboarding"])

    def test_another_day_or_slot_is_not_done(self):
        self.nuit("soir", "--date", MONDAY)
        self.assertEqual(self.nuit("soir", "--date", SUNDAY)[0].names, ["relance_onboarding"])
        self.assertEqual(self.nuit("matin", "--date", MONDAY)[0].names, ["envoi"])

    def test_force_replays_and_marks_the_line(self):
        self.nuit("soir", "--date", MONDAY)
        recorder, _ = self.nuit("soir", "--date", MONDAY, "--force")
        self.assertEqual(recorder.names, ["relance_onboarding"])
        self.assertEqual(list(TaskRun.objects.order_by("id").values_list("forced", flat=True)), [False, True])


class PurgeTaskRunsTests(TestCase):
    def test_runs_older_than_90_days_are_deleted(self):
        now = timezone.now()
        old = TaskRun.objects.create(slot="soir", day=now.date(), started_at=now - timedelta(days=91), status="ok")
        recent = TaskRun.objects.create(slot="soir", day=now.date(), started_at=now - timedelta(days=89), status="ok")
        call_command("purge_taskruns", stdout=StringIO())
        self.assertEqual(list(TaskRun.objects.all()), [recent])
        self.assertFalse(TaskRun.objects.filter(pk=old.pk).exists())
