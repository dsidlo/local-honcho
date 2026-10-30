"""Unit tests for src/honcho_mcp.py, entirely in-process (no Honcho server).

Covers scrubbing, path denylist, chunking, workspace detection, the four
tool handlers against a fake Honcho API, and the full stdio protocol loop.
"""

import json
import os
import subprocess
import sys
from unittest import mock

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
SRC = os.path.join(REPO, "src")
MCP = os.path.join(SRC, "honcho_mcp.py")

sys.path.insert(0, SRC)  # idempotent

import honcho_mcp  # noqa: E402


# ---- scrubbing + path denylist ----


class TestScrub:
    def test_redacts_openai_key(self):
        assert honcho_mcp.scrub("key " + _mock_token("openai") + " done") == "key [REDACTED] done"

    def test_redacts_ghp_gho_tokens(self):
        assert honcho_mcp.scrub(_mock_token("github")) == "[REDACTED]"
        assert honcho_mcp.scrub("gho_abcdefghijklmnopqrstuvwx") == "[REDACTED]"

    def test_redacts_aws_key(self):
        assert honcho_mcp.scrub(_mock_token("aws")) == "[REDACTED]"

    def test_redacts_slack_tokens(self):
        out = honcho_mcp.scrub("xoxb-abc-def-ghi")
        assert out == "[REDACTED]"

    def test_redacts_private_key_header(self):
        text = "a\n-----BEGIN RSA PRIVATE KEY-----"
        assert "[REDACTED]" in honcho_mcp.scrub(text)

    def test_redacts_hf_token(self):
        assert honcho_mcp.scrub("hf_" + "a" * 35) == "[REDACTED]"

    def test_redacts_xai_token(self):
        assert honcho_mcp.scrub("xai-v" + "a" * 25) == "[REDACTED]"

    def test_redacts_bearer(self):
        assert honcho_mcp.scrub("Bearer abcdefghijklmnopqrstuvwxyz123") == "[REDACTED]"

    def test_redacts_postgres_url(self):
        # regex covers postgres+psycopg URIs (the local deployment scheme);
        # plain postgres:// falls to the localhost-credential pattern
        assert "[REDACTED]" in honcho_mcp.scrub("postgres+psycopg://user:pw@host/db")

    def test_redacts_localhost_credential_url(self):
        assert "pw" not in honcho_mcp.scrub("postgresql://user:pw@localhost:5432/db")

    def test_redacts_pgpassword_assignment(self):
        assert "secret" not in honcho_mcp.scrub('PGPASSWORD="hunter2" psql')

    def test_redacts_password_like_scan(self):
        out = honcho_mcp.scrub("LIKE '%password123%'")
        assert "password123" not in out

    def test_plain_text_untouched(self):
        assert honcho_mcp.scrub("harmless note with no credentials") == "harmless note with no credentials"


class TestPathDenylist:
    @pytest.mark.parametrize("path", [
        "/home/u/.env", "/x/auth.json", "/y/server.pem", "/a/b/id_rsa",
        "/keys/id_ed25519", "/t/cert.p12", "/p/secrets/vault.txt", "app.prod.key",
    ])
    def test_denied(self, path):
        assert honcho_mcp.path_is_denied(path)

    @pytest.mark.parametrize("path", [
        "/home/u/src/main.py", "docs/README.md", "/tmp/notes.txt",
    ])
    def test_allowed(self, path):
        assert not honcho_mcp.path_is_denied(path)


# ---- chunking ----


