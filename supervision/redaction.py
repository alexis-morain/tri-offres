"""The one masking function for text that leaves the process or lands in the database (S3): `TaskRun.error`, the
console output of the night command, alert mails. The actor's output goes by it too (step 10.2)."""
import re
from urllib.parse import unquote, urlsplit

from django.conf import settings

from accounts.log_filters import redact

MASK = "[masqué]"
# Mail addresses, with Unicode letters (élodie@...). The local part is bounded (64 is the RFC limit): the actor's log
# can hold a megabyte without a space, and an open repetition would be quadratic on it.
ADDRESS = re.compile(r"[\w.%+'-]{1,64}@[\w-]+(?:\.[\w-]+)+")
# `scheme://user:password@host`: everything between `://` and the last `@` before a path, query or fragment.
URL_CREDENTIALS = re.compile(r"(://)[^/\s?#]*@")
# 43 characters is the length of a token_urlsafe(32); one letter or digit within the first 43 keeps rules of dashes
# readable, and bounds the look-ahead (an open one is quadratic on a long run of dashes).
URLSAFE_TOKEN = re.compile(r"(?=[_-]{0,42}[A-Za-z0-9])[A-Za-z0-9_-]{43,}")


def _secrets():
    """Exact strings to remove, longest first: the proxy URL and its password, the ping URL and its key."""
    found = set()
    proxy = settings.SCRAPER_PROXY_URL
    if proxy:
        found.add(proxy)
        try:
            password = urlsplit(proxy).password
        except ValueError:
            password = None
        if password:
            found.update({password, unquote(password)})
    ping = settings.HEALTHCHECKS_PING_URL
    if ping:
        found.add(ping)
        key = ping.rstrip("/").rsplit("/", 1)[-1]
        if key:
            found.add(key)
    return sorted(found, key=len, reverse=True)


def redact_text(text) -> str:
    """`text` with secrets, link tokens, addresses, URL credentials and long url-safe strings masked."""
    text = "" if text is None else str(text)
    for secret in _secrets():
        text = text.replace(secret, MASK)
    text = redact(text)
    text = URL_CREDENTIALS.sub(r"\1", text)
    text = ADDRESS.sub("[adresse]", text)
    return URLSAFE_TOKEN.sub(MASK, text)
