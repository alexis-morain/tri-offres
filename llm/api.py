"""`api` engine: the Anthropic SDK through OpenRouter, always on a key that is not ours to share.

A call made for a person goes out on that person's own key only (accounts/openrouter.py), read again from the database
at each call; a shared call (llm.SHARED, the offer pre-pass) goes out on the site's OPENROUTER_API_KEY only. Neither
ever falls back to the other, nor to an ANTHROPIC_* variable: the client below is given its bearer explicitly, and the
SDK then reads no credential from the environment.

Logs carry the exception class, the HTTP status and the request id only: never str(exc), whose message or body
can echo the content sent, and never a key.
"""
import json
import logging
import os

import anthropic
from cryptography.fernet import InvalidToken
from django.views.decorators.debug import sensitive_variables

from accounts import openrouter
from llm.engine import HAIKU, SHARED, SONNET, Usage, check_effort, check_model, content_blocks, log_context
from llm.errors import (
    EngineCreditExhausted, EngineError, EngineKeyLimitReached, EngineKeyRejected, EngineKeyUnreadable,
    EngineUnavailable,
)
from llm.sdk_loggers import SDK_LOGGERS
from supervision import alerts

# Timeouts come from the profile of the call (llm.INTERACTIVE or llm.BATCH, the design plan
# E6). With gthread workers gunicorn's --timeout no longer cuts a slow request, so these delays bound the call.
# Without an effort from the caller, the "medium" sent since step 3 (CV and summary unchanged).
DEFAULT_EFFORT = "medium"
# The Messages API through OpenRouter, billed per call to the owner of the key. It refuses `fallbacks` and betas (400).
OPENROUTER_URL = "https://openrouter.ai/api"
OPENROUTER_MODELS = {SONNET: "anthropic/claude-sonnet-5.5", HAIKU: "anthropic/claude-haiku-5.5"}
# error.metadata.limit_source of a 403 for the spending limit set on the key (OpenRouter, errors reference).
KEY_LIMIT = "openrouter_key_limit"
# Most specific first: APITimeoutError is an APIConnectionError, RateLimitError an APIStatusError.
SDK_ERRORS = (
    (anthropic.APITimeoutError, "Anthropic API timed out"),
    (anthropic.RateLimitError, "Anthropic API rate limit"),
    (anthropic.APIStatusError, "Anthropic API HTTP error"),
    (anthropic.APIConnectionError, "Anthropic API unreachable"),
    (anthropic.AnthropicError, "Anthropic SDK error"),
)

logger = logging.getLogger(__name__)


def clamp_sdk_loggers():
    """At DEBUG the SDK logs whole requests, content included. ANTHROPIC_LOG=debug sets that level on import."""
    for name in SDK_LOGGERS:
        sdk_logger = logging.getLogger(name)
        if sdk_logger.level < logging.WARNING:
            sdk_logger.setLevel(logging.WARNING)


clamp_sdk_loggers()


@sensitive_variables("key", "credential")
def credential_for(user):
    """The key a call for `user` goes out on: the person's own, or for llm.SHARED the site's. Raises
    EngineUnavailable for a caller that is neither (None, anonymous...: never read as shared) or a person without a
    key, EngineKeyUnreadable for a key that cannot be opened."""
    if user is SHARED:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            logger.warning("llm api: OPENROUTER_API_KEY is not set (shared call)")
            raise EngineUnavailable("OPENROUTER_API_KEY is not set")
        return openrouter.Credential(key, "", 0)
    pk = getattr(user, "pk", None)
    if type(pk) is not int:
        raise EngineUnavailable("the call has no account to take a key from")
    try:
        credential = openrouter.read(pk)
    except InvalidToken:
        logger.error("llm api: the OpenRouter key of account %s cannot be opened (InvalidToken)", pk)
        alerts.send_key_unreadable()
        raise EngineKeyUnreadable("an OpenRouter key cannot be opened") from None
    if credential is None:
        raise EngineUnavailable("the account has no OpenRouter key")
    return credential


