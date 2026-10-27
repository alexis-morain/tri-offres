"""Email backends: Resend over HTTPS with the standard library, and a console backend that says nothing left."""
import json
import logging
import re
import urllib.error
import urllib.request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.console import EmailBackend as DjangoConsoleBackend

from config.http import RefuseRedirects
from mail import MailDeliveryFailed

logger = logging.getLogger(__name__)

# RFC 8058 one-click unsubscribe: the only headers sent to Resend. The link is one https URL in angle brackets, made
# of printable ASCII without space or angle brackets; the second header has one fixed value.
ONE_CLICK_POST = "List-Unsubscribe=One-Click"
LIST_UNSUBSCRIBE = re.compile(r"<https://[!-;=?-~]+>")


def _list_headers(message):
    """The unsubscribe headers of `message`, checked; every other header is dropped. MailDeliveryFailed for a value
    that is not exactly the expected shape: this backend never goes through Django's line-break check."""
    headers = {}
    for name, allowed in (("List-Unsubscribe", LIST_UNSUBSCRIBE.fullmatch),
                          ("List-Unsubscribe-Post", ONE_CLICK_POST.__eq__)):
        if name in message.extra_headers:
            value = message.extra_headers[name]
            if not isinstance(value, str) or not allowed(value):
                raise MailDeliveryFailed(f"Refused header {name}.")
            headers[name] = value
    return headers


def _redirect_refused(code):
    """The error raised instead of following a redirect: Resend never redirects its API, and following one would
    send the Authorization header elsewhere."""
    return MailDeliveryFailed("Resend answered with a redirect, not followed.")


class ResendBackend(BaseEmailBackend):
    """POST each message to Resend's API. One recipient per message, no copies, 10 second timeout."""

    endpoint = "https://api.resend.com/emails"
    timeout = 10

    def __init__(self, api_key=None, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = api_key or settings.RESEND_API_KEY

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            try:
                self._send(message)
            except MailDeliveryFailed:
                if not self.fail_silently:
                    raise
            else:
                sent += 1
        return sent

    def _send(self, message):
        if not self.api_key:
            raise MailDeliveryFailed("RESEND_API_KEY is not set.")
        if len(message.to) != 1 or message.cc or message.bcc:
            raise MailDeliveryFailed("Exactly one recipient and no copies are allowed.")
        payload = {"from": message.from_email, "to": list(message.to), "subject": message.subject,
                   "text": message.body}
        html = next((content for content, mimetype in getattr(message, "alternatives", [])
                     if mimetype == "text/html"), None)
        if html is not None:
            payload["html"] = html
        if message.reply_to:
            payload["reply_to"] = list(message.reply_to)
        headers = _list_headers(message)
        if headers:
            payload["headers"] = headers
        request = urllib.request.Request(
            self.endpoint, data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                     "User-Agent": "tri-offres"})
        try:
            opener = urllib.request.build_opener(RefuseRedirects(_redirect_refused))
            with opener.open(request, timeout=self.timeout) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            raise MailDeliveryFailed(f"Resend answered HTTP {error.code}.") from None
        except (urllib.error.URLError, OSError) as error:  # TimeoutError and connection errors are OSErrors
            raise MailDeliveryFailed(f"Resend unreachable ({error.__class__.__name__}).") from None
        if not 200 <= status < 300:
            raise MailDeliveryFailed(f"Resend answered HTTP {status}.")


class ConsoleBackend(DjangoConsoleBackend):
    """Used when RESEND_API_KEY is missing: nothing leaves the machine.

    With DEBUG on, the whole mail is printed for the developer. With DEBUG off, one line only, with no recipient
    and no content (a code in a server log is a credential), and the mail counts as not sent.
    """

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        if not settings.DEBUG:
            for _ in email_messages:
                logger.warning("Mail not sent: RESEND_API_KEY is missing.")
            return 0
        logger.warning("RESEND_API_KEY is missing: real sending is off, the mail below is only printed here.")
        sent = super().send_messages(email_messages)
        # One line per mail after the MIME dump: the subject carries the sign-in code.
        for message in email_messages:
            self.stream.write(f">>> Mail pour {', '.join(message.to)} : {message.subject}\n\n")
        self.stream.flush()
        return sent
