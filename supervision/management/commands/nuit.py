"""`manage.py nuit <slot>`: chains the steps of a slot, keeps the TaskRun journal (the design plan)."""
import datetime as dt
import time
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from onboarding import schedule
from supervision import alerts, locks, monitor, slots
from supervision.models import TaskRun
from supervision.redaction import redact_text

ERROR_MAX = 300


def error_line(exc):
    """"<Class>: <message>", first line only, masked (S3) and cut: never a trace."""
    lines = redact_text(exc).strip().splitlines()
    return f"{type(exc).__name__}: {lines[0] if lines else ''}"[:ERROR_MAX]


class Command(BaseCommand):
    help = "Enchaîne les étapes d’un créneau (nuit, matin ou soir) et garde le journal des passages."

    def add_arguments(self, parser):
        parser.add_argument("slot", choices=list(slots.SLOTS))
        parser.add_argument("--dry-run", action="store_true",
                            help="Passe --dry-run aux étapes qui l’acceptent, n’écrit aucun journal.")
        parser.add_argument("--force", action="store_true", help="Rejoue un créneau déjà fait ce jour.")
        parser.add_argument("--date", metavar="AAAA-MM-JJ", type=dt.date.fromisoformat,
                            help="Le jour du créneau (par défaut aujourd’hui, heure de Paris).")

    def handle(self, *args, slot, dry_run=False, force=False, date=None, **options):
        now = timezone.now()
        day = date or now.astimezone(schedule.PARIS).date()
        with locks.hold(slot) as free:
            if not free:
                raise CommandError(f"Créneau {slot} déjà en cours.")
            if not dry_run:
                # The lock was free: a run still marked running died without closing its line.
                TaskRun.objects.filter(slot=slot, status="running").update(status="interrupted",
                                                                           finished_at=timezone.now())
                if not force and TaskRun.objects.filter(slot=slot, day=day, status__in=["ok", "failed"]).exists():
                    self.stdout.write(f"Créneau {slot} déjà fait le {day:%d/%m/%Y} (--force pour le rejouer).")
                    return
            self._run(slot, day, dry_run, force, date)

    def _run(self, slot, day, dry_run, force, date=None):
        run, began = None, timezone.now()
        if not dry_run:
            run = TaskRun.objects.create(slot=slot, day=day, started_at=timezone.now(), forced=force)
            monitor.started(run)
        results, outputs = [], {}
        for step, off_day in slots.steps_for(slot, day):
            if off_day:
                result = {"name": step.name, "status": "skipped", "seconds": 0, "error": ""}
                self.stdout.write(f"== {step.name} ==\nignorée : {day:%d/%m/%Y} n’est pas un jour ouvré.")
            elif dry_run and step.dry_run == "skip":
                result = {"name": step.name, "status": "skipped", "seconds": 0, "error": ""}
                self.stdout.write(f"== {step.name} ==\nignorée (--dry-run).")
            else:
                result = self._call(step, dry_run, outputs, day if date and step.takes_date else None)
            results.append(result)
            if run is not None:
                run.steps = results
                run.save(update_fields=["steps"])
        failed = [r["name"] for r in results if r["status"] == "failed"]
        if run is not None:
            run.status = "failed" if failed else "ok"
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at"])
            monitor.finished(run, outputs)
        else:
            self._announce_alerts(slot, day, began, results, outputs)
        self.stdout.write("Résumé : " + ", ".join(f"{r['name']} {r['status']} ({r['seconds']} s)" for r in results))
        if failed:
            raise CommandError(f"Créneau {slot} en échec : {', '.join(failed)}.")

    def _announce_alerts(self, slot, day, began, results, outputs):
        """A dry run sends no mail: it says which reasons would have gone out (labels only, no output)."""
        pending = TaskRun(slot=slot, day=day, started_at=began, steps=results)
        labels = [problem["label"] for problem in alerts.problems_for(pending, outputs)]
        for label in labels:
            self.stdout.write(f"Alerte qui partirait : {label}.")
        if not labels:
            self.stdout.write("Aucune alerte.")

    def _call(self, step, dry_run, outputs, date=None):
        buffer = StringIO()
        args = (["--dry-run"] if dry_run else []) + ([f"--date={date.isoformat()}"] if date else [])
        started, error = time.monotonic(), ""
        try:
            call_command(step.name, *args, stdout=buffer, stderr=buffer)
        except Exception as exc:  # a failing step never stops the next ones
            error = error_line(exc)
        seconds = round(time.monotonic() - started, 1)
        outputs[step.name] = redact_text(buffer.getvalue())  # the console and the alerts only see masked text
        self.stdout.write(f"== {step.name} ==")
        if outputs[step.name]:
            self.stdout.write(outputs[step.name].rstrip("\n"))
        if error:
            self.stdout.write(f"Échec : {error}")
        return {"name": step.name, "status": "failed" if error else "ok", "seconds": seconds, "error": error}
