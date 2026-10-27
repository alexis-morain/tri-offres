"""The limit on password attempts (S10 of the security review of 21/10, addendum of 21/10 to the sign-in contract).

Only the admin's form asks for a password, and only staff have one. Failures are counted in the database, from the
user_login_failed signal (unknown accounts included): 5 per 15 minutes per IP bucket, 10 per hour per address, 30
per hour for the site. The backend asks `blocked()` before it looks at the password, and answers a blocked attempt
as it would a wrong one. A blocked attempt is not counted: the window slides instead of growing.
"""
from datetime import timedelta

from django.utils import timezone
from django.utils.crypto import salted_hmac

from accounts import auth
from accounts.models import AdminLoginFailure, normalize_email

IP_WINDOW = timedelta(minutes=15)
IP_LIMIT = 5
ADDRESS_WINDOW = timedelta(hours=1)
ADDRESS_LIMIT = 10
SITE_WINDOW = timedelta(hours=1)
SITE_LIMIT = 30
SALT = "tri-offres.admin.login.address"


def address_hash(address):
    """Keyed hash of the normalized address, "" for none. Keyed by SECRET_KEY: the table alone does not give the
    addresses back by a dictionary."""
    address = normalize_email(address)
    return salted_hmac(SALT, address, algorithm="sha256").hexdigest() if address else ""


def _bucket(request):
    return auth.ip_bucket(auth.client_ip(request)) if request is not None else None


def blocked(request, username):
    """True when this attempt must be refused before its password is looked at."""
    now = timezone.now()
    failures = AdminLoginFailure.objects
    bucket = _bucket(request)
    if bucket is not None and failures.filter(
            ip_bucket=bucket, created_at__gt=now - IP_WINDOW).count() >= IP_LIMIT:
        return True
    hashed = address_hash(username)
    if hashed and failures.filter(address_hash=hashed, created_at__gt=now - ADDRESS_WINDOW).count() >= ADDRESS_LIMIT:
        return True
    return failures.filter(created_at__gt=now - SITE_WINDOW).count() >= SITE_LIMIT


def record_failure(request, username):
    AdminLoginFailure.objects.create(ip_bucket=_bucket(request), address_hash=address_hash(username))
