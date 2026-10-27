"""The single reminder (the design plan 4.8): a questionnaire started and left the day before gets
one mail at 18:00 with a new sign-in code and link, never a second one.

The code is issued by accounts.auth.issue_code, under the sign-in rules unchanged: the same hashes and lifetime,
the same per-address limits and site cap, and the new code replaces the earlier ones only once its mail went out.
Only the mail differs: "Il vous reste N minutes" instead of the plain sign-in mail.
"""
import datetime as dt
import logging

from django.conf import settings
from django.db.models import Exists, Max, OuterRef, Q

import mail
from accounts import auth, events, unsubscribe
from accounts.models import OnboardingEvent, Profile, User
from onboarding import flow, schedule

logger = logging.getLogger(__name__)

# Signed in counts as started, even before the first screen is validated.
SIGNED_IN = (events.CODE_ENTERED, events.LINK_USED)


def _paris_day_bounds(day):
    start = dt.datetime.combine(day, dt.time(0), tzinfo=schedule.PARIS)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), tzinfo=schedule.PARIS)
    return start, end


def due(now):
    """Profiles to remind at `now`: mailable account (active, not unsubscribed), questionnaire started (a screen
    validated, or signed in) and not finished, last event dated the day before (Paris time), never reminded."""
    start, end = _paris_day_bounds(now.astimezone(schedule.PARIS).date() - dt.timedelta(days=1))
    signed_in = OnboardingEvent.objects.filter(user=OuterRef("user"), name__in=SIGNED_IN)
    candidates = (Profile.objects.select_related("user")
                  .filter(user__in=User.objects.mailable(), onboarding_completed_at=None)
                  .filter(~Q(onboarding_step="") | Q(Exists(signed_in)))
                  .exclude(user__onboarding_events__name=events.REMINDER_SENT)
                  .annotate(last_event=Max("user__onboarding_events__created_at"))
                  .filter(last_event__gte=start, last_event__lt=end).order_by("pk"))
    # Every screen validated without the end screen: nothing left to remind of.
    return [profile for profile in candidates if flow.next_step(profile) is not None]


def minutes_left(profile):
    return flow.minutes_left(flow.next_step(profile).slug, profile)


def remind(profile):
    """Issue a code and send the reminder. True when the mail went out; False when the sign-in limits refused the
    code or the mail did not go out (no reminder_sent then, and the day after, the person no longer qualifies)."""
    email = profile.user.email
    minutes = minutes_left(profile)

    def send(code, link_path):
        # Last look before the mail leaves (E10): the person may have unsubscribed or left since due().
        if not User.objects.mailable().filter(pk=profile.user.pk).exists():
            logger.info("Reminder of user %s not sent: no longer mailable.", profile.user.pk)
            return 0
        return mail.send("reminder", email, {
            "code": auth.group_code(code), "link": settings.SITE_URL + link_path,
            "code_minutes": auth.CODE_TTL_MINUTES, "minutes": minutes, "home_url": settings.SITE_URL + "/",
            "unsubscribe_url": unsubscribe.unsubscribe_url(profile.user),
        }, headers=unsubscribe.mail_headers(profile.user))

    # No IP: the per-address limits and the site cap still apply.
    outcome = auth.issue_code(email, None, send)
    if outcome.throttled:
        logger.info("Reminder of user %s not sent: sign-in limits reached.", profile.user.pk)
    if outcome.sent:
        OnboardingEvent.objects.create(user=profile.user, name=events.REMINDER_SENT)
    return outcome.sent
