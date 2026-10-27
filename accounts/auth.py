"""Passwordless sign-in by a six-digit code or a one-time link.

Contract: the design plan, sections 1 to 8 as amended by section 9 (section 9 wins).
Codes and tokens are never stored in clear; every decision runs inside one IMMEDIATE transaction on SQLite.
"""
import hmac
import ipaddress
import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from django.conf import settings
from django.contrib.auth import login
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac

import mail
from accounts import events
from accounts.models import LoginCode, OnboardingEvent, Profile, User, WaitlistEntry

logger = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=15)
CODE_TTL_MINUTES = int(CODE_TTL / timedelta(minutes=1))
MAX_ATTEMPTS = 5
RESEND_DELAY = timedelta(seconds=60)
RESEND_DELAY_SECONDS = int(RESEND_DELAY.total_seconds())
ADDRESS_SHORT_WINDOW = timedelta(minutes=10)
ADDRESS_SHORT_LIMIT = 3
ADDRESS_DAY_WINDOW = timedelta(hours=24)
ADDRESS_DAY_LIMIT = 10
IP_WINDOW = timedelta(hours=1)
IP_LIMIT = 10
SITE_MAIL_WINDOW = timedelta(hours=24)
SESSION_FAILURE_WINDOW = timedelta(hours=1)
SESSION_FAILURE_LIMIT = 20

CODE_SALT = "tri-offres.login.code"
TOKEN_SALT = "tri-offres.login.token"
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")  # secrets.token_urlsafe(32)
BACKEND = "accounts.backends.ThrottledModelBackend"

# Every session key the sign-in uses.
SESSION_EMAIL = "login_email"  # the address the code is checked against
SESSION_FAILURES = "login_failures"  # timestamps of wrong codes, for the per-session cap
SESSION_THROTTLED = "login_throttled"  # show the rate-limit notice once on the code page


# Secrets

def new_token():
    return secrets.token_urlsafe(32)


def _hmac(salt, value):
    # salted_hmac derives its key from SECRET_KEY only (never the fallbacks): a key rotation voids codes in flight.
    return salted_hmac(salt, value, algorithm="sha256")


def code_for_token(token):
    """The six-digit code is derived from the token, so the link page can show it without storing it."""
    return f"{int.from_bytes(_hmac(CODE_SALT, token).digest()[:8], 'big') % 1_000_000:06d}"


def hash_code(email, code):
    # The address is in the message, so one table of the 10^6 codes cannot serve for every row.
    return _hmac(CODE_SALT, f"{email}:{code}").hexdigest()


def hash_token(token):
    return _hmac(TOKEN_SALT, token).hexdigest()


def group_code(code):
    return f"{code[:3]} {code[3:]}"


# Client IP

def _parse_ip(value):
    value = (value or "").strip()
    if not value or "%" in value or len(value) > 45:
        return None
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def client_ip(request):
    """The caller's IP. CF-Connecting-IP only when TRUST_CF_CONNECTING_IP is on and the value is an IP."""
    ip = None
    if settings.TRUST_CF_CONNECTING_IP:
        ip = _parse_ip(request.META.get("HTTP_CF_CONNECTING_IP"))
    if ip is None:
        ip = _parse_ip(request.META.get("REMOTE_ADDR"))
    return str(ip) if ip is not None else None


def ip_bucket(ip):
    """The value the per-IP limit counts by: an IPv4 address, or the /64 network of an IPv6 address."""
    parsed = _parse_ip(ip)
    if parsed is None:
        return None
    if parsed.version == 6 and parsed.ipv4_mapped:
        return str(parsed.ipv4_mapped)
    if parsed.version == 6:
        return str(ipaddress.ip_network(f"{parsed}/64", strict=False).network_address)
    return str(parsed)


# Requesting a code

def beta_is_full():
    return User.objects.filter(is_staff=False).count() >= settings.BETA_CAPACITY


