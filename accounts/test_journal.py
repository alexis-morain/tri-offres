"""The account journal (arbitrage B of the security review of 21/10, the design plan):
one line per deletion, unsubscription and re-subscription, outside the database, so that a restored backup does not
bring back what people asked to erase. The line, the file, the three hooks, the nightly purge and the replay."""
import stat
from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core import mail as django_mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, SimpleTestCase, TestCase, override_settings

from accounts import deletion, journal, unsubscribe
from accounts.models import Profile, User
from accounts.testing import JournalTestMixin, make_finished_profile
from config import backups
from config.testing import PLAIN_STATIC

EMAIL = "nora@example.fr"
OTHER = "paul@example.fr"
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
NOW = datetime(2026, 10, 21, 10, 0, 0, tzinfo=dt_timezone.utc)
FORM = "application/x-www-form-urlencoded"


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class JournalFileTests(JournalTestMixin, SimpleTestCase):
    def test_fingerprint_is_a_keyed_sha256_of_the_lowercase_address(self):
        value = journal.fingerprint("  Nora@Example.FR ")
        self.assertRegex(value, r"^[0-9a-f]{64}$")
        self.assertEqual(value, journal.fingerprint(EMAIL))
        self.assertNotEqual(value, journal.fingerprint(OTHER))
        self.assertNotIn("nora", value)

    def test_fingerprint_depends_on_the_secret_key_and_on_its_own_salt(self):
        value = journal.fingerprint(EMAIL)
        with override_settings(SECRET_KEY="another-secret-key-for-a-test"):
            self.assertNotEqual(journal.fingerprint(EMAIL), value)
        from accounts import throttle
        self.assertNotEqual(throttle.address_hash(EMAIL), value)  # not the admin throttle's hash: another salt

    def test_line_is_a_utc_stamp_a_kind_and_the_fingerprint_never_the_address(self):
        journal.record(journal.DELETION, 7, "Nora@example.fr", now=NOW)
        line, = self.journal_lines()
        self.assertEqual(line, f"2026-10-21T10:00:00Z suppression {journal.fingerprint(EMAIL)}")
        self.assertNotIn("nora", self.journal_path.read_text().lower())
        self.assertNotIn("example.fr", self.journal_path.read_text())

    def test_kinds(self):
        self.assertEqual(journal.KINDS, ("suppression", "desinscription", "reabonnement", "deconnexion_openrouter"))

    def test_lines_are_appended_in_order(self):
        journal.record(journal.UNSUBSCRIPTION, 1, EMAIL, now=NOW)
        journal.record(journal.RESUBSCRIPTION, 1, EMAIL, now=NOW + timedelta(minutes=1))
        journal.record(journal.DELETION, 2, OTHER, now=NOW + timedelta(minutes=2))
        self.assertEqual([line.split()[1] for line in self.journal_lines()],
                         ["desinscription", "reabonnement", "suppression"])

    def test_file_is_private_even_when_it_existed_with_other_rights(self):
        self.journal_path.write_text("")
        self.journal_path.chmod(0o644)
        journal.record(journal.DELETION, 1, EMAIL, now=NOW)
        self.assertEqual(stat.S_IMODE(self.journal_path.stat().st_mode), 0o600)

    def test_new_file_is_created_private(self):
        journal.record(journal.DELETION, 1, EMAIL, now=NOW)
        self.assertEqual(stat.S_IMODE(self.journal_path.stat().st_mode), 0o600)
        lock = self.journal_path.with_name(self.journal_path.name + ".lock")
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)

    def test_each_line_is_synced_to_disk(self):
        with mock.patch("os.fsync") as fsync:
            journal.record(journal.DELETION, 1, EMAIL, now=NOW)
        fsync.assert_called()

    def test_a_failed_write_never_raises_and_is_logged_by_class_and_key_only(self):
        missing = self.journal_path.parent / "no" / "such" / "folder" / "journal-comptes.txt"
        with override_settings(ACCOUNT_JOURNAL_PATH=missing), self.assertLogs("accounts.journal", "ERROR") as logs:
            journal.record(journal.DELETION, 42, EMAIL, now=NOW)  # does not raise
        message = " ".join(logs.output)
        self.assertIn("42", message)
        self.assertIn("FileNotFoundError", message)
        self.assertNotIn("nora", message.lower())
        self.assertNotIn(journal.fingerprint(EMAIL), message)
        self.assertNotIn(str(missing), message)

    def test_a_write_error_after_opening_is_also_swallowed(self):
        with mock.patch("os.write", side_effect=OSError("disk full: nora@example.fr")), \
                self.assertLogs("accounts.journal", "ERROR") as logs:
            journal.record(journal.DELETION, 5, EMAIL, now=NOW)
        self.assertNotIn("nora", " ".join(logs.output).lower())
        self.assertNotIn("disk full", " ".join(logs.output))

    def test_entries_reads_valid_lines_in_order_and_skips_the_others(self):
        journal.record(journal.DELETION, 1, EMAIL, now=NOW)
        with open(self.journal_path, "a") as handle:
            handle.write("garbage\n\n2026-10-21T10:00:00Z inconnu " + "a" * 64 + "\n")
        journal.record(journal.UNSUBSCRIPTION, 2, OTHER, now=NOW + timedelta(hours=1))
        found = journal.entries()
        self.assertEqual([(e.at, e.kind) for e in found],
                         [(NOW, "suppression"), (NOW + timedelta(hours=1), "desinscription")])
        self.assertEqual(found[0].fingerprint, journal.fingerprint(EMAIL))

    def test_entries_of_a_missing_file_is_empty(self):
        self.assertEqual(journal.entries(), [])

    def test_duration_is_the_backup_days_plus_one(self):
        self.assertEqual(journal.KEEP, timedelta(days=backups.BACKUP_DAYS + 1))
        self.assertEqual(journal.KEEP.days, 31)

    def test_default_path_is_next_to_the_database_and_tests_never_use_the_data_folder(self):
        # manage.py test points the setting at a throwaway folder; the real default is DATABASE_PATH's folder.
        self.assertIsInstance(settings.ACCOUNT_JOURNAL_PATH, Path)
        self.assertNotEqual(settings.ACCOUNT_JOURNAL_PATH.parent, settings.BASE_DIR / "data")


