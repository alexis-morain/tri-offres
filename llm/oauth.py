"""`oauth` engine: `claude -p` on a developer's Max token. Local use for DEV_EMAILS only (guard in llm.core).

Argv in a list (no shell) whose values come from allowlists, never content; content on stdin only, empty
temporary working directory, environment built from an allowlist: with the whole environment, ANTHROPIC_API_KEY
would take precedence over the OAuth token.
Logs carry the exit code at most, never stdout or stderr.
"""
import json
import logging
import os
import subprocess
import tempfile

from django.conf import settings

from llm.engine import Usage, check_effort, check_model, joined, log_context
from llm.errors import EngineError, EngineUnavailable

ENV_ALLOWLIST = ("PATH", "HOME", "LANG")

logger = logging.getLogger(__name__)


def argv(system, schema, model, effort=None):
    check_model(model)  # exact id: the CLI's aliases can point to an older model
    check_effort(effort)
    # --effort is documented by `claude --help` (low, medium, high, xhigh, max); only low and medium get here.
    effort_option = ["--effort", effort] if effort is not None else []
    return [
        settings.CLAUDE_BIN, "-p",
        "--model", model,
        *effort_option,
        "--tools", "",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--no-session-persistence",
        "--strict-mcp-config",
        "--safe-mode",
        "--disable-slash-commands",
        "--system-prompt", system,
    ]


def child_env():
    env = {name: os.environ[name] for name in ENV_ALLOWLIST if name in os.environ}
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    env["DISABLE_AUTOUPDATER"] = "1"
    return env


def complete(task, system, user_content, schema, user, *, model, timeouts, max_tokens, effort):
    # max_tokens has no documented CLI option: it bounds api calls only. The output is still bounded by the schema.
    command = argv(system, schema, model, effort)
    where = log_context(task, model, timeouts)
    try:
        with tempfile.TemporaryDirectory(prefix="tri-claude-") as workdir:
            completed = subprocess.run(
                # Bytes both ways: decoding is done below, so a bad byte cannot raise from subprocess with
                # the output attached to the exception.
                command, input=joined(user_content).encode("utf-8"), capture_output=True,
                cwd=workdir, env=child_env(), timeout=timeouts.oauth_timeout,
            )
    except subprocess.TimeoutExpired:
        logger.warning("llm oauth %s: timeout after %s s", where, timeouts.oauth_timeout)
        raise EngineError("claude timed out") from None
    except OSError as exc:
        # Binary missing, or no temporary directory (disk full...): claude cannot run.
        logger.warning("llm oauth %s: cannot start claude (%s)", where, type(exc).__name__)
        raise EngineUnavailable("claude cannot be started") from None
    if completed.returncode != 0:
        logger.warning("llm oauth %s: claude exited with status %s", where, completed.returncode)
        raise EngineError(f"claude exited with status {completed.returncode}")
    return parse_output(where, model, completed.stdout.decode("utf-8", errors="replace"))


def parse_output(where, model, stdout):
    """`structured_output` first, then `result` (fences stripped). Anything else is an EngineError.

    Returns (output, Usage). The usage comes from the four counts of the envelope's `usage`, nothing else; the model
    is the one asked for (the envelope's model keys are not read).
    """
    try:
        envelope = json.loads(stdout)
    except ValueError:
        logger.warning("llm oauth %s: output is not JSON", where)
        raise EngineError("claude output is not JSON") from None
    if not isinstance(envelope, dict):
        logger.warning("llm oauth %s: envelope is not an object", where)
        raise EngineError("claude returned an error")
    usage = Usage.from_response(model, envelope.get("usage") if isinstance(envelope.get("usage"), dict) else None)
    if envelope.get("is_error"):
        logger.warning("llm oauth %s: claude reported an error", where)
        raise EngineError("claude returned an error", usage=usage)
    if isinstance(envelope.get("structured_output"), dict):
        return envelope["structured_output"], usage
    result = envelope.get("result")
    if not isinstance(result, str):
        logger.warning("llm oauth %s: no structured_output nor result", where)
        raise EngineError("claude output has no result", usage=usage)
    try:
        parsed = json.loads(strip_fences(result))
    except ValueError:
        logger.warning("llm oauth %s: result is not JSON", where)
        raise EngineError("claude result is not JSON", usage=usage) from None
    if not isinstance(parsed, dict):
        logger.warning("llm oauth %s: result is not an object", where)
        raise EngineError("claude result is not an object", usage=usage)
    return parsed, usage


def strip_fences(text):
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = text[first_newline + 1:] if first_newline != -1 else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()