def _throttled(email, bucket, now):
    rows = LoginCode.objects.filter(email=email)
    if rows.filter(created_at__gt=now - RESEND_DELAY).exists():
        return True
    if rows.filter(created_at__gt=now - ADDRESS_SHORT_WINDOW).count() >= ADDRESS_SHORT_LIMIT:
        return True
    if rows.filter(created_at__gt=now - ADDRESS_DAY_WINDOW).count() >= ADDRESS_DAY_LIMIT:
        return True
    return bucket is not None and LoginCode.objects.filter(
        requested_ip=bucket, created_at__gt=now - IP_WINDOW).count() >= IP_LIMIT


@dataclass(frozen=True)
class CodeRequest:
    throttled: bool
    sent: bool = False


def issue_code(email, ip, send, accepted=None):
    """Issue a sign-in code and link for `email` under the sign-in limits, and have `send` mail them.

    `email` must already be validated and normalized; `ip` may be None (no per-IP limit then). Within the limits,
    a LoginCode row is created and `accepted()`, if given, runs in the same transaction. Then, outside it (a slow
    provider never holds the database lock), `send(code, link_path)` mails the code: it returns mail.send's count
    and may raise the mail errors. It is not called once the site's daily cap is reached. The new code replaces
    the earlier ones only once its mail went out; otherwise the earlier ones stay valid and the new row is voided.
    Mail failures are logged, never raised.
    """
    now = timezone.now()
    bucket = ip_bucket(ip)
    with transaction.atomic():
        if _throttled(email, bucket, now):
            return CodeRequest(throttled=True)
        mails_sent = LoginCode.objects.filter(sent_at__gt=now - SITE_MAIL_WINDOW).count()
        token = new_token()
        code = code_for_token(token)
        row = LoginCode.objects.create(email=email, code_hash=hash_code(email, code), token_hash=hash_token(token),
                                       created_at=now, expires_at=now + CODE_TTL, requested_ip=bucket)
        if accepted is not None:
            accepted()

    if mails_sent >= settings.LOGIN_MAIL_DAILY_CAP:
        logger.error("Sign-in mail not sent: the site reached LOGIN_MAIL_DAILY_CAP (%s per 24 h).",
                     settings.LOGIN_MAIL_DAILY_CAP)
        sent = False
    else:
        sent = _send(send, code, reverse("accounts:link", args=[token]))

    with transaction.atomic():
        done = timezone.now()
        if sent:
            LoginCode.objects.filter(pk=row.pk).update(sent_at=done)
            LoginCode.objects.filter(email=email, pk__lt=row.pk, used_at=None, invalidated_at=None).update(
                invalidated_at=done)
        else:
            LoginCode.objects.filter(pk=row.pk).update(invalidated_at=done)
    return CodeRequest(throttled=False, sent=sent)


def _send(send, code, link_path):
    """True when the backend accepted the mail."""
    try:
        return send(code, link_path) > 0
    except mail.MailRefused as error:
        logger.warning("Sign-in mail refused: %s", error)
    except mail.MailError as error:
        logger.error("Sign-in mail not delivered: %s", error)
    return False


def request_login(email, ip):
    """The home page's request: the sign-in code mail, or the waiting-list mail when the beta is full.

    Every accepted request gets a LoginCode row, whatever the address, so the code page answers the same way for
    all (see issue_code for the limits and what happens when the mail does not go out).
    """
    waitlisted = False

    def accepted():
        nonlocal waitlisted
        OnboardingEvent.objects.create(name=events.CODE_REQUESTED)
        waitlisted = beta_is_full() and not User.objects.filter(email=email).exists()
        if waitlisted:
            WaitlistEntry.objects.get_or_create(email=email)

    def send(code, link_path):
        if waitlisted:
            return mail.send("waitlist", email, {})
        return mail.send("login_code", email, {"code": group_code(code), "link": settings.SITE_URL + link_path,
                                                "minutes": CODE_TTL_MINUTES})

    return issue_code(email, ip, send, accepted=accepted)


# Signing in