class PurgeTests(JournalTestMixin, SimpleTestCase):
    def fill(self):
        for days, key in ((40, 1), (32, 2), (30, 3), (1, 4)):
            journal.record(journal.DELETION, key, f"person{key}@example.fr", now=NOW - timedelta(days=days))

    def test_lines_older_than_31_days_go_the_others_stay_in_order(self):
        self.fill()
        removed = journal.purge(now=NOW)
        self.assertEqual(removed, 2)
        kept = journal.entries()
        self.assertEqual([e.fingerprint for e in kept],
                         [journal.fingerprint("person3@example.fr"), journal.fingerprint("person4@example.fr")])

    def test_file_stays_private_after_a_rewrite_and_leaves_no_temporary_file(self):
        self.fill()
        journal.purge(now=NOW)
        self.assertEqual(stat.S_IMODE(self.journal_path.stat().st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in self.journal_path.parent.iterdir()),
                         ["journal-comptes.txt", "journal-comptes.txt.lock"])

    def test_a_line_that_cannot_be_read_is_dropped_with_the_old_ones(self):
        self.fill()
        with open(self.journal_path, "a") as handle:
            handle.write("not a line\n")
        self.assertEqual(journal.purge(now=NOW), 3)

    def test_nothing_to_purge_leaves_the_file_untouched(self):
        journal.record(journal.DELETION, 1, EMAIL, now=NOW)
        before = self.journal_path.stat().st_ino
        self.assertEqual(journal.purge(now=NOW), 0)
        self.assertEqual(self.journal_path.stat().st_ino, before)

    def test_missing_file_is_zero(self):
        self.assertEqual(journal.purge(now=NOW), 0)

    def test_command_reports_and_dry_run_deletes_nothing(self):
        self.fill()
        with mock.patch("accounts.journal.timezone") as clock:
            clock.now.return_value = NOW
            out = StringIO()
            call_command("purge_journal", "--dry-run", stdout=out)
            self.assertIn("2 ligne(s)", out.getvalue())
            self.assertIn("(--dry-run)", out.getvalue())
            self.assertEqual(len(self.journal_lines()), 4)
            out = StringIO()
            call_command("purge_journal", stdout=out)
        self.assertEqual(len(self.journal_lines()), 2)
        self.assertIn("2 ligne(s)", out.getvalue())

    def test_a_failed_purge_leaves_the_old_file_whole(self):
        self.fill()
        before = self.journal_path.read_text()
        with mock.patch("os.replace", side_effect=OSError("boom")), self.assertRaises(OSError):
            journal.purge(now=NOW)
        self.assertEqual(self.journal_path.read_text(), before)


@override_settings(EMAIL_BACKEND=LOCMEM, DEV_EMAILS=[], CLAUDE_ENGINE="fake", LOGIN_MAIL_DAILY_CAP=60,
                   STORAGES=PLAIN_STATIC)
