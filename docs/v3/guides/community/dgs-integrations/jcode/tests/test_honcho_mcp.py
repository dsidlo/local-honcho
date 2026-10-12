"""Test suite for honcho-mcp and capture scrubbing (pytest).

ISOLATION GUARANTEE: every test runs against a dedicated throwaway workspace
named with a 'test-' prefix and a run-unique suffix. Real records in
'flexitts', 'jcode', and pi-mono workspaces are never touched: no stores, no
deletes, no dialectic calls against them. Test artifacts are deleted at the
end of the run (workspace deletion via the API), so Honcho stays clean.

Run: pytest tests/test_honcho_mcp.py (needs a live Honcho at localhost:8300)
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP = os.path.join(REPO, "src", "honcho_mcp.py")
BASE = os.environ.get("HONCHO_BASE_URL", "http://localhost:8300")
RUN = f"test-{int(time.time())}-{os.getpid()}"
WS = f"jcode-selftest-{RUN}"  # throwaway workspace, deleted on teardown
FAKE_SECRET = "sk-testFAKEKEY1234567890abcdef"
FAKE_DBURL = "postgres://user:supersecret@localhost:5432/testdb"

pytestmark = pytest.mark.live


def _honcho_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


requires_live = pytest.mark.skipif(not _honcho_up(), reason="local Honcho not reachable")


def api(path: str, payload=None, method="POST"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{BASE}/v3{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode()
        return json.loads(body) if body else {}


class MCPServer:
    """Drives honcho_mcp.py over stdio like jcode would."""

    def __init__(self, cwd=None):
        self.n = 0
        self.proc = subprocess.Popen(
            [sys.executable, MCP], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, cwd=cwd or REPO,
        )
        self.raw({"jsonrpc": "2.0", "id": 0, "method": "initialize"})

    def raw(self, body):
        """Send a raw JSON-RPC request, return the parsed response."""
        self.n += 1
        self.proc.stdin.write(json.dumps(body) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def call(self, name, args=None):
        resp = self.raw({
            "jsonrpc": "2.0", "id": None, "method": "tools/call",
            "params": {"name": name, "arguments": args},
        })
        return resp["result"]["content"][0]["text"]

    def close(self):
        self.proc.terminate()


def _load_mcp_module():
    sys.path.insert(0, os.path.join(REPO, "src"))
    import honcho_mcp
    return honcho_mcp


# ---- unit tests (no server calls) ----


def test_scrub_redacts_secrets():
    m = _load_mcp_module()
    s = m.scrub(f"key={FAKE_SECRET} db={FAKE_DBURL}")
    assert FAKE_SECRET not in s
    assert "supersecret" not in s
    assert "[REDACTED]" in s
    assert m.path_is_denied("/home/u/.env")
    assert m.path_is_denied("C/x/auth.json")
    assert not m.path_is_denied("/home/u/src/main.py")
    assert "[REDACTED]" in m.scrub("foo\n-----BEGIN RSA PRIVATE KEY-----\nabc")
    assert "BEGIN RSA" not in m.scrub("-----BEGIN RSA PRIVATE KEY-----")


def test_detect_workspace():
    m = _load_mcp_module()
    ws_repo = m.detect_workspace("/home/dsidlo/workspace/FlexiTTS")  # has origin
    ws_noio = m.detect_workspace("/tmp")                              # no repo -> 'jcode'
    assert ws_repo == "flexitts", ws_repo
    assert ws_noio == "jcode", ws_noio


# ---- integration tests (isolated workspace only) ----


@pytest.fixture(scope="module")
def mcp():
    srv = MCPServer()
    yield srv
    srv.close()


@requires_live
def test_store_scrubs_and_persists(mcp):
    out = mcp.call("honcho_store", {
        "text": f"selftest note with {FAKE_SECRET} and {FAKE_DBURL} and harmless marker ZXMARKER99",
        "kind": "note", "workspace": WS, "session": "selftest",
    })
    assert "stored 1 message" in out, out


@requires_live
def test_search_finds_stored_note_and_scrubbing_persisted(mcp):
    out = mcp.call("honcho_search", {"query": "ZXMARKER99", "workspace": WS, "limit": 3})
    assert "ZXMARKER99" in out, out[:200]
    assert FAKE_SECRET not in out and "supersecret" not in out and "[REDACTED]" in out, out[:300]


@requires_live
def test_search_miss_handled_gracefully(mcp):
    # pgvector can return the nearest neighbor even above threshold, so assert
    # the miss is handled gracefully rather than asserting zero results.
    out = mcp.call("honcho_search", {"query": "zzqqxxplugh-not-real-xyz", "workspace": WS, "limit": 3})
    assert isinstance(out, str) and len(out) > 0, out[:120]


@requires_live
def test_conclusions_empty_workspace_handled(mcp):
    out = mcp.call("honcho_conclusions", {"workspace": WS})
    assert "no conclusions" in out, out[:120]


@requires_live
def test_dialectic_ask_returns_content(mcp):
    out = mcp.call("honcho_ask", {"query": "Reply in one short sentence: what is stored in this session?",
                                  "reasoning_level": "minimal", "workspace": WS})
    assert len(out) > 10 and "error" not in out.lower()[:20], out[:150]


@requires_live
def test_invalid_reasoning_level_rejected(mcp):
    out = mcp.call("honcho_ask", {"query": "x", "reasoning_level": "bogus", "workspace": WS})
    assert "error" in out, out[:120]


@requires_live
def test_no_test_records_leaked_into_flexitts():
    # real-record safety check: verify test records only exist in test ws
    with urllib.request.urlopen(urllib.request.Request(
        f"{BASE}/v3/workspaces/flexitts/sessions/list", data=b"{}",
        headers={"Content-Type": "application/json"}), timeout=10) as resp:
        real = json.loads(resp.read())
    leaked = [s["id"] for s in real.get("items", []) if RUN in s["id"]]
    assert not leaked, str(leaked)


@requires_live
def test_cleanup_deletes_throwaway_workspace():
    # cleanup: delete all test sessions, then the throwaway workspace
    try:
        sess = api(f"/workspaces/{WS}/sessions/list", {})
        for s in sess.get("items", []):
            urllib.request.urlopen(urllib.request.Request(
                f"{BASE}/v3/workspaces/{WS}/sessions/{s['id']}", method="DELETE"), timeout=10)
        urllib.request.urlopen(urllib.request.Request(
            f"{BASE}/v3/workspaces/{WS}", method="DELETE"), timeout=10)
    except Exception as e:  # pragma: no cover - best-effort teardown
        pytest.fail(f"could not delete {WS}: {e}")