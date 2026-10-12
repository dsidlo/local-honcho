"""Capture-hook tests: drive src/jcode_honcho_capture.sh end to end.

The capture hook is a fire-and-forget jcode lifecycle observer: it posts
session/turn markers to Honcho and MUST NOT fail loudly on anything. These
tests run it against an in-process fake Honcho over HTTP and assert
exit codes plus recorded requests.

Run: pytest tests/test_capture_hook.py (offline, no real Honcho needed)
"""

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPTURE = os.path.join(REPO, "src", "jcode_honcho_capture.sh")


class FakeHoncho(BaseHTTPRequestHandler):
    """Records every request; responds 200/{} with no logging noise."""

    store = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        parsed = json.loads(body) if body else {}
        FakeHoncho.store.append({"path": self.path, "body": parsed})
        payload = json.dumps({}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):  # silence
        pass


@pytest.fixture()
def fake_honcho():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHoncho)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    FakeHoncho.store = []
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def run_capture(event, cwd=None, base_url="http://127.0.0.1:1"):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/root"),
        "JCODE_HOOK_EVENT": event,
        "JCODE_HOOK_SESSION_ID": "sess-test-1",
        "JCODE_HOOK_CWD": str(cwd or "/tmp/jcode-honcho-capture-no-repo"),
        "JCODE_HOOK_MODEL": "test-model",
        "JCODE_HOOK_STATUS": "ok",
        "JCODE_HOOK_DURATION_MS": "1234",
        "HONCHO_BASE_URL": base_url,
    }
    return subprocess.run(
        ["bash", CAPTURE], capture_output=True, text=True, env=env, timeout=60
    )


def marker_by_event(event):
    for rec in FakeHoncho.store:
        msgs = rec["body"].get("messages") if isinstance(rec["body"], dict) else None
        if msgs and msgs[0].get("metadata", {}).get("event") == event:
            return rec, msgs[0]
    raise AssertionError(f"no marker for event {event!r} in {FakeHoncho.store!r}")  # pragma: no cover - assertion-only path


class TestCaptureHook:
    def test_session_start_creates_session_and_marker(self, fake_honcho):
        proc = run_capture("session_start", base_url=fake_honcho)
        assert proc.returncode == 0
        matches = [r for r in FakeHoncho.store if r["path"].endswith("/sessions")]
        session_call = matches[0]  # IndexError would fail the test just as loudly
        assert session_call["body"]["id"] == "jcode-sess-test-1"
        assert set(session_call["body"]["peers"]) == {"dsidlo", "agent-jcode"}
        rec, marker = marker_by_event("session_start")
        assert marker["content"] == "session started (jcode)"
        assert marker["peer_id"] == "agent-jcode"
        assert marker["metadata"]["type"] == "thought"

    def test_session_end_posts_marker_only(self, fake_honcho):
        proc = run_capture("session_end", base_url=fake_honcho)
        assert proc.returncode == 0
        rec, marker = marker_by_event("session_end")
        assert marker["content"] == "session ended (jcode)"
        # session_end does not re-create the session
        assert not any(r["path"].endswith("/sessions") for r in FakeHoncho.store)

    def test_turn_end_bookmark_includes_model_status_duration(self, fake_honcho):
        proc = run_capture("turn_end", base_url=fake_honcho)
        assert proc.returncode == 0
        rec, marker = marker_by_event("turn_end")
        assert marker["content"] == "turn bookmark: model=test-model status=ok duration=1234ms"
        assert not any(r["path"].endswith("/sessions") for r in FakeHoncho.store)

    def test_unknown_event_is_silent_noop(self, fake_honcho, tmp_path):
        proc = run_capture("some_new_hook", cwd=tmp_path, base_url=fake_honcho)
        assert proc.returncode == 0
        assert FakeHoncho.store == []

    def test_unreachable_server_fire_and_forget_exit_zero(self, tmp_path):
        # nothing listens on 127.0.0.1:1; the hook must still exit 0 silently
        proc = run_capture("session_start", cwd=tmp_path)
        assert proc.returncode == 0
        assert proc.stderr.strip() == ""

    def test_workspace_overrides_env_var(self, fake_honcho, tmp_path):
        # HONCHO_WORKSPACE_OVERRIDE-style input: cwd without git forces
        # 'jcode', and the session path carries it
        proc = run_capture("turn_end", cwd=tmp_path, base_url=fake_honcho)
        assert proc.returncode == 0
        rec, _ = marker_by_event("turn_end")
        assert rec["path"].startswith("/v3/workspaces/jcode/sessions/jcode-sess-test-1")

    def test_git_origin_cwd_maps_to_workspace(self, fake_honcho, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://github.com/acme/capture-cwd-test.git\n'
        )
        proc = run_capture("session_start", cwd=tmp_path, base_url=fake_honcho)
        assert proc.returncode == 0
        session_call = next(r for r in FakeHoncho.store if r["path"].endswith("/sessions"))
        assert session_call["path"].startswith("/v3/workspaces/capture-cwd-test/")
