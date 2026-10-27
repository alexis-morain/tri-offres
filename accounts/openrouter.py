"""A person's OpenRouter key: sealed storage, and what a refusal of it does (the OpenRouter key plan, contract 5).

The key spends the person's own money, so it is a third party's secret: sealed with MultiFernet (OPENROUTER_KEYS_FERNET,
the first key seals, all of them open), opened only to make a call, and never shown, logged, mailed nor put in the
journal. What it is checked against is its SHA-256, kept beside it: OpenRouter's own handle for the key, also the link
to its page.

A refusal acts on the row only if the row still holds the key that was refused (`fingerprint`), so a call that was in
flight while the person reconnected cannot undo the new key. One mail goes per episode of refusal.
"""
import hashlib
import hmac
import logging
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

import mail
from accounts import journal
from accounts.models import OpenRouterKey, User

logger = logging.getLogger(__name__)

# The figures of the education screen, in dollars (OpenRouter bills in $), plan 2026-10-27, decision 3.3.
# shortcut: measured on one signup on 27/10/2026, measure again after a week of real nights before opening wider.
SIGNUP_COST = "0,05\u00a0$"
MONTH_COST_MAX = "2\u00a0$"
ADVISED_CREDIT = "5\u00a0$"

KEY_PAGE = "https://openrouter.ai/keys/{}"
CREDITS_PAGE = "https://openrouter.ai/credits"
# What a refusal was: the key is no good (401), the credit is spent (402), the limit set on the key is reached (403).
REJECTED, CREDIT, LIMIT = "rejected", "credit", "limit"
MAILS = {REJECTED: "openrouter_rejected", CREDIT: "openrouter_credit", LIMIT: "openrouter_limit"}


@dataclass(frozen=True)
class Credential:
    """The key to call with and what to say about it afterwards. The key never shows in a repr or a log line."""

    key: str = field(repr=False)
    fingerprint: str
    user_pk: int
    in_episode: bool = False  # a refusal is in progress: the first success closes it


def fingerprint_of(key):
    return hashlib.sha256(key.encode()).hexdigest()


def key_url(fingerprint):
    """The page of the key at OpenRouter: it opens for its owner only."""
    return KEY_PAGE.format(fingerprint)


def _fernet():
    keys = settings.OPENROUTER_KEYS_FERNET
    if not keys:
        raise ImproperlyConfigured("OPENROUTER_KEYS_FERNET is not set.")
    return MultiFernet([Fernet(key) for key in keys])


def is_connected(user):
    """True when the account holds an OpenRouter key (valid as far as we know)."""
    return OpenRouterKey.objects.filter(user_id=user.pk).exists()


def must_connect(user):
    """True when `user` has to connect OpenRouter before anything else: only under the api engine, the `fake` and
    `oauth` engines keep the journey as it was (plan 2026-10-27, decision 3.8)."""
    return settings.CLAUDE_ENGINE == "api" and not is_connected(user)


@sensitive_variables("key")
def store(user_pk, key):
    """Seal `key` for the account, replacing any earlier one; a new key ends an episode of refusal."""
    values = {"encrypted_key": _fernet().encrypt(key.encode()).decode(), "fingerprint": fingerprint_of(key),
              "connected_at": timezone.now(), "refused_since": None}
    OpenRouterKey.objects.update_or_create(user_id=user_pk, defaults=values)


@sensitive_variables("key", "plain")
def read(user_pk):
    """The Credential of the account, or None without a key. Raises InvalidToken when the row cannot be opened (the
    Fernet keys changed, the row is corrupt or was copied from another account): never an erasure, never a mail to
    the person, it is ours to fix."""
    row = OpenRouterKey.objects.filter(user_id=user_pk).first()
    if row is None:
        return None
    plain = _fernet().decrypt(row.encrypted_key.encode())
    try:
        key = plain.decode()
    except UnicodeDecodeError:
        raise InvalidToken from None
    if not hmac.compare_digest(fingerprint_of(key).encode(), row.fingerprint.encode()):
        raise InvalidToken
    return Credential(key, row.fingerprint, user_pk, row.refused_since is not None)


def refuse(credential, kind):
    """The effects of a refusal of `credential` (REJECTED, CREDIT or LIMIT), the one place they happen. Each is a
    single conditional write on the fingerprint: zero rows, nothing to do and no mail. 401: the key is erased. 402
    or limit: the key stays and the episode starts."""
    user_pk = credential.user_pk
    rows = OpenRouterKey.objects.filter(user_id=user_pk, fingerprint=credential.fingerprint)
    if kind == REJECTED:
        changed = rows.delete()[0]
    else:
        changed = rows.filter(refused_since__isnull=True).update(refused_since=timezone.now())
    if changed:
        _notify(user_pk, kind, credential.fingerprint)


def accept(credential):
    """A call went through: the episode of refusal, if any, is over."""
    OpenRouterKey.objects.filter(user_id=credential.user_pk, fingerprint=credential.fingerprint,
                                 refused_since__isnull=False).update(refused_since=None)


def disconnect(user, *, before=None, replay=False):
    """Erase the key of `user`; True when there was one. `before` limits it to a key connected earlier (the replay of
    the journal must not erase a later reconnection). A person's disconnection leaves a line in the account journal,
    so that a restored backup does not bring the key back (`manage.py rejouer_journal`); its replay leaves none."""
    rows = OpenRouterKey.objects.filter(user_id=user.pk)
    if before is not None:
        rows = rows.filter(connected_at__lt=before)
    erased = bool(rows.delete()[0])
    if erased and not replay:
        journal.record(journal.OPENROUTER_DISCONNECTION, user.pk, user.email)
    return erased


def _notify(user_pk, kind, fingerprint):
    """The one mail of an episode. Never raises: a notice that fails must not hide the refusal that caused it."""
    email = User.objects.filter(pk=user_pk).values_list("email", flat=True).first()
    if email is None:
        return
    context = {"connect_url": settings.SITE_URL + reverse("accounts:openrouter_connect"),
               "credits_url": CREDITS_PAGE, "key_url": key_url(fingerprint)}
    try:
        mail.send(MAILS[kind], email, context)
    except Exception as error:  # noqa: BLE001 - the message may quote the recipient or the request
        logger.warning("OpenRouter notice (%s) of account %s not sent: %s", kind, user_pk, type(error).__name__)
