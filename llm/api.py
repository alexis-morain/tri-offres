"""`api` engine: Anthropic API through the official SDK, key in ANTHROPIC_API_KEY.

Logs carry the exception class, the HTTP status and the request id only: never str(exc), whose message or body
can echo the content sent.
"""
import json
import logging
import os

import anthropic

from llm.engine import HAIKU, SONNET, Usage, check_effort, check_model, content_blocks, log_context
from llm.errors import EngineError, EngineUnavailable
from llm.sdk_loggers import SDK_LOGGERS

# Timeouts come from the profile of the call (llm.INTERACTIVE or llm.BATCH, the design plan
# E6). With gthread workers gunicorn's --timeout no longer cuts a slow request, so these delays bound the call.
# Without an effort from the caller, the "medium" sent since step 3 (CV and summary unchanged).
DEFAULT_EFFORT = "medium"
BETAS = ["server-side-fallback-2026-07-01"]
# OPENROUTER_API_KEY set: same Messages API through OpenRouter, billed per call. It refuses `fallbacks` (400).
OPENROUTER_URL = "https://openrouter.ai/api"
OPENROUTER_MODELS = {SONNET: "anthropic/claude-sonnet-5.5", HAIKU: "anthropic/claude-haiku-5.5"}
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


def complete(task, system, user_content, schema, user, *, model, timeouts, max_tokens, effort):
    check_model(model)
    check_effort(effort)
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if not openrouter_key and not os.environ.get("ANTHROPIC_API_KEY"):
        logger.warning("llm api %s: neither OPENROUTER_API_KEY nor ANTHROPIC_API_KEY is set", task)
        raise EngineUnavailable("ANTHROPIC_API_KEY is not set")
    where = log_context(task, model, timeouts)
    try:
        limits = {"timeout": timeouts.api_timeout, "max_retries": timeouts.api_max_retries}
        if openrouter_key:
            client = anthropic.Anthropic(**limits, base_url=OPENROUTER_URL, auth_token=openrouter_key, api_key=None)
            routing = {"model": OPENROUTER_MODELS[model]}
        else:
            client = anthropic.Anthropic(**limits)
            routing = {"model": model, "betas": BETAS, "fallbacks": "default"}
        message = client.beta.messages.create(
            **routing,
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
        reason = next((text for cls, text in SDK_ERRORS if isinstance(exc, cls)), "Anthropic SDK unexpected error")
        raise EngineError(reason) from None

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
