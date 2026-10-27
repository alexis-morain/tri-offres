"""The on-demand scoring of step 5 (the design plan, 3 bis decisions 1, 8, 9, 14 and 15, and
section 7, E18): up to MAX_OFFERS offers of the last 30 days scored for one person, when their summary shows and
after a change to their search.

request_run() checks the caps and creates the run in one transaction; the run itself goes to a thread started once
that transaction commits (RUNNER), so no request ever waits for Claude. A run still running after STALE_AFTER is
failed and may start again. Logs name a person by their account's key, never by their address.
"""
import json
import logging
import threading
from contextlib import contextmanager
from datetime import timedelta

from django.conf import settings
from django.db import connections, transaction
from django.utils import timezone

import llm
from accounts.models import Profile, SummaryStatus
from jobs import summaries
from jobs.filters import passing_offers
from matching import scoring
from matching.batch import batches
from matching.models import Feedback, Score, ScoringRun
from matching.selection import BROKEN_CRITERIA, candidates, profile_criteria
from onboarding import criteria, preferences, rome
from onboarding.summary import final_text

WINDOW = timedelta(days=30)  # offers collected in this window (decision 1)
MAX_OFFERS = 20
CANDIDATES = 30  # offers picked before the pre-pass, which may route some of them to another domain
STALE_AFTER = ScoringRun.STALE_AFTER
ACCOUNT_WINDOW = timedelta(hours=24)
# Runs per person in ACCOUNT_WINDOW (decision 9): ACCOUNT_RUNS, plus BONUS_RUNS after a change to their search.
ACCOUNT_RUNS = 1
BONUS_RUNS = 1
STARTED, RUNNING, ACCOUNT_CAP, SITE_CAP, NOT_READY = "started", "running", "account_cap", "site_cap", "not_ready"

logger = logging.getLogger(__name__)
State, Error = ScoringRun.State, ScoringRun.Error


def latest_run(user):
    """The person's latest on-demand run, None if there is none; a run running for more than STALE_AFTER is failed
    first (stale)."""
    runs = ScoringRun.objects.filter(user=user, kind=ScoringRun.Kind.ON_DEMAND)
    runs.fail_stale(timezone.now())
    return runs.order_by("-started_at", "-pk").first()


def has_run(user):
    """Whether the person ever had an on-demand run."""
    return ScoringRun.objects.filter(user=user, kind=ScoringRun.Kind.ON_DEMAND).exists()


def _ready(profile):
    """Whether there is something to score against: a summary, preferences or the person's own words."""
    return bool(final_text(profile) or not preferences.is_empty(profile.weighted_preferences)
                or profile.seek_text.strip() or profile.refuse_text.strip())


def request_run(user, *, after_change=False):
    """Start an on-demand run for `user` unless a cap or the profile says otherwise. Returns STARTED, RUNNING,
    ACCOUNT_CAP, SITE_CAP or NOT_READY.
    `after_change`: the person changed their search, which allows BONUS_RUNS more in ACCOUNT_WINDOW
    and drops their scores neither sent nor judged once the run starts (decision 14)."""
    with transaction.atomic():
        now = timezone.now()
        profile = Profile.objects.filter(user=user).first()
        if profile is None or not _ready(profile):
            return NOT_READY
        try:
            profile_criteria(profile)
        except BROKEN_CRITERIA:
            return NOT_READY
        latest = latest_run(user)
        if latest is not None and latest.state == State.RUNNING:
            return RUNNING
        recent = ScoringRun.objects.filter(kind=ScoringRun.Kind.ON_DEMAND, started_at__gte=now - ACCOUNT_WINDOW)
        done = recent.filter(user=user, state__in=[State.DONE, State.PARTIAL]).count()
        if done >= ACCOUNT_RUNS + (BONUS_RUNS if after_change else 0):
            return ACCOUNT_CAP
        if recent.exclude(state=State.REFUSED).count() >= settings.ON_DEMAND_DAILY_CAP:
            ScoringRun.objects.create(user=user, kind=ScoringRun.Kind.ON_DEMAND, state=State.REFUSED, error=Error.CAP,
                                      started_at=now, finished_at=now)
            logger.error("on_demand: ON_DEMAND_DAILY_CAP reached (%s per 24 h), account %s not scored",
                         settings.ON_DEMAND_DAILY_CAP, user.pk)
            return SITE_CAP
        if after_change:
            judged = Feedback.objects.filter(user=user).values("offer_id")
            Score.objects.filter(user=user, sent_on__isnull=True).exclude(offer_id__in=judged).delete()
        run = ScoringRun.objects.create(user=user, kind=ScoringRun.Kind.ON_DEMAND, started_at=now)
        transaction.on_commit(lambda: RUNNER(run.pk))
        return STARTED