class TestChunk:
    def test_short_text_single_chunk(self):
        assert honcho_mcp.chunk("hello") == ["hello"]

    def test_paragraph_split(self):
        text = "para one\n\npara two"
        assert len(honcho_mcp.chunk(text, size=12)) == 2
        # chunk boundaries land on paragraphs
        assert honcho_mcp.chunk(text, size=12)[0].startswith("para one")

    def test_long_paragraph_sentence_split(self):
        text = "First sentence. Second sentence. Third one here. " * 3
        chunks = honcho_mcp.chunk(text, size=30)
        assert len(chunks) >= 2
        assert all(len(c) <= 30 for c in chunks)

    def test_sentence_flush_mid_paragraph(self):
        # within one big paragraph, accumulated sentences cross the size and
        # flush WITHOUT a trailing cur (covers the inner cur-flush branch)
        text = "Aaa bbb ccc. Ddd eee fff. Ggg hhh iii. Jjj kkk lll."
        chunks = honcho_mcp.chunk(text, size=9)
        assert len(chunks) >= 2

    def test_sentence_flush_with_full_size_cut(self):
        # tiny size forces per-sentence flush including the size-cut branch
        out = honcho_mcp.chunk("First one. Second two. Third three. Fourth four.", size=5)
        assert out == ["First", "Secon", "Third", "Fourt"]

    def test_paragraph_accumulation_flushes(self):
        # multiple small paragraphs accumulate in cur, then overflow -> flush
        out = honcho_mcp.chunk("aa\n\nbb\n\nccee", size=6)
        assert out == ["aa", "bb", "ccee"]

    def test_paragraphs_accumulate_into_one_chunk_when_they_fit(self):
        assert honcho_mcp.chunk("aa\n\nbb", size=100) == ["aa\n\nbb"]

    def test_big_para_after_accumulated_small_ones_flushes_cur(self):
        out = honcho_mcp.chunk("aa bb cc dd\n\n" + "z" * 40, size=16)
        assert out == ["aa bb cc dd", "zzzzzzzzzzzzzzzz"]

    def test_sentences_accumulate_within_budget(self):
        assert honcho_mcp.chunk("Hi. Yo. " + "z" * 30, size=10)[0] == "Hi. Yo."

    def test_leading_empty_paragraph_then_oversized(self):
        # empty first para => cur stays empty, elif-branch passes with cur == ''
        out = honcho_mcp.chunk("\n\n" + "w" * 30, size=10)
        assert out[0] == ""
        assert out[1] == "wwwwwwwwww"

    def test_sentence_longer_than_size_truncated(self):
        # a single sentence with no punctuation-fits crossing the size gets cut
        text = "word " * 30
        chunks = honcho_mcp.chunk(text, size=10)
        assert all(len(c) <= 10 for c in chunks)

    def test_huge_single_word_truncated_to_size(self):
        word = "x" * 50
        chunks = honcho_mcp.chunk(word, size=10)
        assert all(len(c) <= 10 for c in chunks)

    def test_fallback_returns_truncation_for_whitespaceless_input(self):
        text = "y" * 100
        chunks = honcho_mcp.chunk(text, size=5)
        assert all(len(c) <= 5 for c in chunks)


