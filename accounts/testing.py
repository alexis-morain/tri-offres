"""Test helpers: sign-in rows made directly, outside the views and their rate limits, and finished profiles."""
import tempfile
from datetime import timedelta
from pathlib import Path

from django.test import override_settings
from django.utils import timezone

from accounts import auth
from accounts.models import LoginCode, Profile, User


def make_login_code(email, ago=0, ip=None, sent=True):
    """A LoginCode issued `ago` seconds ago that replaces the address's earlier codes. Returns (token, code)."""
    now = timezone.now()
    LoginCode.objects.filter(email=email, used_at=None, invalidated_at=None).update(invalidated_at=now)
    token = auth.new_token()
    code = auth.code_for_token(token)
    created = now - timedelta(seconds=ago)
    LoginCode.objects.create(email=email, code_hash=auth.hash_code(email, code), token_hash=auth.hash_token(token),
                             created_at=created, expires_at=created + auth.CODE_TTL, requested_ip=ip,
                             sent_at=created if sent else None)
    return token, code


def make_profile(email="nora@example.fr", **fields):
    """A mailable account whose questionnaire is finished, and its profile (culture domains, no strict criterion).
    `fields` set the profile's fields."""
    user = User.objects.create_user(email)
    return Profile.objects.create(user=user, **{"domains": ["E"], "hard_criteria": {"version": 1},
                                                "onboarding_completed_at": timezone.now(), **fields})


def make_finished_profile(email="nora@example.fr", **fields):
    """make_profile() whose every questionnaire screen is validated, so that onboarding.flow.is_finished() holds."""
    from onboarding import flow  # the onboarding app builds on accounts

    return make_profile(email, **{"onboarding_step": flow.STEPS[-1].slug, **fields})


class JournalTestMixin:
    """Points the account journal (accounts/journal.py) at a file of its own in a throwaway folder, whatever the
    test: no test writes to the real data folder. `self.journal_path` is that file."""

    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.journal_path = Path(directory.name) / "journal-comptes.txt"
        override = override_settings(ACCOUNT_JOURNAL_PATH=self.journal_path)
        override.enable()
        self.addCleanup(override.disable)

    def journal_lines(self):
        return self.journal_path.read_text().splitlines() if self.journal_path.exists() else []
