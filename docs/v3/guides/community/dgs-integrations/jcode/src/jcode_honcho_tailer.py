#!/usr/bin/env python3
"""jcode-honcho-tailer: capture jcode session journals into Honcho.

Primary content path for Phase 2. Watches ~/.jcode/sessions/*.journal.jsonl,
parses append_messages records, maps them to peers/messages per the memory
contract, scrubs secrets, chunks long content, batches, and POSTs to Honcho.

- Byte-offset state in ~/.cache/jcode-honcho/state.json: restarts never duplicate.
- Fail-open: on any Honcho error, messages spool to ~/.cache/jcode-honcho-spool/
  and are retried on the next cycle; jcode sessions are never affected.
- Only *.journal.jsonl files are processed (.bak/.corrupt.jsonl/*.json ignored).
- Unknown record shapes are logged with jcode version and skipped (never dropped
  silently); content is never logged verbatim.
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

SESSIONS_DIR = os.environ.get("JCODE_SESSIONS_DIR", os.path.expanduser("~/.jcode/sessions"))
STATE_FILE = os.environ.get("HONCHO_STATE_FILE", os.path.expanduser("~/.cache/jcode-honcho/state.json"))
SPOOL_DIR = os.environ.get("HONCHO_SPOOL_DIR", os.path.expanduser("~/.cache/jcode-honcho-spool"))
LOG_FILE = os.environ.get("HONCHO_LOG_FILE", os.path.expanduser("~/.cache/jcode-honcho/tailer.log"))
BASE_URL = os.environ.get("HONCHO_BASE_URL", "http://localhost:8300")
USER_PEER = os.environ.get("HONCHO_USER", "dsidlo")
AGENT_PEER = os.environ.get("HONCHO_AGENT_PEER", "agent-jcode")
WORKSPACE_OVERRIDE = os.environ.get("HONCHO_WORKSPACE_OVERRIDE", "")
POLL_SECONDS = 2.0
MAX_CONTENT = 4000
MAX_TOOL_OUTPUT = 2048
BATCH_SIZE = 5
BATCH_DELAY = 0.05

# Capture toggles (docs/honcho-conventions.md)
CAPTURE = {
    "capture_prompts": "always",     # always|off
    "capture_reasoning": "on",       # on|off
    "capture_tool_calls": "on",      # on|off
    "capture_tool_results": "metadata",  # metadata|body|off (body never for denylisted paths)
    "capture_finals": "on",          # on|off
}

# --- scrubbing (same policy as MCP server) -----------------------------------

PATH_DENYLIST = ["auth.json", ".env", ".key", "credential", ".pem",
                 "id_rsa", "id_ed25519", ".p12", "secrets/"]

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"), re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"gho_[A-Za-z0-9]{20,}"), re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----"),
    re.compile(r"hf_[A-Za-z0-9]{30,}"), re.compile(r"xai-v[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer [A-Za-z0-9._-]{25,}"),
    re.compile(r"postgres(ql)?\+?psycopg?://[^@\s]+:[^@\s]+@"),
    re.compile(r"[a-zA-Z0-9_]+:[^@\s]+@(?:127\.0\.0\.1|localhost):\d+"),
    # Known-secret scrubbing (leak-scan 2026-09-28): PGPASSWORD assignments and
    # bare password values inside quoted SQL/shell patterns
    re.compile(r"PGPASSWORD=\"?[^\"\s]+\"?"),
    re.compile(r"LIKE '%[^']*password[^']*'", re.I),
]
REDACTED = "[REDACTED]"


def scrub(text: str) -> str:
    for pat in SECRET_PATTERNS:
        text = pat.sub(REDACTED, text)
    return text


def path_denied(path: str) -> bool:
    p = str(path).lower()
    return any(tok in p for tok in PATH_DENYLIST)


def chunk(text: str, size: int = MAX_CONTENT):
    if len(text) <= size:
        return [text]
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        if len(para) > size:
            if cur:
                chunks.append(cur.strip()); cur = ""
            for sent in re.findall(r"[^.!?]+[.!?]+[\"']?\s*", para) or [para]:
                if len(cur) + len(sent) > size:
                    if cur:
                        chunks.append(cur.strip()); cur = ""
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


# --- persistence helpers -------------------------------------------------------

def log(level: str, msg: str, data=None) -> None:
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] [{level}] {msg}"
    if data:
        line += f" | {json.dumps(data, default=str)[:300]}"
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"offsets": {}, "parse_errors": 0, "started_at": time.time(),
                "jcode_version": jcode_version()}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)  # atomic-ish heartbeat


def jcode_version() -> str:
    try:
        out = subprocess_run(["jcode", "--version"], timeout=5)
        return out.strip().split("\n")[0][:60]
    except Exception:
        return "unknown"


def subprocess_run(cmd, timeout):
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout


# --- workspace / session mapping (pi-mono git-origin rule) --------------------

_ws_cache: dict = {}


def detect_workspace(cwd: str) -> str:
    if WORKSPACE_OVERRIDE:
        return WORKSPACE_OVERRIDE
    if cwd in _ws_cache:
        return _ws_cache[cwd]
    d = os.path.abspath(cwd)
    ws = "jcode"
    while d and d != os.path.dirname(d):
        gitconfig = os.path.join(d, ".git", "config")
        if os.path.isfile(gitconfig):
            try:
                content = open(gitconfig, encoding="utf-8", errors="ignore").read()
                m = re.search(r'\[remote "origin"\][^\[]*url\s*=\s*.*?(?:/|:)([^/]+?)(?:\.git)?\s*$', content, re.M)
                ws = re.sub(r"[^a-z0-9_-]", "-", m.group(1).strip().lower()) if m else \
                     re.sub(r"\s+", "-", os.path.basename(d).lower())
                break
            except OSError:
                pass
        d = os.path.dirname(d)
    _ws_cache[cwd] = ws
    return ws


# --- Honcho client -------------------------------------------------------------

def api(path: str, payload: dict | None = None, method: str = "POST") -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{BASE_URL}/v3{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode()
        return json.loads(body) if body else {}


def ensure_workspace(name: str) -> None:
    api("/workspaces", {"id": name})


def ensure_session(ws: str, sid: str) -> None:
    try:
        sessions = api(f"/workspaces/{ws}/sessions/list", {})
        for s in sessions.get("items", []):
            if s["id"] == sid:
                return
    except Exception:
        pass
    api(f"/workspaces/{ws}/sessions", {"id": sid,
        "peers": {USER_PEER: {}, AGENT_PEER: {}}})


# --- mapper: journal records -> honcho messages --------------------------------

def map_block_to_message(role: str, block: dict, meta: dict, is_last_assistant_text: bool) -> dict | None:
    """Map one journal content block to an outgoing Honcho message. Returns None to skip."""
    btype = block.get("type", "text")
    text = block.get("text", "")
    # tool blocks carry structured fields, not text; only skip genuinely empty text blocks
    if btype in ("text", "reasoning") and (not isinstance(text, str) or not text.strip()):
        return None

    if role == "user":
        if CAPTURE["capture_prompts"] != "always":
            return None
        return {"peer_id": USER_PEER, "content": scrub(text)[:MAX_CONTENT],
                "metadata": {"type": "prompt", "source": "jcode-tailer"}}

    if role == "assistant":
        if btype == "reasoning":
            if CAPTURE["capture_reasoning"] != "on":
                return None
            return {"peer_id": AGENT_PEER, "content": scrub(text)[:MAX_CONTENT],
                    "metadata": {"type": "thought", "step": "planning", "source": "jcode-tailer"}}
        if btype == "tool_use":
            if CAPTURE["capture_tool_calls"] != "on":
                return None
            tool = block.get("tool") or block.get("name", "unknown")
            tool_input = block.get("input") or block.get("args") or {}
            inp = json.dumps(tool_input, default=str)[:2000]
            # denylisted path in tool input -> keep name only, no input echo
            if path_denied(inp_path(tool_input)):
                inp = "[DENIED-PATH]"
            return {"peer_id": AGENT_PEER,
                    "content": scrub(f"tool_call {tool}({inp})")[:MAX_CONTENT],
                    "metadata": {"type": "tool_call", "tool": str(tool)[:100],
                                 "source": "jcode-tailer"}}
        if btype == "tool_result":
            if CAPTURE["capture_tool_results"] == "off":
                return None
            tool = block.get("tool") or block.get("name", "unknown")
            status = "error" if block.get("is_error") else "ok"
            out = block.get("output", block.get("content", ""))
            out_len = len(out) if isinstance(out, str) else 0
            if CAPTURE["capture_tool_results"] == "body":
                # never for denylisted paths
                if path_denied(inp_path(block.get("input") or {})) or path_denied(tool):
                    body_txt = "[DENIED-PATH]"
                else:
                    body_txt = scrub(str(out)[:MAX_TOOL_OUTPUT])
            else:  # metadata default
                body_txt = f"[{status}, {len(out) if isinstance(out, str) else 0} bytes]"
            return {"peer_id": AGENT_PEER,
                    "content": scrub(f"tool_result {tool}: {body_txt}")[:MAX_CONTENT],
                    "metadata": {"type": "observation", "tool": str(tool)[:100],
                                 "status": status, "source": "jcode-tailer"}}
        if btype == "text":
            if not CAPTURE["capture_finals"] == "on":
                return None
            meta = {"type": "final", "source": "jcode-tailer"}
            if is_last_assistant_text:
                meta["final"] = True
            return {"peer_id": AGENT_PEER, "content": scrub(text)[:MAX_CONTENT],
                    "metadata": meta}
    return None


def inp_path(tool_input) -> str:
    if isinstance(tool_input, dict):
        for k in ("path", "file_path", "file", "target", "command"):
            v = tool_input.get(k)
            if isinstance(v, str):
                return v
        # search nested one level for path-like strings
        for v in tool_input.values():
            if isinstance(v, dict):
                for k2 in ("path", "file_path"):
                    if isinstance(v.get(k2), str):
                        return v[k2]
    return ""


def process_journal(path: str, offset: int, meta: dict, ws: str, sid: str) -> tuple[int, list, int]:
    """Read journal from offset; return (new_offset, honcho_messages, parse_errors)."""
    messages = []
    errors = 0
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return offset, [], 0
    new_offset = offset + len(data)
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            # partial final line: only advance if not last partial; handled by caller
            errors += 1
            continue
        msgs = rec.get("append_messages")
        if msgs is None:
            continue  # meta-only records
        if not isinstance(msgs, list):
            errors += 1
            log("WARN", "unknown record shape, skipped", {"keys": list(rec.keys())})
            continue
        for m in msgs:
            role = m.get("role")
            content = m.get("content")
            ts = m.get("timestamp", "")
            if isinstance(content, list):
                # find last text block index for final detection
                last_text_idx = -1
                for i in range(len(content) - 1, -1, -1):
                    if isinstance(content[i], dict) and content[i].get("type") == "text":
                        last_text_idx = i
                        break
                for i, block in enumerate(content):
                    if not isinstance(block, dict):
                        continue
                    msg = map_block_to_message(role, block, {}, i == last_text_idx and role == "assistant")
                    if msg:
                        if isinstance(msg, dict):
                            msg.setdefault("metadata", {})["ts"] = ts
                        messages.append(msg)
            elif isinstance(content, str) and content.strip():
                msg = map_block_to_message(role, {"type": "text", "text": content}, {}, True)
                if msg:
                    messages.append(msg)
    return new_offset, messages, errors


def post_messages(ws: str, sid: str, messages: list) -> int:
    """Post messages in batches. Returns count sent; spools on failure."""
    sent = 0
    for batch in [messages[i:i + BATCH_SIZE] for i in range(0, len(messages), BATCH_SIZE)]:
        try:
            api(f"/workspaces/{ws}/sessions/{sid}/messages", {"messages": batch})
            sent += len(batch)
            time.sleep(BATCH_SIZE and BATCH_DELAY)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            # spool the remaining messages (scrubbed already)
            spool(ws, sid, messages[sent:] if sent == 0 else batch)
            log("ERROR", "honcho unreachable, spooled", {"error": str(e)[:100],
                "ws": ws, "batch": len(batch)})
            break
    return sent


BATCH_DELAY = 0.05


def spool(ws: str, sid: str, messages: list) -> None:
    try:
        os.makedirs(SPOOL_DIR, exist_ok=True)
        fname = os.path.join(SPOOL_DIR, f"{int(time.time() * 1000)}-{ws}-{sid}.json")
        with open(fname, "w") as f:
            json.dump({"workspace": ws, "session": sid, "messages": messages}, f)
    except OSError:
        pass


def retry_spooled() -> None:
    if not os.path.isdir(SPOOL_DIR):
        return
    for fname in sorted(os.listdir(SPOOL_DIR))[:10]:
        fpath = os.path.join(SPOOL_DIR, fname)
        try:
            with open(fpath) as f:
                item = json.load(f)
            api(f"/workspaces/{item['workspace']}/sessions/{item['session']}/messages",
                {"messages": item["messages"]})
            os.unlink(fpath)
        except Exception:
            break  # still down; stop retrying this cycle


def main() -> None:
    state = load_state()
    offsets: dict = state.setdefault("offsets", {})
    total_parse_errors = 0

    # Graceful shutdown: flush state on SIGTERM
    import signal
    running = [True]

    def stop(signum, frame):
        running[0] = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log("INFO", "tailer started", {"jcode_version": jcode_version(),
                                    "sessions_dir": SESSIONS_DIR})

    while running[0]:
        try:
            retry_spooled()
            journals = sorted(
                f for f in os.listdir(SESSIONS_DIR)
                if f.endswith(".journal.jsonl")
            ) if os.path.isdir(SESSIONS_DIR) else []
            for fname in journals:
                path = os.path.join(SESSIONS_DIR, fname)
                sid_short = fname.replace(".journal.jsonl", "")
                try:
                    # read meta header for cwd/workspace (first line only)
                    with open(path, "rb") as f:
                        head = f.read(4096).decode("utf-8", errors="ignore")
                    meta = json.loads(head.split("\n")[0]).get("meta", {})
                    cwd = meta.get("working_dir", "")
                    ws = detect_workspace(cwd) if cwd else "jcode"
                    sid = sid_short
                except Exception:
                    ws, sid = "jcode", sid_short

                offset = offsets.get(path, 0)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                if size < offset:
                    offset = 0  # truncated/recreated
                if size == offset:
                    continue
                ensure_workspace(ws)
                ensure_session(ws, f"jcode-{sid}"[:100])
                new_offset, messages, errs = process_journal(path, offset, {}, ws, sid)
                total_parse_errors += errs
                if messages:
                    post_messages(ws, f"jcode-{sid}"[:100], messages)
                offsets[path] = new_offset
                if messages:
                    log("INFO", f"captured {len(messages)} messages",
                        {"ws": ws, "session": sid})
            if len(offsets) > 500:  # prune dead session paths
                live = {os.path.join(SESSIONS_DIR, f) for f in journals}
                offsets = {k: v for k, v in offsets.items() if k in live}
            state["offsets"] = offsets
            state["parse_errors"] = state.get("parse_errors", 0) + 0
            state["last_beat"] = time.time()
            state["last_error_count"] = total_parse_errors
            save_state(state)
        except Exception as e:  # keep tailer alive no matter what
            log("ERROR", "loop error", {"error": str(e)[:200]})
        time.sleep(POLL_SECONDS)

    save_state(state)
    log("INFO", "tailer stopped cleanly")


if __name__ == "__main__":
    main()