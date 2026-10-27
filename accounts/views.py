from functools import wraps

from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from accounts import auth, deletion, journal, trial, unsubscribe
from accounts.auth import SignIn
from accounts.forms import CodeForm, EmailForm
from accounts.management.commands import purge_login_codes
from accounts.models import Profile
from config.backups import BACKUP_DAYS
from jobs.models import FULL_TEXT_DAYS, OFFER_DAYS

RATE_LIMITED = ("Trop de demandes pour cette adresse. Réessayez dans quelques minutes, "
                "ou utilisez le dernier code reçu.")
CODE_ERRORS = {
    SignIn.EXPIRED: "Ce code a expiré. Demandez-en un nouveau.",
    SignIn.REFUSED: "Ce code a expiré. Demandez-en un nouveau.",  # inactive account: same neutral answer
    SignIn.TOO_MANY: "Trop d’essais. Demandez un nouveau code.",
    SignIn.SESSION_CAP: "Trop d’essais. Réessayez dans une heure, ou utilisez le lien reçu par mail.",
}
GMAIL = ("Ouvrir Gmail", "https://mail.google.com/")
OUTLOOK = ("Ouvrir Outlook", "https://outlook.live.com/mail/")


def after_login_url(user):
    """Where a signed-in person goes. Computed here only: no `next` parameter is ever followed.
    Once the questionnaire is over, the sort page; the questionnaire where it stopped otherwise."""
    from onboarding import flow  # the onboarding app builds on accounts: imported here, not at module level

    profile = Profile.objects.filter(user_id=user.pk).first()
    if profile is not None and flow.is_finished(profile):
        return reverse("matching:sort")
    return reverse("onboarding:start")


def webmail_shortcut(email):
    domain = email.rpartition("@")[2]
    if domain in {"gmail.com", "googlemail.com"}:
        return GMAIL
    if "." in domain and domain.split(".", 1)[0] in {"outlook", "hotmail", "live"}:
        return OUTLOOK
    return None


def private_headers(view):
    """Keep a response out of referrers, caches and search engines: the link page (its URL holds the token)
    and the CSRF failure page.

    strict-origin, not no-referrer: Referer carries the origin only, never the path holding the token, and the
    form's POST keeps a real Origin header. Under no-referrer Chromium sends Origin: null, which CSRF refuses
    (contract section 9.4).
    """
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["Referrer-Policy"] = "strict-origin"
        response["Cache-Control"] = "no-store"
        response["X-Robots-Tag"] = "noindex"
        return response
    return wrapped


def _remember_request(request, email, outcome):
    request.session[auth.SESSION_EMAIL] = email
    request.session[auth.SESSION_THROTTLED] = outcome.throttled


def _finish(request, outcome):
    """The response for a sign-in that went through, or None when the page should show an error."""
    if outcome.status is SignIn.SIGNED_IN:
        return redirect(after_login_url(outcome.user))
    if outcome.status is SignIn.WAITLISTED:
        return render(request, "accounts/beta_full.html")
    return None


def home(request):
    if request.user.is_authenticated:
        return redirect(after_login_url(request.user))
    if request.method == "POST":
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            _remember_request(request, email, auth.request_login(email, auth.client_ip(request)))
            return redirect("accounts:code")
    else:
        # The "wrong address" link on the code page prefills from the session, never from the URL.
        pending = request.session.get(auth.SESSION_EMAIL) if request.GET.get("modifier") else None
        form = EmailForm(initial={"email": pending} if pending else None)
    return render(request, "accounts/home.html", {
        "form": form, "capacity": settings.BETA_CAPACITY, "beta_full": auth.beta_is_full(),
    })


