"""Allowlists and value types shared by llm.core and the engines (the design plan, 7.2).

Not llm/models.py: llm is an installed app and Django would read that module as models.
"""
import re
from dataclasses import dataclass
from typing import NamedTuple

SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"
# Exact ids only: the CLI's aliases can point to an older model, and a string starting with "--" would be an option.
MODELS = frozenset({SONNET, HAIKU})
EFFORTS = frozenset({"low", "medium"})
MAX_CONTENT_CHARS = 200_000
DEFAULT_MAX_TOKENS = 4000
MAX_TOKENS_LIMIT = 16_000
TASK_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}\Z")
TASK_RULE = "lowercase letters, digits and _, 40 characters at most, starting with a letter"
# Model id echoed by the API: kept only when it looks like one, otherwise the model asked for.
RETURNED_MODEL_PATTERN = re.compile(r"^claude-[a-z0-9-]{1,40}\Z")
USAGE_COUNTS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


class _Shared:
    """Marker for a call made for no one (the offer pre-pass): `user=llm.SHARED`, compared by identity."""

    __slots__ = ()

    def __repr__(self):
        return "SHARED"


SHARED = _Shared()


class Timeouts(NamedTuple):
    name: str
    api_timeout: int
    api_max_retries: int
    oauth_timeout: int


# INTERACTIVE: worst case api 30 s * 2, about 65 s with the backoff, under gunicorn's 90 s (contract 8.4).
# BATCH (night command, no request): a batch plus its one resumption, 2 * 90 * 2 + 120 = 480 s, under 600 s.
INTERACTIVE = Timeouts("interactive", 30, 1, 120)
BATCH = Timeouts("batch", 90, 1, 180)
TIMEOUT_PROFILES = (INTERACTIVE, BATCH)


@dataclass(frozen=True)
class Usage:
    """Tokens of one call, integers only: nothing from the response but counts and a model id that passed a pattern."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @classmethod
    def zero(cls, model):
        return cls(model=model)

    @classmethod
    def from_response(cls, requested_model, counts, returned_model=None):
        """`counts` is a mapping or an object with the four count attributes; anything invalid counts as 0."""
        if isinstance(counts, dict):
            values = {name: count(counts.get(name)) for name in USAGE_COUNTS}
        elif counts is None:
            values = {}
        else:
            values = {name: count(getattr(counts, name, None)) for name in USAGE_COUNTS}
        model = returned_model if is_returned_model(returned_model) else requested_model
        return cls(model=model, **values)


def count(value):
    # type() and not isinstance(): a bool is an int to Python.
    return value if type(value) is int and value >= 0 else 0


def is_returned_model(value):
    return isinstance(value, str) and RETURNED_MODEL_PATTERN.match(value) is not None


def is_model(value):
    # type() and not isinstance(): a str subclass could lie in __eq__ and __hash__ and carry anything into argv.
    return type(value) is str and value in MODELS


def is_effort(value):
    return value is None or (type(value) is str and value in EFFORTS)


def is_timeouts(value):
    # Identity, not equality: only the two profiles defined here, never a lookalike built elsewhere.
    return any(value is profile for profile in TIMEOUT_PROFILES)


def log_context(task, model, timeouts):
    """Where a log line comes from. Allowlisted values only: llm.core checked them before the engine runs."""
    return f"{task} model={model} timeouts={timeouts.name}"


def check_model(model):
    """Each engine checks again: a direct call to an engine cannot slip a free string into argv or the request."""
    if not is_model(model):
        raise ValueError("model is not in the allowlist")


def check_effort(effort):
    if not is_effort(effort):
        raise ValueError(f"effort must be None or one of {', '.join(sorted(EFFORTS))}")


def content_blocks(user_content):
    """A string, or a list [cached, rest] of two strings (the first part cached). Returns the list or None."""
    if isinstance(user_content, str):
        return None
    if (type(user_content) is list and len(user_content) == 2
            and all(isinstance(block, str) for block in user_content)):
        return list(user_content)
    raise TypeError("user_content must be a string or a list of two strings")


def joined(user_content):
    """What goes on claude's stdin, and what the size bound counts."""
    return user_content if isinstance(user_content, str) else "".join(user_content)
