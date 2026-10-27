"""The sort page and its two POST (the design plan and its security requirements).

Every answer is `no-store`, the site's default referrer policy stays (offers link out from this page), and a JSON
answer never repeats what was sent: constant bodies only.
"""
import re
from collections import Counter
from functools import wraps

from django.conf import settings
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from accounts.models import Profile
from jobs.summaries import SUMMARY_MAX
from matching import deck
from matching.forms import FollowForm, LearnedForm
from matching.models import Application, Feedback, Score
from matching.scoring import REASON_MAX
from matching.text import clean_text
from onboarding import flow

OFFER_ID = re.compile(r"[0-9]{1,18}")  # ASCII digits only: int() alone takes « +5 », « 1_0 » and other scripts


def _wants_json(request):
    return "application/json" in request.headers.get("Accept", "")


def _failure(request, status):
    return JsonResponse({"ok": False}, status=status) if _wants_json(request) else HttpResponse(status=status)


def _done(request, **body):
    """Success: the JSON body, or a 303 back to the sort page, never to `next` nor the referrer."""
    if _wants_json(request):
        return JsonResponse({"ok": True, **body})
    response = redirect("matching:sort")
    response.status_code = 303
    return response


def sorter(view):
    """A signed-in person whose questionnaire is over; the others are redirected, or get a 401 for a script (a
    `fetch` would follow the redirect and take the verdict for received)."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = request.user
        if not (user.is_authenticated and user.is_active):
            return _failure(request, 401) if _wants_json(request) else redirect(settings.LOGIN_URL)
        profile = Profile.objects.filter(user_id=user.pk).first()
        if profile is None or not flow.is_finished(profile):
            if _wants_json(request):
                return _failure(request, 401)
            return redirect(reverse("onboarding:start") if profile is None else flow.resume_url(profile))
        return view(request, *args, **kwargs)
    return wrapped


def _card(score):
    """What the script gets of a score: an allowlist, never apply_url, the full text, the ROME code nor an id of
    another person. Claude's texts go through clean_text again."""
    offer = score.offer
    return {
        "id": offer.pk, "title": offer.title, "employer": offer.employer, "location": offer.location,
        "contract": offer.contract,
        "date": timezone.localtime(offer.published_at).date().isoformat() if offer.published_at else "",
        "score": score.score, "reason": clean_text(score.reason, REASON_MAX),
        "summary": clean_text(offer.summary, SUMMARY_MAX, sentences=True), "exploration": score.is_exploration,
        "source": offer.source.name, "attribution": offer.attribution, "url": deck.safe_link(offer.origin_url),
    }


@never_cache
@require_safe
@sorter
def sort(request):
    now = timezone.now()
    cards = [_card(score) for score in deck.deck(request.user, now)]
    profile = request.user.profile
    return render(request, "matching/sort.html", {
        "section": "sort", "card": cards[0] if cards else None, "cards": cards,
        "count": deck.deck_count(request.user, now),
        "undo_offer": deck.last_given(request.user),
        "learned": profile.learned_summary.strip(), "learned_own": profile.learned_summary_own,
        # The key of the script's queue in localStorage, tied to this account.
        "queue_key": f"tri.queue.{request.user.pk}",
    })


def _offer_id(request):
    value = request.POST.get("offer", "")
    return int(value) if OFFER_ID.fullmatch(value) else None


@never_cache
@require_POST
@sorter
def verdict(request):
    offer_id = _offer_id(request)
    if offer_id is None:
        return _failure(request, 400)
    try:
        deck.give(request.user, offer_id, request.POST.get("verdict", ""))
    except ValueError:
        return _failure(request, 400)
    except Score.DoesNotExist:  # 404, never 403: nothing is said about an offer the person has no score on
        return _failure(request, 404)
    except deck.CapReached:
        return _failure(request, 429)
    return _done(request)


@never_cache
@require_POST
@sorter
def undo(request):
    offer_id = _offer_id(request)
    if offer_id is None:
        return _failure(request, 400)
    try:
        undone = deck.undo(request.user, offer_id)
    except deck.CapReached:
        return _failure(request, 429)
    return _done(request, offer=undone)


def _write_learned(request, text, own):
    """The person's version of the learned summary, or nothing; no scoring is started (plan, decision 14)."""
    Profile.objects.filter(user_id=request.user.pk).update(learned_summary=text, learned_summary_own=own,
                                                          learned_summary_at=timezone.now())
    return _done(request)


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
@sorter
def learned(request):
    """« Corriger »: the person's own version of « Ce que j’ai appris de vos choix », kept as written."""
    profile = request.user.profile
    form = LearnedForm(request.POST or None, initial={"text": profile.learned_summary})
    if request.method == "POST" and form.is_valid():
        text = form.cleaned_data["text"].strip()
        return _write_learned(request, text, own=bool(text))
    return render(request, "matching/learned.html", {"section": "sort", "form": form})


@never_cache
@require_POST
@sorter
def learned_clear(request):
    return _write_learned(request, "", own=False)


HISTORY_PAGE = 30


def _tab(value):
    return value if value in Feedback.Verdict.values else Feedback.Verdict.YES


@never_cache
@require_safe
@sorter
def history(request):
    """« Mes offres »: the offers the person judged, one tab per verdict, with the follow-up of the yes."""
    tab = _tab(request.GET.get("avis", ""))
    judged = deck.judged(request.user)
    counts = Counter(judged.values_list("verdict", flat=True))
    page = Paginator(judged.filter(verdict=tab), HISTORY_PAGE).get_page(request.GET.get("page"))
    now = timezone.now()
    for score in page:
        score.url = deck.safe_link(score.offer.origin_url)
        score.expired = score.offer.expires_at is not None and score.offer.expires_at <= now
        score.status = score.status or Application.Status.TO_APPLY
    return render(request, "matching/history.html", {
        "section": "history", "tab": tab, "page": page,
        "tabs": [(value, label, counts[value]) for value, label in Feedback.Verdict.choices],
        "verdicts": Feedback.Verdict.choices, "statuses": Application.Status.choices,
        "note_max": Application.NOTE_MAX,
    })


@never_cache
@require_POST
@sorter
def follow(request, offer_id):
    """One offer of « Mes offres »: its verdict, and its status and note when the form sent them; back to the tab."""
    offer_id = int(offer_id)
    form = FollowForm(request.POST)
    if not form.is_valid():
        return HttpResponse(status=400)
    try:
        deck.give(request.user, offer_id, form.cleaned_data["verdict"])
        if "status" in request.POST:
            deck.follow(request.user, offer_id, form.cleaned_data["status"] or Application.Status.TO_APPLY,
                        form.cleaned_data["note"].strip())
    except Score.DoesNotExist:
        return HttpResponse(status=404)
    except deck.CapReached:
        return HttpResponse(status=429)
    response = redirect(f"{reverse('matching:history')}?avis={_tab(request.POST.get('avis', ''))}#offre-{offer_id}")
    response.status_code = 303
    return response
