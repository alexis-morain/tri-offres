"""The end of the trial (sub-step 8.1): accounts/trial.py, the date it computes and the gate of the mails and the
scoring (User.objects.mailable). Fixed times only; BETA is on in the test run, the trial is switched on by
override_settings(BETA=False)."""
import datetime as dt
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts import trial
from accounts.models import User
from onboarding import schedule

PARIS = ZoneInfo("Europe/Paris")
UTC = dt.timezone.utc


def frozen(value):
    """django.utils.timezone.now frozen at `value`."""
    return mock.patch.object(timezone, "now", return_value=value)


def paris(*args):
    return dt.datetime(*args, tzinfo=PARIS)


def user_ending(*args):
    """An unsaved account whose trial ends at the Paris time given as datetime arguments (none: no date)."""
    return User(trial_ends_at=paris(*args) if args else None)


class ConstantsTests(SimpleTestCase):
    def test_seven_days_and_the_clock_of_the_schedule(self):
        self.assertEqual(trial.TRIAL_DAYS, 7)
        self.assertIs(trial.PARIS, schedule.PARIS)

    def test_long_date_is_the_public_one_of_the_schedule(self):
        self.assertEqual(schedule.long_date(dt.date(2026, 10, 27)), "mardi 27 octobre")
        self.assertEqual(schedule.long_date(dt.date(2026, 11, 1)), "dimanche 1er novembre")
        self.assertFalse(hasattr(schedule, "_long_date"))


class EnabledTests(SimpleTestCase):
    def test_off_in_beta_on_out_of_it(self):
        self.assertFalse(trial.enabled())
        with override_settings(BETA=False):
            self.assertTrue(trial.enabled())
        with override_settings(BETA=True):
            self.assertFalse(trial.enabled())


class EndForTests(SimpleTestCase):
    def assertEnds(self, completed, expected_local):
        end = trial.end_for(completed)
        self.assertEqual(end, expected_local)
        self.assertEqual(end.astimezone(PARIS).time(), dt.time(0))

    def test_midnight_at_the_start_of_the_eighth_day_whatever_the_hour(self):
        for completed in (paris(2026, 10, 20, 0, 1), paris(2026, 10, 20, 15, 0), paris(2026, 10, 20, 23, 59)):
            with self.subTest(completed=completed):
                self.assertEnds(completed, paris(2026, 10, 27))

    def test_the_local_date_counts_not_the_utc_one(self):
        # 00:30 in Paris is still the day before in UTC: the Paris day decides.
        completed = paris(2026, 10, 20, 0, 30)
        self.assertEqual(completed.astimezone(UTC).date(), dt.date(2026, 10, 19))
        self.assertEnds(completed, paris(2026, 10, 27))
        self.assertEnds(completed.astimezone(UTC), paris(2026, 10, 27))

    def test_exact_midnight_before_the_autumn_clock_change(self):
        # Mon 19 Oct + 7 days is Mon 26 Oct: the clocks went back on Sun 25 Oct, midnight is UTC+1 then.
        end = trial.end_for(paris(2026, 10, 19, 15, 0))
        self.assertEqual(end, dt.datetime(2026, 10, 25, 23, 0, tzinfo=UTC))
        self.assertEqual(end.astimezone(PARIS), paris(2026, 10, 26))
        self.assertEnds(paris(2026, 10, 17, 9, 0), paris(2026, 10, 24))  # Saturday, before the change
        self.assertEnds(paris(2026, 10, 18, 23, 59), paris(2026, 10, 25))  # Sunday, midnight still UTC+2
        self.assertEqual(trial.end_for(paris(2026, 10, 18, 23, 59)), dt.datetime(2026, 10, 24, 22, 0, tzinfo=UTC))

    def test_exact_midnight_before_the_spring_clock_change(self):
        # Mon 23 Mar + 7 days is Mon 30 Mar: the clocks went forward on Sun 29 Mar, midnight is UTC+2 then.
        end = trial.end_for(paris(2026, 3, 23, 15, 0))
        self.assertEqual(end, dt.datetime(2026, 3, 29, 22, 0, tzinfo=UTC))
        self.assertEqual(end.astimezone(PARIS), paris(2026, 3, 30))
        self.assertEnds(paris(2026, 3, 21, 9, 0), paris(2026, 3, 28))  # Saturday, before the change
        self.assertEnds(paris(2026, 3, 22, 23, 59), paris(2026, 3, 29))  # Sunday, midnight still UTC+1
        self.assertEqual(trial.end_for(paris(2026, 3, 22, 23, 59)), dt.datetime(2026, 3, 28, 23, 0, tzinfo=UTC))

    def test_never_seven_times_twenty_four_hours(self):
        completed = paris(2026, 10, 19, 15, 0)
        self.assertNotEqual(trial.end_for(completed), completed + dt.timedelta(days=7))

    def test_is_independent_of_beta(self):
        with override_settings(BETA=False):
            self.assertEnds(paris(2026, 10, 20, 15, 0), paris(2026, 10, 27))