class SignIn(StrEnum):
    SIGNED_IN = "signed_in"
    WAITLISTED = "waitlisted"
    EXPIRED = "expired"
    WRONG = "wrong"
    TOO_MANY = "too_many"
    SESSION_CAP = "session_cap"
    REFUSED = "refused"  # inactive account


@dataclass(frozen=True)
class Outcome:
    status: SignIn
    user: User | None = None
    remaining: int = 0


def _recent_failures(session, now):
    oldest = now - SESSION_FAILURE_WINDOW.total_seconds()
    return [stamp for stamp in session.get(SESSION_FAILURES, []) if stamp > oldest]


def sign_in_with_code(request, code):
    """Check `code` against the address kept in the session, never against anything posted."""
    email = request.session.get(SESSION_EMAIL)
    if not email:
        return Outcome(SignIn.EXPIRED)
    failures = _recent_failures(request.session, time.time())
    if len(failures) >= SESSION_FAILURE_LIMIT:
        return Outcome(SignIn.SESSION_CAP)
    now = timezone.now()
    with transaction.atomic():
        row = (LoginCode.objects.select_for_update()
               .filter(email=email, used_at=None, invalidated_at=None, expires_at__gt=now)
               .order_by("-created_at").first())
        if row is None:
            return Outcome(SignIn.EXPIRED)
        if row.attempts >= MAX_ATTEMPTS:
            return Outcome(SignIn.TOO_MANY)
        if not hmac.compare_digest(row.code_hash, hash_code(email, code)):
            LoginCode.objects.filter(pk=row.pk).update(attempts=F("attempts") + 1)
            row.refresh_from_db(fields=["attempts"])
            request.session[SESSION_FAILURES] = failures + [int(time.time())]
            remaining = MAX_ATTEMPTS - row.attempts
            return Outcome(SignIn.WRONG, remaining=remaining) if remaining > 0 else Outcome(SignIn.TOO_MANY)
        return _complete(request, row, now, events.CODE_ENTERED)


def link_details(token):
    """For the link page's GET: the row's address and the code to show, or None. Reads only."""
    if not TOKEN_PATTERN.fullmatch(token):
        return None
    row = LoginCode.objects.filter(token_hash=hash_token(token), used_at=None, invalidated_at=None,
                                   expires_at__gt=timezone.now()).first()
    return (row.email, group_code(code_for_token(token))) if row else None


def sign_in_with_link(request, token):
    """The link's POST: the address is the row's, and the pending address in the session is dropped."""
    if not TOKEN_PATTERN.fullmatch(token):
        return Outcome(SignIn.EXPIRED)
    now = timezone.now()
    with transaction.atomic():
        row = (LoginCode.objects.select_for_update()
               .filter(token_hash=hash_token(token), used_at=None, invalidated_at=None, expires_at__gt=now).first())
        if row is None:
            return Outcome(SignIn.EXPIRED)
        request.session.pop(SESSION_EMAIL, None)
        return _complete(request, row, now, events.LINK_USED)


def _complete(request, row, now, event):
    """Consume the row and sign in, inside the caller's transaction."""
    user = User.objects.filter(email=row.email).first()
    if user is not None and not user.is_active:
        return Outcome(SignIn.REFUSED)
    if not LoginCode.objects.filter(pk=row.pk, used_at=None).update(used_at=now):
        return Outcome(SignIn.EXPIRED)
    LoginCode.objects.filter(email=row.email, used_at=None, invalidated_at=None).update(invalidated_at=now)
    if user is None:
        if beta_is_full():
            WaitlistEntry.objects.get_or_create(email=row.email)
            request.session.pop(SESSION_EMAIL, None)
            return Outcome(SignIn.WAITLISTED)
        user = User.objects.create_user(row.email)
    Profile.objects.get_or_create(user=user)
    login(request, user, backend=BACKEND)  # rotates the session key and the CSRF token
    request.session.pop(SESSION_EMAIL, None)
    OnboardingEvent.objects.create(user=user, name=event)
    return Outcome(SignIn.SIGNED_IN, user=user)