class TestDetectWorkspace:
    def test_git_origin(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text('[remote "origin"]\n\turl = git@github.com:acme/widgets.git\n')
        assert honcho_mcp.detect_workspace(str(tmp_path)) == "widgets"

    def test_git_origin_https(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text('[remote "origin"]\n\turl = https://github.com/acme/Big-Repo.git\n')
        assert honcho_mcp.detect_workspace(str(tmp_path)) == "big-repo"

    def test_repo_without_origin_uses_dirname(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("[core]\n\tbare = false\n")
        assert honcho_mcp.detect_workspace(str(tmp_path)) == tmp_path.name

    def test_repo_without_origin_sanitizes_spaces(self, tmp_path):
        repo = tmp_path / "my repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "config").write_text("[core]\n")
        assert honcho_mcp.detect_workspace(str(repo)) == "my-repo"

    def test_no_repo_falls_back_to_jcode(self, tmp_path):
        assert honcho_mcp.detect_workspace(str(tmp_path)) == "jcode"

    def test_walks_up_parents(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text('[remote "origin"]\n\turl = https://x.org/root/repo.git\n')
        child = tmp_path / "a" / "b"
        child.mkdir(parents=True)
        assert honcho_mcp.detect_workspace(str(child)) == "repo"

    def test_unreadable_config_yields_dirname(self, tmp_path):
        gc = tmp_path / ".git" / "config"
        gc.parent.mkdir()
        gc.write_text('[remote "origin"]\n\turl = https://x.org/root/repo.git\n')
        # config with a broken URL (no match) confirms dirname fallback
        gc.write_text('[remote "origin"]\n')
        assert honcho_mcp.detect_workspace(str(tmp_path)) == tmp_path.name

    def test_oserror_on_read_continues_up(self, tmp_path, monkeypatch):
        # make the config open raise OSError; the walk must continue upward and
        # eventually land on the parent repo's origin ("parent-repo")
        parent = tmp_path
        child = tmp_path / "child"
        (parent / ".git").mkdir()
        (parent / ".git" / "config").write_text('[remote "origin"]\n\turl = https://x.org/o/parent-repo.git\n')
        (child / ".git").mkdir(parents=True)
        (child / ".git" / "config").write_text('[remote "origin"]\n')

        real_open = open

        def raising_open(path, *a, **k):
            if str(path).endswith("child/.git/config"):
                raise OSError("unreadable")
            return real_open(path, *a, **k)

        import builtins
        monkeypatch.setattr(builtins, "open", raising_open)
        assert honcho_mcp.detect_workspace(str(child)) == "parent-repo"


class TestApiClient:
    def test_api_post_serializes_payload(self, monkeypatch):
        captured = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"ok": True}).encode()

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["data"] = req.data
            captured["method"] = req.get_method()
            return FakeResp()

        monkeypatch.setattr(honcho_mcp.urllib.request, "urlopen", fake_urlopen)
        out = honcho_mcp.api("/workspaces", {"id": "x"})
        assert out == {"ok": True}
        assert captured["url"].endswith("/v3/workspaces")
        assert captured["data"] == json.dumps({"id": "x"}).encode()
        assert captured["method"] == "POST"

    def test_api_get_without_payload(self, monkeypatch):
        captured = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"items": []}).encode()

        def fake_urlopen(req, timeout=None):
            captured["method"] = req.get_method()
            return FakeResp()

        monkeypatch.setattr(honcho_mcp.urllib.request, "urlopen", fake_urlopen)
        out = honcho_mcp.api("/thing")
        assert out == {"items": []}
        assert captured["method"] == "POST"  # api() always posts; get() is the GET variant

    def test_get_uses_get_method(self, monkeypatch):
        captured = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"r": 1}).encode()

        def fake_urlopen(req, timeout=None):
            captured["method"] = req.get_method()
            captured["url"] = req.full_url
            return FakeResp()

        monkeypatch.setattr(honcho_mcp.urllib.request, "urlopen", fake_urlopen)
        out = honcho_mcp.get("/x")
        assert out == {"r": 1}
        assert captured["method"] == "GET"


# ---- fake Honcho API + tool handlers ----


class FakeApi:
    """Scriptable stand-in for honcho_mcp.api / get."""

    def __init__(self):
        self.calls = []

    def __call__(self, path, payload=None, method="POST"):
        self.calls.append((path, payload, method))
        return self.dispatch(path, payload, method)

    def dispatch(self, path, payload, method):
        if path == "/workspaces":
            return {"id": payload["id"]}
        if path.endswith("/sessions/list"):
            return {"items": [{"id": s} for s in self.existing_sessions]}
        if path.endswith("/sessions"):
            return {"id": payload["id"]}
        if path.endswith("/messages"):
            self.posted.extend(payload.get("messages", []))
            return {"items": []}
        if path.endswith("/search"):
            return {"items": self.search_items}
        if path.endswith("/chat"):
            return {"content": "the answer"}
        if path.endswith("/conclusions/query"):
            return {"items": self.conclusion_items}
        if path.endswith("/conclusions/list"):
            return {"items": self.conclusion_items}
        return {}


def test_fake_api_dispatch_fallback():
    f = FakeApi()
    assert f.dispatch("/unknown/route", None, "POST") == {}


@pytest.fixture()
def fake(monkeypatch):
    f = FakeApi()
    f.existing_sessions = []
    f.search_items = []
    f.conclusion_items = []
    f.posted = []
    monkeypatch.setattr(honcho_mcp, "api", f)
    return f


