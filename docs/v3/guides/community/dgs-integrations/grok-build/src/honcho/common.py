"""Shared Honcho client, scrubber, and workspace rules for Grok.

The functions here are the ones the tailer and the MCP server both use.
They follow the pi-mono workspace rule, with the non-git case settled in
the 2026-10-11 Grok session launched from /home/dsidlo/workspace.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

RESOURCE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
ORIGIN_RE = re.compile(
    r'\[remote "origin"\][^\[]*url\s*=\s*.*?(?:/|:)([^/]+?)(?:\.git)?\s*$',
    re.M,
)
USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)

MAX_CONTENT = 4000
BATCH_SIZE = 5

USER_PEER = os.environ.get("HONCHO_USER", "dsidlo")
AGENT_PEER = os.environ.get("HONCHO_AGENT_PEER", "agent-grok")
BASE_URL = os.environ.get("HONCHO_BASE_URL", "http://localhost:8300").rstrip("/")

PATH_DENYLIST = (
    "auth.json",
    ".env",
    ".key",
    "credential",
    ".pem",
    "id_rsa",
    "id_ed25519",
    ".p12",
    "secrets/",
)

SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"gho_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----"),
    re.compile(r"hf_[A-Za-z0-9]{30,}"),
    re.compile(r"xai-[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer [A-Za-z0-9._\-]{25,}"),
    re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^@\s]+:[^@\s]+@"),
    re.compile(r"[a-zA-Z0-9_]+:[^@\s]+@(?:127\.0\.0\.1|localhost):\d+"),
    re.compile(r"PGPASSWORD=\"?[^\s\"]+\"?"),
)

REDACTED = "[REDACTED]"

_branch_cache: dict[str, str] = {}
_ensured_sessions: set[tuple[str, str]] = set()


class HonchoError(Exception):
    """Honcho returned an HTTP error or could not be reached."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def retryable(self) -> bool:
        """Transport failures and server errors are worth another attempt."""
        if self.status is None:
            return True
        return self.status >= 500 or self.status in (408, 429)


def scrub(text: str) -> str:
    """Replace secret-shaped substrings. The rest of the text stays."""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def path_denied(path: str) -> bool:
    lowered = path.lower()
    return any(token in lowered for token in PATH_DENYLIST)


