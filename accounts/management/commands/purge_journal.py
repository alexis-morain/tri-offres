from django.core.management.base import BaseCommand

from accounts import journal


class Command(BaseCommand):
    help = ("Efface du journal des comptes les lignes de plus de 31 jours, soit la durée des sauvegardes plus un jour "
            "(étape interne de la nuit).")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Compte sans rien effacer.")

    def handle(self, *args, dry_run=False, **options):
        if dry_run:
            self.stdout.write(f"{journal.purge(dry_run=True)} ligne(s) du journal des comptes à effacer (--dry-run).")
            return
        self.stdout.write(f"{journal.purge()} ligne(s) du journal des comptes effacée(s).")
