"""Alert mails to the operator, one per slot at most (the design plan, S3 to S5).

Everything that comes from outside the code (step errors and outputs, which can quote third-party sites) is masked
and its links are neutralised before it reaches the mail. The mail has no link and no unsubscribe header. A failure
to send is logged by its class and changes nothing for the slot.
"""
import datetime as dt
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

import mail
from jobs.models import JobOffer, Source
from onboarding import schedule
from supervision.models import AlertSent
from supervision.redaction import redact_text

logger = logging.getLogger(__name__)

TAIL_LINES = 40
OUTPUT_MAX_CHARS = 6000
DAILY_CAP = 6  # mails per rolling 24 hours, for the whole site


def safe(text):
    """`text` masked (S3), its links turned into `scheme[:]//host` so that no mail client makes them clickable (S4)."""
    return redact_text(text).replace("://", "[:]//")


def _tail(text):
    lines = safe(text).rstrip().splitlines()[-TAIL_LINES:]
    return "\n".join(lines)[-OUTPUT_MAX_CHARS:]


def _problem(reason, label, detail="", output=""):
    return {"reason": reason, "label": label, "title": label[:1].upper() + label[1:], "detail": detail,
            "output": output}


def _day(run):
    return run.day if isinstance(run.day, dt.date) else dt.date.fromisoformat(run.day)


def problems_for(run, outputs):
    """The reasons to write for a final TaskRun (saved or not): a list of dicts with reason, label, title, detail
    and output."""
    problems = []
    for result in run.steps:
        if result.get("status") == "failed":
            name = result.get("name", "")
            problems.append(_problem(f"step:{name}"[:60], f"étape {safe(name)} en échec",
                                     safe(result.get("error", "")), _tail(outputs.get(name, ""))))
    if run.slot == "nuit":
        failing = Source.objects.filter(state=Source.State.FAILING, empty_runs=2, last_run_at__gte=run.started_at)
        names = [f"- {safe(source.name)} ({safe(source.actor_source or source.kind)})"
                 for source in failing.order_by("name")]
        if names:
            total = Source.objects.filter(state=Source.State.FAILING).count()
            problems.append(_problem("sources", "sources passées « En échec » cette nuit",
                                     "\n".join(names) + f"\nAu total : {total} source{'s' if total > 1 else ''}"
                                     " « En échec »."))
        collected = any(r.get("name") == "collecte" and r.get("status") == "ok" for r in run.steps)
        if collected and schedule.is_business_day(_day(run)) \
                and not JobOffer.objects.filter(collected_at__gte=run.started_at).exists():
            problems.append(_problem("no_new_offers", "collecte : aucune offre neuve",
                                     "La collecte s’est terminée sans erreur mais n’a ajouté aucune offre, "
                                     "toutes sources confondues."))
    return problems


def notify(run, outputs):
    """Mail the reasons of a final TaskRun, if any. Never raises."""
    try:
        problems = problems_for(run, outputs)
        if problems:
            _deliver(run.slot, _day(run), problems)
    except Exception as exc:
        logger.error("alert of %s not sent (%s)", run.slot, type(exc).__name__)


def send_killed(slot, day, reason):
    """The scheduler killed or lost a slot (step 10.1). True when the mail went out. Never raises."""
    try:
        problem = _problem("killed", "créneau interrompu", f"Le planificateur a arrêté le créneau : {safe(reason)}")
        return _deliver(slot, day, [problem])
    except Exception as exc:
        logger.error("alert of %s not sent (%s)", slot, type(exc).__name__)
        return False


def send_not_started(slot, day):
    """The scheduler tried a slot three times and `nuit` wrote no pass (step 10.1). Never raises."""
    try:
        problem = _problem("not_started", f"créneau {slot} non lancé",
                           "Le planificateur a essayé trois fois de lancer le créneau sans qu’un passage soit "
                           "journalisé (verrou pris ou erreur avant le début).")
        return _deliver(slot, day, [problem])
    except Exception as exc:
        logger.error("alert of %s not sent (%s)", slot, type(exc).__name__)
        return False


def send_key_unreadable(day=None):
    """A person's OpenRouter key could not be opened (llm/api.py): the Fernet keys changed or a row is corrupt. One mail
    a day at most, whoever hits it first. Never raises."""
    try:
        problem = _problem("key_unreadable", "clé OpenRouter illisible",
                           "Une clé OpenRouter enregistrée ne s’ouvre pas : OPENROUTER_KEYS_FERNET a changé, ou la "
                           "ligne est abîmée. Les appels faits pour les personnes sont arrêtés cette nuit ; rien n’est "
                           "effacé. Rendre la bonne clé de chiffrement (Bitwarden) suffit à tout relancer.")
        return _deliver("clé", day or timezone.localdate(), [problem])
    except Exception as exc:
        logger.error("alert of the unreadable key not sent (%s)", type(exc).__name__)
        return False


def _deliver(slot, day, problems):
    """One mail for the reasons not yet sent for (slot, day), unless the site cap is reached (S5)."""
    done = set(AlertSent.objects.filter(slot=slot, day=day).values_list("reason", flat=True))
    fresh = [problem for problem in problems if problem["reason"] not in done]
    if not fresh:
        return False
    now = timezone.now()
    # One mail writes several rows with the same sent_at: count the distinct instants.
    recent = AlertSent.objects.filter(sent_at__gte=now - timedelta(hours=24)).values("sent_at").distinct().count()
    if recent >= DAILY_CAP:
        logger.error("alert cap reached (%s mails per 24 hours): %s of %s not sent", DAILY_CAP, slot, day)
        return False
    try:
        sent = mail.send("alert", settings.ALERT_EMAIL, {"slot": slot, "day": day, "problems": fresh})
    except Exception as exc:  # the message may quote the recipient
        logger.error("alert mail of %s not sent (%s)", slot, type(exc).__name__)
        return False
    if not sent:
        logger.error("alert mail of %s not sent (the mail backend sent nothing)", slot)
        return False
    AlertSent.objects.bulk_create([AlertSent(slot=slot, day=day, reason=problem["reason"], sent_at=now)
                                   for problem in fresh], ignore_conflicts=True)
    return True
