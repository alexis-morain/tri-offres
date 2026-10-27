"""The end of the questionnaire (the design plan 4.8): the first delivery date, the mail tips and the
welcome mail, sent once.
"""
import datetime as dt
import logging
from email.utils import parseaddr

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

import mail
from accounts import events
from accounts.models import OnboardingEvent, Profile
from matching import deck, on_demand
from matching.models import ScoringRun
from onboarding import schedule

logger = logging.getLogger(__name__)

GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}
OUTLOOK_NAMES = {"outlook", "hotmail", "live"}  # outlook.fr, hotmail.co.uk, live.be...
APPLE_DOMAINS = {"icloud.com", "me.com", "mac.com"}


def sender_address():
    """noreply@example.org, read from the sender the mails really use."""
    return parseaddr(settings.DEFAULT_FROM_EMAIL)[1]


def _tips():
    sender = sender_address()
    return {
        "gmail": ("Gmail", "Si le mail arrive dans l’onglet Promotions, glissez-le vers Principale."),
        "outlook": ("Outlook", f"Ajoutez {sender} à vos expéditeurs approuvés."),
        "apple": ("Apple Mail", f"Ajoutez {sender} à vos VIP."),
    }


def all_tips():
    """[(provider, tip)] for the three mailboxes, as the welcome mail lists them."""
    return list(_tips().values())


def mail_tips(email):
    """[(provider, tip)] for the person's mailbox: one tip for a known provider, all three otherwise."""
    tips = _tips()
    domain = email.rpartition("@")[2].lower()
    if domain in GMAIL_DOMAINS:
        return [tips["gmail"]]
    if domain == "msn.com" or ("." in domain and domain.split(".", 1)[0] in OUTLOOK_NAMES):
        return [tips["outlook"]]
    if domain in APPLE_DOMAINS:
        return [tips["apple"]]
    return list(tips.values())


def rendezvous(start, now=None):
    """The first mail for a person who finished at `start`: {"delivery": datetime at 07:00 Paris time, "day":
    "demain, mardi 14 octobre", "stamp": "MAR. 14 OCT. 2026", "is_tomorrow": bool, "passed": bool (it has left)}."""
    now = now or timezone.now()
    delivery = schedule.first_delivery(start)
    tomorrow = now.astimezone(schedule.PARIS).date() + dt.timedelta(days=1)
    return {"delivery": delivery, "day": schedule.describe_delivery(delivery, now),
            "stamp": schedule.stamp_text(delivery), "is_tomorrow": delivery.date() == tomorrow,
            "passed": now >= delivery}


def first_sort(user):
    """The first sort as the end screen shows it: {"state": "running"}, {"state": "ready", "count": n} (offers
    waiting, whatever the run: they are never hidden), {"state": "empty"} (a finished run found nothing), or None."""
    run = on_demand.latest_run(user)
    if run is not None and run.state == ScoringRun.State.RUNNING:
        return {"state": "running"}
    count = deck.deck_count(user, timezone.now())
    if count:
        return {"state": "ready", "count": count}
    if run is not None and run.state in (ScoringRun.State.DONE, ScoringRun.State.PARTIAL):
        return {"state": "empty"}
    return None


def complete(profile):
    """Record the end of the questionnaire the first time it is reached, then send the welcome mail if it has not
    gone out yet. Safe to call on every visit of the end screen."""
    now = timezone.now()
    with transaction.atomic():
        # A conditional update: two requests at once cannot both record the end.
        if Profile.objects.filter(pk=profile.pk, onboarding_completed_at=None).update(onboarding_completed_at=now,
                                                                                        updated_at=now):
            OnboardingEvent.objects.create(user=profile.user, name=events.ONBOARDING_COMPLETED)
    profile.refresh_from_db(fields=["onboarding_completed_at"])
    return send_welcome(profile)


def send_welcome(profile):
    """The welcome mail, at most once. True once it has gone out (now or on an earlier visit).

    The welcome_sent event is claimed first (one transaction, so a concurrent request sees it) and given back when
    the mail did not go out, whatever the reason, so the next visit (end screen or « Ma recherche ») tries again.
    The delivery date is counted from now: a mail sent late never announces a day already gone."""
    user = profile.user
    with transaction.atomic():
        if OnboardingEvent.objects.filter(user=user, name=events.WELCOME_SENT).exists():
            return True
        claim = OnboardingEvent.objects.create(user=user, name=events.WELCOME_SENT)
    now = timezone.now()
    context = {"day": rendezvous(now, now)["day"], "sender": sender_address(), "tips": all_tips(),
               "my_search_url": settings.SITE_URL + reverse("onboarding:my_search")}
    try:
        if mail.send("welcome", user.email, context) > 0:
            return True
        logger.error("Welcome mail of user %s not accepted by the backend.", user.pk)
    except Exception as error:  # noqa: BLE001 - any failure gives the claim back; the class only, never the text
        logger.error("Welcome mail of user %s not sent: %s", user.pk, type(error).__name__)
    claim.delete()
    return False
