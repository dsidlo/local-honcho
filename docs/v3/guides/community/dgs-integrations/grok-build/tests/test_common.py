"""Branch coverage for src/honcho/common.py. No Honcho server."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "honcho"))

import common  # noqa: E402


def _git_config(url: str) -> str:
    return f'[remote "origin"]\n\turl = {url}\n'


class HonchoErrorTests(unittest.TestCase):
    def test_retryable_statuses(self) -> None:
        self.assertTrue(common.HonchoError("down").retryable)
        self.assertTrue(common.HonchoError("down", status=500).retryable)
        self.assertTrue(common.HonchoError("down", status=503).retryable)
        self.assertTrue(common.HonchoError("down", status=408).retryable)
        self.assertTrue(common.HonchoError("down", status=429).retryable)
        self.assertFalse(common.HonchoError("no", status=404).retryable)
        self.assertFalse(common.HonchoError("no", status=400).retryable)


class ScrubTests(unittest.TestCase):
    def test_every_secret_pattern_is_replaced(self) -> None:
        samples = [
            "sk-" + "a" * 20,
            "ghp_" + "b" * 20,
            "gho_" + "c" * 20,
            "AKIA" + "D" * 16,
            "xoxb-" + "e" * 10,
            "-----BEGIN PRIVATE KEY-----",
            "hf_" + "f" * 30,
            "xai-" + "g" * 20,
            "Bearer " + "h" * 25,
            "postgresql://user:secret@db.example/app",
            "postgres://user:secret@db.example/app",
            "appuser:s3cret@localhost:5432",
            'PGPASSWORD="hunter2"',
            "PGPASSWORD=hunter2",
        ]
        redacted = common.scrub(" ".join(samples))
        for sample in samples:
            self.assertNotIn(sample, redacted)
        self.assertIn(common.REDACTED, redacted)

    def test_plain_text_is_unchanged(self) -> None:
        self.assertEqual(common.scrub("hello world"), "hello world")


class PathAndNameTests(unittest.TestCase):
    def test_path_denied(self) -> None:
        self.assertTrue(common.path_denied("/home/dsidlo/.grok/auth.json"))
        self.assertTrue(common.path_denied("Secrets/token.pem"))
        self.assertFalse(common.path_denied("src/honcho/common.py"))

    def test_resource_name_folds_and_rejects(self) -> None:
        self.assertEqual(common.resource_name("  Local Honcho  "), "local-honcho")
        self.assertEqual(len(common.resource_name("a" * 600)), 512)
        with self.assertRaises(ValueError):
            common.resource_name("   ")
        with self.assertRaises(ValueError):
            common.resource_name("---")
        with self.assertRaises(ValueError):
            common.resource_name("default")

    def test_flatten_relative_path_uses_abspath(self) -> None:
        name = common.flatten_dir("workspace")
        self.assertNotIn("/", name)
        self.assertTrue(name.endswith("workspace"))

    def test_session_id_for(self) -> None:
        self.assertEqual(
            common.session_id_for("01a1276a-d9f9-7783-af90-0ce735d3b84a"),
            "grok-01a1276a-d9f9-7783-af90-0ce735d3b84a",
        )


class GitWorkspaceTests(unittest.TestCase):
    def test_read_missing_file(self) -> None:
        self.assertIsNone(common._read("/no/such/honcho-config"))

    def test_origin_slug_without_origin(self) -> None:
        self.assertIsNone(common._origin_slug("[core]\n\tbare = false\n"))

    def test_origin_named_default_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            common._origin_slug(_git_config("https://github.com/acme/default.git"))

    def test_repo_without_origin_uses_basename(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = os.path.join(root, "Bare_Name")
            os.makedirs(os.path.join(repo, ".git"))
            with open(os.path.join(repo, ".git", "config"), "w", encoding="utf-8") as handle:
                handle.write("[core]\n\tbare = false\n")
            self.assertEqual(common.workspace_from_dir(repo), "bare_name")

    def test_empty_git_dir_does_not_stop_the_walk(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = os.path.join(root, "real")
            os.makedirs(os.path.join(repo, "sub", ".git"))
            os.makedirs(os.path.join(repo, ".git"))
            with open(os.path.join(repo, ".git", "config"), "w", encoding="utf-8") as handle:
                handle.write(_git_config("git@github.com:acme/walked.git"))
            nested = os.path.join(repo, "sub", "child")
            os.makedirs(nested)
            self.assertEqual(common.workspace_from_dir(nested), "walked")

    def test_unreadable_config_falls_back_to_basename(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = os.path.join(root, "Locked")
            os.makedirs(os.path.join(repo, ".git"))
            config = os.path.join(repo, ".git", "config")
            with open(config, "w", encoding="utf-8") as handle:
                handle.write(_git_config("git@github.com:acme/locked.git"))
            with mock.patch.object(common, "_read", return_value=None):
                self.assertEqual(common.workspace_from_dir(repo), "locked")

    def test_gitfile_without_gitdir_line(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            checkout = os.path.join(root, "co")
            os.makedirs(checkout)
            with open(os.path.join(checkout, ".git"), "w", encoding="utf-8") as handle:
                handle.write("not a pointer\n")
            self.assertEqual(common.workspace_from_dir(checkout), "co")

    def test_unreadable_gitfile(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            checkout = os.path.join(root, "co")
            os.makedirs(checkout)
            with open(os.path.join(checkout, ".git"), "w", encoding="utf-8") as handle:
                handle.write("gitdir: /somewhere\n")
            with mock.patch.object(common, "_read", return_value=None):
                self.assertEqual(common.workspace_from_dir(checkout), "co")

    def test_relative_gitdir_with_its_own_origin(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            gitdir = os.path.join(root, "wt")
            os.makedirs(gitdir)
            with open(os.path.join(gitdir, "config"), "w", encoding="utf-8") as handle:
                handle.write(_git_config("https://github.com/acme/relative.git"))
            checkout = os.path.join(root, "checkout")
            os.makedirs(checkout)
            with open(os.path.join(checkout, ".git"), "w", encoding="utf-8") as handle:
                handle.write("gitdir: ../wt\n")
            self.assertEqual(common.workspace_from_dir(checkout), "relative")

    def test_relative_commondir(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            common_git = os.path.join(root, "main.git")
            os.makedirs(common_git)
            with open(os.path.join(common_git, "config"), "w", encoding="utf-8") as handle:
                handle.write(_git_config("ssh://git@example.com/acme/common.git"))
            gitdir = os.path.join(root, "work", "feature")
            os.makedirs(gitdir)
            with open(os.path.join(gitdir, "config"), "w", encoding="utf-8") as handle:
                handle.write("[core]\n\tbare = false\n")
            with open(os.path.join(gitdir, "commondir"), "w", encoding="utf-8") as handle:
                handle.write("../../main.git\n")
            checkout = os.path.join(root, "checkout")
            os.makedirs(checkout)
            with open(os.path.join(checkout, ".git"), "w", encoding="utf-8") as handle:
                handle.write(f"gitdir: {gitdir}\n")
            self.assertEqual(common.workspace_from_dir(checkout), "common")

    def test_gitdir_without_origin_or_commondir(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            gitdir = os.path.join(root, "wt")
            os.makedirs(gitdir)
            with open(os.path.join(gitdir, "config"), "w", encoding="utf-8") as handle:
                handle.write("[core]\n\tbare = false\n")
            checkout = os.path.join(root, "Orphan")
            os.makedirs(checkout)
            with open(os.path.join(checkout, ".git"), "w", encoding="utf-8") as handle:
                handle.write(f"gitdir: {gitdir}\n")
            self.assertEqual(common.workspace_from_dir(checkout), "orphan")

    def test_no_git_root_flattens(self) -> None:
        with mock.patch.object(common, "_git_config_text", return_value=(None, None)):
            self.assertEqual(
                common.workspace_from_dir("/home/dsidlo/workspace"),
                "home-dsidlo-workspace",
            )


class GitBranchTests(unittest.TestCase):
    def setUp(self) -> None:
        common.reset_caches()

    def test_branch_is_cached(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="pi-dev\n")
            with mock.patch("subprocess.run", return_value=completed) as run:
                self.assertEqual(common.git_branch(root), "pi-dev")
                self.assertEqual(common.git_branch(root), "pi-dev")
            self.assertEqual(run.call_count, 1)

    def test_empty_branch_caches_none(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="\n")
            with mock.patch("subprocess.run", return_value=completed) as run:
                self.assertIsNone(common.git_branch(root))
                self.assertIsNone(common.git_branch(root))
            self.assertEqual(run.call_count, 1)

    def test_subprocess_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with mock.patch("subprocess.run", side_effect=OSError("no git")):
                self.assertIsNone(common.git_branch(root))
            common.reset_caches()
            with mock.patch(
                "subprocess.run", side_effect=subprocess.SubprocessError("timed out")
            ):
                self.assertIsNone(common.git_branch(root))


class ChunkTests(unittest.TestCase):
    def test_short_text_is_one_piece(self) -> None:
        self.assertEqual(common.chunk("hello", size=10), ["hello"])

    def test_sentences_flush_and_hard_cut(self) -> None:
        text = "AB. CDEFGHIJKLMNOP."
        parts = common.chunk(text, size=8)
        self.assertTrue(parts)
        self.assertTrue(all(len(part) <= 8 for part in parts))
        self.assertIn("AB.", parts[0])

    def test_short_sentences_fill_then_flush(self) -> None:
        text = "Hi. Yo. Go now. "
        parts = common.chunk(text, size=10)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 10 for part in parts))

    def test_paragraph_boundary(self) -> None:
        parts = common.chunk("12345\n\n12345", size=8)
        self.assertEqual(parts, ["12345", "12345"])

    def test_near_size_paragraph_starts_a_fresh_buffer(self) -> None:
        # The first paragraph fits in size, but adding the separator does not,
        # and the buffer is still empty, so the flush is skipped. A second
        # paragraph keeps the whole string past the early return.
        parts = common.chunk("123456789\n\nx", size=10)
        self.assertEqual(parts, ["123456789", "x"])

    def test_paragraphs_that_fit_stay_together(self) -> None:
        parts = common.chunk("aa\n\nbb", size=20)
        self.assertEqual(len(parts), 1)
        self.assertIn("aa", parts[0])
        self.assertIn("bb", parts[0])

    def test_long_paragraph_after_a_short_one(self) -> None:
        parts = common.chunk("short\n\n" + ("x" * 30), size=10)
        self.assertEqual(parts[0], "short")
        self.assertTrue(all(len(part) <= 10 for part in parts))

    def test_newline_only_text_uses_the_hard_fallback(self) -> None:
        text = "\n\n" * 10
        parts = common.chunk(text, size=5)
        self.assertEqual(parts, [text[:5]])

    def test_unpunctuated_paragraph_is_sliced(self) -> None:
        parts = common.chunk("a" * 25, size=10)
        self.assertEqual(parts, ["a" * 10, "a" * 10, "a" * 5])

    def test_expand_chunks_leaves_a_short_message(self) -> None:
        message = {"content": "short"}
        self.assertEqual(common.expand_chunks(message), [message])

    def test_expand_chunks_without_metadata(self) -> None:
        parts = common.expand_chunks({"content": "a" * 25})
        # default size is 4000, so this stays one piece
        self.assertEqual(parts, [{"content": "a" * 25}])
        long = common.expand_chunks({"peer_id": "agent-grok", "content": "word " * 2000})
        self.assertGreater(len(long), 1)
        self.assertNotIn("metadata", {"content": "a" * 25})
        self.assertEqual(long[1]["metadata"]["chunk_index"], 2)
        self.assertEqual(long[0]["peer_id"], "agent-grok")


class ExtractTests(unittest.TestCase):
    def test_empty_user_query(self) -> None:
        self.assertIsNone(common.extract_user_text("<user_query>   </user_query>"))

    def test_blank_and_harness_wrappers(self) -> None:
        self.assertIsNone(common.extract_user_text("   "))
        self.assertIsNone(common.extract_user_text(""))
        self.assertIsNone(common.extract_user_text("<user_info>\nhello\n</user_info>"))
        self.assertIsNone(common.extract_user_text("<rules>\nbe kind\n</rules>"))

    def test_plain_prompt(self) -> None:
        self.assertEqual(common.extract_user_text("  Fix the tailer.  "), "Fix the tailer.")


class ResultAndRecordTests(unittest.TestCase):
    def test_result_status(self) -> None:
        self.assertEqual(common.result_status("  exit: 0\nok"), "ok")
        self.assertEqual(common.result_status("exit:"), "ok")
        self.assertEqual(common.result_status("exit: 2\ntrace"), "error")
        self.assertEqual(common.result_status("Error: boom"), "error")
        self.assertEqual(common.result_status("all good"), "ok")

    def test_text_from_content_shapes(self) -> None:
        self.assertEqual(common._text_from_content("plain"), "plain")
        self.assertEqual(
            common._text_from_content(
                [
                    {"type": "text", "text": "A"},
                    "B",
                    {"type": "image"},
                    {"type": "text"},
                ]
            ),
            "AB",
        )
        self.assertEqual(common._text_from_content(None), "")
        self.assertEqual(common._text_from_content(3), "")

    def test_map_record_edges(self) -> None:
        self.assertEqual(common.map_record({"type": "user", "content": None}, {}), [])
        self.assertEqual(
            common.map_record({"type": "user", "content": "hello"}, {})[0]["peer_id"],
            common.USER_PEER,
        )
        self.assertEqual(common.map_record({"type": "reasoning", "summary": None}, {}), [])
        self.assertEqual(
            common.map_record(
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "  "}]},
                {},
            ),
            [],
        )
        thought = common.map_record(
            {
                "type": "reasoning",
                "summary": [
                    {"type": "other"},
                    "skip",
                    {"type": "summary_text", "text": "keep"},
                ],
            },
            {},
        )
        self.assertEqual(thought[0]["content"], "keep")

        names: dict[str, str] = {}
        messages = common.map_record(
            {
                "type": "assistant",
                "content": [{"type": "text", "text": "done"}],
                "tool_calls": [
                    "not-a-call",
                    {"name": "", "id": ""},
                    {"id": "call-9"},
                ],
            },
            names,
        )
        self.assertEqual(messages[0]["metadata"]["type"], "final")
        self.assertEqual(messages[1]["content"], "tool_call unknown")
        self.assertEqual(names["call-9"], "unknown")
        self.assertEqual(
            common.map_record({"type": "assistant", "content": "", "tool_calls": "nope"}, {}),
            [],
        )
        observed = common.map_record(
            {"type": "tool_result", "tool_call_id": "missing", "content": {"raw": True}},
            {},
        )
        self.assertEqual(observed[0]["content"], "tool_result unknown: ok")
        self.assertEqual(common.map_record({"type": "system", "content": "prompt"}, {}), [])


class JournalEventTests(unittest.TestCase):
    def test_empty_and_complete_lines(self) -> None:
        self.assertEqual(common.journal_events(b"", {}), ([], 0))
        payload = b'\n{"type":"system","content":"x"}\nnot json\n[1, 2]\n'
        events, consumed = common.journal_events(payload, {})
        self.assertEqual(consumed, len(payload))
        self.assertEqual(len(events), 4)
        self.assertTrue(all(messages == [] for _end, messages in events))

    def test_tool_name_joins_across_lines(self) -> None:
        call = json.dumps(
            {
                "type": "assistant",
                "content": "",
                "tool_calls": [{"id": "call-1", "name": "grep"}],
            }
        )
        result = json.dumps(
            {"type": "tool_result", "tool_call_id": "call-1", "content": "exit: 0\n"}
        )
        names: dict[str, str] = {}
        events, _consumed = common.journal_events(
            (call + "\n" + result + "\n").encode(), names
        )
        self.assertEqual(names["call-1"], "grep")
        self.assertEqual(events[1][1][0]["content"], "tool_result grep: ok")


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        common.reset_caches()

    def _response(self, raw: bytes):
        handle = mock.Mock()
        handle.read.return_value = raw
        handle.__enter__ = mock.Mock(return_value=handle)
        handle.__exit__ = mock.Mock(return_value=False)
        return handle

    def test_success_json_empty_and_auth_header(self) -> None:
        captured = []

        def fake_urlopen(request, timeout=60):
            captured.append((request, timeout))
            if request.get_method() == "GET":
                return self._response(b"")
            return self._response(b'{"id": "local-honcho"}')

        with mock.patch.dict(os.environ, {"HONCHO_API_KEY": "test-token"}, clear=False):
            with mock.patch("urllib.request.urlopen", fake_urlopen):
                self.assertEqual(common.api("/workspaces", {"id": "local-honcho"}), {"id": "local-honcho"})
                self.assertEqual(common.api("/health", method="GET", timeout=3), {})
        self.assertEqual(captured[0][0].get_header("Authorization"), "Bearer test-token")
        self.assertEqual(captured[1][1], 3)
        self.assertIsNone(captured[1][0].data)

    def test_success_without_api_key(self) -> None:
        env = os.environ.copy()
        env.pop("HONCHO_API_KEY", None)
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("urllib.request.urlopen", return_value=self._response(b"{}")):
                request_holder = []

                def fake_urlopen(request, timeout=60):
                    request_holder.append(request)
                    return self._response(b"{}")

                with mock.patch("urllib.request.urlopen", fake_urlopen):
                    self.assertEqual(common.api("/workspaces", {"id": "x"}), {})
        self.assertIsNone(request_holder[0].get_header("Authorization"))

    def test_http_url_and_timeout_errors(self) -> None:
        http = urllib.error.HTTPError(
            url="http://localhost/v3/workspaces",
            code=503,
            msg="unavailable",
            hdrs=None,
            fp=io.BytesIO("busy".encode()),
        )
        with mock.patch("urllib.request.urlopen", side_effect=http):
            with self.assertRaises(common.HonchoError) as raised:
                common.api("/workspaces", {"id": "x"})
        self.assertEqual(raised.exception.status, 503)
        self.assertIn("busy", str(raised.exception))

        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with self.assertRaises(common.HonchoError) as raised:
                common.api("/workspaces", {"id": "x"})
        self.assertIsNone(raised.exception.status)
        self.assertTrue(raised.exception.retryable)

        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError()):
            with self.assertRaises(common.HonchoError) as raised:
                common.api("/workspaces")
        self.assertIn("timeout", str(raised.exception))

    def test_ensure_workspace_and_session_cache(self) -> None:
        calls = []

        def fake_api(path, payload=None, method="POST", timeout=60):
            calls.append(path)
            return {}

        with mock.patch.object(common, "api", fake_api):
            common.ensure_workspace("local-honcho")
            common.ensure_session("local-honcho", "grok-1")
            common.ensure_session("local-honcho", "grok-1")
        self.assertEqual(
            calls,
            ["/workspaces", "/workspaces/local-honcho/sessions"],
        )


if __name__ == "__main__":
    unittest.main()
