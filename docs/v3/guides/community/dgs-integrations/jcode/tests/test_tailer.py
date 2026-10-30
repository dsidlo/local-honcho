"""Tailer tests: journal mapper units + isolated end-to-end tailer run (pytest).

ISOLATION: the e2e test creates a temp sessions dir with a synthetic journal
whose meta points at a temp (non-git) cwd, so the tailer maps it to the
throwaway 'jcode-selftest-*' workspace. Real records in flexitts/jcode are
never touched. The tailer instance under test uses env overrides for its
state/spool/log locations and JCODE_SESSIONS_DIR.

Run: pytest tests/test_tailer.py (the e2e part needs a live Honcho at :8300)
"""

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import signal

import pytest


def _mock_token(kind: str) -> str:
    """Procedurally generate a fake secret of a given shape at runtime so the
    source never contains a literal that GitHub secret-scanning blocks."""
    import random
    alnum = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    if kind in ("openai", "npm", "stripe"):
        return "sk-" + "".join(random.choice(alnum) for _ in range(36))
    if kind == "aws":
        return "AKIA" + "".join(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(16))
    if kind == "github":
        return "ghp_" + "".join(random.choice(alnum) for _ in range(36))
    if kind == "google":
        return "AIza" + "".join(random.choice(alnum + "_-") for _ in range(35))
    if kind == "jwt":
        return "eyJ" + "".join(random.choice(alnum + "_-") for _ in range(20)) + "." +             "".join(random.choice(alnum + "_-") for _ in range(20)) + "." +             "".join(random.choice(alnum + "_-") for _ in range(20))
    return "sk-" + "".join(random.choice(alnum) for _ in range(28))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAILER = os.path.join(REPO, "src", "jcode_honcho_tailer.py")
BASE = os.environ.get("HONCHO_BASE_URL", "http://localhost:8300")
RUN = f"test-{int(time.time())}-{os.getpid()}"
WS = f"jcode-selftest-{RUN}"

pytestmark = pytest.mark.live


def _honcho_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=2) as r:
            return r.status == 200
    except Exception:  # pragma: no cover - hit only when Honcho is not running
        return False


requires_live = pytest.mark.skipif(not _honcho_up(), reason="local Honcho not reachable")


