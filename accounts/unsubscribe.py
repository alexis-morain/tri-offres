"""Unsubscribing from the morning mail (sub-step 7.2, requirements E1 to E7 of the design plan).

The link in a mail's footer and the one-click header carry a signed token: the account's key and join date, never
its address. It is checked only when it is acted on, and every answer is the same whatever it holds.
"""
import logging

from django.conf import settings
from django.core import signing
from django.urls import reverse

from accounts import journal
from accounts.models import User
from mail.backends import ONE_CLICK_POST

logger = logging.getLogger(__name__)

SALT = "tri-offres.unsubscribe"


def make_token(user):
    return signing.dumps([user.pk, int(user.date_joined.timestamp())], salt=SALT, compress=False)


def read_token(value):
    """The account `value` was made for, or None: bad signature, absent account, or one recreated under the same key.

    No expiry: an old mail must still work. django.core.signing also tries SECRET_KEY_FALLBACKS, so rotating the
    key keeps old links valid for as long as the old key stays listed. A leaked token cannot be revoked: it only
    stops the morning mail, which is accepted. The join date backs the account table's AUTOINCREMENT key so that a
    key is never read twice.
    """
    try:
        pk, joined = signing.loads(value, salt=SALT, max_age=None)
    except (signing.BadSignature, ValueError, TypeError):  # TypeError/ValueError: a signed payload of another shape
        return None
    if type(pk) is not int or type(joined) is not int:
        return None
    user = User.objects.filter(pk=pk).first()
    if user is None or int(user.date_joined.timestamp()) != joined:
        return None
    return user


def unsubscribe_url(user):
    return settings.SITE_URL + reverse("accounts:unsubscribe", args=[make_token(user)])


def one_click_url(user):
    return settings.SITE_URL + reverse("accounts:unsubscribe_one_click", args=[make_token(user)])


def mail_headers(user):
    """RFC 8058 headers for a mail to `user`; none unless SITE_URL is https (the RFC wants an https link)."""
    if not settings.SITE_URL.startswith("https://"):
        return {}
    return {"List-Unsubscribe": f"<{one_click_url(user)}>", "List-Unsubscribe-Post": ONE_CLICK_POST}


def stop_user_mails(user, *, replay=False):
    """Stop the mails of `user`; True when the status changed.

    update(), never save(): only `status` changes, and `is_active` (the right to sign in) is not touched. A change
    leaves a line in the account journal, unless this is its replay (`manage.py rejouer_journal`).
    """
    changed = User.objects.filter(pk=user.pk, status__in=[User.Status.ACTIVE, User.Status.PAUSED]).update(
        status=User.Status.UNSUBSCRIBED)
    if changed:
        logger.info("Account %s unsubscribed.", user.pk)
        if not replay:
            journal.record(journal.UNSUBSCRIPTION, user.pk, user.email)
    return bool(changed)


def resume_mails(user, *, replay=False):
    """Mails on again for an unsubscribed `user` (« Mes données »); True when the status changed, and then written
    to the account journal like an unsubscription."""
    changed = User.objects.filter(pk=user.pk, status=User.Status.UNSUBSCRIBED).update(status=User.Status.ACTIVE)
    if changed and not replay:
        journal.record(journal.RESUBSCRIPTION, user.pk, user.email)
    return bool(changed)


def stop_mails(token):
    """Stop the mails of the account `token` was made for, if any. Silent either way: the caller answers the same."""
    user = read_token(token)
    if user is None:
        return
    stop_user_mails(user)
