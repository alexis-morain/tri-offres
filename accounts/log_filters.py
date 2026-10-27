"""Logging filters. Loaded by settings.LOGGING before the apps, so nothing here may import models."""
import logging
import re

LINK_TOKEN = re.compile(r"(/(?:connexion/lien|desinscription)/)[^/\s\"']+")


def redact(value):
    return LINK_TOKEN.sub(r"\1[redacted]", value) if isinstance(value, str) else value


class RedactLoginTokens(logging.Filter):
    """Remove the token from /connexion/lien/<token>/ and /desinscription/<token>/ paths in a record's message,
    arguments and traceback (the formatted traceback is set as the record's `exc_text`, which handlers reuse)."""

    def filter(self, record):
        record.msg = redact(record.msg)
        if isinstance(record.args, dict):
            record.args = {key: redact(value) for key, value in record.args.items()}
        elif isinstance(record.args, tuple):
            record.args = tuple(redact(value) for value in record.args)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        record.exc_text = redact(record.exc_text)
        record.stack_info = redact(record.stack_info)
        return True
