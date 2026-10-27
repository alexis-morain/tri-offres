import functools
import logging
import os

from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts import deletion, events, trial, unsubscribe
from accounts.models import OnboardingEvent, Profile, SummaryStatus, User
from accounts.views import private_headers
from jobs import rome_titles
from jobs.filters import counter_text
from matching import on_demand
from onboarding import criteria, cv, cv_extract, finish, flow, forms, preferences, rome, schedule, summary, uploads

logger = logging.getLogger(__name__)

# Session flag: slug of the block whose last screen was just validated. Consumed by the next page shown,
# whose card for that block slides into the file.
FILED_SESSION_KEY = "onboarding_filed_block"
# The green card of the file, shown with the three blocks once the questionnaire is over.
UNDERSTOOD = {"slug": "understood", "name": "Ce que j’ai compris", "tone": "green"}
# Session flag: « Ma recherche » says « Modifications enregistrées. » once, after an edit made from it.
SAVED_SESSION_KEY = "my_search_saved"
# Session flag: the end screen's stamp was printed; later visits show it without the animation.
STAMP_SESSION_KEY = "end_stamp_printed"

# The profile's JSON answers and the function that checks each against its schema.
SCHEMAS = (("domains", rome.normalize_domains), ("hard_criteria", criteria.normalize),
           ("weighted_preferences", preferences.normalize), ("cv_profile", cv.normalize))
CV = "cv"


def _profile(user):
    """The person's profile, with any JSON answer that does not fit its schema read as empty.

    Such a value (hand-edited, or from an older schema) must not break the questionnaire: the person resumes at the
    first screen whose answer is gone, and the next validated screen saves the repaired profile.
    """
    profile, _ = Profile.objects.get_or_create(user=user)
    profile.user = user
    damaged = False
    for field, normalize in SCHEMAS:
        try:
            # Read as its schema writes it: a hand-written ["K", "E", "E"] becomes ["E", "K"].
            setattr(profile, field, normalize(getattr(profile, field)))
        except ValueError:
            # The user id only: the answers themselves stay out of the logs.
            logger.warning("Profile of user %s: %s does not fit its schema, read as empty.", user.pk, field)
            setattr(profile, field, normalize(None))
            damaged = True
    if damaged:
        flow.rewind(profile)
    return profile


def _never_fails(call, user):
    """`call(user)`, an on-demand scoring request: the run goes to a thread, and no error of the scoring ever fails
    the page. Logged by its class and the account's key."""
    try:
        call(user)
    except Exception as error:  # noqa: BLE001 - the page matters more than the scores
        logger.error("On-demand scoring of user %s not requested: %s", user.pk, type(error).__name__)


def _summary_shown(user):
    """A summary was just written: the first run, or the bonus run when the search changed since that one (the
    edit that staled the summary left it to this rewriting)."""
    on_demand.request_run(user, after_change=on_demand.has_run(user))


def _first_sort(user):
    """The first on-demand run, unless one was already asked for (the summary showed, or failed and waits)."""
    if on_demand.latest_run(user) is None:
        on_demand.request_run(user)


def _counter(profile):
    return counter_text(criteria.normalize(profile.hard_criteria), timezone.now(), profile.domains)


def _cards(profile, current, filed):
    """The file: one card per block with a validated screen, or the block being answered."""
    current_block = current.block if current else None
    validated = flow.validated_steps(profile)
    cards = []
    for block in flow.BLOCKS:
        keys = set().union(*(step.form_class.card_keys() for step in validated if step.block == block))
        if current is not None and current.block == block:
            keys |= current.form_class.live_card_keys()
        if not keys and block != current_block:
            continue
        cards.append({
            "block": block,
            "rows": [(label, text) for key, label, text in block.rows(profile) if key in keys],
            "filed": block.slug == filed,
            "counter": _counter(profile) if block.has_counter else None,
        })
    return cards


def _page(request, profile, step=None, **context):
    """Context shared by the questionnaire pages: block progress, time left and the file."""
    current_block = step.block if step else None
    filed = request.session.pop(FILED_SESSION_KEY, None)
    # The search screen comes before the blocks: no file yet. Pages outside the screens show it once filled.
    show_file = current_block is not None or (step is None and flow.validated_steps(profile))
    cards = _cards(profile, step, filed) if show_file else []
    return {
        **context,
        "step": step,
        "progress": [{"block": block, "done": flow.block_done(block, profile), "current": block == current_block}
                     for block in flow.BLOCKS],
        # Once the questionnaire is over, a screen opened again to edit an answer has no time left to tell.
        "time_left": flow.time_left_text(step.slug, profile) if step and not flow.is_finished(profile) else None,
        "cards": cards,
    }


