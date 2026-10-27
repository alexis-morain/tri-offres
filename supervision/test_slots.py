"""`slots.due`: which slots the scheduler must start at a given instant (the design plan)."""
import datetime as dt
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings

from onboarding.schedule import PARIS
from supervision import locks, slots
from supervision.models import TaskRun

MONDAY, SATURDAY, HOLIDAY = dt.date(2026, 10, 19), dt.date(2026, 10, 17), dt.date(2026, 11, 11)


def paris(day, hour, minute=0):
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=PARIS)


class DueTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        override = override_settings(TASK_LOCK_DIR=Path(directory.name))
        override.enable()
        self.addCleanup(override.disable)

    def record(self, slot, day, status):
        return TaskRun.objects.create(slot=slot, day=day, started_at=paris(day, 4), status=status)

    def test_nothing_before_the_hour(self):
        self.assertEqual(slots.due(paris(MONDAY, 3, 59)), [])

    def test_slot_is_due_on_the_hour(self):
        self.assertEqual(slots.due(paris(MONDAY, 4)), ["nuit"])
        self.assertEqual(slots.due(paris(MONDAY, 7)), ["matin"])
        self.assertEqual(slots.due(paris(MONDAY, 18)), ["soir"])

    def test_catch_up_until_the_end_of_the_window(self):
        self.assertEqual(slots.due(paris(MONDAY, 6, 30)), ["nuit"])
        self.assertEqual(slots.due(paris(MONDAY, 6, 31)), [])
        self.assertEqual(slots.due(paris(MONDAY, 12)), ["matin"])
        self.assertEqual(slots.due(paris(MONDAY, 12, 1)), [])
        self.assertEqual(slots.due(paris(MONDAY, 21)), ["soir"])
        self.assertEqual(slots.due(paris(MONDAY, 21, 1)), [])

    def test_morning_waits_for_a_business_day_but_the_others_do_not(self):
        self.assertEqual(slots.due(paris(SATURDAY, 7)), [])
        self.assertEqual(slots.due(paris(HOLIDAY, 7)), [])
        self.assertEqual(slots.due(paris(SATURDAY, 4)), ["nuit"])
        self.assertEqual(slots.due(paris(SATURDAY, 18)), ["soir"])

    def test_table_order_when_several_are_due(self):
        wide = {name: slots.Window(dt.time(4), dt.time(21), dt.timedelta(minutes=30), window.business_only)
                for name, window in slots.WINDOWS.items()}
        with mock.patch.object(slots, "WINDOWS", wide):
            self.assertEqual(slots.due(paris(MONDAY, 7)), ["nuit", "matin", "soir"])

    def test_finished_runs_close_the_slot_for_the_day(self):
        for status in ("ok", "failed", "interrupted"):
            with self.subTest(status=status):
                TaskRun.objects.all().delete()
                self.record("nuit", MONDAY, status)
                self.assertEqual(slots.due(paris(MONDAY, 5)), [])

    def test_a_running_line_does_not_close_the_slot(self):
        self.record("nuit", MONDAY, "running")
        self.assertEqual(slots.due(paris(MONDAY, 5)), ["nuit"])

    def test_another_day_or_slot_does_not_close_it(self):
        self.record("nuit", MONDAY - dt.timedelta(days=1), "ok")
        self.record("soir", MONDAY, "ok")
        self.assertEqual(slots.due(paris(MONDAY, 5)), ["nuit"])

    def test_held_lock_hides_the_slot(self):
        with locks.hold("nuit") as free:
            self.assertTrue(free)
            self.assertEqual(slots.due(paris(MONDAY, 5)), [])
        self.assertEqual(slots.due(paris(MONDAY, 5)), ["nuit"])

    def test_clocks_change_on_the_last_sunday_of_march(self):
        day = dt.date(2026, 3, 29)  # 02:00 jumps to 03:00
        self.assertEqual(slots.due(paris(day, 3, 59)), [])
        self.assertEqual(slots.due(paris(day, 4)), ["nuit"])

    def test_clocks_change_on_the_last_sunday_of_october(self):
        day = dt.date(2026, 10, 25)  # 03:00 goes back to 02:00
        self.assertEqual(slots.due(paris(day, 4)), ["nuit"])
        self.record("nuit", day, "ok")
        # Same wall time, one hour later in absolute time: still done, never started twice.
        self.assertEqual(slots.due(paris(day, 4).astimezone(dt.timezone.utc) + dt.timedelta(hours=1)), [])
        self.assertEqual(slots.due(paris(day, 5)), [])

    def test_aware_instant_in_any_zone_is_read_in_paris(self):
        utc = paris(MONDAY, 4).astimezone(dt.timezone.utc)
        self.assertEqual(slots.due(utc), ["nuit"])