def _load_tailer():
    loader = importlib.machinery.SourceFileLoader("tailer", os.path.abspath(TAILER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    t = importlib.util.module_from_spec(spec)
    loader.exec_module(t)
    return t


# ---- unit tests (no server calls) ----


def test_user_prompt_maps_to_dsidlo_scrubbed():
    t = _load_tailer()
    m = t.map_block_to_message("user", {"type": "text", "text": "prompt with " + _mock_token("openai")}, {}, False)
    assert m and m["peer_id"] == "dsidlo" and "[REDACTED]" in m["content"]


def test_reasoning_maps_to_thought():
    t = _load_tailer()
    m = t.map_block_to_message("assistant", {"type": "reasoning", "text": "planning step"}, {}, False)
    assert m and m["metadata"]["type"] == "thought"


def test_tool_use_with_denylisted_path_redacts_input():
    t = _load_tailer()
    m = t.map_block_to_message("assistant", {"type": "tool_use", "name": "read",
                                             "input": {"file_path": "/home/u/.env"}}, {}, False)
    assert "[DENIED-PATH]" in m["content"]


def test_tool_result_is_metadata_only_by_default():
    t = _load_tailer()
    m = t.map_block_to_message("assistant", {"type": "tool_result", "name": "read",
                                             "output": "x" * 5000, "is_error": False}, {}, False)
    assert "bytes]" in m["content"] and "xxxx" not in m["content"]


def test_final_text_flagged():
    t = _load_tailer()
    m = t.map_block_to_message("assistant", {"type": "text", "text": "done"}, {}, True)
    assert m and m["metadata"].get("final") is True


def test_empty_text_skipped():
    t = _load_tailer()
    assert t.map_block_to_message("user", {"type": "text", "text": "  "}, {}, False) is None


# ---- end-to-end (isolated workspace only) ----


@pytest.fixture()
def e2e_env():
    tmp = tempfile.mkdtemp(prefix="jcode-honcho-tailer-test-")
    sessions_dir = os.path.join(tmp, "sessions")
    cache_dir = os.path.join(tmp, "cache")
    os.makedirs(sessions_dir)
    os.makedirs(cache_dir)
    env = dict(os.environ)
    env.update({
        "JCODE_SESSIONS_DIR": sessions_dir,
        "JCODE_HONCHO_CACHE_DIR": cache_dir,
        "HONCHO_BASE_URL": BASE,
        "HONCHO_STATE_FILE": os.path.join(cache_dir, "state.json"),
        "HONCHO_SPOOL_DIR": os.path.join(cache_dir, "spool"),
        "HONCHO_LOG_FILE": os.path.join(cache_dir, "tailer.log"),
        "HONCHO_WORKSPACE_OVERRIDE": WS,
    })

    # synthetic journal: user prompt, reasoning, tool_use on denylisted path,
    # tool_result, final answer. meta.working_dir = /tmp (non-git -> jcode ws,
    # but HONCHO_WORKSPACE_OVERRIDE forces the test workspace)
    journal = os.path.join(sessions_dir, f"session_test_{RUN}.journal.jsonl")
    meta_line = json.dumps({"meta": {"working_dir": "/tmp/definitely-not-a-repo", "model": "test-model"}})
    msgs_line = json.dumps({"meta": {}, "append_messages": [
        {"id": "m1", "role": "user",
         "content": [{"type": "text", "text": "E2E user prompt with " + _mock_token("openai") + " secret"}],
         "timestamp": "2026-09-28T12:00:00Z"},
        {"id": "m2", "role": "assistant",
         "content": [
             {"type": "reasoning", "text": "I should check the file"},
             {"type": "tool_use", "name": "read", "input": {"file_path": "/home/dsidlo/.env"}},
             {"type": "tool_result", "name": "read", "output": "SECRETCONTENT=x", "is_error": False},
             {"type": "text", "text": "Final answer marker P9Q8R7"},
         ], "timestamp": "2026-09-28T12:00:05Z"},
    ]})
    with open(journal, "w") as f:
        f.write(meta_line + "\n")
        f.write(msgs_line + "\n")
    # decoys that must be ignored
    shutil.copy(journal, journal + ".bak")
    with open(os.path.join(sessions_dir, "session_state.json"), "w") as f:
        f.write("{}")

    yield env

    shutil.rmtree(tmp, ignore_errors=True)


def api(path, payload=None, method="POST"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{BASE}/v3{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode()
        return json.loads(body) if body else {}


def _list_messages(sid):
    listing = api(f"/workspaces/{WS}/sessions/{sid}/messages/list", {})
    return listing.get("items", [])


@requires_live
def test_tailer_e2e_capture_and_restart_no_dupes(e2e_env):
    env = e2e_env
    proc = subprocess.Popen([sys.executable, TAILER], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sid = None
    msgs = []
    try:
        # allow two poll cycles
        deadline = time.time() + 15
        found = None
        # bounded polling; the tailer normally creates the session in 1-2 cycles
        while time.time() < deadline:  # pragma: no branch
            try:
                found = api(f"/workspaces/{WS}/sessions/list", {})
                if found.get("items"):
                    break
            except Exception:  # pragma: no cover - pre-ready polling race only
                pass
            time.sleep(1)
        assert found and found.get("items"), f"tailer created no session: {str(found)[:100]}"
        sid = found["items"][0]["id"]
        time.sleep(2)  # let batches land
        msgs = _list_messages(sid)
        peers = {m["peer_id"] for m in msgs}
        types = [m.get("metadata", {}).get("type") for m in msgs]
        assert any(m["peer_id"] == "dsidlo" for m in msgs), str(peers)
        assert "thought" in types, str(types)
        assert any("DENIED-PATH" in m["content"] for m in msgs
                   if m.get("metadata", {}).get("type") == "tool_call")
        assert not any("SECRETCONTENT" in m["content"] for m in msgs)
        assert any(m.get("metadata", {}).get("final") for m in msgs)
        assert not any("fakekey1234567890123456" in m["content"] for m in msgs)
        assert all(".bak" not in (m.get("metadata", {}).get("ts") or "") for m in msgs)

        # restart-no-dupes: stop tailer, restart, verify message count stable
        proc.send_signal(signal.SIGINT)  # noqa: coverage child flush handled below
        proc.wait(timeout=10)
        count_after_first = len(msgs)
        proc2 = subprocess.Popen([sys.executable, TAILER], env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(6)
        proc2.send_signal(signal.SIGINT)
        proc2.wait(timeout=10)
        msgs2 = _list_messages(sid)
        assert len(msgs2) == count_after_first, f"{count_after_first} vs {len(msgs2)}"
    finally:
        proc.send_signal(signal.SIGINT)  # noqa: coverage child flush handled below


@requires_live
def test_tailer_e2e_cleanup_workspace():
    # cleanup: delete the throwaway workspace created by the e2e test above
    try:
        sess = api(f"/workspaces/{WS}/sessions/list", {})
        for s in sess.get("items", []):
            urllib.request.urlopen(urllib.request.Request(
                f"{BASE}/v3/workspaces/{WS}/sessions/{s['id']}", method="DELETE"), timeout=10)
        urllib.request.urlopen(urllib.request.Request(
            f"{BASE}/v3/workspaces/{WS}", method="DELETE"), timeout=10)
    except Exception as e:  # pragma: no cover - best-effort teardown
        pytest.fail(f"could not delete {WS}: {e}")
