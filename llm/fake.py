"""Fake engine for tests: programmed responses per task, and a record of every call received.

    fake.reset()
    fake.set_response("cv_extract", {...})        # a dict, an exception instance, or a callable(call) -> dict
    fake.set_usage("cv_extract", llm.Usage(...))  # the usage returned (zeros by default)
    ...
    fake.calls[0].user_content                    # what would have been sent to Claude (blocks joined)
    fake.calls[0].content_blocks                  # [cached, rest], or None for a single string
"""
import copy
from typing import Any, NamedTuple

from llm.engine import Timeouts, Usage, check_model, content_blocks, joined
from llm.errors import EngineError


class FakeCall(NamedTuple):
    task: str
    system: str
    user_content: str
    schema: dict
    user: Any
    model: str
    timeouts: Timeouts
    max_tokens: int
    effort: Any
    usage: Usage
    content_blocks: Any


calls = []
_responses = {}
_usages = {}


def reset():
    calls.clear()
    _responses.clear()
    _usages.clear()


def set_response(task, response):
    _responses[task] = response


def set_usage(task, usage):
    if not isinstance(usage, Usage):
        raise TypeError("usage must be an llm.Usage")
    _usages[task] = usage


def complete(task, system, user_content, schema, user, *, model, timeouts, max_tokens, effort):
    check_model(model)
    usage = _usages.get(task) or Usage.zero(model)
    received = FakeCall(task, system, joined(user_content), copy.deepcopy(schema), user, model, timeouts, max_tokens,
                        effort, usage, content_blocks(user_content))
    calls.append(received)
    if task not in _responses:
        raise EngineError(f"fake engine: no response programmed for task {task!r}")
    response = _responses[task]
    if isinstance(response, BaseException):
        raise response
    if callable(response):
        response = response(received)
    return copy.deepcopy(response), usage
