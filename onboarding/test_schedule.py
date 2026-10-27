import datetime as dt
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from onboarding.schedule import (
    describe_delivery,
    easter,
    first_delivery,
    french_holidays,
    is_business_day,
    stamp_text,
)

PARIS = ZoneInfo("Europe/Paris")


def paris(year, month, day, hour=12, minute=0):
    return dt.datetime(year, month, day, hour, minute, tzinfo=PARIS)


class FirstDeliveryTests(SimpleTestCase):
    def check(self, now, expected_day):
        result = first_delivery(now)
        self.assertEqual(result, paris(*expected_day, 7, 0))
        self.assertEqual(result.tzinfo, PARIS)

    def test_friday_evening_goes_to_monday(self):
        self.assertEqual(dt.date(2026, 10, 9).weekday(), 4)
        self.check(paris(2026, 10, 9, 20), (2026, 10, 12))

    def test_weekend_goes_to_monday(self):
        self.assertEqual(dt.date(2026, 10, 10).weekday(), 5)
        self.check(paris(2026, 10, 10, 10), (2026, 10, 12))
        self.check(paris(2026, 10, 11, 10), (2026, 10, 12))

    def test_holiday_then_weekend(self):
        self.assertEqual(dt.date(2026, 12, 24).weekday(), 3)
        self.check(paris(2026, 12, 24, 20), (2026, 12, 28))

    def test_cutoff(self):
        self.assertEqual(dt.date(2026, 10, 13).weekday(), 1)
        self.check(paris(2026, 10, 13, 3, 29), (2026, 10, 13))
        self.check(paris(2026, 10, 13, 3, 31), (2026, 10, 14))

    def test_early_morning_on_a_weekend_waits_for_monday(self):
        self.check(paris(2026, 10, 10, 1), (2026, 10, 12))

    def test_early_morning_on_a_holiday_waits(self):
        self.assertEqual(dt.date(2026, 7, 14).weekday(), 1)
        self.check(paris(2026, 7, 14, 1), (2026, 7, 15))

    def test_eve_of_a_holiday_skips_it(self):
        self.assertEqual(dt.date(2026, 7, 13).weekday(), 0)
        self.check(paris(2026, 7, 13, 10), (2026, 7, 15))

    def test_new_year(self):
        self.assertEqual(dt.date(2026, 12, 31).weekday(), 3)
        self.check(paris(2026, 12, 31, 20), (2027, 1, 4))

    def test_dst_end(self):
        self.assertEqual(dt.date(2026, 10, 25).weekday(), 6)
        result = first_delivery(paris(2026, 10, 25, 10))
        self.assertEqual(result, paris(2026, 10, 26, 7))
        self.assertEqual(result.utcoffset(), dt.timedelta(hours=1))

    def test_dst_start(self):
        self.assertEqual(dt.date(2026, 3, 29).weekday(), 6)
        result = first_delivery(paris(2026, 3, 29, 10))
        self.assertEqual(result, paris(2026, 3, 30, 7))
        self.assertEqual(result.utcoffset(), dt.timedelta(hours=2))

    def test_utc_now_is_converted_to_paris(self):
        utc = dt.timezone.utc
        # Summer time: 01:45 UTC is 03:45 in Paris, past the cutoff.
        self.check(dt.datetime(2026, 10, 13, 1, 45, tzinfo=utc), (2026, 10, 14))
        # 01:15 UTC is 03:15 in Paris, before the cutoff.
        self.check(dt.datetime(2026, 10, 13, 1, 15, tzinfo=utc), (2026, 10, 13))

    def test_utc_evening_crossing_midnight_in_paris(self):
        # Sunday 22:30 UTC is Monday 00:30 in Paris (summer time): same Monday morning.
        self.check(dt.datetime(2026, 10, 11, 22, 30, tzinfo=dt.timezone.utc), (2026, 10, 12))


class HolidayTests(SimpleTestCase):
    def test_easter(self):
        self.assertEqual(easter(2026), dt.date(2026, 4, 5))
        self.assertEqual(easter(2027), dt.date(2027, 3, 28))

    def test_movable_holidays_2026(self):
        holidays = french_holidays(2026)
        self.assertIn(dt.date(2026, 4, 6), holidays)
        self.assertIn(dt.date(2026, 5, 14), holidays)
        self.assertIn(dt.date(2026, 5, 25), holidays)

    def test_fixed_holidays(self):
        holidays = french_holidays(2026)
        for month, day in [(1, 1), (5, 1), (5, 8), (7, 14), (8, 15), (11, 1), (11, 11), (12, 25)]:
            self.assertIn(dt.date(2026, month, day), holidays)
        self.assertEqual(len(holidays), 11)

    def test_is_business_day(self):
        self.assertTrue(is_business_day(dt.date(2026, 10, 12)))
        self.assertFalse(is_business_day(dt.date(2026, 10, 10)))
        self.assertFalse(is_business_day(dt.date(2026, 5, 25)))


class WordingTests(SimpleTestCase):
    def test_today(self):
        now = paris(2026, 10, 14, 2)
        self.assertEqual(describe_delivery(paris(2026, 10, 14, 7), now), "aujourd’hui, mercredi 14 octobre")

    def test_tomorrow(self):
        now = paris(2026, 10, 13, 20)
        self.assertEqual(describe_delivery(paris(2026, 10, 14, 7), now), "demain, mercredi 14 octobre")

    def test_other_day(self):
        now = paris(2026, 10, 9, 20)
        self.assertEqual(describe_delivery(paris(2026, 10, 12, 7), now), "lundi 12 octobre")

    def test_first_of_month(self):
        self.assertEqual(dt.date(2026, 4, 1).weekday(), 2)
        now = paris(2026, 3, 25, 20)
        self.assertEqual(describe_delivery(paris(2026, 4, 1, 7), now), "mercredi 1er avril")

    def test_today_uses_paris_date(self):
        now = dt.datetime(2026, 10, 13, 22, 30, tzinfo=dt.timezone.utc)  # 00:30 on the 14th in Paris
        self.assertEqual(describe_delivery(paris(2026, 10, 14, 7), now), "aujourd’hui, mercredi 14 octobre")

    def test_stamp(self):
        self.assertEqual(stamp_text(paris(2026, 10, 13, 7)), "MAR. 13 OCT. 2026")

    def test_stamp_february(self):
        self.assertEqual(dt.date(2027, 2, 2).weekday(), 1)
        self.assertEqual(stamp_text(paris(2027, 2, 2, 7)), "MAR. 2 FÉVR. 2027")
