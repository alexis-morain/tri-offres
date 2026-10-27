"""The OpenRouter PKCE flow (contract 5.9 to 5.11): the start, the single slot that holds it in the session, and the
exchange of the code for the person's key.

The slot is the only place the verifier lives, in the session, never in the database. It is single use: a return that
carries a code takes it out whatever happens, so a code cannot be replayed and a started flow cannot be finished by
someone else (the slot names the account that started it).
"""
import base64
import hashlib
import hmac
import logging
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx2
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

logger = logging.getLogger(__name__)

SLOT = "openrouter_pkce"
AUTH_URL = "https://openrouter.ai/auth"
EXCHANGE_URL = "https://openrouter.ai/api/v1/auth/keys"
KEY_LABEL = "tri-offres"
TTL = timedelta(minutes=10)  # what OpenRouter gives a code
EXCHANGE_TIMEOUT = 10
MAX_CODE = 512
MAX_ANSWER = 4096
KEY_SHAPE = re.compile(r"sk-or-[A-Za-z0-9_-]{14,190}\Z")


def begin(session, user_pk):
    """Open the slot (a new start overwrites the last) and give the OpenRouter URL to send the person to."""
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    session[SLOT] = {"verifier": verifier, "state": state, "user_pk": user_pk,
                     "issued_at": int(timezone.now().timestamp())}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return AUTH_URL + "?" + urlencode({
        "callback_url": settings.SITE_URL + reverse("accounts:openrouter_return"),
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state, "key_label": KEY_LABEL,
    })


def take(session, user, state):
    """The verifier of the flow `user` started, or None; the slot is taken out either way. Refused when the person is
    not signed in, is not the one who started, the slot is older than TTL or `state` is not its own."""
    slot = session.pop(SLOT, None)
    if not isinstance(slot, dict) or not user.is_authenticated:
        return None
    verifier, expected = slot.get("verifier"), slot.get("state")
    user_pk, issued_at = slot.get("user_pk"), slot.get("issued_at")
    if not (isinstance(verifier, str) and isinstance(expected, str) and type(user_pk) is int
            and type(issued_at) is int):
        return None
    if user_pk != user.pk or timezone.now().timestamp() - issued_at > TTL.total_seconds():
        return None
    if not hmac.compare_digest(expected.encode(), state.encode()):
        return None
    return verifier


@sensitive_variables("code", "verifier", "key")
def exchange(code, verifier):
    """The key OpenRouter gives for `code`, or None for any failure. Called outside any transaction (SQLite writes
    wait on each other), to a fixed URL, redirections off. Nothing of the answer but the key is kept, and nothing of
    it is logged: not the body, not the exception's message."""
    try:
        response = httpx2.post(EXCHANGE_URL, json={"code": code, "code_verifier": verifier,
                                                   "code_challenge_method": "S256"},
                               timeout=EXCHANGE_TIMEOUT, follow_redirects=False)
    except httpx2.HTTPError as error:
        logger.warning("OpenRouter exchange failed (%s)", type(error).__name__)
        return None
    if response.status_code != 200 or len(response.content) > MAX_ANSWER:
        logger.warning("OpenRouter exchange refused (status %s)", response.status_code)
        return None
    try:
        key = response.json().get("key")
    except (ValueError, AttributeError):
        key = None
    if not isinstance(key, str) or KEY_SHAPE.match(key) is None:
        logger.warning("OpenRouter exchange answered without a usable key")
        return None
    return key
