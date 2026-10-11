#!/usr/bin/env python3
"""Tail Grok ``chat_history.jsonl`` files into the local Honcho server.

The session never waits on this process. The byte offset advances past a
line only after Honcho has accepted that line, or after the line produced
nothing to send. A refused post leaves the offset on the unsent line so
the next poll retries it once Honcho is back.

Run from a user service after review. Importing this module does not start
the loop.
"""

from __future__ import annotations

import json
import os
import signal
import time
import urllib.parse
from typing import Any

import common

SESSIONS_DIR = os.environ.get(
    "GROK_SESSIONS_DIR", os.path.expanduser("~/.grok/sessions")
)
STATE_FILE = os.environ.get(
    "GROK_HONCHO_STATE", os.path.expanduser("~/.cache/grok-honcho/state.json")
)
LOG_FILE = os.environ.get(
    "GROK_HONCHO_LOG", os.path.expanduser("~/.cache/grok-honcho/tailer.log")
)
POLL_SECONDS = float(os.environ.get("GROK_HONCHO_POLL", "2"))
MAX_READ = 2_000_000
MAX_TOOL_IDS = 2000


def log(level: str, message: str, **fields: Any) -> None:
    """Append one line. Fields are counts and ids, never message text."""
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] [{level}] {message}"
    if fields:
        line += " | " + json.dumps(fields, default=str)[:300]
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def load_state() -> dict[str, Any]:
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, json.JSONDecodeError):
        state = {}
    state.setdefault("offsets", {})
    state.setdefault("tools", {})
    return state


def save_state(state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    temporary = STATE_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.replace(temporary, STATE_FILE)


def trim_tools(tools: dict[str, str]) -> None:
    overflow = len(tools) - MAX_TOOL_IDS
    if overflow <= 0:
        return
    for key in list(tools)[:overflow]:
        del tools[key]


def session_cwd(session_dir: str, encoded_parent: str) -> str:
    summary_path = os.path.join(session_dir, "summary.json")
    try:
        with open(summary_path, encoding="utf-8") as handle:
            info = json.load(handle).get("info") or {}
        cwd = info.get("cwd")
        if isinstance(cwd, str) and cwd:
            return cwd
    except (OSError, json.JSONDecodeError):
        pass
    return urllib.parse.unquote(encoded_parent)


def list_journals(root: str) -> list[tuple[str, str, str, str]] | None:
    """Return journals, or ``None`` when the sessions directory is missing.

    ``None`` means "do not prune offsets". An empty list means the directory
    exists and has no journals.
    """
    if not os.path.isdir(root):
        return None
    found: list[tuple[str, str, str, str]] = []
    for encoded in os.scandir(root):
        if not encoded.is_dir():
            continue
        for session in os.scandir(encoded.path):
            if not session.is_dir():
                continue
            journal = os.path.join(session.path, "chat_history.jsonl")
            if os.path.isfile(journal):
                found.append((journal, session.name, session.path, encoded.name))
    return found


def decorate(messages: list[dict[str, Any]], extra: dict[str, Any]) -> list[dict[str, Any]]:
    decorated: list[dict[str, Any]] = []
    for message in messages:
        metadata = dict(message.get("metadata") or {})
        metadata.update(extra)
        message = {**message, "metadata": metadata}
        decorated.extend(common.expand_chunks(message))
    return decorated


def post_messages(workspace: str, session_id: str, messages: list[dict[str, Any]]) -> str:
    """Post one source line. Return ``ok``, ``retry``, or ``drop``.

    The line goes out as a single request when it fits in the API limit of
    100 messages. A retry leaves the file offset on this line, so splitting
    a line across two successful-then-failed requests would duplicate the
    first request. Lines that chunk past 100 messages are the only case
    that can duplicate, and they are past 400 KB of text.
    """
    if not messages:
        return "ok"
    for start in range(0, len(messages), 100):
        batch = messages[start : start + 100]
        try:
            common.api(
                f"/workspaces/{workspace}/sessions/{session_id}/messages",
                {"messages": batch},
            )
        except common.HonchoError as exc:
            log(
                "ERROR",
                "post failed",
                status=exc.status,
                retryable=exc.retryable,
                workspace=workspace,
                session=session_id,
                batch=len(batch),
            )
            return "retry" if exc.retryable else "drop"
    return "ok"


def handle_journal(
    path: str,
    grok_session_id: str,
    session_dir: str,
    encoded_parent: str,
    state: dict[str, Any],
) -> None:
    offsets: dict[str, int] = state["offsets"]
    tools: dict[str, str] = state["tools"]
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    offset = int(offsets.get(path, 0))
    if size < offset:
        offset = 0
    if size == offset:
        return
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            data = handle.read(MAX_READ)
    except OSError:
        return

    events, consumed = common.journal_events(data, tools)
    if consumed == 0:
        # A single line longer than MAX_READ has no newline in this read.
        # Drop that slice so the tail cannot stall on it.
        if len(data) >= MAX_READ and b"\n" not in data:
            offsets[path] = offset + len(data)
            log("WARN", "dropped oversized line", session=grok_session_id, bytes=len(data))
        return

    cwd = session_cwd(session_dir, encoded_parent)
    try:
        workspace = common.workspace_from_dir(cwd)
    except ValueError as exc:
        log("ERROR", "workspace name rejected", error=str(exc)[:160])
        return
    session_id = common.session_id_for(grok_session_id)
    extra: dict[str, Any] = {}
    branch = common.git_branch(cwd)
    if branch:
        extra["git_branch"] = branch

    posted_end = offset
    ensured = False
    sent = 0
    for end, mapped in events:
        line_end = offset + end
        if not mapped:
            posted_end = line_end
            continue
        pieces = decorate(mapped, extra)
        if not ensured:
            try:
                common.ensure_workspace(workspace)
                common.ensure_session(workspace, session_id)
            except common.HonchoError as exc:
                offsets[path] = posted_end if exc.retryable else offset + consumed
                log(
                    "ERROR",
                    "ensure failed",
                    status=exc.status,
                    retryable=exc.retryable,
                    workspace=workspace,
                )
                trim_tools(tools)
                return
            ensured = True
        outcome = post_messages(workspace, session_id, pieces)
        if outcome == "retry":
            offsets[path] = posted_end
            trim_tools(tools)
            return
        if outcome == "drop":
            log("ERROR", "dropped journal line", session=session_id, workspace=workspace)
        else:
            sent += len(pieces)
        posted_end = line_end
        time.sleep(0.05)
    offsets[path] = posted_end
    if sent:
        log("INFO", "captured", count=sent, workspace=workspace, session=session_id)
    trim_tools(tools)


def main() -> None:
    running = True

    def stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log("INFO", "tailer started", sessions=SESSIONS_DIR, base_url=common.BASE_URL)
    while running:
        state = load_state()
        try:
            journals = list_journals(SESSIONS_DIR)
            if journals is not None:
                for journal, session_id, session_dir, encoded in journals:
                    handle_journal(journal, session_id, session_dir, encoded, state)
                    save_state(state)
                live = {item[0] for item in journals}
                state["offsets"] = {
                    path: pos for path, pos in state["offsets"].items() if path in live
                }
            state["last_beat"] = time.time()
            save_state(state)
        except Exception as exc:  # the Grok session must not depend on this loop
            log("ERROR", "loop error", error=type(exc).__name__)
        time.sleep(POLL_SECONDS)
    log("INFO", "tailer stopped")


if __name__ == "__main__":
    main()
