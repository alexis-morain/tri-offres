"""healthchecks.io pings, one check per slot (the design plan).

The URL is a secret: whoever has it can ping. It never appears in a message, a log or an exception, and the body
holds nothing but step names of the slot table, state words and durations (S1, S2). A failed ping never fails a slot.
"""
import logging
import time
import urllib.error
import urllib.request

from django.conf import settings

from config.http import RefuseRedirects
from supervision import slots

logger = logging.getLogger(__name__)

SLUGS = {"nuit": "tri-offres-nuit", "matin": "tri-offres-matin", "soir": "tri-offres-soir"}
SUFFIXES = {"start": "/start", "success": "", "fail": "/fail"}
BODY_MAX = 10_000  # bytes kept, from the end
TIMEOUT = 10
RETRY_DELAY = 2

STEP_WORDS = {"ok": "ok", "failed": "échec", "skipped": "sautée"}
RUN_WORDS = {"ok": "ok", "failed": "échec", "interrupted": "interrompu", "running": "en cours"}


class PingRefused(Exception):
    """healthchecks.io answered with a redirect, which is never followed."""


def _redirect_refused(code):
    return PingRefused()


def _seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    return f" ({round(value, 1):g} s)".replace(".", ",")


def ping_body(run):
    """One line per step, `<step> : <ok|échec|sautée> (<duration>)`, then the state of the slot. Only names of the
    slot table and known state words get through: no output, error message or account key."""
    known = {step.name for step in slots.SLOTS.get(run.slot, [])}
    lines = []
    for result in run.steps:
        name, word = result.get("name"), STEP_WORDS.get(result.get("status"))
        if name in known and word:
            lines.append(f"{name} : {word}{_seconds(result.get('seconds'))}")
    lines.append(f"Créneau : {RUN_WORDS.get(run.status, 'inconnu')}")
    return "\n".join(lines)


def ping(slot, kind, body=""):
    """POST to the check of `slot`; `kind` is start, success or fail. True when healthchecks.io took it. Without
    HEALTHCHECKS_PING_URL: nothing happens. Any error is swallowed and logged by its class only."""
    if slot not in SLUGS or kind not in SUFFIXES:
        raise ValueError("Unknown slot or kind of ping.")
    base = settings.HEALTHCHECKS_PING_URL
    if not base:
        return False
    url = f"{base.rstrip('/')}/{SLUGS[slot]}{SUFFIXES[kind]}"
    data = body.encode()[-BODY_MAX:]
    for attempt in (1, 2):
        try:
            request = urllib.request.Request(url, data=data, method="POST", headers={"User-Agent": "tri-offres"})
            # No proxy: the hôte proxy is for the actor only; and no redirect, whatever the answer says.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RefuseRedirects(_redirect_refused))
            with opener.open(request, timeout=TIMEOUT):
                pass
            return True
        except Exception as exc:  # never the message: it may quote the URL
            retry = attempt == 1 and _worth_retrying(exc)
            logger.warning("healthchecks ping %s of %s failed (%s)%s", kind, slot, _describe(exc),
                           ", retrying" if retry else "")
            if not retry:
                return False
            time.sleep(RETRY_DELAY)
    return False


def _worth_retrying(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500
    return isinstance(exc, OSError)  # URLError, timeouts and connection errors


def _describe(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    return type(exc).__name__