def key_refusal(exc, credential):
    """What a refusal of a person's own key looks like (contract 5.6), judged on the status, the Retry-After header
    and error.metadata.limit_source, never on a message: openrouter.REJECTED, CREDIT, LIMIT, or None (an ordinary
    failure, and always None for a shared call)."""
    if not credential.user_pk:
        return None
    status = getattr(exc, "status_code", None)
    if status == 401:
        return openrouter.REJECTED
    response = getattr(exc, "response", None)
    if status == 402 and response is not None and "retry-after" not in response.headers:
        return openrouter.CREDIT
    if status == 403 and limit_source(getattr(exc, "body", None)) == KEY_LIMIT:
        return openrouter.LIMIT
    return None


def limit_source(body):
    """error.metadata.limit_source of an OpenRouter error body (also read at error.limit_source: the Messages skin
    wraps errors its own way); None when it is not there as a string."""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None
    metadata = error.get("metadata")
    value = metadata.get("limit_source") if isinstance(metadata, dict) else error.get("limit_source")
    return value if isinstance(value, str) else None


REFUSALS = {
    openrouter.REJECTED: (EngineKeyRejected, "OpenRouter rejected the key"),
    openrouter.CREDIT: (EngineCreditExhausted, "OpenRouter credit exhausted"),
    openrouter.LIMIT: (EngineKeyLimitReached, "OpenRouter key limit reached"),
}


def complete(task, system, user_content, schema, user, *, model, timeouts, max_tokens, effort):
    check_model(model)
    check_effort(effort)
    credential = credential_for(user)
    where = log_context(task, model, timeouts)
    try:
        client = anthropic.Anthropic(timeout=timeouts.api_timeout, max_retries=timeouts.api_max_retries,
                                     base_url=OPENROUTER_URL, auth_token=credential.key, api_key=None)
        message = client.beta.messages.create(
            model=OPENROUTER_MODELS[model],
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": message_content(user_content)}],
            # The API does not take every bound (maxLength, maxItems...): the SDK moves those into descriptions.
            # llm.core revalidates the output against the full schema anyway.
            output_config={"format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)},
                           "effort": effort or DEFAULT_EFFORT},
        )
    except Exception as exc:  # noqa: BLE001 - callers catching LLMError must always be able to carry on
        logger.warning("llm api %s: %s status=%s request_id=%s", where, type(exc).__name__,
                       getattr(exc, "status_code", None), getattr(exc, "request_id", None))
        refusal = key_refusal(exc, credential)
        if refusal:
            # The one place the effects of a refusal of a person's key are triggered (accounts/openrouter.py).
            openrouter.refuse(credential, refusal)
            cls, text = REFUSALS[refusal]
            raise cls(text) from None
        reason = next((text for cls, text in SDK_ERRORS if isinstance(exc, cls)), "Anthropic SDK unexpected error")
        raise EngineError(reason) from None
    if credential.in_episode:
        openrouter.accept(credential)

    # A response came back: it was billed, so every error from here on carries its usage.
    usage = Usage.from_response(model, getattr(message, "usage", None), getattr(message, "model", None))
    request_id = getattr(message, "_request_id", None)
    if message.stop_reason in ("refusal", "max_tokens"):
        logger.warning("llm api %s: stop_reason=%s request_id=%s", where, message.stop_reason, request_id)
        raise EngineError(f"Anthropic API stop_reason {message.stop_reason}", usage=usage)
    text = next((block.text for block in message.content if getattr(block, "type", None) == "text"), None)
    if text is None:
        logger.warning("llm api %s: no text block request_id=%s", where, request_id)
        raise EngineError("Anthropic API returned no text", usage=usage)
    try:
        return json.loads(text), usage
    except ValueError:
        logger.warning("llm api %s: output is not JSON request_id=%s", where, request_id)
        raise EngineError("Anthropic API output is not JSON", usage=usage) from None


def message_content(user_content):
    """A string as is; [cached, rest] as two text blocks, the first (the profile) cached (decision Q25)."""
    blocks = content_blocks(user_content)
    if blocks is None:
        return user_content
    cached, rest = blocks
    return [
        {"type": "text", "text": cached, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": rest},
    ]