@login_required
@require_GET
def start(request):
    """Where sign-in lands: the resume screen, shown in place (its form posts to the screen's own URL)."""
    profile = _profile(request.user)
    current = flow.next_step(profile)
    if current is None:
        return redirect("onboarding:my_search" if flow.is_finished(profile) else "onboarding:end")
    if current.slug == CV:
        return _cv_page(request, profile, current)
    if current.slug == flow.SUMMARY:
        return _summary_page(request, profile)
    return _step_page(request, profile, current, current.form_class(profile=profile))


def _return_to(request):
    """(query string, URL) of the page this screen goes back to once saved (?retour=, allowlisted), or ("", None)."""
    value = request.GET.get("retour")
    back = flow.return_url(value)
    return (f"?retour={value}", back) if back else ("", None)


def _back_label(request):
    return flow.return_label(request.GET.get("retour"))


def _step_page(request, profile, current, form):
    title, why = flow.wording(current, profile)
    query, back = _return_to(request)
    context = _page(request, profile, current, form=form, previous=flow.previous_step(current.slug, profile),
                    title=title, why=why, live_counter=flow.has_counter(current), return_query=query, back=back,
                    back_label=_back_label(request))
    if isinstance(form, forms.CriteriaForm) and form.consequence_after:
        context["consequence"] = form.consequence(form.saved)
    return render(request, "onboarding/step.html", context)



@login_required
@require_http_methods(["GET", "POST"])
def step(request, slug):
    current = flow.get_step(slug)
    if current is None or current.url_name:  # a screen with its own URL is not served here
        raise Http404
    profile = _profile(request.user)
    if not flow.can_open(profile, slug):
        return redirect(flow.resume_url(profile))
    form = current.form_class(request.POST if request.method == "POST" else None, profile=profile)
    if request.method == "POST" and form.is_valid():
        before = summary.part_b(profile)
        with on_demand.watch(request.user, profile):
            form.save()
            summary.note_change(profile, before)
        return _advance(request, profile, current, back=_return_to(request)[1])
    return _step_page(request, profile, current, form)


def _advance(request, profile, current, back=None):
    """The screen is validated: record it and go to the next one, or `back` to the page it was opened from."""
    if flow.is_further(current.slug, profile):
        profile.onboarding_step = current.slug
    # An earlier screen may have dropped later answers (domain or offer kind switch): resume before them.
    flow.rewind(profile)
    profile.save(update_fields=["onboarding_step"])
    OnboardingEvent.objects.create(user=request.user, name=events.step(current.slug))
    if flow.ends_block(current):
        request.session[FILED_SESSION_KEY] = current.block.slug
    if back:
        if back == reverse("onboarding:my_search"):
            request.session[SAVED_SESSION_KEY] = True
        return redirect(back)
    following = flow.following_step(current.slug, profile)
    return redirect(flow.step_url(following) if following else flow.resume_url(profile))


@login_required
@require_POST
def counter(request):
    """The counter line for a screen being answered: its unsaved fields over the saved criteria.

    POST, so typed cities and pay floors never land in access logs; the script sends the CSRF token as a header.
    """
    current = flow.get_step(request.GET.get("step", ""))
    profile = _profile(request.user)
    # Only the screens that filter offers have something to count.
    if current is None or not flow.has_counter(current) or not flow.can_open(profile, current.slug):
        raise Http404
    form = current.form_class(request.POST, profile=profile)
    valid = form.is_valid()
    answers = criteria.normalize(form.apply(form.saved)) if valid else form.saved
    data = {"text": counter_text(answers, timezone.now(), profile.domains)}
    if form.consequence_after:
        data["consequence"] = form.consequence(answers) if valid else None
    return JsonResponse(data)


# Characters of a typed poste read for the suggestions: the start is what narrows the list.
QUERY_MAX = 60


@login_required
@require_GET
def job_suggestions(request):
    """The ROME job titles starting with what is typed in a poste field, for its suggestions."""
    return JsonResponse({"suggestions": rome_titles.suggest(request.GET.get("q", "")[:QUERY_MAX])})