class HookTests(JournalTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = make_finished_profile(EMAIL).user
        self.fingerprint = journal.fingerprint(EMAIL)

    def kinds(self):
        return [line.split()[1] for line in self.journal_lines()]

    def test_deleting_an_account_writes_a_deletion_line_for_that_address(self):
        with self.captureOnCommitCallbacks(execute=True):
            deletion.delete_account(self.user)
        line, = self.journal_lines()
        self.assertTrue(line.endswith(f" suppression {self.fingerprint}"))

    def test_a_deletion_that_rolls_back_writes_nothing(self):
        with mock.patch.object(User, "delete", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
            deletion.delete_account(self.user)
        self.assertEqual(self.journal_lines(), [])

    def test_a_journal_failure_does_not_undo_the_deletion_nor_the_mail(self):
        missing = self.journal_path.parent / "gone" / "journal-comptes.txt"
        with override_settings(ACCOUNT_JOURNAL_PATH=missing), self.assertLogs("accounts.journal", "ERROR"), \
                self.captureOnCommitCallbacks(execute=True):
            deletion.delete_account(self.user)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertEqual(len(django_mail.outbox), 1)

    def test_deletion_through_the_page(self):
        client = Client()
        client.force_login(self.user)
        response = client.post("/mes-donnees/supprimer/")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.kinds(), ["suppression"])

    def test_the_unsubscribe_page_post_writes_a_line(self):
        token = unsubscribe.make_token(self.user)
        Client().post(f"/desinscription/{token}/")
        line, = self.journal_lines()
        self.assertTrue(line.endswith(f" desinscription {self.fingerprint}"))

    def test_the_one_click_post_writes_a_line(self):
        token = unsubscribe.make_token(self.user)
        response = Client().post(f"/desinscription/{token}/un-clic/", "List-Unsubscribe=One-Click", content_type=FORM)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.kinds(), ["desinscription"])

    def test_no_line_for_a_second_unsubscription_a_forged_token_or_a_get(self):
        token = unsubscribe.make_token(self.user)
        Client().get(f"/desinscription/{token}/")
        Client().post(f"/desinscription/{token}x/")
        self.assertEqual(self.journal_lines(), [])
        Client().post(f"/desinscription/{token}/")
        Client().post(f"/desinscription/{token}/")
        self.assertEqual(self.kinds(), ["desinscription"])

    def test_a_journal_failure_does_not_stop_the_unsubscription(self):
        missing = self.journal_path.parent / "gone" / "journal-comptes.txt"
        token = unsubscribe.make_token(self.user)
        with override_settings(ACCOUNT_JOURNAL_PATH=missing), self.assertLogs("accounts.journal", "ERROR"):
            response = Client().post(f"/desinscription/{token}/")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.status, User.Status.UNSUBSCRIBED)

    def test_resubscribing_writes_a_line_only_when_the_status_changes(self):
        client = Client()
        client.force_login(self.user)
        client.post("/mes-donnees/reabonner/")  # active already: nothing changes
        self.assertEqual(self.journal_lines(), [])
        User.objects.filter(pk=self.user.pk).update(status=User.Status.UNSUBSCRIBED)
        client.post("/mes-donnees/reabonner/")
        line, = self.journal_lines()
        self.assertTrue(line.endswith(f" reabonnement {self.fingerprint}"))

    def test_no_line_leaks_an_address_or_a_token(self):
        token = unsubscribe.make_token(self.user)
        Client().post(f"/desinscription/{token}/")
        text = self.journal_path.read_text()
        self.assertNotIn(token, text)
        self.assertNotIn("@", text)


