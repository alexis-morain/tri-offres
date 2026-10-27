"""`manage.py planificateur`: starts `manage.py nuit <slot>` when a slot is due (the design plan)."""
import datetime as dt
import os
import signal
import subprocess
import sys
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Max
from django.utils import timezone

from onboarding import schedule
from supervision import alerts, healthchecks, slots
from supervision.models import TaskRun

TICK = 30  # seconds between two looks at the table
MIGRATION_TICK = 10
POLL = 1.0  # how often a running pass is checked for the stop flag and its deadline
SLEEP_STEP = 0.5
GRACE = 20  # seconds between the signal to the group and the kill
MAX_ATTEMPTS = 3
RETRY_AFTER = dt.timedelta(seconds=60)


def pending_migrations():
    executor = MigrationExecutor(connection)
    return executor.migration_plan(executor.loader.graph.leaf_nodes())


def nuit_command(slot, dry_run=False, day=None):
    args = [sys.executable, "manage.py", "nuit", slot]
    return args + ["--dry-run", f"--date={day.isoformat()}"] if dry_run else args


def parse_now(value):
    try:
        return dt.datetime.fromisoformat(value).replace(tzinfo=schedule.PARIS)
    except ValueError:
        raise CommandError("--now attend AAAA-MM-JJTHH:MM (heure de Paris).") from None


def format_duration(seconds):
    seconds = round(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60:02d}"
    return f"{seconds // 60} min" if seconds >= 60 else f"{seconds} s"


class Command(BaseCommand):
    help = "Lance les créneaux (nuit, matin, soir) à leur heure, un sous-processus `nuit` à la fois."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stopping = False
        self.attempts, self.last_try = {}, {}  # by (slot, day), in memory

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Un seul tour, puis sortie.")
        parser.add_argument("--now", metavar="AAAA-MM-JJTHH:MM", type=parse_now,
                            help="Heure de Paris simulée, avec --once : essai à blanc (--dry-run pour nuit).")

    def handle(self, *args, once=False, now=None, **options):
        if now and not once:
            raise CommandError("--now demande --once.")
        previous = {number: signal.signal(number, self._stop) for number in (signal.SIGTERM, signal.SIGINT)}
        try:
            self._wait_for_migrations()
            if once:
                self.tour(now or self._now(), dry_run=bool(now))
            while not once and not self.stopping:
                self.tour(self._now())
                self._sleep(TICK)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)

    def _stop(self, number, frame):
        self.stopping = True

    def _now(self):
        return timezone.now()

    def _sleep(self, seconds):
        end = time.monotonic() + seconds
        while not self.stopping and end - time.monotonic() > 0:
            time.sleep(min(SLEEP_STEP, max(end - time.monotonic(), 0)))

    def _wait_for_migrations(self):
        said = False
        while not self.stopping and pending_migrations():
            if not said:
                self.stdout.write("En attente des migrations de la base.")
                said = True
            self._sleep(MIGRATION_TICK)

    def tour(self, now, dry_run=False):
        day = now.astimezone(schedule.PARIS).date()
        for slot in slots.due(now):
            if self.stopping:
                return
            key = (slot, day)
            if not dry_run:
                if self.attempts.get(key, 0) >= MAX_ATTEMPTS or now - self.last_try.get(key, now - 2 * RETRY_AFTER) \
                        < RETRY_AFTER:
                    continue
                self.last_try[key] = now
            self._launch(slot, day, dry_run)

    def _launch(self, slot, day, dry_run):
        last_id = TaskRun.objects.aggregate(last=Max("id"))["last"] or 0
        started = time.monotonic()
        process = subprocess.Popen(nuit_command(slot, dry_run, day), cwd=settings.BASE_DIR, start_new_session=True)
        outcome, code = self._wait(process, slots.WINDOWS[slot].max_duration.total_seconds())
        label = f"{slot} du {day:%d/%m}"
        elapsed = format_duration(time.monotonic() - started)
        written = TaskRun.objects.filter(id__gt=last_id, slot=slot, day=day)
        if outcome == "stopped":
            self._end_group(process)
            self.stdout.write(f"{label} : arrêté après {elapsed}.")
        elif outcome == "timeout":
            self._end_group(process)
            written.filter(status="running").update(status="interrupted", finished_at=timezone.now())
            alerts.send_killed(slot, day, "délai dépassé")
            healthchecks.ping(slot, "fail")
            self.stdout.write(f"{label} : délai dépassé, arrêté après {elapsed}.")
        else:
            self.stdout.write(f"{label} : {'ok' if code == 0 else f'échec (code {code})'} en {elapsed}")
            if not dry_run and not written.exists():
                self._count_attempt(slot, day)

    def _count_attempt(self, slot, day):
        key = (slot, day)
        self.attempts[key] = self.attempts.get(key, 0) + 1
        if self.attempts[key] == MAX_ATTEMPTS:
            alerts.send_not_started(slot, day)

    def _wait(self, process, limit):
        deadline = time.monotonic() + limit
        while True:
            if self.stopping:
                return "stopped", None
            try:
                return "done", process.wait(timeout=POLL)
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    return "timeout", None

    def _end_group(self, process):
        """The whole group of the pass (the actor included) gets SIGTERM, then SIGKILL if it is still there."""
        self._signal(process, signal.SIGTERM)
        try:
            process.wait(timeout=GRACE)
        except subprocess.TimeoutExpired:
            self._signal(process, signal.SIGKILL)
            try:
                process.wait(timeout=GRACE)
            except subprocess.TimeoutExpired:
                pass

    @staticmethod
    def _signal(process, number):
        try:
            os.killpg(process.pid, number)
        except ProcessLookupError:
            pass
