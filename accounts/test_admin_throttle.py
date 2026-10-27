"""The limit on password attempts (S10): 5 failures per 15 minutes per IP, 10 per hour per address, 30 per hour
for the site. Counted in the database from the user_login_failed signal, checked by the backend before the password."""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import authenticate
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from accounts import throttle
from accounts.models import AdminLoginFailure, User
from config.testing import PLAIN_STATIC

LOGIN = "/admin/login/"
ADDRESS = "staff@example.fr"
PASSWORD = "correct horse battery staple"
WRONG = "not-the-password"


def stamp(ago_seconds=0, bucket="203.0.113.7", address=ADDRESS):
    """A failure `ago_seconds` ago, made directly."""
    row = AdminLoginFailure.objects.create(ip_bucket=bucket, address_hash=throttle.address_hash(address))
    AdminLoginFailure.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(seconds=ago_seconds))
    return row


@override_settings(STORAGES=PLAIN_STATIC)
class ThrottleTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(ADDRESS, PASSWORD)

    def post(self, password=WRONG, address=ADDRESS, ip="203.0.113.7"):
        return self.client.post(LOGIN, {"username": address, "password": password, "next": "/admin/"},
                                REMOTE_ADDR=ip)

    def signed_in(self):
        return "_auth_user_id" in self.client.session

    def failures(self):
        return AdminLoginFailure.objects.count()


