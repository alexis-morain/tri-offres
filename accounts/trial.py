"""The end of the trial (the design plan, decisions 1 to 5).

The trial is calculated, never a status: `User.trial_ends_at` is set when the questionnaire ends, and
`User.objects.mailable()` is the one gate of the morning mail and of every scoring that costs a call (pre-pass, night,
learned summary, on-demand run): it leaves out an account whose trial is over. `status` keeps its three values and
stays the person's own choice.

While `settings.BETA` is on (the default) the trial does not exist: no date is set and none is read, so switching the
beta off never cuts off the people who joined during it. Still allowed after the end, within their daily caps: the
summary of the questionnaire, the CV analysis and the sorting page (nothing there costs a call per offer).
"""
import datetime as dt

from django.conf import settings

from onboarding import schedule
from onboarding.schedule import PARIS

TRIAL_DAYS = 7


def enabled():
    """Whether the trial exists: not during the beta."""
    return not settings.BETA


def end_for(completed_at):
    """Midnight (Paris time) at the start of the eighth day, the day the questionnaire ends being the first. Counted
    on the local date, never as 7 x 24 hours: a clock change inside the week cannot move the end off midnight."""
    day = completed_at.astimezone(PARIS).date() + dt.timedelta(days=TRIAL_DAYS)
    return dt.datetime.combine(day, dt.time(0), tzinfo=PARIS)


def is_over(user, now):
    """Whether the trial of `user` is over at `now`: false during the beta and without a date."""
    return enabled() and user.trial_ends_at is not None and now >= user.trial_ends_at


def end_day(user):
    """The Paris date of the end, the eighth day and the first one without mail; None without a date."""
    if user.trial_ends_at is None:
        return None
    return user.trial_ends_at.astimezone(PARIS).date()


def end_label(user):
    """The day of the end as the person reads it ("mardi 27 octobre"), the same in the welcome mail, the last morning
    mail and « Mes données »; None without a date. The caller decides whether the trial exists."""
    day = end_day(user)
    return None if day is None else schedule.long_date(day)


def last_day(user):
    """The seventh day, the last one of the trial; None without a date."""
    day = end_day(user)
    return None if day is None else day - dt.timedelta(days=1)


def is_last_mail(user, day):
    """Whether the morning mail of `day` (a date) is the last one the person can get: it leaves before the end and the
    next one, on the next business day, would leave at or after it. False during the beta and without a date. `day`
    must be a business day (envoi checks it): a weekend day before the end would read as the last."""
    if not enabled() or user.trial_ends_at is None:
        return False
    leaves = dt.datetime.combine(day, schedule.SEND_TIME, tzinfo=PARIS)
    following = day + dt.timedelta(days=1)
    while not schedule.is_business_day(following):
        following += dt.timedelta(days=1)
    next_leaves = dt.datetime.combine(following, schedule.SEND_TIME, tzinfo=PARIS)
    return leaves < user.trial_ends_at <= next_leaves
