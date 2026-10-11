"""Unit tests for the Grok Honcho mapping rules. No server required."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "src", "honcho"),
)

import common  # noqa: E402


class WorkspaceTests(unittest.TestCase):
    def test_flatten_home_workspace(self) -> None:
        self.assertEqual(
            common.flatten_dir("/home/dsidlo/workspace"),
            "home-dsidlo-workspace",
        )

    def test_flatten_rejects_default(self) -> None:
        with self.assertRaises(ValueError):
            common.resource_name("default")

    def test_origin_slug_matches_pi_rule(self) -> None:
        config = (
            '[remote "origin"]\n'
            "\turl = git@github.com:dsidlo/local-honcho.git\n"
            '[remote "upstream"]\n'
            "\turl = https://github.com/plastic-labs/honcho.git\n"
        )
        self.assertEqual(common._origin_slug(config), "local-honcho")

    def test_directory_inside_repo_uses_origin(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            git = os.path.join(root, "local-honcho", ".git")
            os.makedirs(git)
            with open(os.path.join(git, "config"), "w", encoding="utf-8") as handle:
                handle.write(
                    '[remote "origin"]\n'
                    "\turl = git@github.com:dsidlo/local-honcho.git\n"
                )
            nested = os.path.join(root, "local-honcho", "src")
            os.makedirs(nested)
            self.assertEqual(common.workspace_from_dir(nested), "local-honcho")

    def test_directory_without_git_uses_full_path(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "workspace")
            os.makedirs(path)
            name = common.workspace_from_dir(path)
            self.assertNotEqual(name, "workspace")
            self.assertTrue(name.endswith("workspace"))
            self.assertNotIn("/", name)

    def test_worktree_reads_commondir_origin(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            common_git = os.path.join(root, "main.git")
            os.makedirs(common_git)
            with open(os.path.join(common_git, "config"), "w", encoding="utf-8") as handle:
                handle.write(
                    '[remote "origin"]\n\turl = https://github.com/acme/openclaw.git\n'
                )
            gitdir = os.path.join(root, "worktrees", "feature")
            os.makedirs(gitdir)
            with open(os.path.join(gitdir, "commondir"), "w", encoding="utf-8") as handle:
                handle.write(common_git + "\n")
            with open(os.path.join(gitdir, "config"), "w", encoding="utf-8") as handle:
                handle.write("[core]\n\trepositoryformatversion = 0\n")
            checkout = os.path.join(root, "checkout")
            os.makedirs(checkout)
            with open(os.path.join(checkout, ".git"), "w", encoding="utf-8") as handle:
                handle.write(f"gitdir: {gitdir}\n")
            self.assertEqual(common.workspace_from_dir(checkout), "openclaw")


class CaptureTests(unittest.TestCase):
    def test_user_query_is_kept_and_wrapper_is_dropped(self) -> None:
        text = "<user_info>\nsecret-looking\n</user_info>\n<user_query>\nFix the tailer.\n</user_query>"
        self.assertEqual(common.extract_user_text(text), "Fix the tailer.")

    def test_system_reminder_is_not_a_prompt(self) -> None:
        self.assertIsNone(
            common.extract_user_text("<system-reminder>\nSkills are available.\n</system-reminder>")
        )

    def test_reasoning_keeps_summary_and_ignores_ciphertext(self) -> None:
        record = {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Check the journal shape."}],
            "encrypted_content": "not-a-real-cipher-but-must-not-leak",
        }
        messages = common.map_record(record, {})
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["metadata"]["type"], "thought")
        self.assertNotIn("encrypted", messages[0]["content"])
        self.assertNotIn("not-a-real-cipher", json.dumps(messages[0]))

    def test_reasoning_without_plaintext_is_skipped(self) -> None:
        record = {"type": "reasoning", "summary": [], "encrypted_content": "abc"}
        self.assertEqual(common.map_record(record, {}), [])

    def test_tool_call_keeps_the_name_only(self) -> None:
        secret = "sk-" + ("a" * 24)
        record = {
            "type": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "run_terminal_command",
                    "arguments": json.dumps({"command": f"echo {secret}"}),
                }
            ],
        }
        messages = common.map_record(record, {})
        self.assertEqual(messages[0]["content"], "tool_call run_terminal_command")
        self.assertNotIn(secret, json.dumps(messages))
        self.assertNotIn("arguments", messages[0])

    def test_tool_result_is_name_and_status(self) -> None:
        names = {"call-1": "run_terminal_command"}
        record = {
            "type": "tool_result",
            "tool_call_id": "call-1",
            "content": "exit: 1\n" + ("trace " * 500),
        }
        messages = common.map_record(record, names)
        self.assertEqual(messages[0]["content"], "tool_result run_terminal_command: error")
        self.assertLess(len(messages[0]["content"]), 80)

    def test_secret_in_a_prompt_is_scrubbed(self) -> None:
        secret = "sk-" + ("b" * 24)
        messages = common.map_record(
            {"type": "user", "content": [{"type": "text", "text": f"key is {secret}"}]},
            {},
        )
        self.assertIn(common.REDACTED, messages[0]["content"])
        self.assertNotIn(secret, messages[0]["content"])

    def test_partial_line_is_not_consumed(self) -> None:
        payload = b'{"type":"user","content":"hello"}\n{"type":"user","cont'
        events, consumed = common.journal_events(payload, {})
        self.assertEqual(consumed, payload.index(b"\n") + 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1][0]["content"], "hello")

    def test_long_text_is_chunked_with_pi_metadata(self) -> None:
        message = {
            "peer_id": "agent-grok",
            "content": "para one.\n\n" + ("word " * 2000),
            "metadata": {"type": "final"},
        }
        parts = common.expand_chunks(message)
        self.assertGreater(len(parts), 1)
        self.assertEqual(parts[0]["metadata"]["chunk_index"], 1)
        self.assertEqual(parts[0]["metadata"]["total_chunks"], len(parts))
        self.assertTrue(all(len(part["content"]) <= common.MAX_CONTENT for part in parts))


if __name__ == "__main__":
    unittest.main()
