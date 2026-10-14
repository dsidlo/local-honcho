"""Per-session switch for secret redaction.

``~/.grok/scrubber/off/<session_id>`` means that session turned redaction
off. Every other session is on, and session start deletes that session's
marker. A missing or invalid id, or a read error, leaves redaction on.

``disabled`` is the old global switch. It is not consulted. Session start
deletes it so an older off state cannot leak forward.

``SCRUBBER_STATE_DIR`` overrides the directory so tests do not touch the
real home directory.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


def state_dir() -> Path:
    """Return the directory that holds the switch files."""
    override = os.environ.get("SCRUBBER_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".grok" / "scrubber"


def valid_session_id(session_id: str | None) -> str | None:
    """Return ``session_id`` when it is safe to use as a single path segment."""
    if session_id is None or _SESSION_ID.fullmatch(session_id) is None:
        return None
    return session_id


def off_marker(session_id: str) -> Path:
    """Return the off marker for a session id that has already been checked."""
    return state_dir() / "off" / session_id


def legacy_disabled_file() -> Path:
    """Return the old global switch path. Presence no longer means off."""
    return state_dir() / "disabled"


def redaction_enabled(session_id: str | None) -> bool:
    """Return whether secrets should be masked for ``session_id``.

    A missing marker means on. If the path cannot be read, stay on so a
    broken switch does not start sending secrets.
    """
    checked = valid_session_id(session_id)
    if checked is None:
        return True
    try:
        return not off_marker(checked).is_file()
    except OSError:
        return True


def set_redaction(enabled: bool, session_id: str | None) -> bool:
    """Turn redaction on or off for one session. Return whether it was written."""
    checked = valid_session_id(session_id)
    if checked is None:
        return False
    path = off_marker(checked)
    try:
        if enabled:
            path.unlink(missing_ok=True)
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("off\n", encoding="utf-8")
    except OSError:
        return False
    return True


def reset_session(session_id: str | None) -> bool:
    """Force redaction on for one session and delete the legacy global switch."""
    ok = True
    if valid_session_id(session_id) is not None:
        ok = set_redaction(True, session_id)
    try:
        legacy_disabled_file().unlink(missing_ok=True)
    except OSError:
        return False
    return ok