def code(request):
    if request.user.is_authenticated:
        return redirect(after_login_url(request.user))
    email = request.session.get(auth.SESSION_EMAIL)
    if not email:
        return redirect("accounts:home")
    throttled = False
    if request.method == "POST":
        form = CodeForm(request.POST)
        if form.is_valid():
            outcome = auth.sign_in_with_code(request, form.cleaned_data["code"])
            response = _finish(request, outcome)
            if response is not None:
                return response
            if outcome.status is SignIn.WRONG:
                plural = "s" if outcome.remaining > 1 else ""
                form.add_error("code", f"Ce code ne correspond pas. Il vous reste {outcome.remaining} essai{plural}.")
            else:
                form.add_error("code", CODE_ERRORS[outcome.status])
    else:
        form = CodeForm()
        throttled = request.session.pop(auth.SESSION_THROTTLED, False)
    return render(request, "accounts/code.html", {
        "form": form, "email": email, "throttled": throttled, "rate_limited": RATE_LIMITED,
        "beta_full": auth.beta_is_full(), "webmail": webmail_shortcut(email),
        "code_minutes": auth.CODE_TTL_MINUTES, "resend_seconds": auth.RESEND_DELAY_SECONDS,
        "sender": settings.DEFAULT_FROM_EMAIL,
    })


@require_POST
def resend(request):
    email = request.session.get(auth.SESSION_EMAIL)
    if request.user.is_authenticated or not email:
        return redirect("accounts:home")
    _remember_request(request, email, auth.request_login(email, auth.client_ip(request)))
    return redirect("accounts:code")


@private_headers
@require_http_methods(["GET", "POST"])
def link(request, token):
    if request.method == "POST":
        outcome = auth.sign_in_with_link(request, token)
        return _finish(request, outcome) or render(request, "accounts/link.html", {"valid": False})
    # A GET changes nothing: mail scanners open links, only the button's POST signs in.
    details = auth.link_details(token)
    if details is None:
        return render(request, "accounts/link.html", {"valid": False})
    email, code = details
    return render(request, "accounts/link.html", {"valid": True, "email": email, "code": code})


@require_POST
def sign_out(request):
    logout(request)
    return redirect("accounts:home")


@private_headers
def csrf_failure(request, reason=""):
    """CSRF_FAILURE_VIEW: a French page that says what to do. The reason is never shown."""
    return render(request, "accounts/csrf_failure.html", status=403)


@require_safe
def privacy(request):
    """The privacy page. Its durations are read from the constants that apply them, so it cannot drift."""
    return render(request, "accounts/privacy.html", {
        "full_text_days": FULL_TEXT_DAYS, "offer_days": OFFER_DAYS, "code_days": purge_login_codes.KEEP.days,
        "session_days": settings.SESSION_COOKIE_AGE // 86400, "backup_days": BACKUP_DAYS,
        "journal_days": journal.KEEP.days, "failure_days": purge_login_codes.KEEP.days,
        "kept_hours": int(deletion.CODE_KEEP.total_seconds() // 3600), "legal_email": settings.LEGAL_EMAIL,
        "trial": trial.enabled(), "trial_days": trial.TRIAL_DAYS})


@require_safe
def legal_notice(request):
    return render(request, "accounts/legal.html", {
        "name": settings.LEGAL_NAME, "address": settings.LEGAL_ADDRESS, "siret": settings.LEGAL_SIRET,
        "email": settings.LEGAL_EMAIL})


@private_headers
@require_safe
def account_deleted(request):
    """The page after a deletion: public and the same for everyone, so it holds neither the address nor anything
    read from the session."""
    return render(request, "accounts/account_deleted.html")


@private_headers
@require_http_methods(["GET", "POST"])
def unsubscribe_page(request, token):
    """The page a mail's footer links to. The GET changes nothing (mail scanners open links) and shows the same page
    for every token, signature unread; only the button's POST acts. The answer is the same for a good token, a
    forged one, a deleted account and an account already unsubscribed (E3)."""
    if request.method == "POST":
        unsubscribe.stop_mails(token)
        return render(request, "accounts/unsubscribed.html")
    return render(request, "accounts/unsubscribe.html")


# The only exempt view (contract 9.4, amendment of 20/10/2026): a mail client's POST carries no CSRF token.
@csrf_exempt
@private_headers
@require_POST
def unsubscribe_one_click(request, token):
    """RFC 8058: the mail client POSTs `List-Unsubscribe=One-Click` itself, with no page. Any other body is a 400."""
    form = request.content_type == "application/x-www-form-urlencoded"
    if not form or request.POST.get("List-Unsubscribe") != "One-Click":
        return HttpResponse(status=400)
    unsubscribe.stop_mails(token)
    return HttpResponse()