def resource_name(raw: str) -> str:
    """Fold a string into a Honcho resource id (max 512, ``^[a-zA-Z0-9_-]+$``)."""
    name = raw.strip().lower()
    name = re.sub(r"[^a-z0-9_-]", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    if not name or name == "default":
        raise ValueError(f"refusing workspace name {raw!r}")
    return name[:512]


def flatten_dir(path: str) -> str:
    """Turn an absolute directory into a workspace id.

    ``/home/dsidlo/workspace`` becomes ``home-dsidlo-workspace``. The leading
    slash is dropped so the name is the path, and the basename alone is never
    used: ``workspace`` is already a workspace on the local server.
    """
    absolute = os.path.abspath(path)
    return resource_name(absolute.lstrip("/"))


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    except OSError:
        return None


def _origin_slug(config_text: str) -> str | None:
    match = ORIGIN_RE.search(config_text)
    if not match:
        return None
    return resource_name(match.group(1))


def _git_config_text(start: str) -> tuple[str | None, str | None]:
    """Return ``(config text, git root)`` walking up from ``start``.

    A worktree's ``.git`` is a file. Its gitdir config often has no origin,
    so the commondir config is the one that carries ``remote.origin``.
    """
    directory = os.path.abspath(start)
    while True:
        git_path = os.path.join(directory, ".git")
        config_path = os.path.join(git_path, "config")
        if os.path.isfile(config_path):
            return _read(config_path), directory
        if os.path.isfile(git_path):
            pointer = _read(git_path) or ""
            gitdir_match = re.search(r"^gitdir:\s*(.+)$", pointer, re.M)
            if gitdir_match:
                gitdir = gitdir_match.group(1).strip()
                if not os.path.isabs(gitdir):
                    gitdir = os.path.join(directory, gitdir)
                text = _read(os.path.join(gitdir, "config"))
                if text and '[remote "origin"]' in text:
                    return text, directory
                commondir = _read(os.path.join(gitdir, "commondir"))
                if commondir:
                    common = commondir.strip()
                    if not os.path.isabs(common):
                        common = os.path.normpath(os.path.join(gitdir, common))
                    return _read(os.path.join(common, "config")), directory
            return None, directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return None, None
        directory = parent


def workspace_from_dir(path: str) -> str:
    """pi-mono's rule when ``path`` is inside a git repo, else the flat path.

    A repo with ``remote.origin`` uses that URL's repository name, lowercased.
    A repo without an origin uses the repository directory's basename. A
    directory that is not inside a repo uses :func:`flatten_dir`. The name
    ``default`` is never returned.
    """
    config_text, root = _git_config_text(path)
    if config_text:
        slug = _origin_slug(config_text)
        if slug:
            return slug
    if root:
        return resource_name(os.path.basename(root))
    return flatten_dir(path)


def git_branch(path: str) -> str | None:
    """Current branch name, cached per directory. Empty when git cannot say."""
    directory = os.path.abspath(path)
    if directory in _branch_cache:
        cached = _branch_cache[directory]
        return cached or None
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", directory, "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        name = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ""
    _branch_cache[directory] = name
    return name or None


def chunk(text: str, size: int = MAX_CONTENT) -> list[str]:
    """Split on paragraphs, then sentences, then a hard length. Mirrors pi-mono."""
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    current = ""
    paragraphs = re.split(r"\n\n+", text)
    for paragraph in paragraphs:
        if len(paragraph) > size:
            if current:
                chunks.append(current.strip())
                current = ""
            sentences = re.findall(r"[^.!?]+[.!?]+[\"']?\s*", paragraph) or [paragraph]
            for sentence in sentences:
                if len(current) + len(sentence) > size:
                    if current:
                        chunks.append(current.strip())
                        current = ""
                    if len(sentence) > size:
                        for index in range(0, len(sentence), size):
                            chunks.append(sentence[index : index + size])
                    else:
                        current = sentence
                else:
                    current += sentence
        elif len(current) + len(paragraph) + 2 > size:
            if current:
                chunks.append(current.strip())
            current = paragraph + "\n\n"
        else:
            current += paragraph + "\n\n"
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text[:size]]