@login_required
@require_GET
def end(request):
    """F1, the appointment: reached after « Ça me va ». The first visit records the end and sends the welcome mail."""
    profile = _profile(request.user)
    if flow.next_step(profile) is not None:
        return redirect(flow.resume_url(profile))
    welcome_sent = finish.complete(profile)
    meeting = finish.rendezvous(profile.onboarding_completed_at)
    # Printed once, and never for a date already gone.
    press = not request.session.get(STAMP_SESSION_KEY) and not meeting["passed"]
    request.session[STAMP_SESSION_KEY] = True
    context = _page(request, profile, day=meeting["day"], stamp=meeting["stamp"], passed=meeting["passed"],
                    is_tomorrow=meeting["is_tomorrow"], press=press, sender=finish.sender_address(),
                    tips=finish.mail_tips(request.user.email), welcome_sent=welcome_sent,
                    first_sort=finish.first_sort(request.user))
    # The signature ends with its object: the whole file beside the stamp, read only (wide screens only).
    context["cards"] = [{**card, "counter": None, "filed": False} for card in context["cards"]]
    context["cards"].append(_understood_card(profile))
    context["drawer_wide_only"] = True
    return render(request, "onboarding/end.html", context)


def _understood_card(profile):
    """The green card of the file: the summary as the person last read it, or the night's promise.

    The person's own text is theirs, never labelled as written by the AI."""
    card = {"block": UNDERSTOOD, "rows": [], "own_text": profile.summary_text, "paragraphs": [], "ai_label": False,
            "counter": None, "filed": False}
    if profile.summary_sentences and not profile.summary_text:
        card.update(paragraphs=[sentence["text"] for sentence in profile.summary_sentences], ai_label=True)
    elif not profile.summary_text:
        card["paragraphs"] = ["Je rédige cette synthèse cette nuit."]
    return card


# The CV screen (the design plan): upload, editable list, deletion.

def discard_uploads(view):
    """Close every uploaded file and delete any copy on disk once the view is done, whatever happened.

    With CappedMemoryUploadHandler, the site's only upload handler, nothing reaches the disk: this holds for any
    handler a configuration change might add (tested with one that writes to disk)."""
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        finally:
            _discard(request)
    return wrapper


def _discard(request):
    files = getattr(request, "_files", None)  # only once the body was parsed; never parse it here
    if not files:
        return
    for _, uploaded in files.lists():
        for upload in uploaded:
            path = upload.temporary_file_path() if hasattr(upload, "temporary_file_path") else None
            upload.close()
            if path:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass


def _cv_page(request, profile, current, upload_form=None, list_form=None, status=200):
    title, why = flow.wording(current, profile)
    has_cv = bool(profile.cv_profile)
    if list_form is None and has_cv:
        list_form = forms.CVListForm(cv_profile=profile.cv_profile)
    query, back = _return_to(request)
    context = _page(request, profile, current, title=title, why=why,
                    previous=flow.previous_step(current.slug, profile), upload_form=upload_form or forms.CVUploadForm(),
                    list_form=list_form, has_cv=has_cv, return_query=query, back=back, back_label=_back_label(request))
    return render(request, "onboarding/cv.html", context, status=status)


def _upload_bytes(request, form):
    """The uploaded file's bytes, or None with the form's error set. Its name is never read."""
    if uploads.too_large(request):
        raise cv.CVError("too_large")
    if not form.is_valid():
        return None
    upload = form.cleaned_data["cv"]
    if upload.size > cv_extract.MAX_INPUT:
        raise cv.CVError("too_large")
    data = upload.read(cv_extract.MAX_INPUT + 1)
    if len(data) > cv_extract.MAX_INPUT:
        raise cv.CVError("too_large")
    return data


