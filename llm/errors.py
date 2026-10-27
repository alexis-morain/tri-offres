"""Engine errors. Their messages are fixed strings: never the content sent, the output, stderr or str() of a cause."""


class LLMError(Exception):
    """Base of every engine error: callers catch this one and carry on without Claude."""


class EngineUnavailable(LLMError):
    """No usable engine (CLAUDE_ENGINE empty or unknown, binary or key missing)."""


class EngineKeyUnreadable(EngineUnavailable):
    """A person's OpenRouter key is there but cannot be opened (the Fernet keys changed, a corrupt row): ours to fix,
    so every person's call stops (matching/night.py) and the operator is told."""


class EngineRefused(LLMError):
    """The engine may not serve this user (oauth outside DEV_EMAILS)."""


class EngineError(LLMError):
    """The call failed: timeout, HTTP error, refusal, output that is not JSON or not in the schema.

    `usage` is the llm.Usage of a failed call that may have been billed; llm.core sets zeros when it is unknown.
    """

    def __init__(self, message="", usage=None):
        super().__init__(message)
        self.usage = usage


class EngineKeyError(LLMError):
    """OpenRouter refused the person's own key: calls for that person stop for now (the night tries again the next
    one). The effects on the key (accounts/openrouter.py) are done by llm.api before this is raised."""


class EngineKeyRejected(EngineKeyError):
    """401: the key was revoked or deleted. It is erased and the person is asked to reconnect."""


class EngineCreditExhausted(EngineKeyError):
    """402: the person's credit is spent. The key stays."""


class EngineKeyLimitReached(EngineCreditExhausted):
    """403 for the spending limit the person set on the key. The key stays."""
