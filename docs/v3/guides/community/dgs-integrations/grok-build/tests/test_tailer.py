"""Branch coverage for src/honcho/tailer.py. Honcho is mocked."""

from __future__ import annotations

import json
import os
import runpy
import signal
import sys
import tempfile
import time
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "honcho"))

import common  # noqa: E402
import tailer  # noqa: E402

TAILER_PATH = os.path.join(os.path.dirname(__file__), "..", "src", "honcho", "tailer.py")


class TailerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        common.reset_caches()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = self._tmpdir.name
        self._env = mock.patch.dict(
            os.environ,
            {
                "GROK_HONCHO_STATE": os.path.join(self.root, "state.json"),
                "GROK_HONCHO_LOG": os.path.join(self.root, "tailer.log"),
                "GROK_SESSIONS_DIR": os.path.join(self.root, "sessions"),
            },
            clear=False,
        )
        self._env.start()
        tailer.STATE_FILE = os.environ["GROK_HONCHO_STATE"]
        tailer.LOG_FILE = os.environ["GROK_HONCHO_LOG"]
        tailer.SESSIONS_DIR = os.environ["GROK_SESSIONS_DIR"]
        self.sleep = mock.patch.object(time, "sleep")
        self.sleep.start()

    def tearDown(self) -> None:
        self.sleep.stop()
        self._env.stop()
        self._tmpdir.cleanup()
        common.reset_caches()

    def log_text(self) -> str:
        try:
            with open(tailer.LOG_FILE, encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return ""


class LogAndStateTests(TailerTestCase):
    def test_log_with_and_without_fields(self) -> None:
        tailer.log("INFO", "quiet")
        tailer.log("INFO", "fields", count=1, label="a")
        text = self.log_text()
        self.assertIn("quiet", text)
        self.assertIn('"count": 1', text)

    def test_log_swallows_oserror(self) -> None:
        with mock.patch.object(tailer.os, "makedirs", side_effect=OSError("nope")):
            tailer.log("ERROR", "cannot write", count=1)
        self.assertFalse(os.path.exists(tailer.LOG_FILE))

    def test_load_missing_invalid_and_present_state(self) -> None:
        missing = tailer.load_state()
        self.assertEqual(missing["offsets"], {})
        self.assertEqual(missing["tools"], {})

        with open(tailer.STATE_FILE, "w", encoding="utf-8") as handle:
            handle.write("{")
        broken = tailer.load_state()
        self.assertEqual(broken["offsets"], {})

        tailer.save_state({"offsets": {"/j": 4}, "tools": {"call": "grep"}})
        loaded = tailer.load_state()
        self.assertEqual(loaded["offsets"]["/j"], 4)
        self.assertEqual(loaded["tools"]["call"], "grep")

    def test_trim_tools(self) -> None:
        small = {"a": "grep"}
        tailer.trim_tools(small)
        self.assertEqual(small, {"a": "grep"})
        tools = {f"c{i}": "grep" for i in range(tailer.MAX_TOOL_IDS + 3)}
        tailer.trim_tools(tools)
        self.assertEqual(len(tools), tailer.MAX_TOOL_IDS)
        self.assertNotIn("c0", tools)


class SessionDiscoveryTests(TailerTestCase):
    def test_session_cwd_sources(self) -> None:
        session = os.path.join(self.root, "session")
        os.makedirs(session)
        self.assertEqual(
            tailer.session_cwd(session, "%2Ftmp%2Ffrom-parent"),
            "/tmp/from-parent",
        )
        with open(os.path.join(session, "summary.json"), "w", encoding="utf-8") as handle:
            handle.write("{")
        self.assertEqual(tailer.session_cwd(session, "plain"), "plain")
        with open(os.path.join(session, "summary.json"), "w", encoding="utf-8") as handle:
            json.dump({"info": {"cwd": ""}}, handle)
        self.assertEqual(tailer.session_cwd(session, "fallback"), "fallback")
        with open(os.path.join(session, "summary.json"), "w", encoding="utf-8") as handle:
            json.dump({"info": {"cwd": 5}}, handle)
        self.assertEqual(tailer.session_cwd(session, "fallback"), "fallback")
        with open(os.path.join(session, "summary.json"), "w", encoding="utf-8") as handle:
            json.dump({"info": []}, handle)
        self.assertEqual(tailer.session_cwd(session, "fallback"), "fallback")
        with open(os.path.join(session, "summary.json"), "w", encoding="utf-8") as handle:
            json.dump({"info": {"cwd": "/repo"}}, handle)
        self.assertEqual(tailer.session_cwd(session, "ignored"), "/repo")

    def test_list_journals(self) -> None:
        self.assertIsNone(tailer.list_journals(os.path.join(self.root, "missing")))
        sessions = os.path.join(self.root, "sessions")
        os.makedirs(sessions)
        self.assertEqual(tailer.list_journals(sessions), [])
        with open(os.path.join(sessions, "notes.txt"), "w", encoding="utf-8") as handle:
            handle.write("skip")
        encoded = os.path.join(sessions, "%2Ftmp%2Fproj")
        os.makedirs(os.path.join(encoded, "empty-session"))
        with open(os.path.join(encoded, "not-a-session.txt"), "w", encoding="utf-8") as handle:
            handle.write("skip")
        session = os.path.join(encoded, "sid-1")
        os.makedirs(session)
        journal = os.path.join(session, "chat_history.jsonl")
        with open(journal, "w", encoding="utf-8") as handle:
            handle.write("")
        found = tailer.list_journals(sessions)
        self.assertEqual(found, [(journal, "sid-1", session, "%2Ftmp%2Fproj")])


class DecorateAndPostTests(TailerTestCase):
    def test_decorate_adds_metadata_and_chunks(self) -> None:
        short = tailer.decorate([{"content": "hi"}], {"git_branch": "pi-dev"})
        self.assertEqual(short[0]["metadata"]["git_branch"], "pi-dev")
        bare = tailer.decorate([{"content": "hi", "metadata": {"type": "final"}}], {})
        self.assertEqual(bare[0]["metadata"]["type"], "final")
        long = tailer.decorate([{"content": "word " * 2000, "metadata": {"type": "final"}}], {})
        self.assertGreater(len(long), 1)
        self.assertEqual(long[0]["metadata"]["type"], "final")

    def test_post_messages_outcomes(self) -> None:
        self.assertEqual(tailer.post_messages("ws", "sid", []), "ok")

        with mock.patch.object(common, "api", return_value={}) as api:
            messages = [{"content": str(i), "peer_id": "dsidlo"} for i in range(101)]
            self.assertEqual(tailer.post_messages("ws", "sid", messages), "ok")
            self.assertEqual(api.call_count, 2)

        def fail_second(path, payload=None, method="POST", timeout=60):
            if payload and payload["messages"][0]["content"] == "100":
                raise common.HonchoError("later", status=503)
            return {}

        with mock.patch.object(common, "api", fail_second):
            messages = [{"content": str(i), "peer_id": "dsidlo"} for i in range(101)]
            self.assertEqual(tailer.post_messages("ws", "sid", messages), "retry")
        self.assertIn("post failed", self.log_text())

        with mock.patch.object(
            common, "api", side_effect=common.HonchoError("no", status=422)
        ):
            self.assertEqual(
                tailer.post_messages("ws", "sid", [{"content": "x", "peer_id": "dsidlo"}]),
                "drop",
            )


class HandleJournalTests(TailerTestCase):
    def write_journal(self, body: bytes, cwd: str | None = None) -> tuple[str, str, str]:
        encoded = urllib.parse.quote("/tmp/proj", safe="")
        session = os.path.join(self.root, "sess", encoded, "sid")
        os.makedirs(session)
        path = os.path.join(session, "chat_history.jsonl")
        with open(path, "wb") as handle:
            handle.write(body)
        if cwd is not None:
            with open(os.path.join(session, "summary.json"), "w", encoding="utf-8") as handle:
                json.dump({"info": {"cwd": cwd}}, handle)
        return path, session, encoded

    def state(self, **extra):
        base = {"offsets": {}, "tools": {}}
        base.update(extra)
        return base

    def test_missing_file_shrink_and_caught_up(self) -> None:
        state = self.state()
        tailer.handle_journal("/no/such/journal", "sid", self.root, "parent", state)
        path, _session, _encoded = self.write_journal(b"abc\n")
        state["offsets"][path] = 100
        tailer.handle_journal(path, "sid", self.root, "parent", state)
        self.assertEqual(state["offsets"][path], 4)
        tailer.handle_journal(path, "sid", self.root, "parent", state)
        self.assertEqual(state["offsets"][path], 4)

    def test_unreadable_file_and_partial_line(self) -> None:
        state = self.state()
        directory = os.path.join(self.root, "dir-as-file")
        os.makedirs(directory)
        tailer.handle_journal(directory, "sid", self.root, "parent", state)
        self.assertNotIn(directory, state["offsets"])

        path, session, encoded = self.write_journal(b'{"type":"user","content":"hel')
        tailer.handle_journal(path, "sid", session, encoded, state)
        self.assertNotIn(path, state["offsets"])

    def test_oversized_line_is_dropped(self) -> None:
        path, session, encoded = self.write_journal(b"x" * 50)
        state = self.state()
        with mock.patch.object(tailer, "MAX_READ", 8):
            tailer.handle_journal(path, "sid", session, encoded, state)
        self.assertEqual(state["offsets"][path], 8)
        self.assertIn("dropped oversized line", self.log_text())

    def test_rejected_workspace_leaves_the_offset(self) -> None:
        line = json.dumps({"type": "user", "content": "hello"}).encode() + b"\n"
        path, session, encoded = self.write_journal(line, cwd="/tmp/proj")
        state = self.state()
        with mock.patch.object(
            common, "workspace_from_dir", side_effect=ValueError("default")
        ):
            tailer.handle_journal(path, "sid", session, encoded, state)
        self.assertNotIn(path, state["offsets"])
        self.assertIn("workspace name rejected", self.log_text())

    def test_skips_advance_without_a_post(self) -> None:
        body = b'\n{"type":"system","content":"hidden"}\n'
        path, session, encoded = self.write_journal(body)
        state = self.state()
        with mock.patch.object(common, "api") as api:
            tailer.handle_journal(path, "sid", session, encoded, state)
        api.assert_not_called()
        self.assertEqual(state["offsets"][path], len(body))

    def test_ensure_retry_and_drop(self) -> None:
        user = json.dumps({"type": "user", "content": "hello"}).encode()
        body = b"\n" + user + b"\n"
        path, session, encoded = self.write_journal(body, cwd=self.root)
        state = self.state()
        with mock.patch.object(common, "git_branch", return_value=None):
            with mock.patch.object(
                common,
                "ensure_workspace",
                side_effect=common.HonchoError("down", status=503),
            ):
                tailer.handle_journal(path, "sid", session, encoded, state)
        self.assertEqual(state["offsets"][path], 1)
        self.assertIn("ensure failed", self.log_text())

        state["offsets"][path] = 0
        with mock.patch.object(common, "git_branch", return_value=None):
            with mock.patch.object(
                common,
                "ensure_workspace",
                side_effect=common.HonchoError("no", status=422),
            ):
                tailer.handle_journal(path, "sid", session, encoded, state)
        self.assertEqual(state["offsets"][path], len(body))

    def test_post_retry_drop_and_success(self) -> None:
        first = json.dumps({"type": "user", "content": "one"}).encode()
        second = json.dumps({"type": "user", "content": "two"}).encode()
        body = first + b"\n" + second + b"\n"
        path, session, encoded = self.write_journal(body, cwd=self.root)
        state = self.state(tools={f"c{i}": "grep" for i in range(3)})

        with mock.patch.object(tailer, "MAX_TOOL_IDS", 2):
            with mock.patch.object(common, "git_branch", return_value="pi-dev"):
                with mock.patch.object(common, "ensure_workspace"):
                    with mock.patch.object(common, "ensure_session"):
                        with mock.patch.object(tailer, "post_messages", return_value="retry") as post:
                            tailer.handle_journal(path, "ab cd", session, encoded, state)
        self.assertEqual(state["offsets"][path], 0)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0], common.workspace_from_dir(self.root))
        self.assertTrue(post.call_args.args[1].startswith("grok-"))
        self.assertEqual(post.call_args.args[2][0]["metadata"]["git_branch"], "pi-dev")
        self.assertLessEqual(len(state["tools"]), 2)

        state = self.state()
        outcomes = iter(["drop", "ok"])

        def sequenced(*_args):
            return next(outcomes)

        with mock.patch.object(common, "git_branch", return_value=None):
            with mock.patch.object(common, "ensure_workspace"):
                with mock.patch.object(common, "ensure_session"):
                    with mock.patch.object(tailer, "post_messages", side_effect=sequenced):
                        tailer.handle_journal(path, "sid", session, encoded, state)
        self.assertEqual(state["offsets"][path], len(body))
        text = self.log_text()
        self.assertIn("dropped journal line", text)
        self.assertIn("captured", text)

    def test_resume_from_the_middle_of_the_file(self) -> None:
        first = json.dumps({"type": "user", "content": "one"}).encode() + b"\n"
        second = json.dumps({"type": "user", "content": "two"}).encode() + b"\n"
        path, session, encoded = self.write_journal(first + second, cwd=self.root)
        state = self.state(offsets={path: len(first)})
        with mock.patch.object(common, "git_branch", return_value=None):
            with mock.patch.object(common, "ensure_workspace") as ensure_workspace:
                with mock.patch.object(common, "ensure_session") as ensure_session:
                    with mock.patch.object(tailer, "post_messages", return_value="ok") as post:
                        tailer.handle_journal(path, "sid", session, encoded, state)
        posted = post.call_args.args[2][0]["content"]
        self.assertEqual(posted, "two")
        self.assertEqual(state["offsets"][path], len(first) + len(second))
        ensure_workspace.assert_called_once()
        ensure_session.assert_called_once()


