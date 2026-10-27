from datetime import datetime, timezone as dt_timezone

from django.core.management.base import BaseCommand, CommandError

from accounts import deletion, journal, openrouter, unsubscribe
from accounts.models import OpenRouterKey, User

INSTANT = "%Y-%m-%dT%H:%M"


class Command(BaseCommand):
    help = ("Après une restauration de la base : rejoue dans l’ordre les suppressions, désinscriptions, "
            "réabonnements et déconnexions d’OpenRouter du journal des comptes postérieurs à l’instant de la "
            "sauvegarde (docs/restauration.md). Aucun mail ne part. La sortie ne nomme les comptes que par leur clé.")

    def add_arguments(self, parser):
        parser.add_argument("--depuis", required=True, metavar="AAAA-MM-JJTHH:MM",
                            help="Instant de la sauvegarde restaurée, en UTC (les lignes de cet instant et des "
                                 "suivants sont rejouées ; viser un peu avant est sans risque).")
        parser.add_argument("--dry-run", action="store_true", help="Dit ce qui serait fait sans rien changer.")

    def handle(self, *args, depuis, dry_run=False, **options):
        try:
            since = datetime.strptime(depuis, INSTANT).replace(tzinfo=dt_timezone.utc)
        except ValueError:
            raise CommandError("--depuis attend un instant UTC au format AAAA-MM-JJTHH:MM.") from None
        # sorted() is stable: lines of the same second keep the order of the file.
        lines = sorted((entry for entry in journal.entries() if entry.at >= since), key=lambda entry: entry.at)
        accounts = {journal.fingerprint(email): pk for pk, email in User.objects.values_list("pk", "email")}
        replayed = 0
        for entry in lines:
            replayed += self.replay(entry, accounts, dry_run)
        suffix = " (--dry-run)" if dry_run else ""
        self.stdout.write(f"{len(lines)} ligne(s) lue(s), {replayed} ligne(s) rejouée(s){suffix}.")

    def replay(self, entry, accounts, dry_run):
        """Apply one line; True when it changed (or, in a dry run, would change) an account."""
        head = f"{entry.at.strftime(journal.STAMP)} {entry.kind}"
        user = User.objects.filter(pk=accounts.get(entry.fingerprint)).first()
        if user is None:
            self.stdout.write(f"{head} : aucun compte correspondant.")
            return False
        key = user.pk  # delete() clears the pk of the instance
        if user.is_staff or user.is_superuser:
            self.stdout.write(f"{head} : compte {key} de l’équipe, laissé tel quel.")
            return False
        if entry.kind == journal.DELETION:
            done, text, plan = True, "supprimé", "à supprimer"
        elif entry.kind == journal.UNSUBSCRIPTION:
            done, text, plan = user.status != User.Status.UNSUBSCRIBED, "désinscrit", "à désinscrire"
        elif entry.kind == journal.OPENROUTER_DISCONNECTION:
            # Only a key connected before the line: a reconnection after it survives.
            done = OpenRouterKey.objects.filter(user_id=user.pk, connected_at__lt=entry.at).exists()
            text, plan = "déconnecté d’OpenRouter", "à déconnecter d’OpenRouter"
        else:
            done, text, plan = user.status == User.Status.UNSUBSCRIBED, "réabonné", "à réabonner"
        if not done:
            self.stdout.write(f"{head} : compte {key} déjà dans cet état.")
            return False
        if dry_run:
            self.stdout.write(f"{head} : compte {key} {plan}.")
            return True
        if entry.kind == journal.DELETION:
            deletion.delete_account(user, replay=True)
        elif entry.kind == journal.UNSUBSCRIPTION:
            unsubscribe.stop_user_mails(user, replay=True)
        elif entry.kind == journal.OPENROUTER_DISCONNECTION:
            openrouter.disconnect(user, before=entry.at, replay=True)
        else:
            unsubscribe.resume_mails(user, replay=True)
        self.stdout.write(f"{head} : compte {key} {text}.")
        return True
