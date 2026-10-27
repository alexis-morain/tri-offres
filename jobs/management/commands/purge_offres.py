from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import FULL_TEXT_DAYS, OFFER_DAYS, JobOffer


class Command(BaseCommand):
    """Last job of the night: collection (`collecte`), then scoring (step 5), then this purge, so a fresh offer
    is scored before its text can go. Scheduled at step 10."""

    help = (f"Efface le texte complet des offres collectées il y a plus de {FULL_TEXT_DAYS} jours et supprime les "
            f"offres de plus de {OFFER_DAYS} jours ou expirées, sauf celles qui portent un avis.")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Compte sans rien écrire.")

    def handle(self, *args, dry_run=False, **options):
        result = JobOffer.objects.purge(timezone.now(), dry_run=dry_run)
        # "gardées pour leur avis" counts every stale offer kept, not only tonight's.
        label = "purge (--dry-run, rien d’écrit)" if dry_run else "purge"
        self.stdout.write(f"{label} : {result.texts} textes effacés, {result.deleted} offres supprimées "
                          f"({result.kept} gardées pour leur avis)")