class RecordingTests(ThrottleTestCase):
    def test_a_wrong_password_is_recorded_with_the_ip_bucket_and_a_hash_of_the_address(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        row = AdminLoginFailure.objects.get()
        self.assertEqual(row.ip_bucket, "203.0.113.7")
        self.assertEqual(row.address_hash, throttle.address_hash(ADDRESS))
        self.assertEqual(len(row.address_hash), 64)

    def test_an_unknown_address_is_recorded_too(self):
        self.post(address="nobody@example.fr")
        self.assertEqual(AdminLoginFailure.objects.get().address_hash, throttle.address_hash("nobody@example.fr"))

    def test_neither_the_password_nor_the_address_is_stored(self):
        self.post(password="hunter2-hunter2")
        row = AdminLoginFailure.objects.get()
        stored = " ".join(str(value) for value in (row.ip_bucket, row.address_hash, row.created_at))
        for secret in ("hunter2", ADDRESS, "staff"):
            self.assertNotIn(secret, stored)

    def test_the_hash_ignores_case_and_edges_and_is_keyed_by_the_secret_key(self):
        self.assertEqual(throttle.address_hash(" Staff@Example.FR "), throttle.address_hash(ADDRESS))
        self.assertNotEqual(throttle.address_hash(ADDRESS), throttle.address_hash("other@example.fr"))
        before = throttle.address_hash(ADDRESS)
        with override_settings(SECRET_KEY="another-key" * 5):
            self.assertNotEqual(throttle.address_hash(ADDRESS), before)
        self.assertEqual(throttle.address_hash("  "), "")

    def test_a_right_password_signs_in_and_records_nothing(self):
        self.post(password=PASSWORD)
        self.assertTrue(self.signed_in())
        self.assertEqual(self.failures(), 0)

    def test_ipv6_is_counted_by_its_network(self):
        self.post(ip="2001:db8:1:2:aaaa:bbbb:cccc:dddd")
        self.assertEqual(AdminLoginFailure.objects.get().ip_bucket, "2001:db8:1:2::")

    @override_settings(TRUST_CF_CONNECTING_IP=True)
    def test_the_ip_is_the_cloudflare_one_behind_the_tunnel(self):
        self.client.post(LOGIN, {"username": ADDRESS, "password": WRONG}, REMOTE_ADDR="172.18.0.2",
                         HTTP_CF_CONNECTING_IP="198.51.100.9")
        self.assertEqual(AdminLoginFailure.objects.get().ip_bucket, "198.51.100.9")


class LimitTests(ThrottleTestCase):
    def assertBlocked(self, **kwargs):
        before = self.failures()
        response = self.post(password=PASSWORD, **kwargs)  # even the right password
        self.assertFalse(self.signed_in())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.failures(), before, "a blocked attempt is not counted: the window slides")
        return response

    def assertAllowed(self, **kwargs):
        self.post(password=PASSWORD, **kwargs)
        self.assertTrue(self.signed_in())
        self.client.logout()

    def test_five_failures_from_one_ip_in_fifteen_minutes_block_the_sixth(self):
        for _ in range(5):
            self.post()
        self.assertBlocked()

    def test_four_failures_do_not(self):
        for _ in range(4):
            self.post()
        self.assertAllowed()

    def test_failures_older_than_fifteen_minutes_do_not_count_for_the_ip(self):
        for _ in range(5):
            stamp(15 * 60 + 5, address=f"x{_}@example.fr")
        self.assertAllowed()

    def test_the_limit_is_per_ip_bucket(self):
        for _ in range(5):
            stamp(60, bucket="198.51.100.1", address=f"x{_}@example.fr")
        self.assertAllowed(ip="203.0.113.7")
        self.assertBlocked(ip="198.51.100.1")

    def test_ten_failures_per_hour_for_one_address_whatever_the_ip(self):
        for number in range(10):
            stamp(20 * 60, bucket=f"198.51.100.{number}")
        self.assertBlocked(ip="203.0.113.99")

    def test_nine_failures_per_hour_for_an_address_do_not_block(self):
        for number in range(9):
            stamp(20 * 60, bucket=f"198.51.100.{number}")
        self.assertAllowed(ip="203.0.113.99")

    def test_the_address_limit_ignores_case(self):
        for number in range(10):
            stamp(20 * 60, bucket=f"198.51.100.{number}", address=ADDRESS.upper())
        self.assertBlocked(ip="203.0.113.99")

    def test_thirty_failures_per_hour_for_the_site(self):
        for number in range(30):
            stamp(30 * 60, bucket=f"198.51.{number}.1", address=f"x{number}@example.fr")
        self.assertBlocked(ip="203.0.113.99")

    def test_twenty_nine_failures_per_hour_for_the_site_do_not_block(self):
        for number in range(29):
            stamp(30 * 60, bucket=f"198.51.{number}.1", address=f"x{number}@example.fr")
        self.assertAllowed(ip="203.0.113.99")

    def test_failures_older_than_an_hour_count_for_nothing(self):
        for number in range(40):
            stamp(3600 + 30, bucket=f"198.51.{number}.1", address=ADDRESS)
        self.assertAllowed()

    def test_a_blocked_attempt_is_answered_as_a_wrong_password(self):
        wrong = self.post(address="nobody@example.fr", ip="198.51.100.200")
        for _ in range(5):
            stamp(10)
        blocked = self.post(password=PASSWORD)
        self.assertEqual(blocked.status_code, wrong.status_code)
        self.assertEqual(blocked.context["form"].errors, wrong.context["form"].errors)

    def test_the_password_is_not_checked_while_blocked(self):
        for _ in range(5):
            stamp(10)
        with mock.patch("django.contrib.auth.backends.ModelBackend.authenticate") as checked:
            self.post(password=PASSWORD)
        checked.assert_not_called()

    def test_an_unknown_address_is_limited_like_any_other(self):
        for _ in range(5):
            self.post(address="nobody@example.fr")
        self.assertBlocked(address="nobody@example.fr")

    def test_signing_in_by_code_is_not_limited(self):
        for _ in range(40):
            stamp(60, bucket=f"198.51.{_}.1", address=f"x{_}@example.fr")
        self.client.force_login(self.staff)
        self.assertTrue(self.signed_in())

    def test_without_a_request_only_the_address_and_site_limits_apply(self):
        for _ in range(5):
            stamp(60)
        self.assertEqual(authenticate(username=ADDRESS, password=PASSWORD), self.staff)  # no IP known
        for number in range(10):
            stamp(60, bucket=f"198.51.100.{number}")
        self.assertIsNone(authenticate(username=ADDRESS, password=PASSWORD))

    def test_a_request_without_a_username_counts_for_its_ip_and_the_site_only(self):
        request = RequestFactory().post(LOGIN, REMOTE_ADDR="203.0.113.7")
        self.assertIsNone(authenticate(request, username=None, password=WRONG))
        row = AdminLoginFailure.objects.get()
        self.assertEqual((row.ip_bucket, row.address_hash), ("203.0.113.7", ""))
        for _ in range(12):
            stamp(60, bucket=f"198.51.100.{_}", address="")
        self.assertFalse(throttle.blocked(RequestFactory().post(LOGIN, REMOTE_ADDR="192.0.2.1"), ""))


class PurgeTests(TestCase):
    def test_rows_older_than_seven_days_are_purged_with_the_login_codes(self):
        old, recent = stamp(7 * 24 * 3600 + 60), stamp(6 * 24 * 3600)
        out = StringIO()
        call_command("purge_login_codes", stdout=out)
        self.assertEqual(list(AdminLoginFailure.objects.values_list("pk", flat=True)), [recent.pk])
        self.assertNotIn(old.pk, AdminLoginFailure.objects.values_list("pk", flat=True))
        self.assertIn("1 admin login failure(s) deleted.", out.getvalue())

    def test_dry_run_counts_and_deletes_nothing(self):
        stamp(8 * 24 * 3600)
        out = StringIO()
        call_command("purge_login_codes", "--dry-run", stdout=out)
        self.assertEqual(AdminLoginFailure.objects.count(), 1)
        self.assertIn("1 admin login failure(s) to delete (--dry-run).", out.getvalue())
