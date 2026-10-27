"""Figures of the admin dashboard (the design plan): counts only, never an address, a name or
a text written by a person or by a third party (S6). Pure on `now`, so the page only renders them."""
import datetime as dt
from datetime import timedelta

from django.db.models import Q

from accounts.models import User
from jobs.models import Source
from matching.models import Feedback
from onboarding.schedule import PARIS
from supervision.models import TaskRun

ACTIVE_DAYS = 7
CHART_DAYS = 14
RUNS_SHOWN = 14
# Shown instead of the name of a source a person added: that name is free text.
PRIVATE_SOURCE = "Source ajoutée par une personne"


def team_excluded(users):
    return users.filter(is_staff=False, is_superuser=False)


def _active_count(subscribers, now):
    """Subscribers with a feedback that is not cancelled, or a sign-in, in the last 7 days (rolling). The sign-in is
    the only visit seen: mail opens and clicks are not tracked."""
    since = now - timedelta(days=ACTIVE_DAYS)
    swiped = Feedback.objects.filter(cancelled=False, created_at__gte=since).values("user_id")
    return subscribers.filter(Q(last_login__gte=since) | Q(pk__in=swiped)).count()


def _days(now):
    """Feedback per Paris day over the last 14 days, today first: the rows not cancelled and not the team's, and the
    number of people behind them. Empty days stay in."""
    today = now.astimezone(PARIS).date()
    first = today - timedelta(days=CHART_DAYS - 1)
    start = dt.datetime.combine(first, dt.time.min, tzinfo=PARIS)
    rows = (Feedback.objects.filter(cancelled=False, created_at__gte=start, user__is_staff=False,
                                    user__is_superuser=False).values_list("user_id", "created_at"))
    swipes, people = {}, {}
    for user_id, created_at in rows:
        day = created_at.astimezone(PARIS).date()
        swipes[day] = swipes.get(day, 0) + 1
        people.setdefault(day, set()).add(user_id)
    days = (today - timedelta(days=offset) for offset in range(CHART_DAYS))
    return [{"day": day, "feedback": swipes.get(day, 0), "people": len(people.get(day, ()))} for day in days]


def _failing_sources():
    sources = Source.objects.filter(state=Source.State.FAILING).order_by("name", "pk")
    return [{"pk": source.pk, "name": PRIVATE_SOURCE if source.owner_id else source.name,
             "actor_source": source.actor_source, "empty_runs": source.empty_runs, "last_run_at": source.last_run_at}
            for source in sources]


def _runs():
    """The latest passes of the night command. Only the names of the steps that failed: their error lines can carry
    text from third-party sites."""
    return [{"pk": run.pk, "slot": run.get_slot_display(), "day": run.day, "status": run.status,
             "status_label": run.get_status_display(), "duration": run.duration,
             "failed_steps": [step["name"] for step in run.steps if step.get("status") == "failed"]}
            for run in TaskRun.objects.order_by("-started_at", "-pk")[:RUNS_SHOWN]]


def build(now):
    people = team_excluded(User.objects.all())
    subscribers = team_excluded(User.objects.mailable())
    subscriber_count = subscribers.count()
    active = _active_count(subscribers, now)
    return {
        "signed_up": people.count(),
        "finished": people.filter(profile__onboarding_completed_at__isnull=False).count(),
        "subscribers": subscriber_count,
        "unsubscribed": people.filter(status=User.Status.UNSUBSCRIBED).count(),
        "active": active,
        "active_percent": round(100 * active / subscriber_count) if subscriber_count else None,
        "days": _days(now),
        "failing_sources": _failing_sources(),
        "runs": _runs(),
    }
