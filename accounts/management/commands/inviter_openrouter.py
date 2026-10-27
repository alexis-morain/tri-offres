"""One-off at the deployment of the OpenRouter key (plan 2026-10-27, decision 3.6): every active account without a key
gets one mail « connectez OpenRouter ». Safe to run again: the OPENROUTER_INVITED event is the trace, so nobody is
written to twice."""
import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import reverse

import mail
from accounts import events, openrouter, unsubscribe
from accounts.models import OnboardingEvent, User

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Envoie une fois à chaque compte actif sans clé OpenRouter le mail « connectez OpenRouter »."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Compte les mails sans rien envoyer ni écrire.")

    def handle(self, *args, dry_run=False, **options):
        users = (User.objects.reachable().filter(openrouter_key__isnull=True)
                 .exclude(onboarding_events__name=events.OPENROUTER_INVITED).order_by("pk"))
        if dry_run:
            self.stdout.write(f"{users.count()} mail(s) à envoyer, aucun envoyé (--dry-run).")
            return
        context = {"connect_url": settings.SITE_URL + reverse("accounts:openrouter_connect"),
                   "signup_cost": openrouter.SIGNUP_COST, "month_cost": openrouter.MONTH_COST_MAX,
                   "advised_credit": openrouter.ADVISED_CREDIT}
        sent = failed = 0
        for user in users:
            try:
                accepted = mail.send("openrouter_invite", user.email,
                                     {**context, "unsubscribe_url": unsubscribe.unsubscribe_url(user)},
                                     headers=unsubscribe.mail_headers(user)) > 0
            except Exception as error:  # noqa: BLE001 - the class only: the message may quote the address
                accepted = False
                logger.error("OpenRouter invitation of user %s not sent: %s", user.pk, type(error).__name__)
            if accepted:
                OnboardingEvent.objects.create(user=user, name=events.OPENROUTER_INVITED)
            sent += accepted
            failed += not accepted
        # User ids stay out of the output: counts only.
        self.stdout.write(f"{sent} mail(s) envoyé(s), {failed} en échec.")
