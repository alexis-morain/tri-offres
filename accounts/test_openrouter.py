"""A person's OpenRouter key (contract 5.2 to 5.4, 5.7, 5.12, 5.14): sealed storage, refusals and their effects, the
disconnection and its replay. Mail goes to locmem, nothing leaves the machine."""
import hashlib
import io
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken
from django.contrib import admin
from django.core import mail as django_mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts import deletion, journal, openrouter
from accounts.models import OpenRouterKey, User
from accounts.testing import JournalTestMixin
from config.testing import assert_french_copy

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
KEY = "sk-or-v1-CANARY0123456789abcdefABCDEF"  # fictive
OTHER_KEY = "sk-or-v1-OTHER0123456789abcdefABCDEF"  # fictive
FERNET_A, FERNET_B, FERNET_C = (Fernet.generate_key().decode() for _ in range(3))
SITE = "https://jobs.example.org"


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


@override_settings(OPENROUTER_KEYS_FERNET=[FERNET_A], EMAIL_BACKEND=LOCMEM, SITE_URL=SITE, CLAUDE_ENGINE="",
                   DEV_EMAILS=[])
class KeyCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("nora@example.fr")


class KeyStorageTests(KeyCase):
    def test_the_column_holds_a_token_never_the_key(self):
        openrouter.store(self.user.pk, KEY)
        row = OpenRouterKey.objects.get(user=self.user)
        self.assertNotIn("CANARY", row.encrypted_key)
        self.assertNotIn("CANARY", str(row))
        self.assertEqual(row.fingerprint, sha(KEY))
        self.assertIsNone(row.refused_since)
        self.assertIsNotNone(row.connected_at)

    def test_read_gives_the_key_and_its_fingerprint(self):
        openrouter.store(self.user.pk, KEY)
        credential = openrouter.read(self.user.pk)
        self.assertEqual((credential.key, credential.fingerprint), (KEY, sha(KEY)))
        self.assertNotIn("CANARY", repr(credential))

    def test_no_row_reads_none_and_is_not_connected(self):
        self.assertIsNone(openrouter.read(self.user.pk))
        self.assertFalse(openrouter.is_connected(self.user))
        openrouter.store(self.user.pk, KEY)
        self.assertTrue(openrouter.is_connected(self.user))

    def test_one_key_per_account_the_second_replaces_the_first(self):
        openrouter.store(self.user.pk, KEY)
        openrouter.store(self.user.pk, OTHER_KEY)
        self.assertEqual(OpenRouterKey.objects.count(), 1)
        self.assertEqual(openrouter.read(self.user.pk).key, OTHER_KEY)

    def test_a_new_key_ends_an_episode_of_refusal(self):
        openrouter.store(self.user.pk, KEY)
        OpenRouterKey.objects.update(refused_since=timezone.now())
        openrouter.store(self.user.pk, OTHER_KEY)
        self.assertIsNone(OpenRouterKey.objects.get().refused_since)

    def test_the_key_page_link_uses_the_fingerprint(self):
        self.assertEqual(openrouter.key_url(sha(KEY)), f"https://openrouter.ai/keys/{sha(KEY)}")

    def test_the_model_is_never_in_the_admin(self):
        self.assertFalse(admin.site.is_registered(OpenRouterKey))

    def test_the_encrypted_field_is_not_editable(self):
        self.assertFalse(OpenRouterKey._meta.get_field("encrypted_key").editable)

    def test_deleting_the_account_removes_the_key(self):
        openrouter.store(self.user.pk, KEY)
        deletion.delete_account(self.user, replay=True)
        self.assertFalse(OpenRouterKey.objects.exists())


