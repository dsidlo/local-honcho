"""Unit tests for src/jcode_honcho_tailer.py helpers, fully in-process.

Covers scrubbing, chunking, path denylist, state persistence, logging,
jcode version probe, workspace detection, journal parsing, spooling, and
the main-loop behaviors (offsets reset on truncation, pruning, error
resilience) without a live Honcho.
"""

import importlib.machinery
import importlib.util
import json
import os
import signal
import sys
import threading
import time
from unittest import mock

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAILER = os.path.join(REPO, "src", "jcode_honcho_tailer.py")


def _load_tailer():
    if "tailer_mod" in sys.modules:
        return sys.modules["tailer_mod"]  # cached on repeat calls within the run
    loader = importlib.machinery.SourceFileLoader("tailer_mod", os.path.abspath(TAILER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    t = importlib.util.module_from_spec(spec)
    loader.exec_module(t)
    sys.modules["tailer_mod"] = t
    return t


@pytest.fixture(scope="module")
def t():
    return _load_tailer()


# ---- scrubbing / denylist / chunking ----


def test_load_tailer_uses_cached_module():
    first = _load_tailer()
    second = _load_tailer()
    assert first is second


class TestScrub:
    def test_secret_redacted(self, t):
        assert t.scrub("tok sk-abcdefghijklmnopqrstuv12 end") == "tok [REDACTED] end"

    def test_pgpassword(self, t):
        assert "hunter2" not in t.scrub("PGPASSWORD=hunter2 psql")

    def test_plain_survives(self, t):
        assert t.scrub("clean") == "clean"


class TestPathDenied:
    @pytest.mark.parametrize("path", ["/u/.env", "/u/x/auth.json", "/k/id_rsa", "/s/secrets/v"])
    def test_denied(self, t, path):
        assert t.path_denied(path)

    def test_allowed(self, t):
        assert not t.path_denied("/src/main.py")
        assert not t.path_denied(None)


class TestChunk:
    def test_short(self, t):
        assert t.chunk("ok") == ["ok"]

    def test_paragraph_boundary(self, t):
        assert len(t.chunk("aaa\n\nbbb", size=5)) == 2

    def test_sentence_flush_mid_paragraph(self, t):
        # accumulated sentences cross size inside one paragraph -> inner flush
        text = "Aaa bbb ccc. Ddd eee fff. Ggg hhh iii. Jjj kkk lll."
        assert len(t.chunk(text, size=9)) >= 2

    def test_sentence_flush_with_size_cut(self, t):
        out = t.chunk("First one. Second two. Third three. Fourth four.", size=5)
        assert out == ["First", "Secon", "Third", "Fourt"]

    def test_paragraph_accumulation_flushes(self, t):
        assert t.chunk("aa\n\nbb\n\nccee", size=6) == ["aa", "bb", "ccee"]

    def test_paragraphs_fit_into_one_chunk(self, t):
        assert t.chunk("aa\n\nbb", size=100) == ["aa\n\nbb"]

    def test_big_para_after_accumulated_small_ones(self, t):
        # accumulated cur flushed when an oversized paragraph arrives
        out = t.chunk("aa bb cc dd\n\n" + "z" * 40, size=16)
        assert out == ["aa bb cc dd", "zzzzzzzzzzzzzzzz"]

    def test_sentences_accumulate_within_budget(self, t):
        # sentences that FIT the budget accumulate (else branch at 94)
        assert t.chunk("Hi. Yo. " + "z" * 30, size=10)[0] == "Hi. Yo."

    def test_big_paragraph_flushes_accumulated_cur(self, t):
        # para > size flushes the pending cur first (87-88)
        out = t.chunk("small\n\n" + "word " * 30, size=20)
        assert out[0] == "small"

    def test_oversized_sentence_flushes_cur_first(self, t):
        # inside big paragraph the cur-flush line (94) fires when a sentence
        # crossing the size arrives with cur already full
        out = t.chunk("One two. " + "x " * 40, size=8)
        assert out[0] == "One two."

    def test_sentence_longer_than_size_truncated(self, t):
        chunks = t.chunk("word " * 30, size=10)
        assert all(len(c) <= 10 for c in chunks)

    def test_truncation_fallback(self, t):
        chunks = t.chunk("z" * 60, size=10)
        assert all(len(c) <= 10 for c in chunks)


class TestApiClientTailer:
    def test_api_post_and_empty_body(self, monkeypatch, t):
        captured = {}

        class FakeResp:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self.body

        def fake_urlopen(req, timeout=None):
            captured["data"] = req.data
            captured["method"] = req.get_method()
            return FakeResp(json.dumps({"ok": 1}).encode())

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        out = t.api("/workspaces", {"id": "w"})
        assert out == {"ok": 1}
        assert captured["method"] == "POST"

        # empty body -> {}
        def empty_urlopen(req, timeout=None):
            return FakeResp(b"")

        monkeypatch.setattr("urllib.request.urlopen", empty_urlopen)
        assert t.api("/x") == {}

    def test_ensure_session_api_error_continues_to_create(self, t, monkeypatch):
        calls = []

        def explode_on_list(path, payload=None, method="POST"):
            calls.append(path)
            if path.endswith("/sessions/list"):
                raise OSError("list failed")
            return {"id": "s"}

        monkeypatch.setattr(t, "api", explode_on_list)
        t.ensure_session("ws", "sid")
        assert any(c.endswith("/sessions") and not c.endswith("/sessions/list") for c in calls)

    def test_ensure_session_existing_listed_no_recreate(self, t, monkeypatch):
        calls = []

        def listing(path, payload=None, method="POST"):
            calls.append(path)
            return {"items": [{"id": "other"}]}

        monkeypatch.setattr(t, "api", listing)
        t.ensure_session("ws", "wanted")  # miss -> create; hit case covered above
        assert any(c.endswith("/sessions") and not c.endswith("/sessions/list") for c in calls)
    def test_ensure_session_list_miss_creates(self, t, monkeypatch):
        calls = []

        def listing(path, payload=None, method="POST"):
            calls.append(path)
            if path.endswith("/sessions/list"):
                return {"items": [{"id": "other"}]}
            return {"id": "made"}

        monkeypatch.setattr(t, "api", listing)
        t.ensure_session("ws", "wanted")
        assert any(c.endswith("/sessions") and not c.endswith("/sessions/list") for c in calls)
    def test_ensure_session_list_hit_no_recreate(self, t, monkeypatch):
        # item id matches -> early return (198), no create call
        calls = []
        endpoint_paths = []

        def listing(path, payload=None, method="POST"):
            calls.append(path)
            assert path.endswith("/sessions/list"), "no create call is allowed here"
            return {"items": [{"id": "have"}]}

        monkeypatch.setattr(t, "api", listing)
        t.ensure_session("ws", "have")
        assert calls == [calls[0]]  # only the list call


class TestDetectWorkspaceTailerOSError:
    def test_oserror_continues_up(self, t, tmp_path, monkeypatch):
        parent = tmp_path
        child = tmp_path / "kid"
        (parent / ".git").mkdir()
        (parent / ".git" / "config").write_text('[remote "origin"]\n\turl = https://x.org/o/tail-parent.git\n')
        (child / ".git").mkdir(parents=True)
        (child / ".git" / "config").write_text('[remote "origin"]\n')
        t._ws_cache.clear()

        real_open = open

        def raising_open(path, *a, **k):
            if str(path).endswith("kid/.git/config"):
                raise OSError("unreadable")
            return real_open(path, *a, **k)

        import builtins
        monkeypatch.setattr(builtins, "open", raising_open)
        assert t.detect_workspace(str(child)) == "tail-parent"
        t._ws_cache.clear()


# ---- persistence helpers ----


class TestStateAndLogging:
    def test_load_state_missing_file(self, t, tmp_path, monkeypatch):
        f = tmp_path / "state.json"
        monkeypatch.setattr(t, "STATE_FILE", str(f))
        state = t.load_state()
        assert state["offsets"] == {}
        assert state["parse_errors"] == 0
        assert "started_at" in state

    def test_load_state_corrupt_file(self, t, tmp_path, monkeypatch):
        f = tmp_path / "state.json"
        f.write_text("{not json")
        monkeypatch.setattr(t, "STATE_FILE", str(f))
        assert t.load_state()["offsets"] == {}

    def test_save_then_load_roundtrip(self, t, tmp_path, monkeypatch):
        f = tmp_path / "state.json"
        monkeypatch.setattr(t, "STATE_FILE", str(f))
        t.save_state({"offsets": {"/a": 5}})
        assert t.load_state()["offsets"] == {"/a": 5}

    def test_log_appends_line(self, t, tmp_path, monkeypatch):
        f = tmp_path / "logs" / "t.log"
        monkeypatch.setattr(t, "LOG_FILE", str(f))
        t.log("INFO", "hello", {"k": "v"})
        content = f.read_text()
        assert "[INFO] hello" in content and '"k": "v"' in content

    def test_log_data_truncated_to_300(self, t, tmp_path, monkeypatch):
        f = tmp_path / "t.log"
        monkeypatch.setattr(t, "LOG_FILE", str(f))
        t.log("WARN", "m", {"big": "x" * 1000})
        line = f.read_text().strip().rsplit("|", 1)[1]
        assert len(line.strip()) <= 300

    def test_log_swallows_oserror(self, t, tmp_path, monkeypatch):
        # LOG_FILE inside a nonexistent, uncreatable directory (a file, not dir)
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        monkeypatch.setattr(t, "LOG_FILE", str(blocker / "child" / "t.log"))
        t.log("ERROR", "boom")  # must not raise

    def test_jcode_version_success(self, t):
        with mock.patch.object(t, "subprocess_run", return_value="jcode v0.95.0\n"):
            assert t.jcode_version() == "jcode v0.95.0"

    def test_jcode_version_failure(self, t):
        with mock.patch.object(t, "subprocess_run", side_effect=OSError("nope")):
            assert t.jcode_version() == "unknown"

    def test_subprocess_run_uses_subprocess(self, t):
        assert t.subprocess_run(["echo", "hi"], timeout=5).strip() == "hi"


class TestDetectWorkspaceTailer:
    def test_override_env(self, t):
        with mock.patch.object(t, "WORKSPACE_OVERRIDE", "forced"):
            assert t.detect_workspace("/anywhere") == "forced"

    def test_git_origin(self, t, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/acme/tw.git\n')
        assert t.detect_workspace(str(tmp_path)) == "tw"

    def test_caching(self, t, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/acme/cache-ws.git\n')
        t._ws_cache.clear()
        first = t.detect_workspace(str(tmp_path))
        # mutate the cache directly to prove the second call hits it
        t._ws_cache[str(tmp_path)] = "mutated"
        assert t.detect_workspace(str(tmp_path)) == "mutated"
        t._ws_cache.clear()
        assert first == "cache-ws"

    def test_no_repo_jcode(self, t, tmp_path):
        t._ws_cache.clear()
        assert t.detect_workspace(str(tmp_path)) == "jcode"


# ---- mapper ----


class TestInpPath:
    def test_direct_keys(self, t):
        assert t.inp_path({"path": "/a"}) == "/a"
        assert t.inp_path({"file_path": "/b"}) == "/b"
        assert t.inp_path({"file": "/c"}) == "/c"
        assert t.inp_path({"target": "/d"}) == "/d"
        assert t.inp_path({"command": "cat e"}) == "cat e"

    def test_nested_one_level(self, t):
        assert t.inp_path({"x": {"file_path": "/nested"}}) == "/nested"
        assert t.inp_path({"y": {"path": "/nested2"}}) == "/nested2"

    def test_missing(self, t):
        assert t.inp_path({}) == ""
        assert t.inp_path("not-a-dict") == ""


class TestMapBlock:
    def test_prompt_capture_off(self, t):
        with mock.patch.dict(t.CAPTURE, {"capture_prompts": "off"}):
            assert t.map_block_to_message("user", {"type": "text", "text": "x"}, {}, False) is None

    def test_reasoning_off(self, t):
        with mock.patch.dict(t.CAPTURE, {"capture_reasoning": "off"}):
            assert t.map_block_to_message("assistant", {"type": "reasoning", "text": "x"}, {}, False) is None

    def test_tool_calls_off(self, t):
        with mock.patch.dict(t.CAPTURE, {"capture_tool_calls": "off"}):
            assert t.map_block_to_message("assistant", {"type": "tool_use", "name": "ls"}, {}, False) is None

    def test_tool_results_off(self, t):
        with mock.patch.dict(t.CAPTURE, {"capture_tool_results": "off"}):
            assert t.map_block_to_message("assistant", {"type": "tool_result", "name": "ls"}, {}, False) is None

    def test_finals_off(self, t):
        with mock.patch.dict(t.CAPTURE, {"capture_finals": "off"}):
            assert t.map_block_to_message("assistant", {"type": "text", "text": "x"}, {}, False) is None

    def test_tool_use_uses_block_tool_field(self, t):
        m = t.map_block_to_message("assistant", {"type": "tool_use", "tool": "grep", "input": {"q": "x"}}, {}, False)
        assert "tool_call grep" in m["content"]

    def test_tool_use_defaults_to_unknown(self, t):
        m = t.map_block_to_message("assistant", {"type": "tool_use"}, {}, False)
        assert "unknown" in m["content"]

    def test_tool_result_error_status(self, t):
        m = t.map_block_to_message("assistant", {"type": "tool_result", "name": "ls", "is_error": True, "output": "boom"}, {}, False)
        assert "error" in m["metadata"]["status"]
        assert "tool_result ls:" in m["content"]

    def test_tool_result_body_mode_allowed(self, t):
        with mock.patch.dict(t.CAPTURE, {"capture_tool_results": "body"}):
            m = t.map_block_to_message(
                "assistant", {"type": "tool_result", "name": "grep",
                              "output": "found it", "is_error": False}, {}, False)
        assert "found it" in m["content"]

    def test_tool_result_body_mode_denied_path(self, t):
        with mock.patch.dict(t.CAPTURE, {"capture_tool_results": "body"}):
            m = t.map_block_to_message(
                "assistant", {"type": "tool_result", "name": "read",
                              "input": {"file_path": "/u/.env"},
                              "output": "SECRET", "is_error": False}, {}, False)
        assert "[DENIED-PATH]" in m["content"]

    def test_tool_result_body_mode_denied_tool_name(self, t):
        with mock.patch.dict(t.CAPTURE, {"capture_tool_results": "body"}):
            m = t.map_block_to_message(
                "assistant", {"type": "tool_result", "name": "credential-reader",
                              "output": "SECRET", "is_error": False}, {}, False)
        assert "SECRET" not in m["content"]

    def test_tool_result_non_string_output_counts_zero_bytes(self, t):
        m = t.map_block_to_message("assistant", {"type": "tool_result", "name": "ls", "output": 42}, {}, False)
        assert "0 bytes]" in m["content"]

    def test_finals_toggle_string_not_on(self, t):
        # capture_finals != "on" (263->265 branch): skip final text
        with mock.patch.dict(t.CAPTURE, {"capture_finals": "sometimes"}):
            assert t.map_block_to_message("assistant", {"type": "text", "text": "x"}, {}, True) is None

    def test_inp_path_nested_dict_without_path_keys(self, t):
        # nested dict present but no path/file_path keys -> falls through (279->277)
        assert t.inp_path({"opts": {"depth": 2, "other": "x"}}) == ""

    def test_map_block_non_dict_result_appended_without_meta(self, t, tmp_path, monkeypatch):
        # defensive branch 328->330: a non-dict truthy mapper result is appended
        # (skipping the ts metadata attachment) rather than crashing
        monkeypatch.setattr(t, "map_block_to_message",
                            lambda *a, **k: ["not", "a", "dict"])
        path = tmp_path / "s2.journal.jsonl"
        path.write_text(json.dumps({"meta": {}}) + "\n" + json.dumps(
            {"append_messages": [
                {"role": "user", "content": [{"type": "text", "text": "x"}], "timestamp": "T"}]}) + "\n")
        _, msgs, errs = t.process_journal(str(path), 0, {}, "ws", "sid")
        assert errs == 0
        assert msgs == [["not", "a", "dict"]]

    def test_non_last_assistant_text_not_final_flagged(self, t):
        m = t.map_block_to_message("assistant", {"type": "text", "text": "mid"}, {}, False)
        assert m and "final" not in m["metadata"] or m["metadata"].get("final") is not True

    def test_non_dict_block_ignored_by_caller(self, t):
        # map_block_to_message itself would raise on non-dict; the caller filters
        with pytest.raises(AttributeError):
            t.map_block_to_message("assistant", "not-a-dict", {}, False)

    def test_unknown_role_returns_none(self, t):
        assert t.map_block_to_message("system", {"type": "text", "text": "x"}, {}, False) is None

    def test_unknown_block_type(self, t):
        assert t.map_block_to_message("assistant", {"type": "image", "url": "x"}, {}, False) is None


class TestProcessJournal:
    def _journal(self, tmp_path, records, meta_line=None):
        path = tmp_path / "s.journal.jsonl"
        lines = [meta_line or json.dumps({"meta": {"working_dir": "/tmp"}})]
        lines += [json.dumps(r) for r in records]
        path.write_text("\n".join(lines) + "\n")
        return str(path)

    def test_full_mapping(self, t, tmp_path):
        path = self._journal(tmp_path, [{
            "append_messages": [
                {"role": "user", "content": [{"type": "text", "text": "hi"}], "timestamp": "T1"},
                {"role": "assistant", "content": [
                    {"type": "reasoning", "text": "think"},
                    {"type": "text", "text": "answer"},
                ], "timestamp": "T2"},
            ]}])
        new_offset, msgs, errs = t.process_journal(path, 0, {}, "ws", "sid")
        assert errs == 0
        assert [m["metadata"]["type"] for m in msgs] == ["prompt", "thought", "final"]
        assert msgs[0]["metadata"]["ts"] == "T1"
        assert msgs[-1]["metadata"].get("final") is True
        assert new_offset > 0

    def test_string_content_shortcut(self, t, tmp_path):
        path = self._journal(tmp_path, [{"append_messages": [
            {"role": "user", "content": "plain string", "timestamp": "T"}]}])
        _, msgs, errs = t.process_journal(path, 0, {}, "ws", "sid")
        assert errs == 0
        assert msgs[0]["content"] == "plain string"

    def test_blank_and_non_dict_blocks_skipped(self, t, tmp_path):
        path = self._journal(tmp_path, [{"append_messages": [
            {"role": "user", "content": ["not-a-dict", {"type": "text", "text": "  "}],
             "timestamp": "T"}]}])
        _, msgs, errs = t.process_journal(path, 0, {}, "ws", "sid")
        assert msgs == []

    def test_meta_only_records_skipped(self, t, tmp_path):
        path = self._journal(tmp_path, [{"meta": {"x": 1}}, {"other": True}])
        _, msgs, _ = t.process_journal(path, 0, {}, "ws", "sid")
        assert msgs == []

    def test_bad_json_line_counts_error(self, t, tmp_path):
        path = tmp_path / "s.journal.jsonl"
        path.write_text(json.dumps({"meta": {}}) + "\n{broken\n")
        _, msgs, errs = t.process_journal(str(path), 0, {}, "ws", "sid")
        assert errs == 1 and msgs == []

    def test_unknown_append_shape_logs_and_counts_error(self, t, tmp_path, monkeypatch):
        seen = []
        monkeypatch.setattr(t, "log", lambda level, msg, data=None: seen.append((level, msg)))
        path = self._journal(tmp_path, [{"append_messages": "not-a-list"}])
        _, msgs, errs = t.process_journal(path, 0, {}, "ws", "sid")
        assert any(lv == "WARN" for lv, _ in seen)   # logged, never dropped silently
        assert errs == 1                              # counted as a parse error
        assert msgs == []

    def test_meta_only_and_non_list_append_skipped(self, t, tmp_path):
        # non-append keys are skipped without errors entirely
        path = self._journal(tmp_path, [{"something": 1}])
        _, msgs, errs = t.process_journal(path, 0, {}, "ws", "sid")
        assert (msgs, errs) == ([], 0)

    def test_unreadable_file_returns_same_offset(self, t, tmp_path):
        _, msgs, errs = t.process_journal(str(tmp_path / "nope.jsonl"), 42, {}, "ws", "sid")
        assert (msgs, errs) == ([], 0)

    def test_content_non_list_non_string_ignored(self, t, tmp_path):
        path = self._journal(tmp_path, [{"append_messages": [
            {"role": "user", "content": 12345, "timestamp": "T"}]}])
        _, msgs, errs = t.process_journal(path, 0, {}, "ws", "sid")
        assert (msgs, errs) == ([], 0)

    def test_whitespace_string_content_ignored(self, t, tmp_path):
        path = self._journal(tmp_path, [{"append_messages": [
            {"role": "user", "content": "   ", "timestamp": "T"}]}])
        _, msgs, errs = t.process_journal(path, 0, {}, "ws", "sid")
        assert (msgs, errs) == ([], 0)

    def test_empty_string_content_message_rejected_by_mapper(self, t, tmp_path):
        # whitespace-only string content passes the caller strip check? no:
        # content.strip() empty -> skipped. A short string maps via the string path.
        path = self._journal(tmp_path, [{"append_messages": [
            {"role": "user", "content": "tiny", "timestamp": "T"}]}])
        _, msgs, errs = t.process_journal(path, 0, {}, "ws", "sid")
        assert len(msgs) == 1 and msgs[0]["content"] == "tiny"

    def test_last_text_index_scanning_skips_non_dict_tail(self, t, tmp_path):
        # content list with non-dict junk in the middle: scanner skips it for
        # last-text detection but the mapper is invoked for dict blocks only;
        # the single text block IS the last dict text, so it is final
        path = self._journal(tmp_path, [{"append_messages": [
            {"role": "assistant", "content": [
                {"type": "text", "text": "first"},
                "junk", 42,
                {"type": "reasoning", "text": "more"},
            ], "timestamp": "T"}]}])
        _, msgs, errs = t.process_journal(path, 0, {}, "ws", "sid")
        assert errs == 0
        assert [m["metadata"]["type"] for m in msgs] == ["final", "thought"]
        assert msgs[0]["metadata"].get("final") is True

    def test_reverse_scan_without_text_block(self, t, tmp_path):
        # content list with no dict text blocks -> loop exhausts (319->323)
        path = self._journal(tmp_path, [{"append_messages": [
            {"role": "assistant", "content": [
                {"type": "reasoning", "text": "r"},
                {"type": "tool_use", "name": "ls", "input": {}},
            ], "timestamp": "T"}]}])
        _, msgs, errs = t.process_journal(path, 0, {}, "ws", "sid")
        assert errs == 0
        types = [m["metadata"]["type"] for m in msgs]
        assert types == ["thought", "tool_call"]
        assert not any(m["metadata"].get("final") for m in msgs)
    def test_scan_breaks_on_first_found_text(self, t, tmp_path):
        # break path at 322: text block found mid-list stops the reverse scan
        path = self._journal(tmp_path, [{"append_messages": [
            {"role": "assistant", "content": [
                {"type": "reasoning", "text": "r1"},
                {"type": "text", "text": "only-text"},
                {"type": "tool_use", "name": "ls", "input": {}},
            ], "timestamp": "T"}]}])
        _, msgs, errs = t.process_journal(path, 0, {}, "ws", "sid")
        assert errs == 0
        types = [m["metadata"]["type"] for m in msgs]
        assert types == ["thought", "final", "tool_call"]
        # final detection scans for the last TEXT block only; tool_use after
        # the text does not affect the final flag
        assert msgs[1]["metadata"].get("final") is True


class TestPostMessagesAndSpool:
    def test_success_batches(self, t):
        sent_paths = []
        with mock.patch.object(t, "api", lambda path, payload=None, method="POST":
                               sent_paths.append(path)):
            with mock.patch.object(t.time, "sleep"):
                n = t.post_messages("ws", "sid",
                                    [{"peer_id": "p", "content": f"m{i}"} for i in range(7)])
        assert n == 7
        assert len(sent_paths) == 2  # 5 + 2

    def test_failure_spools_remaining(self, t, tmp_path, monkeypatch):
        spool_dir = tmp_path / "spool"
        monkeypatch.setattr(t, "SPOOL_DIR", str(spool_dir))
        attempts = {"n": 0}

        def flaky(path, payload=None, method="POST"):
            attempts["n"] += 1
            if attempts["n"] == 2:
                raise OSError("down")

        with mock.patch.object(t, "api", flaky):
            with mock.patch.object(t.time, "sleep"):
                errs = []
                monkeypatch.setattr(t, "log", lambda *a, **k: errs.append(a))
                n = t.post_messages("ws", "sid",
                                    [{"peer_id": "p", "content": f"m{i}"} for i in range(6)])
        assert n == 5  # first batch ok
        files = list(spool_dir.iterdir())
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["workspace"] == "ws" and len(data["messages"]) == 1

    def test_spool_write_failure_swallowed(self, t, tmp_path, monkeypatch):
        # SPOOL_DIR path blocked by an existing file -> makedirs raises OSError
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        monkeypatch.setattr(t, "SPOOL_DIR", str(blocker / "spool"))
        t.spool("ws", "sid", [{"peer_id": "p", "content": "m"}])  # must not raise

    def test_retry_spooled_success(self, t, tmp_path, monkeypatch):
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        monkeypatch.setattr(t, "SPOOL_DIR", str(spool_dir))
        item = {"workspace": "ws", "session": "sid", "messages": [{"peer_id": "p", "content": "m"}]}
        f1 = spool_dir / "1-ws-sid.json"
        f1.write_text(json.dumps(item))
        sent = []
        with mock.patch.object(t, "api", lambda path, payload=None, method="POST": sent.append(path)):
            t.retry_spooled()
        assert sent and not f1.exists()

    def test_retry_stops_on_persistent_failure(self, t, tmp_path, monkeypatch):
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        monkeypatch.setattr(t, "SPOOL_DIR", str(spool_dir))
        for i in range(3):
            (spool_dir / f"{i}-ws-sid.json").write_text(json.dumps(
                {"workspace": "ws", "session": "sid", "messages": []}))
        calls = {"n": 0}

        def down(path, payload=None, method="POST"):
            calls["n"] += 1
            raise OSError("still down")

        with mock.patch.object(t, "api", down):
            t.retry_spooled()
        assert calls["n"] == 1  # bailed after first failure

    def test_retry_missing_dir_noop(self, t, tmp_path, monkeypatch):
        monkeypatch.setattr(t, "SPOOL_DIR", str(tmp_path / "absent"))
        t.retry_spooled()  # must not raise

    def test_retry_corrupt_file_stops(self, t, tmp_path, monkeypatch):
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        monkeypatch.setattr(t, "SPOOL_DIR", str(spool_dir))
        (spool_dir / "0-bad.json").write_text("{broken")
        called = []
        with mock.patch.object(t, "api", lambda *a, **k: called.append(1)):
            t.retry_spooled()  # corrupt json raises inside try -> break quietly
        assert not called


class TestMainLoop:
    MAX_WAIT = 20

    def _run_main(self, t, monkeypatch, tmp_path, *, journals=None, existing_state=None,
                  stop_after=None, retry_impl=None):
        """Run t.main() in a daemon thread, then trigger the SIGTERM handler it
        registers (captured via a signal.signal stub) and join. Returns
        (api_calls, state_file, sleep_calls)."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(exist_ok=True)
        for name, content in (journals or {}).items():
            (sessions_dir / name).write_text(content)
        cache = tmp_path / "cache"
        cache.mkdir(exist_ok=True)
        state_file = cache / "state.json"
        if existing_state is not None:
            state_file.write_text(json.dumps(existing_state))

        monkeypatch.setattr(t, "SESSIONS_DIR", str(sessions_dir))
        monkeypatch.setattr(t, "STATE_FILE", str(state_file))
        monkeypatch.setattr(t, "POLL_SECONDS", 0)
        monkeypatch.setattr(t, "jcode_version", lambda: "jcode x")
        monkeypatch.setattr(t, "log", lambda *a, **k: None)

        api_calls = []

        def fake_api(path, payload=None, method="POST"):
            api_calls.append((path, payload))
            return {"items": []}

        monkeypatch.setattr(t, "api", fake_api)

        registered = {}
        monkeypatch.setattr("signal.signal",
                            lambda sig, fn: registered.__setitem__(sig, fn))

        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)
            if stop_after is not None and len(sleep_calls) >= stop_after:
                for fn in registered.values():
                    fn(signal.SIGTERM, None)  # graceful stop

        monkeypatch.setattr(t.time, "sleep", fake_sleep)

        with mock.patch.object(t, "retry_spooled", retry_impl or (lambda: None)):
            thread = threading.Thread(target=t.main, daemon=True)
            thread.start()
            thread.join(timeout=self.MAX_WAIT)
            assert not thread.is_alive(), "main() did not stop gracefully"
        return api_calls, state_file, sleep_calls

    def test_first_run_processes_and_persists(self, t, tmp_path, monkeypatch):
        msgs = json.dumps({"meta": {}, "append_messages": [
            {"role": "user", "content": "hello", "timestamp": "T"}]})
        content = json.dumps({"meta": {"working_dir": "/tmp/nowhere"}}) + "\n" + msgs + "\n"
        api_calls, state_file, _ = self._run_main(
            t, monkeypatch, tmp_path, journals={"sess_x.journal.jsonl": content}, stop_after=1)
        assert any("/messages" in p for p, _ in api_calls)
        state = json.loads(state_file.read_text())
        assert any(v > 0 for v in state["offsets"].values())
        assert state["last_beat"] > 0

    def test_truncated_journal_resets_offset(self, t, tmp_path, monkeypatch):
        msgs = json.dumps({"meta": {}, "append_messages": [
            {"role": "user", "content": "hello", "timestamp": "T"}]})
        jname = "sess_t.journal.jsonl"
        full = json.dumps({"meta": {"working_dir": "/tmp"}}) + "\n" + msgs + "\n"
        journal_path = str(tmp_path / "sessions" / jname)
        api_calls, _, _ = self._run_main(
            t, monkeypatch, tmp_path, journals={jname: full}, stop_after=1,
            existing_state={"offsets": {journal_path: 10**9}})
        # offset was clamped to 0 and the journal was re-read
        assert any("/messages" in p for p, _ in api_calls)

    def test_corrupt_meta_line_falls_back_to_jcode_ws(self, t, tmp_path, monkeypatch):
        msgs = json.dumps({"meta": {}, "append_messages": [
            {"role": "user", "content": "hello", "timestamp": "T"}]})
        content = "{broken meta json\n" + msgs + "\n"
        api_calls, _, _ = self._run_main(
            t, monkeypatch, tmp_path, journals={"sess_c.journal.jsonl": content}, stop_after=1)
        ws_paths = [p for p, _ in api_calls if "/workspaces/" in p]
        assert ws_paths and ws_paths[0].startswith("/workspaces/jcode/")
        assert any(p.endswith("/sessions") for p in ws_paths)

    def test_offsets_pruned_when_over_limit(self, t, tmp_path, monkeypatch):
        jname = "live.journal.jsonl"
        live_path = str(tmp_path / "sessions" / jname)
        stale = {f"/gone/{i}.journal.jsonl": i for i in range(600)}
        stale[live_path] = 0
        api_calls, state_file, _ = self._run_main(
            t, monkeypatch, tmp_path, journals={jname: ""}, stop_after=1,
            existing_state={"offsets": stale})
        state = json.loads(state_file.read_text())
        assert list(state["offsets"]) == [live_path]

    def test_loop_survives_exception(self, t, tmp_path, monkeypatch):
        # retry_spooled raises once; the catch-all keeps the loop alive and
        # the next iteration continues (stop after two sleeps)
        boom = {"n": 0}

        def raising_retry():
            boom["n"] += 1
            if boom["n"] == 1:
                raise RuntimeError("boom")

        api_calls, state_file, _ = self._run_main(
            t, monkeypatch, tmp_path, journals={}, stop_after=2, retry_impl=raising_retry)
        assert boom["n"] >= 2
        assert state_file.exists()

    def test_state_loaded_at_start_no_repost(self, t, tmp_path, monkeypatch):
        jname = "done.journal.jsonl"
        msgs = json.dumps({"meta": {}, "append_messages": [
            {"role": "user", "content": "hello", "timestamp": "T"}]})
        content = json.dumps({"meta": {"working_dir": "/tmp"}}) + "\n" + msgs + "\n"
        journal_path = str(tmp_path / "sessions" / jname)
        # write the journal in the fixture dir BEFORE _run_main so the size matches
        (tmp_path / "sessions").mkdir(exist_ok=True)
        path = tmp_path / "sessions" / jname
        path.write_text(content)
        committed = path.stat().st_size
        api_calls, _, _ = self._run_main(
            t, monkeypatch, tmp_path, stop_after=1,
            existing_state={"offsets": {journal_path: committed}})
        assert not any("/messages" in p for p, _ in api_calls)

    def test_vanished_journal_is_skipped(self, t, tmp_path, monkeypatch):
        # offset points at a journal that no longer exists -> OSError -> continue
        jname = "gone.journal.jsonl"
        journal_path = str(tmp_path / "sessions" / jname)
        api_calls, state_file, _ = self._run_main(
            t, monkeypatch, tmp_path, stop_after=1,
            existing_state={"offsets": {journal_path: 10}})
        assert not any("/messages" in p for p, _ in api_calls)
        assert journal_path in json.loads(state_file.read_text())["offsets"]

    def test_getsize_failure_skips_journal(self, t, tmp_path, monkeypatch):
        # journal listed but stat fails -> the OSError continue branch runs
        jname = "vanish.journal.jsonl"
        real_size = os.path.getsize

        def flaky_size(path, *a, **k):
            if str(path).endswith(jname):
                raise OSError("stat gone")
            return real_size(path, *a, **k)

        monkeypatch.setattr(os.path, "getsize", flaky_size)
        jpath = str(tmp_path / "sessions" / jname)
        other = "healthy.journal.jsonl"
        api_calls, state_file, _ = self._run_main(
            t, monkeypatch, tmp_path, stop_after=1,
            journals={jname: "junk", other: ""},
            existing_state={"offsets": {jpath: 0}})
        assert not any("/messages" in p for p, _ in api_calls)

    def test_string_content_prompt_capture_off_dropped(self, t, tmp_path, monkeypatch):
        # string-content path where the mapper returns None (333->312 arc)
        with mock.patch.dict(t.CAPTURE, {"capture_prompts": "off"}):
            msgs_line = json.dumps({"meta": {}, "append_messages": [
                {"role": "user", "content": "off-prompts", "timestamp": "T"}]})
            content = json.dumps({"meta": {"working_dir": "/tmp"}}) + "\n" + msgs_line + "\n"
            api_calls, state_file, _ = self._run_main(
                t, monkeypatch, tmp_path, journals={"off.journal.jsonl": content}, stop_after=1)
        assert not any("/messages" in p for p, _ in api_calls)
        assert json.loads(state_file.read_text())["offsets"].get(
            str(tmp_path / "sessions" / "off.journal.jsonl"), 0) > 0

    def test_journal_without_postable_messages_sets_offset(self, t, tmp_path, monkeypatch):
        # journal with only meta records -> no messages posted, offset advanced (434->436)
        jname = "metaonly.journal.jsonl"
        content = json.dumps({"meta": {"working_dir": "/tmp"}}) + "\n" + json.dumps({"meta": {}}) + "\n"
        jpath = str(tmp_path / "sessions" / jname)
        api_calls, state_file, _ = self._run_main(
            t, monkeypatch, tmp_path, journals={jname: content}, stop_after=1)
        assert not any("/messages" in p for p, _ in api_calls)
        assert json.loads(state_file.read_text())["offsets"].get(jpath, 0) > 0

    def test_sigterm_stops_cleanly_with_state_flush(self, t, tmp_path, monkeypatch):
        # stop after the first sleep; the loop flushes state and logs cleanly
        api_calls, state_file, _ = self._run_main(
            t, monkeypatch, tmp_path, journals={}, stop_after=1)
        assert state_file.exists()  # final save_state ran


class TestJcodeVersionInInitialState:
    def test_missing_state_includes_version(self, t, tmp_path, monkeypatch):
        monkeypatch.setattr(t, "STATE_FILE", str(tmp_path / "none.json"))
        monkeypatch.setattr(t, "jcode_version", lambda: "jcode v9")
        state = t.load_state()
        assert state["jcode_version"] == "jcode v9"
