"""Tiny helpers to read settings from environment variables."""

import os
import re
import warnings
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def env(name, default="", environ=None):
    return (os.environ if environ is None else environ).get(name, default)


def env_bool(name, default=False, environ=None):
    value = env(name, None, environ)
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def env_strict_bool(name, default, environ=None):
    """A switch that fails loudly: absent or empty gives `default`, anything but the known spellings stops the start
    (env_bool would read a typo as false). The error never repeats the value read."""
    value = env(name, "", environ).strip().lower()
    if not value:
        return default
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ImproperlyConfigured(f"{name} must be 1 or 0.")


def env_list(name, default="", environ=None):
    return [item.strip() for item in env(name, default, environ).split(",") if item.strip()]


def env_int(name, default, environ=None):
    value = env(name, "", environ).strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        raise ImproperlyConfigured(f"{name} must be an integer.") from None


def env_https_url(name, environ=None):
    """An optional https:// URL that is a secret (a ping key): the error never repeats the value."""
    value = env(name, "", environ).strip()
    if not value:
        return ""
    if not value.startswith("https://") or len(value) == len("https://") or any(c.isspace() for c in value):
        raise ImproperlyConfigured(f"{name} must be an https:// URL.")
    return value


def env_proxy_url(name, environ=None):
    """An optional http:// or https:// proxy URL that may hold a password: the error never repeats the value."""
    value = env(name, "", environ).strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        valid = parts.scheme in ("http", "https") and bool(parts.hostname) and not any(c.isspace() for c in value)
        parts.port  # raises ValueError on a port that is not a number or out of range
    except ValueError:
        valid = False
    if not valid:
        raise ImproperlyConfigured(f"{name} must be an http:// or https:// URL with a host.")
    return value


HOSTNAME = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))*")


def env_hostname(name, environ=None):
    """An optional bare host name (no scheme, port, path or credentials), lowercased."""
    value = env(name, "", environ).strip().lower()
    if not value:
        return ""
    if len(value) > 253 or not HOSTNAME.fullmatch(value):
        raise ImproperlyConfigured(f"{name} must be a host name without scheme, port or path.")
    return value


def security_settings(site_url, trust_cf_connecting_ip, trusted_origins, environ=None):
    """The HTTPS settings for `site_url` (steps S12 to S14 of the security review of 21/10), as a dict of Django
    settings. In http (local) every one keeps its default.

    https: cookies Secure and __Host- prefixed (Secure, Path=/, no Domain), the site's origin trusted for CSRF, HSTS
    for HSTS_SECONDS (an hour by default) with its subdomains and never preload. The proxy header is believed
    only behind the tunnel (`trust_cf_connecting_ip`): anywhere else a client could forge it. No redirect to https:
    the tunnel ends TLS and the container's probe speaks http.
    """
    https = site_url.startswith("https://")
    hsts = env_int("HSTS_SECONDS", 3600 if https else 0, environ)
    if hsts < 0:
        raise ImproperlyConfigured("HSTS_SECONDS must not be negative.")
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(site_url))
    origins = list(trusted_origins)
    if https and origin not in origins:
        origins.append(origin)
    return {
        "SESSION_COOKIE_SECURE": https,
        "CSRF_COOKIE_SECURE": https,
        "SESSION_COOKIE_NAME": "__Host-sessionid" if https else "sessionid",
        "CSRF_COOKIE_NAME": "__Host-csrftoken" if https else "csrftoken",
        "CSRF_TRUSTED_ORIGINS": origins,
        "SECURE_HSTS_SECONDS": hsts,
        "SECURE_HSTS_INCLUDE_SUBDOMAINS": https and hsts > 0,
        "SECURE_HSTS_PRELOAD": False,
        "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https") if https and trust_cf_connecting_ip else None,
        "SECURE_SSL_REDIRECT": False,
    }


def email_backend(resend_api_key):
    """Real sending through Resend only when its key is set; otherwise mail is printed to the console."""
    return "mail.backends.ResendBackend" if resend_api_key else "mail.backends.ConsoleBackend"


def publisher_identity(debug, resend_api_key, environ=None):
    """(sender, reply-to, publisher name). No personal default: real mail in production without them stops the
    start, so an instance never writes in someone else's name; elsewhere example.org stands in."""
    values = {name: env(name, "", environ).strip() for name in ("DEFAULT_FROM_EMAIL", "MAIL_REPLY_TO", "LEGAL_NAME")}
    if resend_api_key and not debug:
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ImproperlyConfigured(f"{', '.join(missing)} must be set when mail is sent and DJANGO_DEBUG is off.")
    return (values["DEFAULT_FROM_EMAIL"] or "tri-offres <noreply@example.org>",
            values["MAIL_REPLY_TO"] or "contact@example.org", values["LEGAL_NAME"] or "Nom de l’éditeur")


def secret_key(debug, environ=None):
    key = env("DJANGO_SECRET_KEY", "", environ)
    if key:
        return key
    if debug:
        # Never a constant: the repo is public, a known key would let anyone forge signed data.
        warnings.warn("DJANGO_SECRET_KEY unset: using a random per-process dev key.", RuntimeWarning, stacklevel=2)
        return get_random_secret_key()
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")
