"""The morning mail's offers (the design plan, decisions 9 to 11; spec 6.4): which scores go out,
and the cards the templates show."""
from django.conf import settings
from django.urls import reverse

from matching import deck
from matching.models import Score, ScoringRun

MAIL_MAX = 15


def pick(user, now):
    """`user`'s scores for the mail: {"to_review": [...], "maybe": [...], "side_track": score or None}. The
    sort page's own deck (deck.pending), not sent yet. The best exploration is the side track, alone;
    the MAIL_MAX best of the others (score, then newest offer) are split by category."""
    scores = list(deck.pending(user, now).filter(sent_on__isnull=True,
                                                 category__in=[Score.Category.TO_REVIEW, Score.Category.MAYBE])
                  .select_related("offer__source"))
    side_track = next((score for score in scores if score.is_exploration), None)
    kept = [score for score in scores if score is not side_track][:MAIL_MAX]
    return {"to_review": [score for score in kept if score.category == Score.Category.TO_REVIEW],
            "maybe": [score for score in kept if score.category == Score.Category.MAYBE],
            "side_track": side_track}


def read_tonight(user):
    """How many offers the person's latest night scored, 0 without a run."""
    return (ScoringRun.objects.filter(user=user, kind=ScoringRun.Kind.NIGHT).order_by("-started_at", "-pk")
            .values_list("offers_scored", flat=True).first() or 0)


def card(score):
    """What the templates show of a score. The link goes to the source's page, checked http(s), never apply_url."""
    offer = score.offer
    return {"title": offer.title, "employer": offer.employer, "location": offer.location, "contract": offer.contract,
            "score": score.score, "reason": score.reason, "source": offer.source.name,
            "attribution": offer.attribution, "link": deck.safe_link(offer.origin_url)}


def context(picked, read):
    """The morning templates' context for `picked` (pick()) and `read` (read_tonight())."""
    return {"to_review": [card(score) for score in picked["to_review"]],
            "maybe": [card(score) for score in picked["maybe"]],
            "side_track": picked["side_track"] and card(picked["side_track"]), "read_tonight": read,
            "sort_url": settings.SITE_URL + reverse("matching:sort"),
            "my_search_url": settings.SITE_URL + reverse("onboarding:my_search")}


def all_picked(picked):
    """The scores the mail carries, flat."""
    return [*picked["to_review"], *picked["maybe"], *([picked["side_track"]] if picked["side_track"] else [])]
