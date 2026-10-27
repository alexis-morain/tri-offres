"""What the night command does around a slot: healthchecks.io pings and alert mails (the design plan).
Neither can fail a slot."""
import logging

from supervision import alerts, healthchecks

logger = logging.getLogger(__name__)


def started(run):
    """Called once the TaskRun exists, before the first step."""
    healthchecks.ping(run.slot, "start")


def finished(run, outputs):
    """Called once the TaskRun is final. `outputs` maps each step name to its captured output."""
    try:
        healthchecks.ping(run.slot, "success" if run.status == "ok" else "fail", healthchecks.ping_body(run))
    except Exception as exc:
        logger.error("ping of %s not sent (%s)", run.slot, type(exc).__name__)
    alerts.notify(run, outputs)
