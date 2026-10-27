"""The night of step 5 (the design plan, 3 bis decisions 6, 11, 13, 15 and 16, section 7, E16
and E18): the pre-pass on the new offers, then the scoring of each active person, run by `manage.py notation`.

Every database write stays in the main thread; only the calls to Claude go to a pool of PARALLEL_BATCHES threads
(SQLite: no write from the threads; the shared-call guard of oauth reads the accounts from a thread). A batch that
fails is tried again once after RETRY_DELAY, unless the engine refused it or the error was unexpected; it then stays
without summary or score, and the next night takes it again. Logs and failures name a person by their account's
key, never by their address.
"""
import logging
import math
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, NamedTuple

from django.conf import settings
from django.db import connections, transaction
from django.db.models import Sum

import llm
from jobs import rome_titles, summaries
from matching import scoring
from matching.batch import batches
from matching.models import ScoringRun
from matching.selection import (BROKEN_CRITERIA, NIGHT_PER_PERSON, NIGHT_WINDOW, active_profiles, candidates,
                                offers_to_summarize)
from onboarding.criteria import format_number

PARALLEL_BATCHES = 4
RETRY_DELAY = 120  # seconds before the one retry of a failed batch (decision 15)
BUDGET_WINDOW = timedelta(hours=20)  # the night runs started in this sliding window count in the site budget
SUMMARIES, PEOPLE = "resumes", "personnes"
PHASES = (SUMMARIES, PEOPLE)
CRITERIA = "criteria"  # a person skipped because their criteria do not read
CRASH = ScoringRun.Error.CRASH  # a person skipped because preparing their batches failed
SKIP_REASONS = {CRITERIA: "critères illisibles", CRASH: "erreur imprévue"}

logger = logging.getLogger(__name__)


@dataclass
class RunReport:
    """One run of the night: the pre-pass or one person. Counts only."""

    label: str  # « résumés » or « compte <key> », for the printed report only
    account: int | None = None  # key of the person's account, None for the pre-pass
    requested: int = 0  # offers asked for
    batches: int = 0
    written: int = 0  # summaries or scores written
    codes: int = 0  # ROME codes written by the pre-pass
    rome: Counter = field(default_factory=Counter)  # {jobs.rome_titles outcome: answers} of the pre-pass
    refused: int = 0  # summaries or reasons refused by matching.text.clean_text
    missing: int = 0  # offers absent from Claude's answers
    failed_batches: int = 0  # still failed after the retry, refused by the engine, or crashed
    errors: set = field(default_factory=set)  # ScoringRun.Error codes of the failed batches
    state: str = ""
    tokens: dict[str, int] = field(default_factory=dict)  # {ScoringRun usage field: count}

    @property
    def engine_refused(self):
        return ScoringRun.Error.REFUSED in self.errors

    @property
    def crashed(self):
        """An unexpected error in a call or while writing."""
        return ScoringRun.Error.CRASH in self.errors

    @property
    def name(self):
        """How failures and logs name the run: never an address."""
        return "résumés" if self.account is None else f"compte {self.account}"


@dataclass
class NightReport:
    dry_run: bool
    stale: int = 0  # runs failed as stale before the night (their thread or command died)
    summaries: RunReport | None = None  # None when the phase did not run
    summaries_cut: int = 0  # offers left out by PREPASS_NIGHT_CAP
    people: list[RunReport] = field(default_factory=list)  # each person with at least one offer
    skipped: list["Skipped"] = field(default_factory=list)  # people left out before any run
    budget: int | None = None  # offers the site may still score tonight, None when the phase did not run
    people_cut_offers: int = 0  # offers left out by NIGHT_SCORE_CAP
    people_cut_persons: int = 0

    def failures(self):
        """One French line per run with a failed batch, and per skipped person."""
        lines = []
        for report in ([self.summaries] if self.summaries else []) + self.people:
            if report.engine_refused:
                lines.append(f"{report.name} : refusé par le moteur")
            elif report.failed_batches:
                line = f"{report.name} : {_count(report.failed_batches, 'lot')} en échec"
                lines.append(line + (" (erreur imprévue)" if report.crashed else ""))
        lines += [f"compte {skipped.account} : {SKIP_REASONS[skipped.reason]}, personne sautée"
                  for skipped in self.skipped]
        return lines


class Skipped(NamedTuple):
    account: int
    reason: str  # CRITERIA or CRASH