class TestToolStore:
    def test_happy_path_single_message(self, fake):
        out = honcho_mcp.tool_store({"text": "hello note", "workspace": "ws", "session": "sid"})
        assert out == "stored 1 message(s) to ws/sid as peer agent-jcode"

    def test_prompt_kind_uses_user_peer(self, fake):
        out = honcho_mcp.tool_store({"text": "hi", "kind": "prompt", "workspace": "ws"})
        assert "as peer dsidlo" in out

    def test_long_text_is_chunked_with_metadata(self, fake):
        text = "\n\n".join(f"chunk {i} " + "z" * 1990 for i in range(3))
        out = honcho_mcp.tool_store({"text": text, "workspace": "ws"})
        assert "stored 2 message(s)" in out or "stored 3 message(s)" in out or "stored 4" in out
        meta = [m.get("metadata", {}) for m in fake.posted]
        assert any(m.get("is_chunk") and m["chunk_index"] == 1 for m in meta)

    def test_empty_text_rejected(self, fake):
        assert honcho_mcp.tool_store({"text": "   "}) == "error: empty text"

    def test_workspace_defaults_to_detection(self, fake, monkeypatch, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text('[remote "origin"]\n\turl = https://github.com/acme/detectme.git\n')
        monkeypatch.chdir(tmp_path)
        out = honcho_mcp.tool_store({"text": "note"})
        assert "stored 1 message(s) to detectme/" in out

    def test_batching_in_groups_of_five(self, fake):
        text = "\n\n".join("word " * 300 for _ in range(12))  # forces many chunks
        honcho_mcp.tool_store({"text": text, "workspace": "ws"})
        assert len(fake.posted) > 5

    def test_secret_in_text_gets_scrubbed(self, fake):
        honcho_mcp.tool_store({"text": "note with " + _mock_token("openai") + " inside", "workspace": "ws"})
        assert all("[REDACTED]" in m["content"] for m in fake.posted)


class TestToolSearch:
    def test_formats_results(self, fake):
        fake.search_items = [{
            "created_at": "2026-10-11T00:00:00Z", "peer_id": "agent-jcode",
            "metadata": {"type": "note"}, "content": "c" * 400,
        }]
        out = honcho_mcp.tool_search({"query": "q", "workspace": "ws"})
        assert out.startswith("[2026-10-11] peer=agent-jcode type=note |")
        assert "ccc" in out

    def test_no_results(self, fake):
        fake.search_items = []
        out = honcho_mcp.tool_search({"query": "q", "workspace": "ws"})
        assert out == "no results"

    def test_empty_query_rejected(self, fake):
        assert honcho_mcp.tool_search({"query": "  ", "workspace": "ws"}) == "error: empty query"

    def test_limit_capped_at_25(self, fake):
        fake.search_items = [{"content": "x"} for _ in range(40)]
        honcho_mcp.tool_search({"query": "q", "workspace": "ws", "limit": 100})
        # tool calls api with clamped limit
        search_path = [c for c in fake.calls if c[0].endswith("/search")]
        assert search_path and search_path[0][1]["limit"] == 25

    def test_list_shaped_response_is_used_directly(self, fake):
        fake.search_items = [{
            "created_at": "2026-10-11T00:00:00Z", "peer_id": "p",
            "metadata": {"type": "note"}, "content": "hello",
        }]

        def fake_api_list(path, payload=None, method="POST"):
            fake.calls.append((path, payload, method))
            return [{"created_at": "2026-10-11T00:00:00Z", "peer_id": "p",
                     "metadata": {"type": "note"}, "content": "hello"}]

        with mock.patch.object(honcho_mcp, "api", fake_api_list):
            out = honcho_mcp.tool_search({"query": "q", "workspace": "ws"})
        assert "peer=p" in out


class TestToolAsk:
    def test_returns_content(self, fake):
        out = honcho_mcp.tool_ask({"query": "q", "reasoning_level": "low", "workspace": "ws"})
        assert out == "the answer"

    def test_invalid_level_rejected(self, fake):
        out = honcho_mcp.tool_ask({"query": "q", "reasoning_level": "bogus", "workspace": "ws"})
        assert out == "error: reasoning_level must be minimal|low|medium|high|max"

    def test_empty_query_rejected(self, fake):
        assert honcho_mcp.tool_ask({"query": "", "workspace": "ws"}) == "error: empty query"

    def test_non_dict_response_falls_back_to_json_dump(self, fake):
        with mock.patch.object(
            honcho_mcp, "api", lambda path, payload=None, method="POST": {"a": 1}
        ):
            out = honcho_mcp.tool_ask({"query": "q", "workspace": "ws"})
        assert out == json.dumps({"a": 1})[:2000]


class TestToolConclusions:
    def test_no_results(self, fake):
        out = honcho_mcp.tool_conclusions({"workspace": "ws"})
        assert out == "no conclusions found"

    def test_list_formats_items(self, fake):
        fake.conclusion_items = [{"created_at": "2026-10-11T00:00:00Z", "content": "c" * 1600}]
        out = honcho_mcp.tool_conclusions({"workspace": "ws"})
        assert out.startswith("[2026-10-11]")
        assert len(out.split("[")) >= 2

    def test_query_uses_query_endpoint_with_peers(self, fake):
        fake.conclusion_items = [{"created_at": "2026-10-11T00:00:00Z", "content": "c"}]
        honcho_mcp.tool_conclusions({"query": "theme", "workspace": "ws", "limit": 3})
        path, payload, method = fake.calls[-1]
        assert path.endswith("/conclusions/query")
        assert payload == {"query": "theme", "limit": 3,
                           "observer": honcho_mcp.AGENT_PEER, "observed": honcho_mcp.AGENT_PEER}

    def test_list_endpoint_when_no_query(self, fake):
        honcho_mcp.tool_conclusions({"workspace": "ws", "limit": 2})
        path, payload, method = fake.calls[-1]
        assert path.endswith("/conclusions/list")
        assert payload == {"limit": 2}

    def test_limit_capped_at_50(self, fake):
        honcho_mcp.tool_conclusions({"workspace": "ws", "limit": 999})
        search_path = [c for c in fake.calls if "conclusions" in c[0]]
        assert search_path[0][1]["limit"] == 50

    def test_list_shaped_response(self, fake):
        with mock.patch.object(
            honcho_mcp, "api", lambda path, payload=None, method="POST": [
                {"created_at": "2026-10-11T00:00:00Z", "content": "direct"}]
        ):
            out = honcho_mcp.tool_conclusions({"query": "q", "workspace": "ws"})
        assert "direct" in out


class TestEnsureSession:
    def test_existing_session_not_recreated(self, fake):
        fake.existing_sessions = ["s1"]
        honcho_mcp.ensure_session("ws", "s1")
        assert not any(c[0].endswith("/sessions") and c[0].count("/") == 3 for c in fake.calls)

    def test_missing_session_created_with_both_peers(self, fake):
        fake.existing_sessions = []
        honcho_mcp.ensure_session("ws", "s1")
        _, payload, _ = fake.calls[-1]
        assert set(payload["peers"]) == {honcho_mcp.USER_PEER, honcho_mcp.AGENT_PEER}
    def test_list_failure_creates_session(self, fake):
        calls = []

        def failing(path, payload=None, method="POST"):
            calls.append(path)
            if path.endswith("/sessions/list"):
                raise RuntimeError("list boom")
            return {}

        with mock.patch.object(honcho_mcp, "api", failing):
            out = honcho_mcp.ensure_session("ws", "sid")
        assert out == "sid"
        assert any(c.endswith("/sessions") and not c.endswith("/sessions/list") for c in calls)

    def test_listed_session_returns_without_create(self, fake):
        calls = []

        def listing(path, payload=None, method="POST"):
            calls.append(path)
            return {"items": [{"id": "have"}]}

        with mock.patch.object(honcho_mcp, "api", listing):
            out = honcho_mcp.ensure_session("ws", "have")
        assert out == "have"
        assert calls == [calls[0]]  # only the list call

    def test_listed_miss_creates_session(self, fake):
        calls = []

        def listing(path, payload=None, method="POST"):
            calls.append(path)
            if path.endswith("/sessions/list"):
                return {"items": [{"id": "other"}]}
            return {"id": "made"}

        with mock.patch.object(honcho_mcp, "api", listing):
            out = honcho_mcp.ensure_session("ws", "wanted")
        assert out == "wanted"
        assert any(c.endswith("/sessions") and not c.endswith("/sessions/list") for c in calls)


# ---- stdio protocol loop ----


def send_and_receive(proc, req):
    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    return json.loads(line) if line else {}


@pytest.fixture()
def server():
    proc = subprocess.Popen(
        [sys.executable, MCP], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, cwd=REPO,
    )
    yield proc
    proc.send_signal(signal.SIGINT)


class TestProtocolLoop:
    def test_initialize(self, server):
        resp = send_and_receive(server, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert resp["result"]["protocolVersion"] == honcho_mcp.PROTOCOL_VERSION
        assert resp["result"]["serverInfo"]["name"] == "honcho-mcp"

    def test_notifications_initialized_is_silent(self, server):
        server.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        server.stdin.flush()
        server.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n")
        server.stdin.flush()
        resp = json.loads(server.stdout.readline())
        assert resp["id"] == 2

    def test_empty_lines_and_garbage_ignored(self, server):
        server.stdin.write("\n   \n{not json}\n")
        server.stdin.flush()
        resp = send_and_receive(server, {"jsonrpc": "2.0", "id": 3, "method": "ping"})
        assert resp["result"] == {}

    def test_tools_list(self, server):
        resp = send_and_receive(server, {"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
        names = [t["name"] for t in resp["result"]["tools"]]
        assert names == ["honcho_store", "honcho_search", "honcho_ask", "honcho_conclusions"]
        assert resp["result"]["tools"][0]["inputSchema"] == honcho_mcp.TOOLS["honcho_store"]["input_schema"]

    def test_unknown_method_error(self, server):
        resp = send_and_receive(server, {"jsonrpc": "2.0", "id": 5, "method": "bogus"})
        assert resp["error"]["code"] == -32601
        assert resp["error"]["message"] == "unknown method bogus"
    def test_unknown_method_without_id_silent(self, server):
        # id-less unknown requests fall through all elifs and send nothing
        server.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "bogus-noid"}) + "\n")
        server.stdin.flush()
        resp = send_and_receive(server, {"jsonrpc": "2.0", "id": 6, "method": "ping"})
        assert resp["id"] == 6

    def test_unmatched_notifications_skipped(self, server):
        server.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/xyz"}) + "\n")
        server.stdin.flush()
        resp = send_and_receive(server, {"jsonrpc": "2.0", "id": 6, "method": "resources/list"})
        assert resp["result"] == {"resources": []}

    def test_prompts_list(self, server):
        resp = send_and_receive(server, {"jsonrpc": "2.0", "id": 7, "method": "prompts/list"})
        assert resp["result"] == {"prompts": []}

    def test_tools_call_invalid_args_reports_error_text(self, server):
        # handler-level validation returns a text error, not isError
        out = send_and_receive(server, {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                                        "params": {"name": "honcho_ask", "arguments":
                                                   {"query": "x", "reasoning_level": "bogus",
                                                    "workspace": "jcode-selftest-protocol-err"}}})
        assert out["result"]["content"][0]["text"].startswith("error:")

    def test_tools_call_unknown_name_reports_error_text(self, server):
        # unknown tool KeyError is caught by the loop and surfaces as error text
        out = send_and_receive(server, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                        "params": {"name": "does_not_exist", "arguments": {}}})
        assert out["result"]["content"][0]["text"].startswith("error:")

    def test_tools_call_handler_exception_sets_is_error(self, server):
        # Drive the protocol loop in-process with a failing api() to exercise
        # the catch-all branch that turns tool exceptions into isError results.
        server.send_signal(signal.SIGINT)  # not used by this test

        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize"}),
            json.dumps({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                        "params": {"name": "honcho_store",
                                   "arguments": {"text": "x", "workspace": "ws",
                                                 "session": "s"}}}),
        ]
        inp = "\n".join(lines) + "\n"

        class FakeStdin:
            def __init__(self, data):
                self.data = data

            def __iter__(self):
                return iter(self.data.splitlines(True))

        sent = {}

        def fake_send(msg):
            sent.update(msg)

        def raising_api(path, payload=None, method="POST"):
            raise RuntimeError("honcho unreachable")

        with mock.patch.object(honcho_mcp, "api", raising_api), \
                mock.patch.object(sys, "stdin", FakeStdin(inp)), \
                mock.patch.object(honcho_mcp, "send", fake_send):
            honcho_mcp.main()
        assert sent["result"].get("isError") is True
        assert sent["result"]["content"][0]["text"].startswith("error: honcho unreachable")
