"""The guard on /admin: a Cloudflare Access token, checked in the code, as well as at Cloudflare (S7 to S9).

Cloudflare Access in front of /admin* is not enough on its own: the port published on the LAN reaches Django
without going through it, and a staff account also signs in by code on the public site, whose session would open
/admin without a password. So, once SITE_URL is https://, every request to /admin and below must carry a
Cf-Access-Jwt-Assertion header whose token is checked here: RS256 only, signature by a key of the team's JWKS,
`iss`, `aud`, `exp` and `nbf` (60 s of clock tolerance). Any doubt, any error: 404, never an exception and never a
word about the reason. Without the two settings (team host and audience) the admin stays closed. Over http (local)
the guard does nothing.
"""
import json
import logging
import re
import threading
import time
import urllib.request

import jwt
from django.conf import settings
from django.http import HttpResponseNotFound

from config.http import RefuseRedirects

logger = logging.getLogger(__name__)

HEADER = "HTTP_CF_ACCESS_JWT_ASSERTION"
MAX_TOKEN_LENGTH = 8192
ALGORITHM = "RS256"
LEEWAY = 60
FETCH_TIMEOUT = 3
MAX_CERTS_BYTES = 64 * 1024
MAX_KEYS = 20
MAX_KID_LENGTH = 100
KEY_LIFETIME = 3600  # seconds a key is trusted after its fetch
# Seconds between two fetches, for an unknown kid or a failure alike: bad tokens never hammer Cloudflare.
REFRESH_PAUSE = 60


class _CertsRefused(Exception):
    """The team's host answered with a redirect, which is never followed."""


def _redirect_refused(code):
    return _CertsRefused()


class _Keys:
    """The team's public keys by kid, fetched together, kept for KEY_LIFETIME. One lock for the fetch."""

    def __init__(self):
        self.lock = threading.Lock()
        self.keys = {}
        self.fetched_at = None  # monotonic time of the last successful fetch
        self.attempted_at = None  # monotonic time of the last fetch, successful or not


_keys = _Keys()


def clear_cache():
    with _keys.lock:
        _keys.keys, _keys.fetched_at, _keys.attempted_at = {}, None, None


def is_guarded_path(path):
    """/admin and everything below, whatever the case or the repeated slashes. Not /administrateur/."""
    path = re.sub(r"/+", "/", path or "").lower()
    return path == "/admin" or path.startswith("/admin/")


def _fetch(team):
    """{kid: public key} of the team's RSA keys. Raises on any failure. No proxy, no redirect, a short delay."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RefuseRedirects(_redirect_refused))
    with opener.open(f"https://{team}/cdn-cgi/access/certs", timeout=FETCH_TIMEOUT) as response:
        raw = response.read(MAX_CERTS_BYTES + 1)
    if len(raw) > MAX_CERTS_BYTES:
        raise ValueError("certs too long")
    entries = json.loads(raw)["keys"]
    keys = {}
    for entry in entries[:MAX_KEYS]:
        try:
            if entry["kty"] == "RSA" and isinstance(entry["kid"], str) and len(entry["kid"]) <= MAX_KID_LENGTH:
                keys[entry["kid"]] = jwt.PyJWK.from_dict(entry, algorithm=ALGORITHM).key
        except Exception:  # noqa: BLE001 one unusable key must not hide the others
            continue
    if not keys:
        raise ValueError("no usable key")
    return keys


def _key_for(team, kid):
    """The key of `kid`, fetching the team's keys when the cache is empty, expired, or lacks the kid (at most one
    fetch per REFRESH_PAUSE). None when there is none: an expired cache is never used if its refresh fails."""
    now = time.monotonic()
    with _keys.lock:
        fresh = _keys.fetched_at is not None and now - _keys.fetched_at < KEY_LIFETIME
        if fresh and kid in _keys.keys:
            return _keys.keys[kid]
        paused = _keys.attempted_at is not None and now - _keys.attempted_at < REFRESH_PAUSE
        if paused:
            return _keys.keys.get(kid) if fresh else None
        _keys.attempted_at = now
        try:
            keys = _fetch(team)
        except Exception as error:  # noqa: BLE001 any failure is a refusal, logged by its class only
            logger.warning("Cloudflare Access keys not fetched (%s).", type(error).__name__)
            if not fresh:
                _keys.keys, _keys.fetched_at = {}, None
            return _keys.keys.get(kid) if fresh else None
        _keys.keys, _keys.fetched_at = keys, now
        return keys.get(kid)


def token_is_valid(value, team, audience):
    """True only for a token signed by a key of the team, for this audience and issuer, within its dates."""
    try:
        if not team or not audience or not isinstance(value, str) or not value or len(value) > MAX_TOKEN_LENGTH:
            return False
        header = jwt.get_unverified_header(value)
        kid = header.get("kid")
        if header.get("alg") != ALGORITHM or not isinstance(kid, str) or not kid or len(kid) > MAX_KID_LENGTH:
            return False
        key = _key_for(team, kid)
        if key is None:
            return False
        jwt.decode(value, key, algorithms=[ALGORITHM], audience=audience, issuer=f"https://{team}", leeway=LEEWAY,
                   options={"require": ["exp", "iss", "aud"]})
        return True
    except Exception:  # noqa: BLE001 any doubt is a refusal
        return False


class CloudflareAccessGuard:
    """404 for /admin and below unless the Cloudflare Access token is valid; in http, the request goes through."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.SITE_URL.startswith("https://") and is_guarded_path(request.path_info) and not token_is_valid(
                request.META.get(HEADER), settings.ADMIN_ACCESS_TEAM_DOMAIN, settings.ADMIN_ACCESS_AUD):
            return HttpResponseNotFound()
        return self.get_response(request)
