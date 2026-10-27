"""Transactional mail: one call per mail, three templates per mail, and the DEV_EMAILS guard.

mail.send("login_code", "nora@exemple.fr", {...}) renders mail/login_code_subject.txt, .txt and .html and hands
the message to Django's configured backend (Resend, the console, or locmem in tests).
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.template.loader import render_to_string

MAX_ADDRESS_LENGTH = 254
# Quoted local parts, domain literals and list or header separators have no place in a sign-up address.
FORBIDDEN_CHARACTERS = frozenset('"<>,;()[]\\')


class MailError(Exception):
    """A mail did not go out."""


class MailRefused(MailError):
    """send() refused to hand the mail to the backend."""


class MailDeliveryFailed(MailError):
    """The backend could not deliver the mail (provider error, timeout, network)."""


def is_valid_address(address):
    """One plain address: Django's validator, plus the characters and length the sign-in contract refuses."""
    if not isinstance(address, str) or not address or len(address) > MAX_ADDRESS_LENGTH:
        return False
    if FORBIDDEN_CHARACTERS.intersection(address) or any(char.isspace() for char in address):
        return False
    try:
        validate_email(address)
    except ValidationError:
        return False
    return True


def send(template, to, context, headers=None):
    """Send one mail to one address. Raises MailRefused, or MailDeliveryFailed from the Resend backend.

    `headers` are extra message headers; the Resend backend passes on only the two unsubscribe ones."""
    if not is_valid_address(to):
        raise MailRefused("Invalid recipient address.")
    # The Max token may only serve the developers (Anthropic's rules): with the oauth engine, nobody else is mailed.
    engine = (settings.CLAUDE_ENGINE or "").strip().lower()
    if engine == "oauth" and to.lower() not in [address.lower() for address in settings.DEV_EMAILS]:
        raise MailRefused("CLAUDE_ENGINE is oauth and the recipient is not in DEV_EMAILS.")
    subject = " ".join(render_to_string(f"mail/{template}_subject.txt", context).split())
    text = render_to_string(f"mail/{template}.txt", context)
    html = render_to_string(f"mail/{template}.html", context)
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to],
                                     reply_to=[settings.MAIL_REPLY_TO], headers=headers)
    message.attach_alternative(html, "text/html")
    return message.send()