class RotationTests(KeyCase):
    def test_rotation_with_two_keys_reads_the_old_and_seals_with_the_first(self):
        openrouter.store(self.user.pk, KEY)  # sealed by A
        with override_settings(OPENROUTER_KEYS_FERNET=[FERNET_B, FERNET_A]):
            self.assertEqual(openrouter.read(self.user.pk).key, KEY)
            openrouter.store(self.user.pk, OTHER_KEY)  # sealed by B
        with override_settings(OPENROUTER_KEYS_FERNET=[FERNET_B]):
            self.assertEqual(openrouter.read(self.user.pk).key, OTHER_KEY)

    def test_a_lost_key_is_an_invalid_token_and_nothing_is_erased_or_mailed(self):
        openrouter.store(self.user.pk, KEY)
        with override_settings(OPENROUTER_KEYS_FERNET=[FERNET_C]):
            with self.assertRaises(InvalidToken):
                openrouter.read(self.user.pk)
        self.assertEqual(OpenRouterKey.objects.count(), 1)
        self.assertEqual(django_mail.outbox, [])

    def test_a_row_whose_fingerprint_does_not_match_is_an_invalid_token(self):
        openrouter.store(self.user.pk, KEY)
        OpenRouterKey.objects.update(fingerprint=sha(OTHER_KEY))
        with self.assertRaises(InvalidToken):
            openrouter.read(self.user.pk)

    def test_a_row_copied_from_another_account_is_an_invalid_token_for_its_fingerprint_only(self):
        other = User.objects.create_user("lea@example.fr")
        openrouter.store(self.user.pk, KEY)
        openrouter.store(other.pk, OTHER_KEY)
        token = OpenRouterKey.objects.get(user=other).encrypted_key
        OpenRouterKey.objects.filter(user=self.user).update(encrypted_key=token)
        with self.assertRaises(InvalidToken):
            openrouter.read(self.user.pk)


class RefusalEffectsTests(KeyCase):
    def setUp(self):
        super().setUp()
        openrouter.store(self.user.pk, KEY)
        self.credential = openrouter.read(self.user.pk)

    def test_a_401_erases_the_key_and_mails_once(self):
        openrouter.refuse(self.credential, openrouter.REJECTED)
        self.assertFalse(OpenRouterKey.objects.exists())
        self.assertEqual(len(django_mail.outbox), 1)
        message = django_mail.outbox[0]
        self.assertEqual(message.to, ["nora@example.fr"])
        self.assertIn("Reconnectez", message.subject)
        self.assertIn(f"{SITE}/openrouter/", message.body)

    def test_two_concurrent_401_send_one_mail(self):
        openrouter.refuse(self.credential, openrouter.REJECTED)
        openrouter.refuse(self.credential, openrouter.REJECTED)
        self.assertEqual(len(django_mail.outbox), 1)

    def test_a_401_of_an_old_fingerprint_leaves_the_new_key_and_sends_nothing(self):
        openrouter.store(self.user.pk, OTHER_KEY)  # reconnected while the old call was in flight
        openrouter.refuse(self.credential, openrouter.REJECTED)
        self.assertEqual(openrouter.read(self.user.pk).key, OTHER_KEY)
        self.assertEqual(django_mail.outbox, [])

    def test_a_402_keeps_the_key_starts_an_episode_and_mails_once(self):
        openrouter.refuse(self.credential, openrouter.CREDIT)
        row = OpenRouterKey.objects.get()
        self.assertIsNotNone(row.refused_since)
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn("crédit", django_mail.outbox[0].body)
        self.assertIn("https://openrouter.ai/credits", django_mail.outbox[0].body)
        openrouter.refuse(self.credential, openrouter.CREDIT)
        openrouter.refuse(self.credential, openrouter.LIMIT)  # the same episode: no second mail
        self.assertEqual(len(django_mail.outbox), 1)

    def test_a_key_limit_has_its_own_text_and_the_key_page_link(self):
        openrouter.refuse(self.credential, openrouter.LIMIT)
        message = django_mail.outbox[0]
        self.assertIn("limite", message.subject.lower())
        self.assertIn(f"https://openrouter.ai/keys/{sha(KEY)}", message.body)
        self.assertNotEqual(message.subject, "")

    def test_the_episode_closes_at_the_first_success_and_the_next_refusal_mails_again(self):
        openrouter.refuse(self.credential, openrouter.CREDIT)
        credential = openrouter.read(self.user.pk)
        self.assertTrue(credential.in_episode)
        openrouter.accept(credential)
        self.assertIsNone(OpenRouterKey.objects.get().refused_since)
        openrouter.refuse(openrouter.read(self.user.pk), openrouter.CREDIT)
        self.assertEqual(len(django_mail.outbox), 2)

    def test_a_success_of_an_old_fingerprint_does_not_close_the_new_episode(self):
        openrouter.store(self.user.pk, OTHER_KEY)
        openrouter.refuse(openrouter.read(self.user.pk), openrouter.CREDIT)
        openrouter.accept(self.credential)
        self.assertIsNotNone(OpenRouterKey.objects.get().refused_since)

    def test_a_refusal_for_an_account_that_is_gone_does_nothing(self):
        user_pk = self.user.pk
        deletion.delete_account(self.user, replay=True)
        django_mail.outbox.clear()
        openrouter.refuse(self.credential, openrouter.REJECTED)
        openrouter.refuse(self.credential, openrouter.CREDIT)
        self.assertEqual(django_mail.outbox, [])
        self.assertFalse(User.objects.filter(pk=user_pk).exists())

    def test_the_mails_hold_no_key_and_keep_the_house_style(self):
        for kind in (openrouter.REJECTED, openrouter.CREDIT, openrouter.LIMIT):
            openrouter.store(self.user.pk, KEY)
            django_mail.outbox.clear()
            openrouter.refuse(openrouter.read(self.user.pk), kind)
            message = django_mail.outbox[0]
            html = message.alternatives[0][0]
            with self.subTest(kind=kind):
                for text in (message.subject, message.body, html):
                    self.assertNotIn("CANARY", text)
                    self.assertNotIn("sk-or-", text)
                assert_french_copy(self, html)
                assert_french_copy(self, message.body)
                self.assertNotIn("List-Unsubscribe", message.extra_headers)


