"""The sort page's deck and verdicts (the design plan, decisions 3 to 7; spec 6.1).

The deck is the person's own scores worth showing that they have not judged yet. give() and undo() read the person
they are given and no one else: an offer the person has no score on is the same refusal as an offer that does not
exist.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone

from matching import on_demand, scoring
from matching.models import Application, Feedback, Score

SHOW_MIN = scoring.MAYBE_MIN
WINDOW = on_demand.WINDOW
DECK_SIZE = 30
# Verdicts and cancellations per person in CAP_WINDOW: counted as the rows written in it (a row is cancelled at most
# once, so cancellations cannot outnumber them).
DAILY_CAP = 500
CAP_WINDOW = timedelta(hours=24)
_LINK = URLValidator(schemes=["http", "https"])


class CapReached(Exception):
    """The person already gave DAILY_CAP verdicts in CAP_WINDOW."""


def pending(user, now):
    """The person's scores to show, best first: from SHOW_MIN, a live offer of the last WINDOW, no verdict standing
    (a cancelled one puts the offer back)."""
    return (Score.objects.filter(Q(offer__expires_at__isnull=True) | Q(offer__expires_at__gt=now), user=user,
                                 score__gte=SHOW_MIN, offer__collected_at__gte=now - WINDOW)
            .exclude(offer_id__in=Feedback.objects.filter(user=user, cancelled=False).values("offer_id"))
            .order_by("-score", "-offer__collected_at", "-pk"))


def deck(user, now):
    """At most DECK_SIZE scores of `user`, with their offer and its source, in one query."""
    return list(pending(user, now).select_related("offer__source")[:DECK_SIZE])


def deck_count(user, now):
    """How many offers wait for `user`, whatever DECK_SIZE."""
    return pending(user, now).count()


def _check_cap(user):
    if Feedback.objects.filter(user=user, created_at__gte=timezone.now() - CAP_WINDOW).count() >= DAILY_CAP:
        raise CapReached


@transaction.atomic
def give(user, offer_id, verdict):
    """`user`'s verdict on the offer: a new row, none when it already is their current verdict. Raises ValueError for
    an unknown verdict, Score.DoesNotExist when the person has no score on the offer, CapReached past DAILY_CAP."""
    if verdict not in Feedback.Verdict.values:
        raise ValueError(verdict)
    Score.objects.get(user=user, offer_id=offer_id)
    if Feedback.objects.current_verdict(user, offer_id) == verdict:
        return
    _check_cap(user)
    Feedback.objects.create(user=user, offer_id=offer_id, verdict=verdict)


@transaction.atomic
def undo(user, offer_id):
    """Cancels `user`'s latest standing verdict on the offer and returns `offer_id`; None when there is none, so a
    replay never reaches another offer. Raises CapReached past DAILY_CAP."""
    row = (Feedback.objects.filter(user=user, offer_id=offer_id, cancelled=False)
           .order_by("-created_at", "-pk").first())
    if row is None:
        return None
    _check_cap(user)
    row.cancelled = True
    row.save(update_fields=["cancelled"])
    return offer_id


def judged(user):
    """`user`'s scores that carry a standing verdict, annotated with it (`verdict`, `given_at`) and with their
    application (`status`, `note`, None without one), latest verdict first: « Mes offres »."""
    latest = (Feedback.objects.filter(user=user, offer_id=OuterRef("offer_id"), cancelled=False)
              .order_by("-created_at", "-pk"))
    application = Application.objects.filter(user=user, offer_id=OuterRef("offer_id"))
    return (Score.objects.filter(user=user)
            .annotate(verdict=Subquery(latest.values("verdict")[:1]), given_at=Subquery(latest.values("created_at")[:1]),
                      status=Subquery(application.values("status")[:1]), note=Subquery(application.values("note")[:1]))
            .filter(verdict__isnull=False).select_related("offer__source").order_by("-given_at", "-pk"))


@transaction.atomic
def follow(user, offer_id, status, note):
    """`user`'s application status and note on the offer. Raises Score.DoesNotExist when the person has no score on
    it (the same refusal as give())."""
    Score.objects.get(user=user, offer_id=offer_id)
    Application.objects.update_or_create(user=user, offer_id=offer_id, defaults={"status": status, "note": note})


def last_given(user):
    """The offer of `user`'s latest standing verdict, None without one: what the page without JavaScript undoes."""
    return (Feedback.objects.filter(user=user, cancelled=False).order_by("-created_at", "-pk")
            .values_list("offer_id", flat=True).first())


def safe_link(url):
    """`url` when it is an http(s) address, "" otherwise: checked at display, not only at import."""
    try:
        _LINK(url)
    except ValidationError:
        return ""
    return url
