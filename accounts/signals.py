from datetime import timedelta

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver

from accounts import throttle
from accounts.backends import THROTTLED_FLAG

STAFF_SESSION = timedelta(hours=12)


@receiver(user_logged_in)
def fix_session_expiry(sender, request, user, **kwargs):
    """An absolute expiry from sign-in: 12 hours for staff, SESSION_COOKIE_AGE for everyone else, never sliding."""
    session = getattr(request, "session", None)
    if session is None:
        return
    session.set_expiry(STAFF_SESSION if user.is_staff else timedelta(seconds=settings.SESSION_COOKIE_AGE))


@receiver(user_login_failed)
def count_password_failure(sender, credentials, request=None, **kwargs):
    """Every failed password attempt, an unknown account included, counts for the limit of accounts/throttle.py;
    one the limit itself refused does not."""
    if request is not None and getattr(request, THROTTLED_FLAG, False):
        return
    throttle.record_failure(request, credentials.get("username"))