@discard_uploads
@login_required
@require_http_methods(["GET", "POST"])
def cv_screen(request):
    """GET: the screen. POST: "Envoyer mon CV" (multipart), or "Passer cette étape" and "Continuer"."""
    current = flow.get_step(CV)
    profile = _profile(request.user)
    if not flow.can_open(profile, CV):
        return redirect(flow.resume_url(profile))
    if request.method == "GET":
        return _cv_page(request, profile, current)
    query, back = _return_to(request)
    if request.POST.get("action") == "continue":
        return _advance(request, profile, current, back=back)
    form = forms.CVUploadForm(request.POST, request.FILES)
    try:
        data = _upload_bytes(request, form)
        if data is not None:
            before = summary.part_b(profile)
            cv.store(profile, cv.analyse(request.user, data))
            summary.note_change(profile, before)
            return redirect(reverse("onboarding:cv") + query)
    except cv.CVError as error:
        form.refuse(error.code, has_list=bool(profile.cv_profile))
    return _cv_page(request, profile, current, upload_form=form)


@discard_uploads
@login_required
@require_POST
def cv_list(request):
    """"Enregistrer la liste": the list read from the CV, edited by hand."""
    current = flow.get_step(CV)
    profile = _profile(request.user)
    if not flow.can_open(profile, CV):
        return redirect(flow.resume_url(profile))
    form = forms.CVListForm(request.POST, cv_profile=profile.cv_profile)
    if form.is_valid():
        before = summary.part_b(profile)
        cv.save_list(profile, form.cv_profile())
        summary.note_change(profile, before)
        return redirect(reverse("onboarding:cv") + _return_to(request)[0])
    return _cv_page(request, profile, current, list_form=form)


@discard_uploads
@login_required
@require_POST
def cv_delete(request):
    """"Supprimer le résumé de mon CV". Open whatever the questionnaire's state: deleting is always allowed."""
    _clear_cv(_profile(request.user))
    return redirect(reverse("onboarding:cv") + _return_to(request)[0])


def _clear_cv(profile):
    """The CV summary deleted; a ready summary written from it goes stale."""
    before = summary.part_b(profile)
    cv.clear(profile)
    summary.note_change(profile, before)


# The summary screen, « Voici ce que j’ai compris » (onboarding/summary.py).

# Session flash after a failed rewrite: {"code": key of summary.MESSAGES, "correction": the text typed}.
SUMMARY_ERROR_SESSION_KEY = "summary_error"


def _edit_name(key):
    """What a "Modifier" link names for screen readers: the answer's label, "CV" kept in capitals."""
    label = summary.SOURCES[key]
    return label if label.isupper() else label[:1].lower() + label[1:]


def _answer_cards(profile, back=flow.SUMMARY):
    """The file spread open: one card per block, each row with its "Modifier" link to its screen, which goes `back`
    (a key of flow.RETURNS) once saved."""
    query = f"?retour={back}"
    rows = summary.answer_rows(profile)
    return [{"block": block,
             "rows": [{"label": label, "text": text, "missing": text == criteria.NOT_ANSWERED,
                       "url": flow.step_url(flow.edit_screen(key)) + query, "name": _edit_name(key)}
                      for key, label, text in rows if flow.card_block(key) == block]}
            for block in flow.BLOCKS]


def _summary_page(request, profile, correction_form=None, text_form=None, status=200):
    current = flow.get_step(flow.SUMMARY)
    state = profile.summary_status
    error = request.session.pop(SUMMARY_ERROR_SESSION_KEY, None)
    # Nothing written yet, or answers changed before « Ça me va »: write it now, unless that just failed (the previous
    # summary is shown with the error, never the waiting screen again). Once validated, a stale summary stays on
    # screen until the person asks for a new one (plan 4.9).
    if state == SummaryStatus.NONE or (state == SummaryStatus.STALE and not profile.summary_validated_at
                                       and error is None):
        return render(request, "onboarding/summary_wait.html",
                      _page(request, profile, current, lines=summary.reading_lines(profile),
                            replaces_own_text=bool(profile.summary_text)))
    if correction_form is None:
        initial = {"correction": error["correction"]} if error and error.get("correction") else None
        correction_form = forms.CorrectionForm(initial=initial, questions=profile.summary_questions)
    sentences = summary.sentences_with_sources(profile)
    # Once the questionnaire is over, the summary is opened from « Ma recherche »: its links lead back there.
    back = flow.MY_SEARCH if flow.is_finished(profile) else flow.SUMMARY
    context = _page(
        request, profile, current, title=current.title, why=current.why,
        previous=flow.previous_step(current.slug, profile),
        answer_cards=_answer_cards(profile, back=back), pending=state == SummaryStatus.PENDING,
        stale=state == SummaryStatus.STALE, own_text=profile.summary_text, sentences=sentences,
        error=summary.MESSAGES[error["code"]] if error else None, correction_form=correction_form,
        text_form=text_form or forms.SummaryTextForm(
            initial={"text": summary.final_text(profile)}),
        text_open=bool(text_form and text_form.errors))
    return render(request, "onboarding/summary.html", context, status=status)