class DaysTests(SimpleTestCase):
    def test_end_day_is_the_eighth_day_and_last_day_the_seventh(self):
        user = user_ending(2026, 10, 27)
        self.assertEqual(trial.end_day(user), dt.date(2026, 10, 27))
        self.assertEqual(trial.last_day(user), dt.date(2026, 10, 26))

    def test_the_paris_date_counts_for_a_date_stored_in_utc(self):
        user = User(trial_ends_at=dt.datetime(2026, 10, 25, 23, 0, tzinfo=UTC))  # Mon 26 Oct 00:00 in Paris
        self.assertEqual(trial.end_day(user), dt.date(2026, 10, 26))
        self.assertEqual(trial.last_day(user), dt.date(2026, 10, 25))

    def test_no_date_no_day(self):
        user = user_ending()
        self.assertIsNone(trial.end_day(user))
        self.assertIsNone(trial.last_day(user))

    def test_days_are_read_whatever_the_beta_flag(self):
        user = user_ending(2026, 10, 27)
        for beta in (True, False):
            with override_settings(BETA=beta):
                self.assertEqual(trial.end_day(user), dt.date(2026, 10, 27))


class EndLabelTests(SimpleTestCase):
    """The one place that writes the day of the end for the welcome mail, the last morning mail and « Mes données »
    (8.2, S11)."""

    def test_the_long_date_of_the_eighth_day(self):
        self.assertEqual(trial.end_label(user_ending(2026, 10, 27)), "mardi 27 octobre")
        self.assertEqual(trial.end_label(user_ending(2026, 11, 1)), "dimanche 1er novembre")

    def test_the_paris_date_counts_for_a_date_stored_in_utc(self):
        self.assertEqual(trial.end_label(User(trial_ends_at=dt.datetime(2026, 10, 25, 23, 0, tzinfo=UTC))),
                         "lundi 26 octobre")

    def test_no_date_no_label(self):
        self.assertIsNone(trial.end_label(user_ending()))

    def test_read_whatever_the_beta_flag(self):
        # Callers decide whether the trial exists; the label only formats the date.
        with override_settings(BETA=True):
            self.assertEqual(trial.end_label(user_ending(2026, 10, 27)), "mardi 27 octobre")


@override_settings(BETA=False)
class IsOverTests(SimpleTestCase):
    def test_one_second_before_and_at_midnight(self):
        user = user_ending(2026, 10, 27)
        end = user.trial_ends_at
        self.assertFalse(trial.is_over(user, end - dt.timedelta(seconds=1)))
        self.assertTrue(trial.is_over(user, end))
        self.assertTrue(trial.is_over(user, end + dt.timedelta(days=30)))

    def test_no_date_is_never_over(self):
        self.assertFalse(trial.is_over(user_ending(), paris(2030, 1, 1)))

    def test_a_date_in_beta_is_not_read(self):
        user = user_ending(2026, 10, 27)
        with override_settings(BETA=True):
            self.assertFalse(trial.is_over(user, paris(2026, 11, 30)))


