"""Daily at 18:00 (night job, step 10): the single reminder for questionnaires left the day before."""
from django.core.management.base import BaseCommand
from django.utils import timezone

from onboarding import reminder


class Command(BaseCommand):
    help = "Envoie la relance unique aux questionnaires commencés la veille et pas finis."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Liste les relances sans rien envoyer.")

    def handle(self, *args, dry_run=False, **options):
        profiles = reminder.due(timezone.now())
        # User ids only: addresses stay out of the night job's output.
        if dry_run:
            for profile in profiles:
                self.stdout.write(f"Relance à envoyer : utilisateur {profile.user.pk}, "
                                  f"{reminder.minutes_left(profile)} min restantes")
            self.stdout.write(f"{len(profiles)} relance(s) à envoyer, aucune envoyée (--dry-run).")
            return
        sent = sum(reminder.remind(profile) for profile in profiles)
        self.stdout.write(f"{sent} relance(s) envoyée(s) sur {len(profiles)}.")
