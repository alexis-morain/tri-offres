"""Each business morning at 07:00 Paris time (step 10), after the night: one mail per active person with their best
unsent offers, or an empty one (the design plan). At most one mail per person and day.

Only the mailable people are selected (User.objects.mailable), as of the real time, not `--date`."""
import datetime as dt
import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

import mail
from accounts import unsubscribe
from accounts.models import User
from matching import delivery
from matching.models import Delivery, Score
from matching.selection import active_profiles
from onboarding import schedule

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Envoie à chaque personne active son mail du matin, un jour ouvré, une fois par jour."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Compte les offres par section sans rien envoyer ni écrire.")
        parser.add_argument("--user", metavar="ADRESSE", help="Cette personne seulement.")
        parser.add_argument("--date", metavar="AAAA-MM-JJ", type=dt.date.fromisoformat,
                            help="Le jour de l’envoi (par défaut aujourd’hui, heure de Paris).")

    def handle(self, *args, dry_run=False, user=None, date=None, **options):
        now = timezone.now()
        day = date or now.astimezone(schedule.PARIS).date()
        if not schedule.is_business_day(day):
            self.stdout.write(f"{day:%d/%m/%Y} n’est pas ouvré : rien à envoyer.")
            return
        profiles = active_profiles()
        if user is not None:
            profiles = profiles.filter(user__email__iexact=user)
            if not profiles.exists():
                raise CommandError("Aucune personne active pour cette adresse.")
        sent = empty = failed = 0
        for profile in profiles:
            picked = delivery.pick(profile.user, now)
            if dry_run:
                if Delivery.objects.filter(user=profile.user, date=day).exists():
                    self.stdout.write(f"compte {profile.user_id} : déjà envoyé")
                    continue
                self.stdout.write(f"compte {profile.user_id} : {len(picked['to_review'])} à regarder, "
                                  f"{len(picked['maybe'])} peut-être, "
                                  f"{0 if picked['side_track'] is None else 1} piste à côté")
                empty += not delivery.all_picked(picked)
                sent += bool(delivery.all_picked(picked))
                continue
            outcome = self._send(profile.user, picked, day)
            sent += outcome == "sent"
            empty += outcome == "empty"
            failed += outcome == "failed"
        label = "à envoyer" if dry_run else "envoyés"
        line = f"{sent} mails {label}, {empty} vides, {failed} en échec"
        self.stdout.write(line)
        if failed:
            raise CommandError(f"Envoi incomplet ({line})")

    def _send(self, user, picked, day):
        """"sent", "empty", "failed" or "skipped" (already sent that day). The Delivery line is the reservation, as
        send_welcome's event is: given back when the mail did not go out."""
        with transaction.atomic():  # the check and the reservation in one lock: the account cannot go in between
            if not User.objects.filter(pk=user.pk).exists():
                logger.warning("Morning mail of account %s skipped: the account was deleted.", user.pk)
                return "skipped"
            reservation, created = Delivery.objects.get_or_create(user=user, date=day, defaults={"offer_count": 0})
        if not created:
            return "skipped"
        try:
            context = {**delivery.context(picked, delivery.read_tonight(user)),
                       "unsubscribe_url": unsubscribe.unsubscribe_url(user)}
            # Last look before the mail leaves (E10): the person may have unsubscribed or left since the reservation.
            if not User.objects.mailable().filter(pk=user.pk).exists():
                reservation.delete()
                logger.warning("Morning mail of account %s skipped: no longer mailable.", user.pk)
                return "skipped"
            accepted = mail.send("morning", user.email, context, headers=unsubscribe.mail_headers(user)) > 0
            if not accepted:
                logger.error("Morning mail of account %s not accepted by the backend.", user.pk)
        except Exception as error:  # noqa: BLE001 - the class only, never the text
            accepted = False
            logger.error("Morning mail of account %s not sent: %s", user.pk, type(error).__name__)
        if not accepted:
            reservation.delete()
            return "failed"
        # The mail is gone: whatever happens here, the reservation stays so that a rerun never sends a second one.
        picked_scores = None
        try:
            with transaction.atomic():
                picked_scores = delivery.all_picked(picked)
                reservation.offer_count = len(picked_scores)
                reservation.save(update_fields=["offer_count"])
                Score.objects.filter(pk__in=[score.pk for score in picked_scores]).update(sent_on=day)
        except Exception as error:  # noqa: BLE001 - the class only, never the text
            logger.error("Morning mail of account %s sent but not recorded: %s", user.pk, type(error).__name__)
        return "empty" if picked_scores == [] else "sent"
