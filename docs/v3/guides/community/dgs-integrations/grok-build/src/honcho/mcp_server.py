#!/usr/bin/env python3
"""stdio MCP server: pull Honcho memory into a Grok session.

Tools:
  honcho_search        workspace message search (both peers)
  honcho_conclusions   hybrid document search, observer agent-grok
  honcho_ask           dialectic, slow; pass target to choose who is modeled.
                       Default target is the observer itself.
  honcho_store         explicit note. The tailer is the automatic path.

Workspace selection matches the tailer: git origin inside a repo, flattened
directory path otherwise. An explicit ``workspace`` argument wins.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import common

PROTOCOL_VERSION = "2024-11-05"
LEVELS = ("minimal", "low", "medium", "high", "max")


def detect(args: dict[str, Any]) -> str:
    explicit = args.get("workspace")
    if isinstance(explicit, str) and explicit.strip():
        return common.resource_name(explicit)
    cwd = args.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        cwd = os.getcwd()
    return common.workspace_from_dir(cwd)


def tool_search(args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "error: empty query"
    limit = min(int(args.get("limit", 5)), 25)
    workspace = detect(args)
    common.ensure_workspace(workspace)
    result = common.api(
        f"/workspaces/{workspace}/search",
        {"query": query, "limit": limit},
        timeout=20,
    )
    items = result if isinstance(result, list) else result.get("items", [])
    if not items:
        return "no results"
    lines = []
    for item in items[:limit]:
        metadata = item.get("metadata") or {}
        created = str(item.get("created_at", ""))[:10]
        lines.append(
            f"[{created}] peer={item.get('peer_id')} type={metadata.get('type', '?')} "
            f"| {str(item.get('content', ''))[:300]}"
        )
    return "\n---\n".join(lines)


def tool_conclusions(args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "error: empty query"
    limit = min(int(args.get("limit", 8)), 25)
    workspace = detect(args)
    observer = str(args.get("observer") or common.AGENT_PEER)
    # The deriver writes (agent-grok, agent-grok) while observe_others is off.
    # (agent-grok, dsidlo) stays empty, so the default observed peer is the agent.
    observed = str(args.get("observed") or common.AGENT_PEER)
    common.ensure_workspace(workspace)
    body = {
        "query": query,
        "observer": observer,
        "observed": observed,
        "top_k": limit,
        "method": "rrf",
    }
    try:
        result = common.api(
            f"/workspaces/{workspace}/documents/search",
            body,
            timeout=30,
        )
    except common.HonchoError as exc:
        if exc.status != 404:
            raise
        result = common.api(
            f"/workspaces/{workspace}/conclusions/query",
            {
                "query": query,
                "top_k": limit,
                "filters": {"observer": observer, "observed": observed},
            },
            timeout=30,
        )
    items = result if isinstance(result, list) else result.get("items", [])
    if not items:
        return "no conclusions"
    lines = []
    for item in items[:limit]:
        created = str(item.get("created_at", ""))[:10]
        lines.append(f"[{created}] {str(item.get('content', ''))[:1500]}")
    return "\n\n---\n\n".join(lines)


def tool_ask(args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "error: empty query"
    level = str(args.get("reasoning_level", "low"))
    if level not in LEVELS:
        return "error: reasoning_level must be minimal|low|medium|high|max"
    workspace = detect(args)
    peer = str(args.get("peer") or common.AGENT_PEER)
    # Omitted target: the dialectic reads the observer's own representation.
    target = args.get("target")
    common.ensure_workspace(workspace)
    # conclusion_search is honored by this checkout's dialectic. The API
    # already running on port 8300 fuses with RRF and ignores the field.
    payload: dict[str, Any] = {
        "query": query,
        "reasoning_level": level,
        "conclusion_search": "rrf",
    }
    if isinstance(target, str) and target.strip():
        payload["target"] = target.strip()
    result = common.api(
        f"/workspaces/{workspace}/peers/{peer}/chat",
        payload,
        timeout=120,
    )
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return json.dumps(result)[:2000]


def tool_store(args: dict[str, Any]) -> str:
    text = common.scrub(str(args.get("text", "")).strip())
    if not text:
        return "error: empty text"
    kind = str(args.get("kind", "note"))
    workspace = detect(args)
    session_name = common.resource_name(str(args.get("session") or "grok-manual"))
    peer = common.USER_PEER if kind == "prompt" else common.AGENT_PEER
    common.ensure_workspace(workspace)
    common.ensure_session(workspace, session_name)
    message = {
        "peer_id": peer,
        "content": text,
        "metadata": {"type": kind, "source": "grok-mcp"},
    }
    pieces = common.expand_chunks(message)
    for start in range(0, len(pieces), common.BATCH_SIZE):
        common.api(
            f"/workspaces/{workspace}/sessions/{session_name}/messages",
            {"messages": pieces[start : start + common.BATCH_SIZE]},
            timeout=20,
        )
    return (
        f"stored {len(pieces)} message(s) in {workspace}/{session_name} as {peer}"
    )


TOOLS = {
    "honcho_search": {
        "description": (
            "Fast semantic search over Honcho messages for the current "
            "workspace, both the user and agent-grok. Use for project history. "
            "Do not call on every turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
                "workspace": {
                    "type": "string",
                    "description": "Override. Default is git origin, or the flattened directory.",
                },
            },
            "required": ["query"],
        },
        "fn": tool_search,
    },
    "honcho_conclusions": {
        "description": (
            "Hybrid search (vector + full text + trigram) over Honcho "
            "conclusions. Default pair is agent-grok observing agent-grok. "
            "Pass observed=dsidlo for the user; that pair stays empty until "
            "observe_others is on. Use for distilled memory, not for finding a file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 8},
                "workspace": {"type": "string"},
                "observer": {"type": "string"},
                "observed": {"type": "string"},
            },
            "required": ["query"],
        },
        "fn": tool_conclusions,
    },
    "honcho_ask": {
        "description": (
            "Dialectic question against Honcho. Takes about 5-30 seconds. "
            "Default asks agent-grok about its own representation. Pass "
            "target=dsidlo to ask about the user. Do not call at session "
            "start and do not call on every turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "reasoning_level": {
                    "type": "string",
                    "enum": list(LEVELS),
                },
                "workspace": {"type": "string"},
                "peer": {
                    "type": "string",
                    "description": "Observer peer. Default agent-grok.",
                },
                "target": {
                    "type": "string",
                    "description": "Observed peer. Default is the observer itself. Pass dsidlo to ask about the user.",
                },
            },
            "required": ["query"],
        },
        "fn": tool_ask,
    },
    "honcho_store": {
        "description": (
            "Store one explicit note in Honcho. Automatic capture is the "
            "tailer's job. Secret-shaped text is scrubbed before it is sent."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["note", "prompt", "thought", "finding", "final"],
                },
                "workspace": {"type": "string"},
                "session": {"type": "string"},
            },
            "required": ["text"],
        },
        "fn": tool_store,
    },
}


def send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = request.get("method", "")
        request_id = request.get("id")
        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "grok-honcho", "version": "0.1.0"},
                    },
                }
            )
        elif method == "notifications/initialized" or method.startswith("notifications/"):
            continue
        elif method == "tools/list":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": name,
                                "description": spec["description"],
                                "inputSchema": spec["input_schema"],
                            }
                            for name, spec in TOOLS.items()
                        ]
                    },
                }
            )
        elif method == "tools/call":
            params = request.get("params") or {}
            name = params.get("name", "")
            args = params.get("arguments") or {}
            spec = TOOLS.get(name)
            if spec is None:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32602, "message": f"unknown tool {name}"},
                    }
                )
                continue
            try:
                text = spec["fn"](args)
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"content": [{"type": "text", "text": str(text)}]},
                    }
                )
            except Exception as exc:  # fail the tool call, keep the server up
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [{"type": "text", "text": f"error: {exc}"}],
                            "isError": True,
                        },
                    }
                )
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        elif method in ("resources/list", "prompts/list"):
            key = "resources" if method.startswith("resources") else "prompts"
            send({"jsonrpc": "2.0", "id": request_id, "result": {key: []}})
        elif request_id is not None:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"unknown method {method}"},
                }
            )


if __name__ == "__main__":
    main()
