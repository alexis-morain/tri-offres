"""First delivery date of the daily mail, and its French wording for the end screen.

Mails leave at 07:00 Paris time on business days (Monday to Friday, French public holidays excluded).
Scoring runs overnight and ends before 03:30: a signup before that cutoff on a business day is served the same
morning, any later one waits for the next business day.

Day and month names are written here, so the output never depends on the server locale.
"""

import datetime as dt
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
SEND_TIME = dt.time(7, 0)
CUTOFF = dt.time(3, 30)

DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre"]
DAYS_SHORT = ["LUN.", "MAR.", "MER.", "JEU.", "VEN.", "SAM.", "DIM."]
MONTHS_SHORT = ["JANV.", "FÉVR.", "MARS", "AVR.", "MAI", "JUIN", "JUIL.", "AOÛT", "SEPT.", "OCT.", "NOV.", "DÉC."]


def easter(year):
    """Easter Sunday (anonymous Gregorian algorithm, Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month, day = divmod(h + ll - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def french_holidays(year):
    """The public holidays of a year, as a set of dates."""
    paques = easter(year)
    return {
        dt.date(year, 1, 1),
        paques + dt.timedelta(days=1),
        dt.date(year, 5, 1),
        dt.date(year, 5, 8),
        paques + dt.timedelta(days=39),
        paques + dt.timedelta(days=50),
        dt.date(year, 7, 14),
        dt.date(year, 8, 15),
        dt.date(year, 11, 1),
        dt.date(year, 11, 11),
        dt.date(year, 12, 25),
    }


def is_business_day(day):
    return day.weekday() < 5 and day not in french_holidays(day.year)


def first_delivery(now):
    """07:00 Paris time on the first business day the mail can leave; `now` must be timezone-aware."""
    local = now.astimezone(PARIS)
    day = local.date()
    if not (is_business_day(day) and local.time() < CUTOFF):
        day += dt.timedelta(days=1)
        while not is_business_day(day):
            day += dt.timedelta(days=1)
    return dt.datetime.combine(day, SEND_TIME, tzinfo=PARIS)


def long_date(day):
    """"mardi 14 octobre", "dimanche 1er novembre"."""
    number = "1er" if day.day == 1 else str(day.day)
    return f"{DAYS[day.weekday()]} {number} {MONTHS[day.month - 1]}"


def describe_delivery(delivery, now):
    """"aujourd’hui, mardi 14 octobre", "demain, ..." or "lundi 13 octobre"."""
    day = delivery.astimezone(PARIS).date()
    today = now.astimezone(PARIS).date()
    if day == today:
        return f"aujourd’hui, {long_date(day)}"
    if day == today + dt.timedelta(days=1):
        return f"demain, {long_date(day)}"
    return long_date(day)


def stamp_text(delivery):
    """Rubber-stamp form: "MAR. 14 OCT. 2026"."""
    day = delivery.astimezone(PARIS).date()
    return f"{DAYS_SHORT[day.weekday()]} {day.day} {MONTHS_SHORT[day.month - 1]} {day.year}"
