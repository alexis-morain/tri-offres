"""Engine errors. Their messages are fixed strings: never the content sent, the output, stderr or str() of a cause."""


class LLMError(Exception):
    """Base of every engine error: callers catch this one and carry on without Claude."""


class EngineUnavailable(LLMError):
    """No usable engine (CLAUDE_ENGINE empty or unknown, binary or key missing)."""


class EngineRefused(LLMError):
    """The engine may not serve this user (oauth outside DEV_EMAILS)."""


class EngineError(LLMError):
    """The call failed: timeout, HTTP error, refusal, output that is not JSON or not in the schema.

    `usage` is the llm.Usage of a failed call that may have been billed; llm.core sets zeros when it is unknown.
    """

    def __init__(self, message="", usage=None):
        super().__init__(message)
        self.usage = usage
