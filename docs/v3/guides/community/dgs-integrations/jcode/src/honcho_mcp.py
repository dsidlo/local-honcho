#!/usr/bin/env python3
"""honcho-mcp: stdio MCP server bridging jcode to self-hosted Honcho memory.

Tools exposed:
  honcho_store(text, kind?, workspace?, session?)       -> store a message (scrubbed)
  honcho_search(query, limit?, workspace?, peer?)       -> vector search over history
  honcho_ask(query, reasoning_level?)                   -> dialectic synthesis (slow: seconds)
  honcho_conclusions(query?, limit?)                    -> distilled insights (list/query)

Scrubbing policy (docs/honcho-conventions.md) is enforced on every store.
Workspace rule: explicit workspace arg wins; else git-origin detection from cwd;
else the dedicated 'jcode' global workspace. Never 'default'.
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error

BASE_URL = os.environ.get("HONCHO_BASE_URL", "http://localhost:8300")
USER_PEER = os.environ.get("HONCHO_USER", "dsidlo")
AGENT_PEER = os.environ.get("HONCHO_AGENT_PEER", "agent-jcode")
MAX_CONTENT = 4000
REQUEST_TIMEOUT = 120  # dialectic can take ~10-30s

# --- scrubbing policy (docs/honcho-conventions.md) --------------------------

PATH_DENYLIST = [
    "auth.json", ".env", ".key", "credential", ".pem",
    "id_rsa", "id_ed25519", ".p12", "secrets/",
]

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"gho_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----"),
    re.compile(r"hf_[A-Za-z0-9]{30,}"),
    re.compile(r"xai-v[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer [A-Za-z0-9._-]{25,}"),
    re.compile(r"postgres(ql)?\+?psycopg?://[^@\s]+:[^@\s]+@"),
    re.compile(r"[a-zA-Z0-9_]+:[^@\s]+@(?:127\.0\.0\.1|localhost):\d+"),
    # Known-secret scrubbing (leak-scan 2026-09-28): bare password values in
    # quoted patterns (SQL LIKE, shell), PGPASSWORD env assignments, and the
    # SIMPA/local DB password token itself
    re.compile(r"PGPASSWORD=\"?[^\"\s]+\"?"),
    re.compile(r"LIKE '%[^']*password[^']*'", re.I),
]

REDACTED = "[REDACTED]"


def scrub(text: str) -> str:
    for pat in SECRET_PATTERNS:
        text = pat.sub(REDACTED, text)
    return text


def path_is_denied(path: str) -> bool:
    p = path.lower()
    return any(tok in p for tok in PATH_DENYLIST)


def chunk(text: str, size: int = MAX_CONTENT):
    if len(text) <= size:
        return [text]
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        if len(para) > size:
            if cur:
                chunks.append(cur.strip())
                cur = ""
            for sent in re.findall(r"[^.!?]+[.!?]+[\"']?\s*", para) or [para]:
                if len(cur) + len(sent) > size:
                    if cur:
                        chunks.append(cur.strip())
                        cur = ""
                    cur = sent[:size]
                else:
                    cur += sent
        elif len(cur) + len(para) + 2 > size:  # pragma: no branch
            if cur:  # pragma: no branch
                chunks.append(cur.strip())
            cur = para + "\n\n"
        else:
            cur += para + "\n\n"
    if cur:  # pragma: no branch
        chunks.append(cur.strip())
    return chunks or [text[:size]]


# --- git-origin workspace detection (pi-mono rule) ---------------------------

def detect_workspace(cwd: str | None = None) -> str:
    """Git-origin-derived workspace name, else 'jcode' global workspace."""
    d = os.path.abspath(cwd or os.getcwd())
    root = os.path.dirname(d) if d == "/" else d
    while d and d != os.path.dirname(d):
        gitconfig = os.path.join(d, ".git", "config")
        if os.path.isfile(gitconfig):
            try:
                content = open(gitconfig, encoding="utf-8", errors="ignore").read()
                m = re.search(
                    r'\[remote "origin"\][^\[]*url\s*=\s*.*?(?:/|:)([^/]+?)(?:\.git)?\s*$',
                    content,
                    re.M,
                )
                if m:
                    return re.sub(r"[^a-z0-9_-]", "-", m.group(1).strip().lower())
                return re.sub(r"\s+", "-", os.path.basename(d).lower())
            except OSError:
                pass
        d = os.path.dirname(d)
    return "jcode"


# --- Honcho API client -------------------------------------------------------

def api(path: str, payload: dict | None = None, method: str = "POST") -> dict:
    url = f"{BASE_URL}/v3{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def get(path: str) -> dict:
    url = f"{BASE_URL}/v3{path}"
    with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=REQUEST_TIMEOUT) as r:
        return json.loads(r.read().decode())


def ensure_workspace(name: str) -> None:
    api("/workspaces", {"id": name})


def ensure_session(ws: str, sid: str) -> str:
    """Create session with both peers if missing; return session id."""
    try:
        sessions = api(f"/workspaces/{ws}/sessions/list", {})
        for s in sessions.get("items", []):
            if s["id"] == sid:
                return sid
    except Exception:
        pass
    api(f"/workspaces/{ws}/sessions", {
        "id": sid,
        "peers": {USER_PEER: {}, AGENT_PEER: {}},
    })
    return sid


# --- tool implementations -----------------------------------------------------

def tool_store(args: dict) -> str:
    text = scrub(str(args.get("text", "")).strip())
    if not text:
        return "error: empty text"
    kind = str(args.get("kind", "note"))
    ws = str(args.get("workspace") or detect_workspace(args.get("cwd")))
    session_name = str(args.get("session") or f"jcode-manual-{ws}")
    peer = USER_PEER if kind == "prompt" else AGENT_PEER

    ensure_workspace(ws)
    ensure_session(ws, session_name)
    body = []
    all_chunks = chunk(text)
    for i, chunk_text in enumerate(all_chunks):
        msg = {"content": chunk_text, "peer_id": peer}
        meta = {"type": kind, "source": "jcode-mcp"}
        if len(all_chunks) > 1:
            meta.update({"is_chunk": True, "chunk_index": i + 1,
                         "total_chunks": len(all_chunks)})
        msg["metadata"] = meta
        body.append(msg)
    sent = 0
    for batch in [body[i:i + 5] for i in range(0, len(body), 5)]:
        api(f"/workspaces/{ws}/sessions/{session_name}/messages",
            {"messages": batch, "peer_id": peer})
        sent += len(batch)
    return f"stored {sent} message(s) to {ws}/{session_name} as peer {peer}"


def tool_search(args: dict) -> str:
    ws = str(args.get("workspace") or detect_workspace(args.get("cwd")))
    query = str(args.get("query", "")).strip()
    limit = min(int(args.get("limit", 5)), 25)
    if not query:
        return "error: empty query"
    ensure_workspace(ws)
    res = api(f"/workspaces/{ws}/peers/{AGENT_PEER}/search",
              {"query": query, "limit": limit})
    items = res if isinstance(res, list) else res.get("items", [])
    if not items:
        return "no results"
    out = []
    for m in items[:limit]:
        meta = m.get("metadata", {})
        out.append(f"[{m.get('created_at', '?')[:10]}] peer={m.get('peer_id')} "
                   f"type={meta.get('type', '?')} | {str(m.get('content', ''))[:300]}")
    return "\n---\n".join(out)


def tool_ask(args: dict) -> str:
    ws = str(args.get("workspace") or detect_workspace(args.get("cwd")))
    query = str(args.get("query", "")).strip()
    level = str(args.get("reasoning_level", "low"))
    if not query:
        return "error: empty query"
    if level not in ("minimal", "low", "medium", "high", "max"):
        return "error: reasoning_level must be minimal|low|medium|high|max"
    ensure_workspace(ws)
    res = api(f"/workspaces/{ws}/peers/{AGENT_PEER}/chat",
              {"query": query, "reasoning_level": level})
    return res.get("content", json.dumps(res)[:2000])


def tool_conclusions(args: dict) -> str:
    ws = str(args.get("workspace") or detect_workspace(args.get("cwd")))
    ensure_workspace(ws)
    query = str(args.get("query") or "").strip()
    limit = min(int(args.get("limit", 10)), 50)
    if query:
        res = api(f"/workspaces/{ws}/conclusions/query",
                  {"query": query, "limit": limit,
                   "observer": AGENT_PEER, "observed": AGENT_PEER})
        items = res if isinstance(res, list) else res.get("items", [])
    else:
        res = api(f"/workspaces/{ws}/conclusions/list", {"limit": limit})
        items = res.get("items", [])
    if not items:
        return "no conclusions found"
    out = []
    for c in items[:limit]:
        created = c.get("created_at", "?")[:10]
        content = str(c.get("content", ""))[:1500]
        out.append(f"[{created}] {content}")
    return "\n\n---\n\n".join(out)


TOOLS = {
    "honcho_store": {
        "description": "Store a message/note into Honcho memory. kind: note|prompt|thought|finding. Workspace auto-detected from git origin (falls back to global 'jcode' workspace).",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Content to store (scrubbed of secrets automatically)"},
                "kind": {"type": "string", "enum": ["note", "prompt", "thought", "observation", "final"]},
                "workspace": {"type": "string", "description": "Override workspace; default git-origin detection"},
                "session": {"type": "string", "description": "Override session id; default jcode-manual-{workspace}"},
            },
            "required": ["text"],
        },
    },
    "honcho_search": {
        "description": "Semantic vector search over Honcho memory for the current workspace (all peers).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
                "workspace": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    "honcho_ask": {
        "description": "Ask the Dialectic API a natural-language question synthesized from memory (takes ~5-30s; use sparingly).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "reasoning_level": {"type": "string", "enum": ["minimal", "low", "medium", "high", "max"]},
                "workspace": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    "honcho_conclusions": {
        "description": "List or semantically query distilled conclusions produced by Honcho dreaming.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional semantic filter; omit to list recent"},
                "limit": {"type": "integer", "default": 10},
                "workspace": {"type": "string"},
            },
        },
    },
}


# --- minimal MCP stdio protocol ----------------------------------------------

PROTOCOL_VERSION = "2024-11-05"


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> None:
    # Initialize handshake is handled per-request below (stateless loop)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method", "")
        rid = req.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "honcho-mcp", "version": "0.1.0"},
            }})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "tools": [
                    {"name": name, "description": spec["description"],
                     "inputSchema": spec["input_schema"]}
                    for name, spec in TOOLS.items()
                ]
            }})
        elif method == "tools/call":
            name = req.get("params", {}).get("name", "")
            args = req.get("params", {}).get("arguments", {}) or {}
            try:
                fn = {"honcho_store": tool_store, "honcho_search": tool_search,
                      "honcho_ask": tool_ask, "honcho_conclusions": tool_conclusions}[name]
                result = fn(args)
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": str(result)}]
                }})
            except Exception as e:  # noqa: BLE001
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": f"error: {e}"}],
                    "isError": True,
                }})
        elif method in ("ping",):
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "resources/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"resources": []}})
        elif method == "prompts/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"prompts": []}})
        elif method.startswith("notifications/"):
            continue
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32601, "message": f"unknown method {method}"}})


if __name__ == "__main__":
    main()