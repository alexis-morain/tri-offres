"""complete_json(): one call to Claude for one user (or for no one, llm.SHARED), output revalidated by the schema.

Contracts: the design plan section 8, amended by the design plan section 7.
"""
import importlib
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.functions import Lower

from llm import schema
from llm.engine import (
    DEFAULT_MAX_TOKENS, INTERACTIVE, MAX_CONTENT_CHARS, MAX_TOKENS_LIMIT, SHARED, SONNET, TASK_PATTERN, TASK_RULE,
    Usage, check_effort, check_model, content_blocks, is_timeouts, joined,
)
from llm.errors import EngineError, EngineRefused, EngineUnavailable

ENGINES = {"fake": "llm.fake", "oauth": "llm.oauth", "api": "llm.api"}
REFUSED = "the oauth engine only serves DEV_EMAILS"

logger = logging.getLogger(__name__)


def complete_json(task, system, user_content, schema_, user, *, model=SONNET, timeouts=INTERACTIVE,
                  max_tokens=DEFAULT_MAX_TOKENS, effort=None):
    """Send `user_content` (data) under the `system` instruction and return a dict matching `schema_`.

    `user` is the one person the call is for, or llm.SHARED for a call that serves no one: never two users in a
    single call. Raises EngineUnavailable, EngineRefused or EngineError (all LLMError); TypeError, ValueError or
    SchemaDefinitionError for a programming error.
    """
    output, _ = complete_json_with_usage(task, system, user_content, schema_, user, model=model, timeouts=timeouts,
                                         max_tokens=max_tokens, effort=effort)
    return output


def complete_json_with_usage(task, system, user_content, schema_, user, *, model=SONNET, timeouts=INTERACTIVE,
                             max_tokens=DEFAULT_MAX_TOKENS, effort=None):
    """Same as complete_json, returns (output, llm.Usage). An EngineError carries the usage of the failed call."""
    engine = settings.CLAUDE_ENGINE
    # 1. The Max token guard comes before anything else (E2): no other check may run for a refused call.
    if engine == "oauth":
        if user is SHARED:
            check_shared_call()
        else:
            check_dev_user(user)
    # 2. to 6. Nothing below logs or raises a value it was given: task, model and options are checked before
    # they appear in a log line.
    if engine not in ENGINES:
        raise EngineUnavailable("no Claude engine configured")
    if not isinstance(task, str) or TASK_PATTERN.match(task) is None:
        raise ValueError(f"task must be {TASK_RULE}")
    check_model(model)
    if not isinstance(system, str):
        raise TypeError("system must be a string")
    content_blocks(user_content)  # TypeError for anything but a string or [str, str]
    length = len(joined(user_content))
    if length > MAX_CONTENT_CHARS:
        logger.warning("llm %s %s model=%s: content too long (%s chars)", engine, task, model, length)
        raise EngineError("content too long", usage=Usage.zero(model))
    if type(max_tokens) is not int or not 1 <= max_tokens <= MAX_TOKENS_LIMIT:
        raise ValueError(f"max_tokens must be an integer between 1 and {MAX_TOKENS_LIMIT}")
    check_effort(effort)
    if not is_timeouts(timeouts):
        raise ValueError("timeouts must be llm.INTERACTIVE or llm.BATCH")
    # 7. Schema.
    schema.check_schema(schema_)
    if schema_["type"] != "object":
        raise schema.SchemaDefinitionError("$: the root of the schema must be an object")

    # 8. Call.
    try:
        output, usage = importlib.import_module(ENGINES[engine]).complete(
            task, system, user_content, schema_, user,
            model=model, timeouts=timeouts, max_tokens=max_tokens, effort=effort,
        )
    except EngineError as exc:
        if exc.usage is None:
            exc.usage = Usage.zero(model)
        raise
    # 9. Revalidation.
    try:
        schema.validate(output, schema_)
    except schema.SchemaValidationError as exc:
        # The message names a path in the schema, never a value.
        logger.warning("llm %s %s model=%s timeouts=%s: output out of schema (%s)", engine, task, model,
                       timeouts.name, exc)
        raise EngineError("output does not match the schema", usage=usage) from None
    return output, usage


def check_dev_user(user):
    """The Max token may only serve the accounts in DEV_EMAILS (Anthropic rules)."""
    email = getattr(user, "email", None) if user is not None else None
    if not email or not settings.DEV_EMAILS or email.lower() not in settings.DEV_EMAILS:
        raise EngineRefused(REFUSED)


def check_shared_call():
    """A call for no one may use the Max token only while every account of the database is in DEV_EMAILS (E3, Q19).

    Read at each call, never cached, whatever is_active and status say: the output of a shared call is kept and read
    by everyone. The log names a reason and a count, never an address.
    """
    dev_emails = {email.lower() for email in settings.DEV_EMAILS if isinstance(email, str) and email}
    if not dev_emails:
        logger.warning("llm oauth: shared call refused (DEV_EMAILS is empty)")
        raise EngineRefused(REFUSED)
    accounts = get_user_model().objects.annotate(email_lower=Lower("email"))
    if not accounts.exists():
        logger.warning("llm oauth: shared call refused (no account)")
        raise EngineRefused(REFUSED)
    # SQLite's LOWER() folds ASCII only: a non-ASCII capital fails the comparison, which refuses (never lets through).
    outside = accounts.exclude(email_lower__in=dev_emails).count()
    if outside:
        logger.warning("llm oauth: shared call refused (%s account(s) outside DEV_EMAILS)", outside)
        raise EngineRefused(REFUSED)