def expand_chunks(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Attach pi-mono chunk metadata when content exceeds ``MAX_CONTENT``."""
    parts = chunk(str(message.get("content", "")))
    if len(parts) == 1:
        return [message]
    expanded: list[dict[str, Any]] = []
    for index, part in enumerate(parts, start=1):
        metadata = dict(message.get("metadata") or {})
        metadata.update(
            {
                "is_chunk": True,
                "chunk_index": index,
                "total_chunks": len(parts),
                "original_length": len(str(message.get("content", ""))),
            }
        )
        expanded.append({**message, "content": part, "metadata": metadata})
    return expanded


def extract_user_text(text: str) -> str | None:
    """Return the typed prompt, dropping harness wrappers.

    Grok stores the real prompt inside ``<user_query>``. Rows that are only
    ``<system-reminder>``, ``<user_info>``, or ``<rules>`` are not prompts.
    """
    match = USER_QUERY_RE.search(text)
    if match:
        body = match.group(1).strip()
        return body or None
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith(("<system-reminder>", "<user_info>", "<rules>")):
        return None
    return stripped


def result_status(content: str) -> str:
    """Classify a tool result without keeping its body."""
    head = content.lstrip()[:80]
    if head.startswith("exit:"):
        parts = head[5:].strip().split(None, 1)
        token = parts[0] if parts else ""
        if token.isdigit() and token != "0":
            return "error"
        return "ok"
    if head.lower().startswith("error"):
        return "error"
    return "ok"


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return ""


def map_record(record: dict[str, Any], tool_names: dict[str, str]) -> list[dict[str, Any]]:
    """Map one ``chat_history.jsonl`` record to Honcho messages.

    ``tool_names`` is updated with ``tool_call_id -> tool name`` so a later
    ``tool_result`` can be named. Arguments and result bodies are not copied.
    ``encrypted_content`` is never read.
    """
    kind = record.get("type")
    messages: list[dict[str, Any]] = []

    if kind == "user":
        text = extract_user_text(_text_from_content(record.get("content")))
        if not text:
            return []
        messages.append(
            {
                "peer_id": USER_PEER,
                "content": scrub(text),
                "metadata": {"type": "prompt", "source": "grok-tailer"},
            }
        )
        return messages

    if kind == "reasoning":
        summary = record.get("summary")
        parts: list[str] = []
        if isinstance(summary, list):
            for item in summary:
                if isinstance(item, dict) and item.get("type") == "summary_text":
                    parts.append(str(item.get("text", "")))
        text = scrub("\n".join(part for part in parts if part.strip())).strip()
        if not text:
            return []
        messages.append(
            {
                "peer_id": AGENT_PEER,
                "content": text,
                "metadata": {"type": "thought", "source": "grok-tailer"},
            }
        )
        return messages

    if kind == "assistant":
        text = scrub(_text_from_content(record.get("content"))).strip()
        if text:
            messages.append(
                {
                    "peer_id": AGENT_PEER,
                    "content": text,
                    "metadata": {"type": "final", "source": "grok-tailer"},
                }
            )
        tool_calls = record.get("tool_calls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                name = str(call.get("name") or "unknown")
                call_id = str(call.get("id") or "")
                if call_id:
                    tool_names[call_id] = name
                messages.append(
                    {
                        "peer_id": AGENT_PEER,
                        "content": f"tool_call {name}",
                        "metadata": {
                            "type": "tool_call",
                            "tool": name[:100],
                            "tool_call_id": call_id[:80],
                            "source": "grok-tailer",
                        },
                    }
                )
        return messages

    if kind == "tool_result":
        call_id = str(record.get("tool_call_id") or "")
        name = tool_names.get(call_id, "unknown")
        body = record.get("content")
        status = result_status(body if isinstance(body, str) else "")
        messages.append(
            {
                "peer_id": AGENT_PEER,
                "content": f"tool_result {name}: {status}",
                "metadata": {
                    "type": "observation",
                    "tool": name[:100],
                    "tool_call_id": call_id[:80],
                    "status": status,
                    "source": "grok-tailer",
                },
            }
        )
        return messages

    return []


def journal_events(
    data: bytes, tool_names: dict[str, str]
) -> tuple[list[tuple[int, list[dict[str, Any]]]], int]:
    """Parse one read of a journal, which may end in the middle of a line.

    Returns ``(events, consumed)``. Each event is ``(end_offset, messages)``
    where ``end_offset`` is relative to the start of ``data``. Blank and
    malformed lines produce an empty message list so the caller can advance
    past them. Bytes after the last complete line are not consumed.
    """
    if not data:
        return [], 0
    parts = data.split(b"\n")
    if data.endswith(b"\n"):
        raw_lines = parts[:-1]
        consumed = len(data)
    else:
        raw_lines = parts[:-1]
        consumed = sum(len(line) + 1 for line in raw_lines)
    events: list[tuple[int, list[dict[str, Any]]]] = []
    cursor = 0
    for raw in raw_lines:
        cursor += len(raw) + 1
        if not raw.strip():
            events.append((cursor, []))
            continue
        try:
            record = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            events.append((cursor, []))
            continue
        if not isinstance(record, dict):
            events.append((cursor, []))
            continue
        events.append((cursor, map_record(record, tool_names)))
    return events, consumed


def api(
    path: str,
    payload: dict[str, Any] | None = None,
    method: str = "POST",
    timeout: float = 60,
) -> Any:
    """Call the local Honcho HTTP API. Raises :class:`HonchoError` on failure."""
    url = f"{BASE_URL}/v3{path}"
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = os.environ.get("HONCHO_API_KEY")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise HonchoError(f"HTTP {exc.code} {path}: {detail}", status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise HonchoError(f"unreachable {path}: {exc.reason}", status=None) from exc
    except TimeoutError as exc:
        raise HonchoError(f"timeout {path}", status=None) from exc


def ensure_workspace(name: str) -> None:
    api("/workspaces", {"id": name})


def ensure_session(workspace: str, session_id: str) -> None:
    key = (workspace, session_id)
    if key in _ensured_sessions:
        return
    api(
        f"/workspaces/{workspace}/sessions",
        {
            "id": session_id,
            "peers": {USER_PEER: {}, AGENT_PEER: {}},
        },
    )
    _ensured_sessions.add(key)


def session_id_for(grok_session_id: str) -> str:
    return resource_name(f"grok-{grok_session_id}")


def reset_caches() -> None:
    """Clear process-local caches. Tests use this."""
    _branch_cache.clear()
    _ensured_sessions.clear()
