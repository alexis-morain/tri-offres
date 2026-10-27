"""Each night (step 10), after `collecte`: the pre-pass writes the summaries of the new offers, then each active
person gets the scores of the offers of the last 48 hours (the design plan, decision 16). Both
phases are idempotent: run again, the command only does what is left."""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import User
from matching import night


class Command(BaseCommand):
    help = ("Résume les offres neuves, puis note pour chaque personne active les offres des dernières 48 heures, "
            "et écrit le rapport.")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Compte les offres, les lots et l’effet des plafonds sans appeler Claude ni rien "
                                 "écrire.")
        parser.add_argument("--user", metavar="EMAIL",
                            help="Note cette personne seulement (phase « personnes »).")
        parser.add_argument("--phase", choices=night.PHASES,
                            help="Une seule phase, à taper resumes ou personnes (par défaut les deux, dans cet "
                                 "ordre).")

    def handle(self, *args, dry_run=False, user=None, phase=None, **options):
        phases = night.PHASES if phase is None else (phase,)
        if user is not None:
            if phase == night.SUMMARIES:
                raise CommandError("--user ne vaut que pour la phase « personnes ».")
            if not User.objects.filter(email__iexact=user).exists():
                raise CommandError("Aucun compte pour cette adresse.")
            phases = (night.PEOPLE,)
        night_report = night.run_night(timezone.now(), phases=phases, email=user, dry_run=dry_run)
        self.stdout.write("\n".join(night.format_report(night_report)))
        failures = night_report.failures()
        if failures:
            # Accounts by their key, never their address: the message goes to the supervision (step 9).
            raise CommandError(f"Notation incomplète ({' ; '.join(failures)})")
