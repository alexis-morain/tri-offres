from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from supervision.models import AlertSent, TaskRun

TASKRUN_DAYS = 90


class Command(BaseCommand):
    help = "Efface les passages de tâches et les alertes envoyées de plus de 90 jours (étape interne de la nuit)."

    def handle(self, *args, **options):
        limit = timezone.now() - timedelta(days=TASKRUN_DAYS)
        deleted, _ = TaskRun.objects.filter(started_at__lt=limit).delete()
        alerts, _ = AlertSent.objects.filter(sent_at__lt=limit).delete()
        self.stdout.write(f"{deleted} passage(s) de tâche effacé(s).")
        self.stdout.write(f"{alerts} alerte(s) effacée(s).")
