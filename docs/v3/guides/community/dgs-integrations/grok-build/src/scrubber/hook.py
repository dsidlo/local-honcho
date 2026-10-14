"""Grok hook: mask secrets in tool output, and block secret-shaped prompts.

Grok has no hook that rewrites the finished model request. ``PostToolUse``
can replace the tool output the model reads, and ``UserPromptSubmit`` can
block a typed prompt so it never enters the conversation. A prompt that does
not match is left unchanged, because an allowing hook cannot edit it.

``/redact`` reports whether redaction is on. ``/redact-off`` and
``/redact-on`` change it for the current session. Each blocks the prompt
and the block reason is the status. The next session starts with redaction
on: ``SessionStart`` removes that session's marker under
``~/.grok/scrubber/off/``.

Stdout is empty when there is nothing to change. A crash or a malformed
event exits 0 and prints nothing, which leaves the original text in place.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any, TextIO

from scrubber.patterns import contains_secret, scrub, scrub_tree
from scrubber.state import (
    redaction_enabled,
    reset_session,
    set_redaction,
    valid_session_id,
)

_BLOCK_REASON = "Prompt contains a secret-shaped value and was not sent to the model."
_OFF_REASON = (
    "Secret redaction is off for this session. Prompts and tool output will "
    "be sent as written. Run /redact-on to turn it back on. The next session "
    "starts with redaction on."
)
_ON_REASON = (
    "Secret redaction is on. Keys, passwords, and bearer tokens are masked "
    "before they are sent."
)
_UNCHANGED_REASON = (
    "Secret redaction was not changed. The switch file could not be written."
)

# The whole prompt is the command, or the command file body contains one
# marker line. A long pasted document that happens to include the marker
# is left alone.
_SLASH_COMMAND = re.compile(
    r"^/(?:[A-Za-z0-9_-]+:)?redact(?:-(?P<mode>off|on))?$",
    re.IGNORECASE,
)
_CONTROL_MARKER = re.compile(
    r"^scrubber-control:\s*(?P<mode>off|on|status)$",
    re.IGNORECASE,
)
_CONTROL_PROMPT_LIMIT = 2000


def event_name(event: dict[str, Any]) -> str:
    """Return the event name folded for comparison.

    Grok sends PascalCase in ``hook_event_name`` and snake_case in
    ``hookEventName``. Both fold to the same token.
    """
    raw = event.get("hook_event_name") or event.get("hookEventName") or ""
    return str(raw).replace("_", "").lower()


def _result(event: dict[str, Any]) -> Any:
    if "toolResult" in event:
        return event.get("toolResult")
    if "tool_response" in event:
        return event.get("tool_response")
    return event.get("tool_result")


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _replace(hook_event_name: str, output: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": hook_event_name,
            "updatedToolOutput": output,
        }
    }


def session_id_from(event: dict[str, Any]) -> str | None:
    """Return the event's session id when it is safe to use as a file name."""
    raw = event.get("sessionId") if "sessionId" in event else event.get("session_id")
    if not isinstance(raw, str):
        return None
    return valid_session_id(raw.strip())


def _replace_result(
    event: dict[str, Any], session_id: str | None
) -> dict[str, Any] | None:
    if not redaction_enabled(session_id):
        return None
    if not any(key in event for key in ("toolResult", "tool_response", "tool_result")):
        return None
    scrubbed, changed = scrub_tree(_result(event))
    if not changed:
        return None
    return _replace("PostToolUse", _render(scrubbed))


def _replace_failure(
    event: dict[str, Any], session_id: str | None
) -> dict[str, Any] | None:
    """Scrub the failure text the model reads.

    ``error`` is that text when it is a string. A dirty tool result is
    included too, so a secret that arrived only in the result is not left
    as the model-facing copy.
    """
    if not redaction_enabled(session_id):
        return None
    error = event.get("error")
    parts: list[str] = []
    changed = False
    if isinstance(error, str):
        scrubbed_error = scrub(error)
        changed = scrubbed_error != error
        parts.append(scrubbed_error)
    result_present = any(
        key in event for key in ("toolResult", "tool_response", "tool_result")
    )
    if result_present:
        scrubbed_result, result_changed = scrub_tree(_result(event))
        changed = changed or result_changed
        if result_changed or not parts:
            parts.append(_render(scrubbed_result))
    if not changed or not parts:
        return None
    return _replace("PostToolUseFailure", "\n".join(parts))