@override_settings(OPENROUTER_KEYS_FERNET=[FERNET_A], SITE_URL=SITE)
class DisconnectionTests(JournalTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("nora@example.fr")

    def test_disconnect_erases_the_key_and_writes_one_journal_line(self):
        openrouter.store(self.user.pk, KEY)
        self.assertTrue(openrouter.disconnect(self.user))
        self.assertFalse(OpenRouterKey.objects.exists())
        (line,) = self.journal_lines()
        self.assertIn(" deconnexion_openrouter ", line)
        self.assertTrue(line.endswith(journal.fingerprint("nora@example.fr")))
        self.assertNotIn("nora", line)

    def test_disconnect_without_a_key_writes_nothing(self):
        self.assertFalse(openrouter.disconnect(self.user))
        self.assertEqual(self.journal_lines(), [])

    def replay(self, *arguments):
        out = io.StringIO()
        call_command("rejouer_journal", "--depuis", "2000-01-01T00:00", *arguments, stdout=out)
        return out.getvalue()

    def test_the_journal_kind_is_known(self):
        self.assertIn("deconnexion_openrouter", journal.KINDS)

    def test_replay_erases_a_key_connected_before_the_line(self):
        openrouter.store(self.user.pk, KEY)
        journal.record(journal.OPENROUTER_DISCONNECTION, self.user.pk, self.user.email,
                       now=timezone.now() + timedelta(minutes=1))
        self.assertIn("1 ligne(s) rejouée(s)", self.replay())
        self.assertFalse(OpenRouterKey.objects.exists())

    def test_replay_keeps_a_key_connected_after_the_line(self):
        journal.record(journal.OPENROUTER_DISCONNECTION, self.user.pk, self.user.email,
                       now=timezone.now() - timedelta(hours=1))
        openrouter.store(self.user.pk, OTHER_KEY)
        self.assertIn("0 ligne(s) rejouée(s)", self.replay())
        self.assertEqual(openrouter.read(self.user.pk).key, OTHER_KEY)

    def test_replay_of_connect_disconnect_reconnect_keeps_the_last_key(self):
        openrouter.store(self.user.pk, KEY)
        openrouter.disconnect(self.user)
        openrouter.store(self.user.pk, OTHER_KEY)
        OpenRouterKey.objects.update(connected_at=timezone.now() + timedelta(minutes=5))
        self.replay()
        self.assertEqual(openrouter.read(self.user.pk).key, OTHER_KEY)

    def test_dry_run_changes_nothing(self):
        openrouter.store(self.user.pk, KEY)
        journal.record(journal.OPENROUTER_DISCONNECTION, self.user.pk, self.user.email,
                       now=timezone.now() + timedelta(minutes=1))
        self.assertIn("à déconnecter", self.replay("--dry-run"))
        self.assertTrue(OpenRouterKey.objects.exists())

    def test_replay_leaves_no_new_journal_line(self):
        openrouter.store(self.user.pk, KEY)
        journal.record(journal.OPENROUTER_DISCONNECTION, self.user.pk, self.user.email,
                       now=timezone.now() + timedelta(minutes=1))
        self.replay()
        self.assertEqual(len(self.journal_lines()), 1)
