from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied

from accounts import throttle

# Set on the request when this backend refused an attempt: the signal then does not count it again.
THROTTLED_FLAG = "_password_throttled"


class ThrottledModelBackend(ModelBackend):
    """Django's backend, behind the limit on password attempts (accounts/throttle.py).

    The limit is looked at before the password, so a blocked attempt learns nothing, not even from the time it
    takes. PermissionDenied makes `authenticate()` stop and answer as for a wrong password.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        username = username if username is not None else kwargs.get(get_user_model().USERNAME_FIELD)
        if username is None or password is None:
            return None
        if throttle.blocked(request, username):
            if request is not None:
                setattr(request, THROTTLED_FLAG, True)
            raise PermissionDenied
        return super().authenticate(request, username=username, password=password, **kwargs)
