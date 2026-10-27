"""Once a week (step 10 plans the Saturday night): rewrites « Ce que j’ai appris de vos choix » for each active person
with at least 5 new verdicts since the last write (the design plan). One call per person,
one after the other; a failure of one never stops the others."""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import User
from matching import learned, selection


def _count(number, one, many):
    return f"{number} {one if number <= 1 else many}"


class Command(BaseCommand):
    help = ("Réécrit pour chaque personne active qui a au moins 5 avis nouveaux la synthèse de ce qu’on a appris "
            "de ses choix.")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Compte les personnes concernées sans appeler Claude ni rien écrire.")
        parser.add_argument("--user", metavar="EMAIL", help="Traite cette personne seulement.")

    def handle(self, *args, dry_run=False, user=None, **options):
        now = timezone.now()
        profiles = selection.active_profiles(now)
        if user is not None:
            if not User.objects.filter(email__iexact=user).exists():
                raise CommandError("Aucun compte pour cette adresse.")
            profiles = profiles.filter(user__email__iexact=user)
        todo = [profile for profile in profiles if learned.due(profile, now)]
        if dry_run:
            self.stdout.write(f"{_count(len(todo), 'personne', 'personnes')} à traiter, rien n’est appelé.")
            return
        outcomes = [learned.refresh(profile, now) for profile in todo]
        self.stdout.write(
            f"{_count(outcomes.count(learned.WRITTEN), 'synthèse écrite', 'synthèses écrites')}, "
            f"{outcomes.count(learned.UNCHANGED)} sans changement, {outcomes.count(learned.FAILED)} en échec.")