class Counts(NamedTuple):
    """What applying one batch wrote."""

    written: int
    codes: int
    refused: int
    missing: int
    rome: Counter | None = None  # the pre-pass only


class Attempt(NamedTuple):
    output: Any  # None when the batch failed
    usages: list[llm.Usage]  # every call made, failed ones included
    error: Exception | None


class Job(NamedTuple):
    prepared: Any
    call: Callable[[Any], tuple[dict, llm.Usage]]  # run in the pool
    apply: Callable[[Any, dict], Counts]  # run in the main thread
    run: ScoringRun
    report: RunReport


def _attempt(call, prepared, sleep):
    """In a pool thread: the call, and its one retry after RETRY_DELAY for an LLMError other than EngineRefused.
    Never raises; closes any connection the engine opened."""
    usages = []
    try:
        for attempt in (1, 2):
            try:
                output, usage = call(prepared)
            except llm.EngineRefused as error:
                return Attempt(None, usages, error)
            except llm.LLMError as error:
                if getattr(error, "usage", None) is not None:
                    usages.append(error.usage)
                if attempt == 2:
                    return Attempt(None, usages, error)
                sleep(RETRY_DELAY)
            except Exception as error:  # noqa: BLE001 - one batch never stops the night
                return Attempt(None, usages, error)
            else:
                usages.append(usage)
                return Attempt(output, usages, None)
    finally:
        connections.close_all()


def _failed(job, error):
    report = job.report
    report.failed_batches += 1
    code = ScoringRun.Error.of(error)
    report.errors.add(code)
    if code == ScoringRun.Error.CRASH:
        # The class and the keys only: the message of an unexpected error may hold an offer's text.
        logger.error("notation: unexpected %s in a batch of run %s (%s)", type(error).__name__, job.run.pk,
                     report.name)