def _summary_profile(request):
    """The person's profile when the summary can be opened, None otherwise."""
    profile = _profile(request.user)
    return profile if flow.can_open(profile, flow.SUMMARY) else None


@login_required
@require_GET
def summary_screen(request):
    profile = _summary_profile(request)
    if profile is None:
        return redirect(flow.resume_url(_profile(request.user)))
    return _summary_page(request, profile)


@login_required
@require_POST
def summary_write(request):
    """"Rédiger la synthèse" (sent by the waiting screen's script) and "Rédiger une nouvelle synthèse".
    A ready summary is never written again by this form: reloading it costs no call."""
    profile = _summary_profile(request)
    if profile is not None and profile.summary_status != SummaryStatus.READY:
        code = summary.generate(profile)
        _keep_error(request, profile, code)
        if code is None:  # the summary shows: its first sort starts (amendment of 12/10, section 4)
            _never_fails(_summary_shown, request.user)
    # From « Ma recherche », back to it; the summary screen otherwise.
    return redirect(_return_to(request)[1] or reverse("onboarding:summary"))


def _keep_error(request, profile, code, correction=""):
    """After a failed rewrite that kept the previous summary, flash why, with the correction typed."""
    if code and summary.has_summary(profile):
        request.session[SUMMARY_ERROR_SESSION_KEY] = {"code": code, "correction": correction}


@login_required
@require_POST
def summary_correct(request):
    """"Corriger": the correction and the replies to Claude's questions, rewritten by Claude."""
    profile = _summary_profile(request)
    if profile is None or not profile.summary_sentences:
        return redirect(reverse("onboarding:summary"))
    form = forms.CorrectionForm(request.POST, questions=profile.summary_questions)
    if not form.is_valid():
        return _summary_page(request, profile, correction_form=form)
    correction = form.cleaned_data["correction"]
    with on_demand.watch(request.user, profile):
        _keep_error(request, profile, summary.correct(profile, correction, form.answers()), correction)
    return redirect(reverse("onboarding:summary"))


@login_required
@require_POST
def summary_text(request):
    """"Enregistrer mon texte": the person's own version, without a call to Claude."""
    profile = _summary_profile(request)
    if profile is None or not profile.summary_sentences:
        return redirect(reverse("onboarding:summary"))
    form = forms.SummaryTextForm(request.POST)
    if not form.is_valid():
        return _summary_page(request, profile, text_form=form)
    with on_demand.watch(request.user, profile):
        summary.save_text(profile, form.cleaned_data["text"])
    return redirect(reverse("onboarding:summary"))


@login_required
@require_POST
def summary_validate(request):
    """"Ça me va": validates the screen, even while the summary waits for the night."""
    profile = _summary_profile(request)
    if profile is None or profile.summary_status == SummaryStatus.NONE:
        return redirect(reverse("onboarding:summary"))
    profile.summary_validated_at = timezone.now()
    profile.save(update_fields=["summary_validated_at", "updated_at"])
    _never_fails(_first_sort, request.user)
    back = reverse("onboarding:my_search") if flow.is_finished(profile) else None
    return _advance(request, profile, flow.get_step(flow.SUMMARY), back=back)


# After the questionnaire: « Ma recherche » (the file kept open) and « Mes données » (plan 4.9).

def _finished_profile(request):
    """The person's profile once the questionnaire is over, None otherwise (they resume it first)."""
    profile = _profile(request.user)
    return profile if flow.is_finished(profile) else None


def _summary_card(request, profile):
    """What the green card shows, as on the summary screen: the person's text, or Claude's sentences, or the night."""
    error = request.session.pop(SUMMARY_ERROR_SESSION_KEY, None)
    return {"pending": profile.summary_status == SummaryStatus.PENDING,
            "stale": profile.summary_status == SummaryStatus.STALE, "own_text": profile.summary_text,
            "sentences": summary.sentences_with_sources(profile),
            "error": summary.MESSAGES[error["code"]] if error else None}