@override_settings(EMAIL_BACKEND=LOCMEM, DEV_EMAILS=[], CLAUDE_ENGINE="fake", LOGIN_MAIL_DAILY_CAP=60)
class ReplayTests(JournalTestMixin, TestCase):
    """What an operator runs on a restored database: the lines after the backup are applied again, in order."""

    def setUp(self):
        super().setUp()
        self.nora = make_finished_profile(EMAIL).user
        self.paul = make_finished_profile(OTHER).user

    def line(self, kind, address, hours):
        journal.record(kind, 0, address, now=NOW + timedelta(hours=hours))

    def replay(self, since="2026-10-21T10:00", *options):
        out = StringIO()
        call_command("rejouer_journal", "--depuis", since, *options, stdout=out)
        return out.getvalue()

    def status(self, user):
        return User.objects.get(pk=user.pk).status

    def test_a_deletion_after_the_backup_deletes_the_account_without_mail_and_without_a_new_line(self):
        self.line(journal.DELETION, EMAIL, 1)
        before = self.journal_path.read_text()
        output = self.replay()
        self.assertFalse(User.objects.filter(pk=self.nora.pk).exists())
        self.assertFalse(Profile.objects.filter(user_id=self.nora.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.paul.pk).exists())
        self.assertEqual(django_mail.outbox, [])
        self.assertEqual(self.journal_path.read_text(), before)
        self.assertIn(f"compte {self.nora.pk} supprimé", output)

    def test_lines_before_the_instant_are_not_replayed_and_the_instant_itself_is(self):
        self.line(journal.DELETION, EMAIL, -1)
        self.line(journal.DELETION, OTHER, 0)
        output = self.replay()
        self.assertTrue(User.objects.filter(pk=self.nora.pk).exists())
        self.assertFalse(User.objects.filter(pk=self.paul.pk).exists())
        self.assertIn("1 ligne(s) rejouée(s)", output)

    def test_unsubscription_then_resubscription_in_order(self):
        self.line(journal.UNSUBSCRIPTION, EMAIL, 1)
        self.line(journal.RESUBSCRIPTION, EMAIL, 2)
        self.line(journal.RESUBSCRIPTION, OTHER, 1)
        self.line(journal.UNSUBSCRIPTION, OTHER, 2)
        self.replay()
        self.assertEqual(self.status(self.nora), User.Status.ACTIVE)
        self.assertEqual(self.status(self.paul), User.Status.UNSUBSCRIBED)

    def test_lines_are_applied_by_time_even_if_the_file_is_out_of_order(self):
        self.line(journal.RESUBSCRIPTION, EMAIL, 2)
        self.line(journal.UNSUBSCRIPTION, EMAIL, 1)
        self.replay()
        self.assertEqual(self.status(self.nora), User.Status.ACTIVE)

    def test_a_paused_account_is_unsubscribed_like_the_page_does(self):
        User.objects.filter(pk=self.nora.pk).update(status=User.Status.PAUSED)
        self.line(journal.UNSUBSCRIPTION, EMAIL, 1)
        self.replay()
        self.assertEqual(self.status(self.nora), User.Status.UNSUBSCRIBED)

    def test_replaying_twice_gives_the_same_state(self):
        self.line(journal.DELETION, EMAIL, 1)
        self.line(journal.UNSUBSCRIPTION, OTHER, 1)
        self.replay()
        second = self.replay()
        self.assertFalse(User.objects.filter(pk=self.nora.pk).exists())
        self.assertEqual(self.status(self.paul), User.Status.UNSUBSCRIBED)
        self.assertIn("aucun compte", second)

    def test_dry_run_changes_nothing_and_says_what_it_would_do(self):
        self.line(journal.DELETION, EMAIL, 1)
        self.line(journal.UNSUBSCRIPTION, OTHER, 1)
        output = self.replay("2026-10-21T10:00", "--dry-run")
        self.assertTrue(User.objects.filter(pk=self.nora.pk).exists())
        self.assertEqual(self.status(self.paul), User.Status.ACTIVE)
        self.assertIn("--dry-run", output)
        self.assertIn(f"compte {self.nora.pk}", output)
        self.assertIn(f"compte {self.paul.pk}", output)

    def test_output_names_accounts_by_key_only(self):
        self.line(journal.DELETION, EMAIL, 1)
        self.line(journal.UNSUBSCRIPTION, OTHER, 1)
        self.line(journal.DELETION, "ghost@example.fr", 1)
        output = self.replay()
        self.assertNotIn("@", output)
        self.assertNotIn("example", output)
        self.assertNotRegex(output, r"[0-9a-f]{32}")
        self.assertIn("aucun compte", output)

    def test_a_team_account_is_never_deleted_by_a_replay(self):
        staff = User.objects.create_user("staff@example.fr", is_staff=True)
        self.line(journal.DELETION, "staff@example.fr", 1)
        output = self.replay()
        self.assertTrue(User.objects.filter(pk=staff.pk).exists())
        self.assertIn("équipe", output)

    def test_a_rotated_secret_key_makes_old_lines_unusable(self):
        self.line(journal.DELETION, EMAIL, 1)
        with override_settings(SECRET_KEY="rotated-secret-key-for-a-test"):
            output = self.replay()
        self.assertTrue(User.objects.filter(pk=self.nora.pk).exists())
        self.assertIn("aucun compte", output)

    def test_an_empty_or_missing_journal_is_not_an_error(self):
        self.assertIn("0 ligne(s)", self.replay())

    def test_the_instant_is_required_and_must_be_a_minute_precision_utc_stamp(self):
        with self.assertRaises(CommandError):
            call_command("rejouer_journal", stdout=StringIO())
        for bad in ("2026-10-21", "21/10/2026 10:00", "2026-10-21T10:00:00Z", "demain"):
            with self.subTest(bad=bad), self.assertRaises(CommandError):
                self.replay(bad)

    def test_a_deletion_of_an_account_with_its_data_goes_through_the_deletion_service(self):
        self.line(journal.DELETION, EMAIL, 1)
        with mock.patch("accounts.management.commands.rejouer_journal.deletion.delete_account",
                        wraps=deletion.delete_account) as service:
            self.replay()
        self.assertEqual(service.call_args.kwargs, {"replay": True})
