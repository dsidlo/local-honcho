"""Pattern scrubber for passwords and API keys.

Assignment values are replaced and the name is kept, so ``password=hunter22``
becomes ``password=[REDACTED]``. A password written as ordinary prose, with
no ``=`` or ``:``, is not detected. Known key shapes are removed wherever
they appear.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

# Longer and more specific shapes go first. ``sk-proj-`` must be removed
# before the plain ``sk-`` pattern, which does not allow a hyphen.
_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
        r".*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"),
    re.compile(r"[A-Za-z0-9_]+:[^@\s/]+@(?:127\.0\.0\.1|localhost):\d+"),
    re.compile(r"sk-(?:proj|ant)-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"hf_[A-Za-z0-9]{30,}"),
    re.compile(r"xai-[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"(?i)bearer [A-Za-z0-9._-]{25,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)

# Any assignment whose name ends with one of these. Hyphens are folded to
# underscores before the check, so ``api-key`` and ``api_key`` both match.
_NAME_SUFFIXES: tuple[str, ...] = (
    "api_key",
    "apikey",
    "secret_key",
    "access_key",
    "private_key",
    "client_secret",
    "pgpassword",
    "password",
    "passwd",
    "secret",
    "token",
)

_ASSIGNMENT = re.compile(
    r"\b(?P<name>[A-Za-z_][A-Za-z0-9_-]*)\s*(?P<sep>[=:])\s*"
    r"(?:(?P<q>[\"'])(?P<qvalue>.*?)(?P=q)|(?P<value>[^\s\"';,]+))"
)

_SAFE_VALUE = re.compile(
    r"(?i)^(none|null|true|false|changeme|placeholder|redacted|todo)$"
)
_ENV_REFERENCE = re.compile(r"[A-Z][A-Z0-9_]*")
_YOUR_PLACEHOLDER = re.compile(r"(?i)^your[-_].+")

_MIN_VALUE_LENGTH = 8


def _sensitive_name(name: str) -> bool:
    """Return whether an assignment name looks like a credential field."""
    folded = name.lower().replace("-", "_")
    return folded.endswith(_NAME_SUFFIXES)


def _should_redact(value: str) -> bool:
    """Return whether an assignment value is long enough to be a secret.

    Short tokens, booleans, and all-caps environment names are kept. An
    all-caps value is a pointer at another variable, not the secret itself.
    """
    if len(value) < _MIN_VALUE_LENGTH:
        return False
    if value == REDACTED:
        return False
    if _SAFE_VALUE.fullmatch(value):
        return False
    if _YOUR_PLACEHOLDER.fullmatch(value):
        return False
    return not _ENV_REFERENCE.fullmatch(value)


def _assignment_sub(match: re.Match[str]) -> str:
    if not _sensitive_name(match.group("name")):
        return match.group(0)
    group = "qvalue" if match.group("q") else "value"
    value = match.group(group)
    if not _should_redact(value):
        return match.group(0)
    text = match.group(0)
    rel_start = match.start(group) - match.start()
    rel_end = match.end(group) - match.start()
    return text[:rel_start] + REDACTED + text[rel_end:]


def scrub(text: str) -> str:
    """Replace secret-shaped substrings. The rest of the text stays."""
    text = _ASSIGNMENT.sub(_assignment_sub, text)
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def contains_secret(text: str) -> bool:
    """Return whether ``scrub`` would change ``text``."""
    return scrub(text) != text


def scrub_tree(value: Any) -> tuple[Any, bool]:
    """Scrub every string in a JSON-like tree.

    Returns the scrubbed value and whether any string changed. Dict keys are
    scrubbed too. Numbers, booleans, and ``None`` pass through.
    """
    if isinstance(value, str):
        scrubbed = scrub(value)
        return scrubbed, scrubbed != value
    if isinstance(value, list):
        changed = False
        items: list[Any] = []
        for item in value:
            new_item, item_changed = scrub_tree(item)
            changed = changed or item_changed
            items.append(new_item)
        return items, changed
    if isinstance(value, dict):
        changed = False
        mapped: dict[Any, Any] = {}
        for key, item in value.items():
            new_key: Any = key
            if isinstance(key, str):
                new_key = scrub(key)
                changed = changed or new_key != key
            new_item, item_changed = scrub_tree(item)
            changed = changed or item_changed
            mapped[new_key] = new_item
        return mapped, changed
    return value, False