def _run_batches(run, items, size, call):
    """`call(batch)` for each batch of `items`, one after the other, without retry (decision 15): an LLMError fails
    its batch only, what it leaves stays for the night; a refusal of the person's key (EngineKeyError) stops the run,
    the next batches would be refused the same. Then the final state of `run`; an unexpected error fails it
    (crash) and goes up."""
    failed = total = 0
    errors = set()
    try:
        for batch in batches(items, size):
            total += 1
            try:
                call(batch)
            except llm.LLMError as error:
                failed += 1
                errors.add(Error.of(error))
                if isinstance(error, llm.EngineKeyError):
                    break
    except Exception:
        run.finish(State.FAILED, Error.CRASH)
        raise
    run.finish_from(failed, total, errors)


def _prepass(offers, now):
    """Summaries of `offers` that have a full text and none yet, in shared calls whose tokens go to a summaries run
    (E18). A failed batch leaves its offers without summary; scoring goes on."""
    pending = [offer for offer in offers if offer.summary_written_at is None and offer.full_text]
    if pending:
        run = ScoringRun.objects.create(kind=ScoringRun.Kind.SUMMARIES, offers_requested=len(pending),
                                        started_at=now)
        _run_batches(run, pending, summaries.BATCH_SIZE, lambda batch: summaries.summarize_batch(batch, now, run))


def _score(run, now):
    profile = run.user.profile
    picked = candidates(profile, now, WINDOW, CANDIDATES)
    _prepass(picked, now)
    # Read again with the pre-pass's summaries; a ROME code it wrote may route an offer away from this person.
    pks = [offer.pk for offer in picked]
    passing = {offer.pk: offer for offer in passing_offers(profile_criteria(profile), now, profile.domains,
                                                           since=now - WINDOW)
               .filter(pk__in=pks).select_related("source")}
    offers = [passing[pk] for pk in pks if pk in passing][:MAX_OFFERS]
    run.offers_requested = len(offers)
    run.save_if_exists(["offers_requested"])
    _run_batches(run, offers, scoring.BATCH_SIZE, lambda batch: scoring.score_batch(run.user, batch, run, llm.BATCH))


def execute(run_id):
    """The run `run_id`, to its end: never raises, never leaves it running."""
    try:
        run = ScoringRun.objects.select_related("user__profile").get(pk=run_id)
    except ScoringRun.DoesNotExist:  # the account was deleted after the run was requested (E16): nothing to do
        logger.warning("on_demand: run %s is gone, its account was deleted", run_id)
        return
    now = timezone.now()
    try:
        _score(run, now)
    except Exception as error:  # noqa: BLE001 - the run must end, whatever happened
        # The class and the keys only: the message of an unexpected error may hold an offer's text.
        logger.error("on_demand: unexpected %s in run %s (account %s)", type(error).__name__, run.pk, run.user_id)
        run.finish(State.FAILED, Error.CRASH)


def _thread(run_id):
    try:
        execute(run_id)
    finally:
        connections.close_all()


def start(run_id):
    """The run in a daemon thread, outside the request: returns the thread."""
    thread = threading.Thread(target=_thread, args=(run_id,), daemon=True)
    thread.start()
    return thread


# What request_run() calls once its transaction commits; the tests swap it for execute().
RUNNER = start


def _normalized(normalize, value):
    """`value` as the questionnaire reads it, so a stored spelling is never taken for a change; as it is when it
    does not fit its schema (the normalize functions raise ValueError). JSON text, so dicts compare whatever their
    key order."""
    try:
        value = normalize(value)
    except ValueError:
        pass
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def search_snapshot(profile):
    """What the scores depend on, comparable with ==: domains, criteria, preferences, own words and summary."""
    return (_normalized(rome.normalize_domains, profile.domains),
            _normalized(criteria.normalize, profile.hard_criteria),
            _normalized(preferences.normalize, profile.weighted_preferences), profile.seek_text, profile.refuse_text,
            final_text(profile))


def search_changed(user, before):
    """After an edit: when the search differs from `before` and the person already had an on-demand run, the bonus
    run (request_run with after_change). Scores are dropped only if it starts: old scores beat none. Nothing while
    the summary is stale: the bonus run waits for its rewriting (summary_write), so it never scores against the old
    summary."""
    profile = Profile.objects.filter(user=user).first()
    if profile is None or profile.summary_status == SummaryStatus.STALE or search_snapshot(profile) == before:
        return
    if has_run(user):
        request_run(user, after_change=True)


@contextmanager
def watch(user, profile):
    """Around an edit of `profile`: its snapshot before, search_changed() after. An error of the scoring never fails
    the page (logged by its class and the account's key), the snapshot included; an error of the edit goes up, with
    no run."""
    try:
        before = search_snapshot(profile)
    except Exception as error:  # noqa: BLE001 - a profile the snapshot cannot read still saves its edit
        logger.error("on_demand: search of account %s not read (%s)", user.pk, type(error).__name__)
        before = None
    yield
    if before is None:
        return
    try:
        search_changed(user, before)
    except Exception as error:  # noqa: BLE001 - the page matters more than the scores
        logger.error("on_demand: bonus run of account %s not requested (%s)", user.pk, type(error).__name__)