@login_required
@require_GET
def my_search(request):
    saved = request.session.pop(SAVED_SESSION_KEY, False)
    profile = _finished_profile(request)
    if profile is None:
        return redirect(flow.resume_url(_profile(request.user)))
    finish.send_welcome(profile)  # a welcome mail that failed on the end screen is tried again here
    return render(request, "onboarding/my_search.html", {
        "section": "search", "answer_cards": _answer_cards(profile, back=flow.MY_SEARCH), "saved": saved,
        "no_poste": not preferences.postes(profile.weighted_preferences), **_summary_card(request, profile)})


def _block_sections(block, profile, data=None):
    sections = []
    for step in flow.block_steps(block, profile):
        title, why = flow.wording(step, profile)
        sections.append({"step": step, "title": title, "why": why,
                         "form": step.form_class(data, profile=profile, prefix=step.slug)})
    return sections


@login_required
@require_http_methods(["GET", "POST"])
def my_search_block(request, block):
    """Every screen of a block on one page, one « Enregistrer »: the same forms and checks as the questionnaire."""
    block = flow.get_block(block)
    if block is None:
        raise Http404
    profile = _finished_profile(request)
    if profile is None:
        return redirect(flow.resume_url(_profile(request.user)))
    sections = _block_sections(block, profile, request.POST if request.method == "POST" else None)
    if request.method == "POST" and all([section["form"].is_valid() for section in sections]):
        before = summary.part_b(profile)
        with on_demand.watch(request.user, profile):
            for section in sections:
                section["form"].store()
            flow.rewind(profile)  # as after any screen: resume before an answer that is gone
            profile.save()
            summary.note_change(profile, before)
        OnboardingEvent.objects.bulk_create(
            OnboardingEvent(user=request.user, name=events.step(section["step"].slug)) for section in sections)
        request.session[SAVED_SESSION_KEY] = True
        return redirect("onboarding:my_search")
    return render(request, "onboarding/my_search_block.html", {
        "section": "search", "page_block": block, "sections": sections,
        "has_errors": any(section["form"].errors for section in sections), "has_cv": bool(profile.cv_profile),
        "cv_processed_at": profile.cv_processed_at, "cv_query": f"?retour={flow.MY_SEARCH}"})


def _trial_row(user):
    """The « Essai » row of « Mes données »: nothing during the beta or without a date, otherwise the seventh day
    while the trial runs and the eighth once it is over."""
    if not trial.enabled() or user.trial_ends_at is None:
        return {"trial_over": False, "trial_until": None, "trial_ended": None}
    over = trial.is_over(user, timezone.now())
    return {"trial_over": over, "trial_until": None if over else schedule.long_date(trial.last_day(user)),
            "trial_ended": trial.end_label(user) if over else None}


@login_required
@require_GET
def my_data(request):
    """What tri-offres keeps about the person. Open whatever the questionnaire's state."""
    profile = _profile(request.user)
    return render(request, "onboarding/my_data.html", {
        "section": "data", "profile": profile, "cv_rows": cv.describe(profile.cv_profile),
        "team": request.user.is_staff or request.user.is_superuser, **_trial_row(request.user),
        **_summary_card(request, profile)})


@discard_uploads
@login_required
@require_POST
def my_data_cv_delete(request):
    """"Supprimer le résumé de mon CV" from « Mes données », back to it."""
    _clear_cv(_profile(request.user))
    return redirect("onboarding:my_data")


@private_headers
@login_required
@require_http_methods(["GET", "POST"])
def delete_account(request):
    """"Supprimer mon compte": the GET asks, only the POST erases (E12). No new code is asked: a stolen session
    already shows the whole profile. The team's accounts are refused, the admin deletes them."""
    if request.user.is_staff or request.user.is_superuser:
        return render(request, "onboarding/delete_account.html", {"section": "data", "team": True}, status=403)
    if request.method == "POST":
        deletion.delete_account(request.user)
        logout(request)
        response = redirect("accounts:deleted")
        response.status_code = 303
        return response
    return render(request, "onboarding/delete_account.html", {"section": "data"})


@login_required
@require_POST
def resubscribe(request):
    """"Recevoir de nouveau le mail du matin" (E11): only an unsubscribed account changes, and only to active."""
    unsubscribe.resume_mails(request.user)
    response = redirect("onboarding:my_data")
    response.status_code = 303
    return response