def _run_jobs(jobs, sleep):
    """Calls in the pool; usage, results and failures written here, in the main thread, as they come."""
    if not jobs:
        return
    with ThreadPoolExecutor(max_workers=PARALLEL_BATCHES) as pool:
        futures = {pool.submit(_attempt, job.call, job.prepared, sleep): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            attempt = future.result()
            for usage in attempt.usages:
                job.run.add_usage(usage)
            error = attempt.error
            if error is None:
                try:
                    with transaction.atomic():  # a batch is written whole or not at all
                        counts = job.apply(job.prepared, attempt.output)
                except Exception as exc:  # noqa: BLE001 - one batch never stops the night
                    error = exc
                else:
                    job.run.offers_scored += counts.written
                    report = job.report
                    report.written += counts.written
                    report.codes += counts.codes
                    report.refused += counts.refused
                    report.missing += counts.missing
                    report.rome.update(counts.rome or {})
            if error is not None:
                _failed(job, error)
            job.run.save_if_exists([*ScoringRun.USAGE_FIELDS, "offers_scored"])


def _new_report(label, account, offers, batch_size):
    return RunReport(label, account, requested=len(offers), batches=math.ceil(len(offers) / batch_size))


def _open_run(report, kind, user, offers, batch_size, prepare, call, apply, now):
    """The jobs of the batches of `report`, all prepared here, in the main thread, before its ScoringRun is created:
    a preparation that raises leaves no run behind."""
    prepared = [prepare(batch) for batch in batches(offers, batch_size)]
    run = ScoringRun.objects.create(kind=kind, user=user, offers_requested=len(offers), started_at=now)
    return run, [Job(item, call, apply, run, report) for item in prepared]


def _finish(run, report):
    """Final state of `run` from its report, copied back into the report."""
    run.finish_from(report.failed_batches, report.batches, report.errors)
    report.state = run.get_state_display()
    report.tokens = {name: getattr(run, name) for name in ScoringRun.USAGE_FIELDS}


def _cap_reached(dry_run, message, *counts):
    """One error per cap and per night; none in a dry run, which only counts."""
    if not dry_run:
        logger.error("notation: " + message, *counts)


def _profiles(profiles, now):
    """The profiles active at `now`; only those among `profiles` when given (manage.py essai_notation)."""
    active = active_profiles(now)
    return active if profiles is None else active.filter(pk__in=[profile.pk for profile in profiles])


def _summaries_phase(now, profiles, dry_run, sleep, night_report):
    offers = list(offers_to_summarize(now, _profiles(profiles, now)))
    night_report.summaries_cut = max(len(offers) - settings.PREPASS_NIGHT_CAP, 0)
    offers = offers[:settings.PREPASS_NIGHT_CAP]
    if night_report.summaries_cut:
        _cap_reached(dry_run, "PREPASS_NIGHT_CAP reached, %s offer(s) left without summary",
                     night_report.summaries_cut)
    report = night_report.summaries = _new_report("résumés", None, offers, summaries.BATCH_SIZE)
    if dry_run or not offers:
        return

    def apply(prepared, output):
        result = summaries.apply_batch(prepared, output, now)
        return Counts(result.written, result.codes_written, result.refused, len(result.missing), result.rome)

    run, jobs = _open_run(report, ScoringRun.Kind.SUMMARIES, None, offers, summaries.BATCH_SIZE,
                          summaries.prepare_batch, summaries.call_batch, apply, now)
    _run_jobs(jobs, sleep)
    _finish(run, report)


def _site_budget(now):
    started = (ScoringRun.objects.filter(kind=ScoringRun.Kind.NIGHT, started_at__gte=now - BUDGET_WINDOW)
               .aggregate(total=Sum("offers_requested"))["total"] or 0)
    return max(settings.NIGHT_SCORE_CAP - started, 0)


def _apply_scores(prepared, output):
    outcome = scoring.apply_batch(prepared, output)
    return Counts(outcome.created, 0, outcome.refused, len(outcome.missing))


def _people_phase(now, email, profiles, dry_run, sleep, night_report):
    profiles = _profiles(profiles, now).order_by("pk")
    if email is not None:
        profiles = profiles.filter(user__email__iexact=email)
    budget = night_report.budget = _site_budget(now)
    selected = []
    for profile in profiles:
        try:
            offers = candidates(profile, now, NIGHT_WINDOW, NIGHT_PER_PERSON)
        except BROKEN_CRITERIA as error:
            night_report.skipped.append(Skipped(profile.user_id, CRITERIA))
            logger.error("notation: account %s skipped, its criteria do not read (%s)", profile.user_id,
                         type(error).__name__)
            continue
        kept = offers[:budget]
        budget -= len(kept)
        if len(kept) < len(offers):
            night_report.people_cut_offers += len(offers) - len(kept)
            night_report.people_cut_persons += 1
        if kept:
            selected.append((profile, kept))
    if night_report.people_cut_offers:
        _cap_reached(dry_run, "NIGHT_SCORE_CAP reached, %s offer(s) of %s person(s) left without score",
                     night_report.people_cut_offers, night_report.people_cut_persons)
    jobs, runs = [], []
    for profile, offers in selected:
        report = _new_report(f"compte {profile.user_id}", profile.user_id, offers, scoring.BATCH_SIZE)
        if dry_run:
            night_report.people.append(report)
            continue

        def prepare(batch, user=profile.user):
            return scoring.prepare_batch(user, batch, llm.BATCH)

        try:
            run, person_jobs = _open_run(report, ScoringRun.Kind.NIGHT, profile.user, offers, scoring.BATCH_SIZE,
                                         prepare, scoring.call_batch, _apply_scores, now)
        except Exception as error:  # noqa: BLE001 - one person never stops the night
            night_report.skipped.append(Skipped(profile.user_id, CRASH))
            # The class and the key only: the message may hold the person's data.
            logger.error("notation: account %s skipped, unexpected %s while preparing its batches",
                         profile.user_id, type(error).__name__)
            continue
        night_report.people.append(report)
        runs.append((run, report))
        jobs += person_jobs
    # The batches of every person share the pool.
    _run_jobs(jobs, sleep)
    for run, report in runs:
        _finish(run, report)


def run_night(now, phases=PHASES, email=None, profiles=None, dry_run=False, sleep=time.sleep):
    """The phases asked for, in the order of PHASES; `email` limits the scoring to that person, `profiles` both
    phases to the active ones among these profiles. A dry run counts the offers, the batches and the effect of the
    caps without calling Claude nor writing anything."""
    night_report = NightReport(dry_run=dry_run)
    if not dry_run:
        # Runs left running by a thread or a command that died (a restart): failed, so none waits forever.
        night_report.stale = ScoringRun.objects.fail_stale(now)
    if SUMMARIES in phases:
        _summaries_phase(now, profiles, dry_run, sleep, night_report)
    if PEOPLE in phases:
        _people_phase(now, email, profiles, dry_run, sleep, night_report)
    return night_report


def _count(number, singular, plural=None):
    """« 0 lot », « 1 lot », « 2 lots »: French agreement, number spaced by format_number."""
    return f"{format_number(number)} {singular if number < 2 else plural or singular + 's'}"


def _tokens(tokens):
    return (f"{format_number(tokens['input_tokens'])} en entrée, {format_number(tokens['output_tokens'])} en "
            f"sortie, {format_number(tokens['cache_read_tokens'])} lus en cache, "
            f"{format_number(tokens['cache_creation_tokens'])} écrits en cache")


def _run_lines(report, written, refused, codes=None):
    """The lines of a run that went to Claude: what was written and refused, the batches, the state, the tokens."""
    done = [written]
    if codes is not None:
        done.append(codes)
    done += [refused, _count(report.missing, "offre absente", "offres absentes") + " des réponses",
             _count(report.failed_batches, "lot") + " en échec"]
    return [f"  {report.label} : {_count(report.requested, 'offre')} en {_count(report.batches, 'lot')}, "
            f"{', '.join(done)}.",
            f"    État : {report.state}. Jetons : {_tokens(report.tokens)}."]


def _rome_line(outcomes):
    """What became of the ROME codes Claude gave (the design plan, section 3): counts only."""
    fiche = [_count(outcomes[rome_titles.EMPTY], "vide"), _count(outcomes[rome_titles.UNKNOWN], "inexistant"),
             _count(outcomes[rome_titles.REFUSED], "refusé") + " par le contrôle des mots",
             _count(outcomes[rome_titles.ACCEPTED], "accepté")]
    exact = _count(outcomes[rome_titles.TITLE], "appellation exacte", "appellations exactes")
    return f"    Codes ROME proposés par Claude : {exact} (code de fiche non lu) ; codes de fiche : {', '.join(fiche)}."


def format_report(night_report):
    """Lines of the night report, in French: counts and account keys only, never an offer's text nor Claude's output."""
    dry_run = night_report.dry_run
    lines = ["Notation : essai à blanc, rien n’est demandé à Claude ni écrit." if dry_run else "Notation"]
    if night_report.stale:
        lines.append(f"{_count(night_report.stale, 'run resté', 'runs restés')} en cours depuis plus de "
                     f"{int(ScoringRun.STALE_AFTER.total_seconds() // 60)} minutes, "
                     f"{'passé' if night_report.stale == 1 else 'passés'} en échec.")
    if (report := night_report.summaries) is not None:
        lines += ["", "1. Résumés"]
        if night_report.summaries_cut:
            lines.append(f"  Plafond de {format_number(settings.PREPASS_NIGHT_CAP)} résumés par nuit atteint : "
                         f"{_count(night_report.summaries_cut, 'offre écartée', 'offres écartées')}.")
        if not report.requested:
            lines.append("  Aucune offre à résumer.")
        elif dry_run:
            lines.append(f"  {_count(report.requested, 'offre')} à résumer en {_count(report.batches, 'lot')}.")
        else:
            lines += _run_lines(report, _count(report.written, "résumé écrit", "résumés écrits"),
                                _count(report.refused, "résumé refusé", "résumés refusés"),
                                codes=_count(report.codes, "code ROME trouvé", "codes ROME trouvés"))
            lines.append(_rome_line(report.rome))
    if night_report.budget is not None:
        lines += ["", "2. Personnes",
                  f"  Budget de la nuit : {_count(night_report.budget, 'note restante', 'notes restantes')} sur "
                  f"{format_number(settings.NIGHT_SCORE_CAP)}."]
        if night_report.people_cut_offers:
            lines.append(f"  Plafond de la nuit atteint : "
                         f"{_count(night_report.people_cut_offers, 'offre écartée', 'offres écartées')} pour "
                         f"{_count(night_report.people_cut_persons, 'personne')}.")
        for reason, text in SKIP_REASONS.items():
            accounts = [str(skipped.account) for skipped in night_report.skipped if skipped.reason == reason]
            if accounts:
                lines.append(f"  {_count(len(accounts), 'personne sautée', 'personnes sautées')}, {text} "
                             f"({'compte' if len(accounts) == 1 else 'comptes'} {', '.join(accounts)}).")
        if not night_report.people:
            lines.append("  Aucune offre à noter.")
        for report in night_report.people:
            if dry_run:
                lines.append(f"  {report.label} : {_count(report.requested, 'offre')} à noter en "
                             f"{_count(report.batches, 'lot')}.")
            else:
                lines += _run_lines(report, _count(report.written, "note écrite", "notes écrites"),
                                    _count(report.refused, "raison refusée", "raisons refusées"))
    return lines
