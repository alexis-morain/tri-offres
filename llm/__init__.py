"""Claude engine (the design plan 5 and 8, the design plan 7). No models.

    llm.complete_json(task, system, user_content, schema, user, *, model=llm.SONNET, timeouts=llm.INTERACTIVE,
                      max_tokens=4000, effort=None) -> dict
    llm.complete_json_with_usage(...) -> (dict, llm.Usage)

`user` is one person, or llm.SHARED for a call that serves no one. `user_content` is a string or [cached, rest].
settings.CLAUDE_ENGINE picks `fake` (tests), `oauth` (`claude -p`, DEV_EMAILS only) or `api` (Anthropic SDK through
OpenRouter: a person's call on their own key, a shared call on the site's).
"""
from llm.core import complete_json, complete_json_with_usage
from llm.engine import BATCH, HAIKU, INTERACTIVE, MAX_CONTENT_CHARS, MODELS, SHARED, SONNET, Timeouts, Usage
from llm.errors import (
    EngineCreditExhausted, EngineError, EngineKeyError, EngineKeyLimitReached, EngineKeyRejected, EngineKeyUnreadable,
    EngineRefused, EngineUnavailable, LLMError,
)

__all__ = [
    "complete_json", "complete_json_with_usage", "BATCH", "HAIKU", "INTERACTIVE", "MAX_CONTENT_CHARS", "MODELS",
    "SHARED", "SONNET", "Timeouts", "Usage", "EngineCreditExhausted", "EngineError", "EngineKeyError",
    "EngineKeyLimitReached", "EngineKeyRejected", "EngineKeyUnreadable", "EngineRefused", "EngineUnavailable",
    "LLMError",
]
