"""Deleting an account (step 7, the design plan, requirements E12 to E16).

Everything that depends on the person goes in one transaction: the user and its cascade (profile, scores, feedback,
deliveries, runs, events), the sessions, the admin log entries about the account, the sign-in codes and the waiting
list entry kept by address. The confirmation mail goes once that transaction is committed. Logs name the account by
its key, never by its address.
"""
import logging
import secrets

from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.auth import SESSION_KEY
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.db import models, transaction
from django.db.models.functions import Cast
from django.utils import timezone

import mail
from accounts import auth, journal
from accounts.models import LoginCode, User, WaitlistEntry

logger = logging.getLogger(__name__)

# Sign-in codes older than this are erased; the younger ones stay, without address, because the site's daily mail
# cap (auth.SITE_MAIL_WINDOW) counts their `sent_at`.
CODE_KEEP = auth.SITE_MAIL_WINDOW


def _delete_sessions(key):
    """Every session of the person, expired or not, whatever browser holds it."""
    wanted = str(key)
    # shortcut: reads every session, one decode each; fine at the beta's size, index the user id beyond a few thousand.
    mine = [session.pk for session in Session.objects.iterator() if session.get_decoded().get(SESSION_KEY) == wanted]
    Session.objects.filter(pk__in=mine).delete()


def _cascaded_rows(user):
    """(model, queryset) for each model that holds the person by a CASCADE key; the admin log aside, whose entries
    made by the person go with the user anyway."""
    for relation in User._meta.related_objects:
        model = relation.related_model
        if relation.on_delete is models.CASCADE and not model._meta.auto_created and model is not LogEntry:
            yield model, model._default_manager.filter(**{relation.field.name: user.pk})


def _delete_admin_log(user, email):
    """The admin log entries about the account and its rows (their `object_repr` holds the address)."""
    about = [(User, User.objects.filter(pk=user.pk)), *_cascaded_rows(user),
             (LoginCode, LoginCode.objects.filter(email__iexact=email)),
             (WaitlistEntry, WaitlistEntry.objects.filter(email__iexact=email))]
    for model, rows in about:
        keys = rows.annotate(text_key=Cast("pk", models.CharField())).values("text_key")
        LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(model), object_id__in=keys).delete()


def _erase_login_codes(email, now):
    """Older codes deleted; the younger ones lose their address and both hashes, which become random values (the
    token hash is unique), so they can neither sign in nor be tied to the person."""
    codes = LoginCode.objects.filter(email__iexact=email)
    codes.filter(created_at__lt=now - CODE_KEEP).delete()
    for pk in codes.values_list("pk", flat=True):
        LoginCode.objects.filter(pk=pk).update(email="", code_hash=secrets.token_hex(32),
                                               token_hash=secrets.token_hex(32), invalidated_at=now)


def delete_account(user, *, replay=False):
    """Erase `user` and all that depends on them, at once and for good; the mail follows once committed. The caller
    signs the person out (their session is already gone from the database).

    One line goes to the account journal once the transaction is over, so that a restored backup can be brought up to
    date (accounts/journal.py). `replay=True` is that replay (`manage.py rejouer_journal`): the line exists already,
    and no mail goes, the person having been told when they asked."""
    key, email = user.pk, user.email
    now = timezone.now()
    with transaction.atomic():
        _delete_sessions(key)
        _delete_admin_log(user, email)
        _erase_login_codes(email, now)
        WaitlistEntry.objects.filter(email__iexact=email).delete()
        user.delete()
        if not replay:
            transaction.on_commit(lambda: _confirm(key, email))
    if not replay:
        journal.record(journal.DELETION, key, email)
    logger.info("Account %s deleted.", key)


def _confirm(key, email):
    """The confirmation mail, counted in the site's daily cap like the sign-in mails: the cap is read in the
    LoginCode rows' `sent_at`, so a sent mail leaves one more such row, without address. Never raises."""
    since = timezone.now() - auth.SITE_MAIL_WINDOW
    if LoginCode.objects.filter(sent_at__gt=since).count() >= settings.LOGIN_MAIL_DAILY_CAP:
        logger.error("Deletion mail of account %s not sent: the site reached LOGIN_MAIL_DAILY_CAP (%s per 24 h).",
                     key, settings.LOGIN_MAIL_DAILY_CAP)
        return
    try:
        sent = mail.send("account_deleted", email, {}) > 0
    except mail.MailError as error:
        logger.warning("Deletion mail of account %s not sent: %s", key, type(error).__name__)
        return
    if not sent:
        logger.warning("Deletion mail of account %s not accepted by the backend.", key)
        return
    now = timezone.now()
    LoginCode.objects.create(email="", code_hash=secrets.token_hex(32), token_hash=secrets.token_hex(32),
                             created_at=now, expires_at=now, invalidated_at=now, sent_at=now)
