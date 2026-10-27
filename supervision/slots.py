"""The steps of each slot, in one table. `nuit` runs at 04:00, `matin` at 07:00, `soir` at 18:00 (step 10)."""
import datetime as dt
from dataclasses import dataclass

from onboarding import schedule
from supervision import locks
from supervision.models import TaskRun

SATURDAY = 5


@dataclass(frozen=True)
class Step:
    name: str
    business_only: bool = False  # skipped on a day without a mail
    saturday_only: bool = False  # absent from the list on other days
    dry_run: str = "pass"  # "pass": gets --dry-run, "skip": skipped in a dry run
    takes_date: bool = False  # gets --date when the slot was given one


NIGHT = [
    # Skipped in a dry run: `collecte --dry-run` still runs the actor, which marks the offers as seen.
    Step("collecte", business_only=True, dry_run="skip"),
    Step("notation", business_only=True),
    Step("purge_offres"),
    Step("purge_login_codes"),
    Step("purge_journal"),
    Step("clearsessions", dry_run="skip"),
    Step("purge_taskruns", dry_run="skip"),
    Step("synthese_apprise", saturday_only=True),
]

SLOTS = {
    "nuit": NIGHT,
    "matin": [Step("envoi", business_only=True, takes_date=True)],
    "soir": [Step("relance_onboarding")],
}


def steps_for(slot, day: dt.date):
    """The steps of a slot for a Paris day, as (Step, skipped_because_not_a_business_day) pairs."""
    business = schedule.is_business_day(day)
    return [(step, step.business_only and not business) for step in SLOTS[slot]
            if not step.saturday_only or day.weekday() == SATURDAY]


@dataclass(frozen=True)
class Window:
    start: dt.time  # Paris time
    until: dt.time  # the last instant a late start is still worth it
    max_duration: dt.timedelta  # the scheduler kills a pass that lasts longer
    business_only: bool = False


WINDOWS = {
    "nuit": Window(dt.time(4), dt.time(6, 30), dt.timedelta(hours=2, minutes=50)),
    "matin": Window(dt.time(7), dt.time(12), dt.timedelta(hours=1), business_only=True),
    "soir": Window(dt.time(18), dt.time(21), dt.timedelta(minutes=30)),
}


def due(now):
    """The slots to start at `now` (aware), in table order: right day, hour reached, window still open, no ok,
    failed or interrupted pass for the Paris day, lock free."""
    local = now.astimezone(schedule.PARIS)
    day, clock = local.date(), local.time()
    business = schedule.is_business_day(day)
    result = []
    for slot, window in WINDOWS.items():
        if window.business_only and not business:
            continue
        if not window.start <= clock <= window.until:
            continue
        if TaskRun.objects.filter(slot=slot, day=day, status__in=["ok", "failed", "interrupted"]).exists():
            continue
        if locks.is_locked(slot):
            continue
        result.append(slot)
    return result
