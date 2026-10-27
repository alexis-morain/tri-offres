"""`manage.py planificateur` (sub-step 10.1): the night command and the clock are doubles, except in the one test of
the process group, which runs real processes."""
import datetime as dt
import os
import signal
import subprocess
import sys
import tempfile
import time
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from onboarding.schedule import PARIS
from supervision import slots
from supervision.management.commands import planificateur
from supervision.models import TaskRun

MONDAY = dt.date(2026, 10, 19)
MODULE = "supervision.management.commands.planificateur"


def paris(hour, minute=0, second=0, day=MONDAY):
    return dt.datetime.combine(day, dt.time(hour, minute, second), tzinfo=PARIS)


class FakeProc:
    """Stands for the Popen of `nuit`: ends at once with `code` unless it `hangs` until the group is signalled.
    `writes` is the TaskRun the real command would have journalled."""

    def __init__(self, code=0, hangs=False, writes=None, pid=4242):
        self.code, self.hangs, self.writes, self.pid = code, hangs, writes, pid
        self.signalled = []
        self.waits = 0

    def wait(self, timeout=None):
        self.waits += 1
        if self.hangs and not self.signalled:
            raise subprocess.TimeoutExpired("nuit", timeout)
        return self.code


class PlanificateurCase(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        override = override_settings(TASK_LOCK_DIR=Path(directory.name))
        override.enable()
        self.addCleanup(override.disable)
        self.launched, self.proc = [], FakeProc()
        self.patch(f"{MODULE}.subprocess.Popen", side_effect=self.popen)
        self.patch(f"{MODULE}.pending_migrations", return_value=[])
        self.killpg = self.patch(f"{MODULE}.os.killpg", side_effect=self.group_signal)
        self.patch(f"{MODULE}.POLL", new=0.01)
        self.killed = self.patch("supervision.alerts.send_killed", return_value=True)
        self.not_started = self.patch("supervision.alerts.send_not_started", return_value=True)
        self.ping = self.patch("supervision.healthchecks.ping", return_value=True)

    def patch(self, target, **kwargs):
        patcher = mock.patch(target, **kwargs)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def popen(self, args, **kwargs):
        self.launched.append((args, kwargs))
        if self.proc.writes:
            TaskRun.objects.create(**self.proc.writes)
        return self.proc

    def group_signal(self, pid, sig):
        self.proc.signalled.append((pid, sig))

    def run_command(self, *options):
        out = StringIO()
        call_command("planificateur", *options, stdout=out)
        return out.getvalue()

    def tour(self, now, command=None):
        out = StringIO()
        command = command or planificateur.Command(stdout=out)
        command.tour(now)
        return command, out.getvalue()


class TourTests(PlanificateurCase):
    def test_starts_the_due_slot_in_its_own_session(self):
        _, out = self.tour(paris(4))
        (args, kwargs), = self.launched
        self.assertEqual(args, [sys.executable, "manage.py", "nuit", "nuit"])
        self.assertEqual(kwargs["cwd"], settings.BASE_DIR)
        self.assertIs(kwargs["start_new_session"], True)
        self.assertNotIn("env", kwargs)  # inherited: the container only has what it was given
        self.assertRegex(out, r"nuit du 19/10 : ok en \d+ s")

    def test_nothing_is_due_nothing_starts(self):
        self.tour(paris(3, 59))
        self.assertEqual(self.launched, [])

    def test_slots_run_one_after_the_other_in_table_order(self):
        wide = {name: slots.Window(dt.time(4), dt.time(21), dt.timedelta(minutes=30), window.business_only)
                for name, window in slots.WINDOWS.items()}
        with mock.patch.object(slots, "WINDOWS", wide):
            self.tour(paris(7))
        self.assertEqual([args[-1] for args, _ in self.launched], ["nuit", "matin", "soir"])

    def test_non_zero_exit_with_a_journalled_pass_does_nothing_more(self):
        self.proc = FakeProc(code=1, writes={"slot": "nuit", "day": MONDAY, "started_at": paris(4), "status": "failed"})
        _, out = self.tour(paris(4))
        self.assertIn("nuit du 19/10 : échec (code 1)", out)
        self.killed.assert_not_called()
        self.not_started.assert_not_called()
        self.ping.assert_not_called()

    def test_line_never_holds_an_address(self):
        _, out = self.tour(paris(4))
        self.assertNotIn("@", out)


class TimeoutTests(PlanificateurCase):
    def short(self):
        window = slots.Window(dt.time(4), dt.time(6, 30), dt.timedelta(seconds=0.05))
        return mock.patch.object(slots, "WINDOWS", {**slots.WINDOWS, "nuit": window})

    def test_overrun_kills_the_group_and_marks_the_pass_interrupted(self):
        self.proc = FakeProc(hangs=True, writes={"slot": "nuit", "day": MONDAY, "started_at": paris(4)})
        with self.short():
            _, out = self.tour(paris(4))
        self.assertEqual(self.proc.signalled[0], (4242, signal.SIGTERM))
        run = TaskRun.objects.get()
        self.assertEqual(run.status, "interrupted")
        self.assertIsNotNone(run.finished_at)
        self.killed.assert_called_once_with("nuit", MONDAY, "délai dépassé")
        self.ping.assert_called_once_with("nuit", "fail")
        self.assertIn("nuit du 19/10 : délai dépassé", out)

    def test_overrun_without_a_journalled_pass_still_alerts(self):
        self.proc = FakeProc(hangs=True)
        with self.short():
            self.tour(paris(4))
        self.assertFalse(TaskRun.objects.exists())
        self.killed.assert_called_once_with("nuit", MONDAY, "délai dépassé")

    def test_process_that_ignores_the_signal_is_killed_after_the_grace_period(self):
        stubborn = FakeProc(hangs=True)
        stubborn.wait = mock.Mock(side_effect=subprocess.TimeoutExpired("nuit", 1))
        self.proc = stubborn
        with self.short(), mock.patch(f"{MODULE}.GRACE", 0.01):
            self.tour(paris(4))
        self.assertEqual([call.args[1] for call in self.killpg.call_args_list], [signal.SIGTERM, signal.SIGKILL])

    def test_other_pass_lines_are_left_alone(self):
        old = TaskRun.objects.create(slot="nuit", day=MONDAY - dt.timedelta(days=1), started_at=paris(4),
                                     status="running")
        self.proc = FakeProc(hangs=True, writes={"slot": "nuit", "day": MONDAY, "started_at": paris(4)})
        with self.short():
            self.tour(paris(4))
        old.refresh_from_db()
        self.assertEqual(old.status, "running")


class AttemptTests(PlanificateurCase):
    def test_three_attempts_a_minute_apart_then_one_alert(self):
        command = None
        for second in (0, 30, 70, 100, 140, 400):
            command, _ = self.tour(paris(4, 0) + dt.timedelta(seconds=second), command)
        # 0 s and 70 s and 140 s start; 30 s, 100 s are too soon; 400 s is past the third attempt.
        self.assertEqual(len(self.launched), 3)
        self.not_started.assert_called_once_with("nuit", MONDAY)

    def test_attempts_are_counted_by_slot_and_day(self):
        command, _ = self.tour(paris(4))
        self.tour(paris(4, 5), command)
        self.tour(paris(4, 10), command)
        self.tour(paris(4, 15), command)
        self.assertEqual(len(self.launched), 3)
        next_day = MONDAY + dt.timedelta(days=1)
        self.tour(paris(4, day=next_day), command)
        self.assertEqual(len(self.launched), 4)

    def test_a_journalled_pass_is_not_an_attempt(self):
        self.proc = FakeProc(writes={"slot": "nuit", "day": MONDAY, "started_at": paris(4), "status": "ok"})
        command, _ = self.tour(paris(4))
        self.tour(paris(4, 10), command)
        self.assertEqual(len(self.launched), 1)
        self.not_started.assert_not_called()


class CommandLineTests(PlanificateurCase):
    def test_now_needs_once(self):
        with self.assertRaises(CommandError):
            self.run_command("--now", "2026-10-19T04:00")

    def test_now_is_a_dry_run_on_the_simulated_day(self):
        out = self.run_command("--once", "--now", "2026-10-19T04:00")
        (args, _), = self.launched
        self.assertEqual(args, [sys.executable, "manage.py", "nuit", "nuit", "--dry-run", "--date=2026-10-19"])
        self.assertIn("nuit du 19/10 : ok", out)

    def test_dry_run_does_not_count_a_missing_pass_as_an_attempt(self):
        command = planificateur.Command(stdout=StringIO())
        for minute in (0, 5, 10, 15):
            command.tour(paris(4, minute), dry_run=True)
        self.assertEqual(len(self.launched), 4)
        self.not_started.assert_not_called()

    def test_once_runs_a_single_tour(self):
        with mock.patch(f"{MODULE}.Command._now", return_value=paris(4)):
            self.run_command("--once")
        self.assertEqual(len(self.launched), 1)


class MigrationWaitTests(PlanificateurCase):
    def test_waits_until_no_migration_is_pending_and_says_so_once(self):
        pending = self.patch(f"{MODULE}.pending_migrations", side_effect=[["a"], ["a"], []])
        sleeps = self.patch(f"{MODULE}.Command._sleep")
        out = self.run_command("--once", "--now", "2026-10-19T04:00")
        self.assertEqual(pending.call_count, 3)
        self.assertEqual([call.args[0] for call in sleeps.call_args_list], [10, 10])
        self.assertEqual(out.count("migrations"), 1)
        self.assertEqual(len(self.launched), 1)

    def test_default_check_is_empty_on_a_migrated_database(self):
        mock.patch.stopall()
        self.assertEqual(planificateur.pending_migrations(), [])


class SignalTests(PlanificateurCase):
    def test_sigterm_during_a_slot_ends_the_process_and_leaves_the_pass_running(self):
        self.proc = FakeProc(hangs=True, writes={"slot": "nuit", "day": MONDAY, "started_at": paris(4)})
        raised = []
        original_wait = self.proc.wait

        def wait(timeout=None):
            if not raised:
                raised.append(True)
                signal.raise_signal(signal.SIGTERM)
            return original_wait(timeout)

        self.proc.wait = wait
        before = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
        with mock.patch(f"{MODULE}.Command._now", return_value=paris(4)):
            self.run_command()  # no --once: only the signal ends the loop
        self.assertEqual(self.proc.signalled, [(4242, signal.SIGTERM)])
        self.assertEqual(TaskRun.objects.get().status, "running")
        self.killed.assert_not_called()
        self.assertEqual((signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)), before)

    def test_sigint_while_waiting_ends_the_loop(self):
        def sleep(seconds):
            signal.raise_signal(signal.SIGINT)

        with mock.patch(f"{MODULE}.Command._sleep", side_effect=sleep), \
                mock.patch(f"{MODULE}.Command._now", return_value=paris(3)):
            self.run_command()
        self.assertEqual(self.launched, [])


class ProcessGroupTests(TestCase):
    """Real processes: the grandchild of `nuit` (the actor) dies with the group at the overrun (S25)."""

    def test_overrun_kills_the_grandchildren_too(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        pid_file = Path(directory.name) / "grandchild.pid"
        script = ("import subprocess, sys, time\n"
                  "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                  f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
                  "time.sleep(60)\n")
        window = slots.Window(dt.time(4), dt.time(6, 30), dt.timedelta(seconds=1))
        with override_settings(TASK_LOCK_DIR=Path(directory.name)), \
                mock.patch.object(slots, "WINDOWS", {**slots.WINDOWS, "nuit": window}), \
                mock.patch(f"{MODULE}.nuit_command", return_value=[sys.executable, "-c", script]), \
                mock.patch(f"{MODULE}.POLL", 0.05), \
                mock.patch("supervision.alerts.send_killed") as killed, \
                mock.patch("supervision.healthchecks.ping"):
            planificateur.Command(stdout=StringIO()).tour(paris(4))
        killed.assert_called_once()
        grandchild = int(pid_file.read_text())
        for _ in range(40):
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(grandchild, signal.SIGKILL)
            self.fail("The grandchild survived the end of its group.")