def _prompt_text(event: dict[str, Any]) -> str | None:
    prompt = event.get("prompt")
    if isinstance(prompt, str):
        return prompt
    user_prompt = event.get("userPrompt")
    if isinstance(user_prompt, str):
        return user_prompt
    return None


def control_mode(prompt: str) -> str | None:
    """Return ``status``, ``off``, or ``on`` when ``prompt`` is a command."""
    stripped = prompt.strip()
    match = _SLASH_COMMAND.match(stripped)
    if match:
        mode = match.group("mode")
        return "status" if mode is None else mode.lower()
    if len(stripped) > _CONTROL_PROMPT_LIMIT:
        return None
    found: str | None = None
    for line in stripped.splitlines():
        marker = _CONTROL_MARKER.match(line.strip())
        if marker:
            found = marker.group("mode").lower()
    return found


def _status_reason(session_id: str | None) -> str:
    if redaction_enabled(session_id):
        return _ON_REASON
    return _OFF_REASON


def _apply_control(mode: str, session_id: str | None) -> dict[str, Any]:
    if mode == "status":
        return {"decision": "block", "reason": _status_reason(session_id)}
    enabled = mode == "on"
    if not set_redaction(enabled, session_id):
        return {"decision": "block", "reason": _UNCHANGED_REASON}
    return {"decision": "block", "reason": _status_reason(session_id)}


def _block_prompt(
    event: dict[str, Any], session_id: str | None
) -> dict[str, Any] | None:
    prompt = _prompt_text(event)
    if prompt is None:
        return None
    mode = control_mode(prompt)
    if mode is not None:
        return _apply_control(mode, session_id)
    if not redaction_enabled(session_id) or not contains_secret(prompt):
        return None
    return {"decision": "block", "reason": _BLOCK_REASON}


def handle(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return the hook response, or ``None`` when the event needs no change."""
    name = event_name(event)
    session_id = session_id_from(event)
    if name == "sessionstart":
        reset_session(session_id)
        return None
    if name == "posttooluse":
        return _replace_result(event, session_id)
    if name == "posttoolusefailure":
        return _replace_failure(event, session_id)
    if name == "userpromptsubmit":
        return _block_prompt(event, session_id)
    return None


def hook_config(src_dir: str, python: str = "/usr/bin/python3") -> dict[str, Any]:
    """Build the ``~/.grok/hooks`` document that runs this package.

    ``src_dir`` is the directory that contains the ``scrubber`` package, so
    ``python -m scrubber`` can import it.
    """
    command = f"PYTHONPATH={src_dir} {python} -m scrubber"
    handler = {"type": "command", "command": command, "timeout": 5}
    group = {"hooks": [handler]}
    return {
        "hooks": {
            "SessionStart": [group],
            "PostToolUse": [group],
            "PostToolUseFailure": [group],
            "UserPromptSubmit": [group],
        }
    }


def main(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Read one hook event from stdin and write a response, if any.

    Always returns 0. A non-zero exit would drop a replacement and, on
    ``PostToolUse``, can also feed stderr to the model.
    """
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        raw = stdin.read()
    except Exception:
        stderr.write("scrubber: failed to read hook input\n")
        return 0
    if not raw.strip():
        return 0
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        stderr.write("scrubber: malformed hook input\n")
        return 0
    if not isinstance(event, dict):
        stderr.write("scrubber: hook input was not an object\n")
        return 0
    try:
        response = handle(event)
    except Exception:
        stderr.write("scrubber: failed to scrub hook input\n")
        return 0
    if response is None:
        return 0
    json.dump(response, stdout, ensure_ascii=False)
    stdout.write("\n")
    return 0