class MainTests(TailerTestCase):
    def _run_once(self) -> None:
        saved = {}

        def fake_signal(sig, handler):
            saved[sig] = handler

        def fake_sleep(_seconds):
            saved[signal.SIGTERM](signal.SIGTERM, None)

        with mock.patch.object(signal, "signal", fake_signal):
            with mock.patch.object(time, "sleep", fake_sleep):
                tailer.main()

    def test_main_missing_sessions_dir_then_stops(self) -> None:
        self._run_once()
        self.assertIn("tailer started", self.log_text())
        self.assertIn("tailer stopped", self.log_text())
        with open(tailer.STATE_FILE, encoding="utf-8") as handle:
            self.assertIn("last_beat", handle.read())

    def test_main_prunes_dead_offsets(self) -> None:
        sessions = tailer.SESSIONS_DIR
        encoded = os.path.join(sessions, "enc")
        session = os.path.join(encoded, "sid")
        os.makedirs(session)
        journal = os.path.join(session, "chat_history.jsonl")
        with open(journal, "w", encoding="utf-8") as handle:
            handle.write("")
        tailer.save_state({"offsets": {journal: 0, "/gone/chat_history.jsonl": 9}, "tools": {}})
        self._run_once()
        with open(tailer.STATE_FILE, encoding="utf-8") as handle:
            state = json.load(handle)
        self.assertEqual(list(state["offsets"]), [journal])

    def test_main_logs_loop_errors(self) -> None:
        with mock.patch.object(tailer, "list_journals", side_effect=RuntimeError("boom")):
            self._run_once()
        self.assertIn("loop error", self.log_text())
        self.assertIn("RuntimeError", self.log_text())

    def test_module_runs_as_main(self) -> None:
        saved = {}

        def fake_signal(sig, handler):
            saved[sig] = handler

        def fake_sleep(_seconds):
            saved[signal.SIGTERM](signal.SIGTERM, None)

        state = os.path.join(self.root, "main-state.json")
        log_path = os.path.join(self.root, "main.log")
        with mock.patch.dict(
            os.environ,
            {
                "GROK_SESSIONS_DIR": os.path.join(self.root, "no-sessions"),
                "GROK_HONCHO_STATE": state,
                "GROK_HONCHO_LOG": log_path,
            },
            clear=False,
        ):
            with mock.patch.object(signal, "signal", fake_signal):
                with mock.patch.object(time, "sleep", fake_sleep):
                    runpy.run_path(TAILER_PATH, run_name="__main__")
        with open(log_path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("tailer stopped", text)


if __name__ == "__main__":
    unittest.main()