@override_settings(BETA=False)
class IsLastMailTests(SimpleTestCase):
    def test_tuesday_before_a_wednesday_midnight(self):
        user = user_ending(2026, 10, 28)  # Wednesday: the mail of Tuesday 27 is the last one
        self.assertTrue(trial.is_last_mail(user, dt.date(2026, 10, 27)))
        self.assertFalse(trial.is_last_mail(user, dt.date(2026, 10, 26)))

    def test_friday_with_the_weekend_in_between(self):
        user = user_ending(2026, 10, 26)  # Monday: Friday 23 is the last mail, Monday would be the next one
        self.assertTrue(trial.is_last_mail(user, dt.date(2026, 10, 23)))
        self.assertFalse(trial.is_last_mail(user, dt.date(2026, 10, 22)))

    def test_the_eve_of_a_holiday(self):
        # Wed 11 Nov is a holiday: the next mail after Tue 10 is Thu 12, at or after the end of Thu 12 at midnight.
        user = user_ending(2026, 11, 12)
        self.assertTrue(trial.is_last_mail(user, dt.date(2026, 11, 10)))
        self.assertFalse(trial.is_last_mail(user, dt.date(2026, 11, 9)))
        holiday_end = user_ending(2026, 11, 11)  # the end falls on the holiday itself
        self.assertTrue(trial.is_last_mail(holiday_end, dt.date(2026, 11, 10)))
        self.assertFalse(trial.is_last_mail(holiday_end, dt.date(2026, 11, 9)))

    def test_a_day_already_at_or_after_the_end_is_not_the_last_mail(self):
        user = user_ending(2026, 10, 27)
        self.assertFalse(trial.is_last_mail(user, dt.date(2026, 10, 27)))
        self.assertFalse(trial.is_last_mail(user, dt.date(2026, 10, 28)))
        self.assertFalse(trial.is_last_mail(user, dt.date(2026, 12, 1)))

    def test_the_mail_hour_is_the_one_of_the_schedule(self):
        # Ending at 07:00 sharp: the mail of that day leaves at the end, not before it.
        user = User(trial_ends_at=dt.datetime.combine(dt.date(2026, 10, 27), schedule.SEND_TIME, tzinfo=PARIS))
        self.assertFalse(trial.is_last_mail(user, dt.date(2026, 10, 27)))
        self.assertTrue(trial.is_last_mail(user, dt.date(2026, 10, 26)))

    def test_no_date_is_never_the_last_mail(self):
        self.assertFalse(trial.is_last_mail(user_ending(), dt.date(2026, 10, 27)))

    def test_false_in_beta_even_with_a_date(self):
        user = user_ending(2026, 10, 28)
        with override_settings(BETA=True):
            self.assertFalse(trial.is_last_mail(user, dt.date(2026, 10, 27)))


class MailableGateTests(TestCase):
    NOW = paris(2026, 10, 27, 7, 0)

    def setUp(self):
        self.open = User.objects.create_user("open@example.fr", trial_ends_at=self.NOW + dt.timedelta(seconds=1))
        self.exact = User.objects.create_user("exact@example.fr", trial_ends_at=self.NOW)
        self.over = User.objects.create_user("over@example.fr", trial_ends_at=self.NOW - dt.timedelta(days=3))
        self.none = User.objects.create_user("none@example.fr")

    def mailable(self, now=None):
        return set(User.objects.mailable(now).values_list("email", flat=True))

    @override_settings(BETA=False)
    def test_the_finished_trial_is_excluded_and_the_exact_second_too(self):
        self.assertEqual(self.mailable(self.NOW), {"open@example.fr", "none@example.fr"})

    @override_settings(BETA=False)
    def test_a_missing_date_stays_served_at_any_time(self):
        self.assertIn("none@example.fr", self.mailable(self.NOW + dt.timedelta(days=3650)))

    @override_settings(BETA=False)
    def test_the_default_now_is_the_real_clock(self):
        with frozen(self.NOW):
            self.assertEqual(self.mailable(), {"open@example.fr", "none@example.fr"})
        with frozen(self.NOW - dt.timedelta(days=10)):
            self.assertEqual(self.mailable(), {"open@example.fr", "exact@example.fr", "over@example.fr",
                                               "none@example.fr"})

    @override_settings(BETA=True)
    def test_in_beta_a_date_is_not_read(self):
        self.assertEqual(self.mailable(self.NOW + dt.timedelta(days=100)),
                         {"open@example.fr", "exact@example.fr", "over@example.fr", "none@example.fr"})

    @override_settings(BETA=False)
    def test_the_status_still_decides(self):
        User.objects.filter(pk=self.open.pk).update(status=User.Status.UNSUBSCRIBED)
        User.objects.filter(pk=self.none.pk).update(is_active=False)
        self.assertEqual(self.mailable(self.NOW), set())

    @override_settings(BETA=False)
    def test_unsubscribing_after_the_end_and_subscribing_again_does_not_serve_the_person(self):
        User.objects.filter(pk=self.over.pk).update(status=User.Status.UNSUBSCRIBED)
        User.objects.filter(pk=self.over.pk).update(status=User.Status.ACTIVE)
        self.assertNotIn("over@example.fr", self.mailable(self.NOW))
        with override_settings(BETA=True):  # back to the beta: active again means served again (S13)
            self.assertIn("over@example.fr", self.mailable(self.NOW))
