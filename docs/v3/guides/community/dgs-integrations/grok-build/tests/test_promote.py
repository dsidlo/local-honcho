"""Promotion of one Honcho conclusion into a Grok memory topic."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "honcho"))

import promote  # noqa: E402


CONCLUSION = {
    "id": "abc_123",
    "content": "The tailer posts one journal line at a time.",
    "observer_id": "agent-grok",
    "observed_id": "agent-grok",
    "workspace": "local-honcho",
}


class MemoryEnabledTests(unittest.TestCase):
    def test_missing_and_invalid_config(self) -> None:
        self.assertFalse(promote.memory_enabled(""))
        self.assertFalse(promote.memory_enabled("["))
        self.assertFalse(promote.memory_enabled("memory = false\n"))

    def test_memory_flag(self) -> None:
        self.assertFalse(promote.memory_enabled("[memory]\n"))
        self.assertTrue(promote.memory_enabled("[memory]\nenabled = true\n"))
        self.assertFalse(promote.memory_enabled("[memory]\nenabled = false\n"))

    def test_memory_v2_wins(self) -> None:
        text = "[memory]\nenabled = true\n\n[memory_v2]\nenabled = false\n"
        self.assertFalse(promote.memory_enabled(text))
        text = "[memory]\nenabled = false\n\n[memory_v2]\nenabled = true\n"
        self.assertTrue(promote.memory_enabled(text))
        self.assertTrue(
            promote.memory_enabled("memory_v2 = false\n\n[memory]\nenabled = true\n")
        )


class PromoteTests(unittest.TestCase):
    def test_refuses_when_memory_is_off(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = promote.promote("", Path(root), CONCLUSION)
            self.assertIsNone(path)
            self.assertEqual(os.listdir(root), [])

    def test_writes_a_topic_once(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "topics"
            config = "[memory]\nenabled = true\n"
            path = promote.promote(config, directory, CONCLUSION)
            assert path is not None
            text = path.read_text(encoding="utf-8")
            self.assertIn("tags: [from-honcho]", text)
            self.assertIn("honcho_conclusion_id: abc_123", text)
            self.assertIn("The tailer posts one journal line at a time.", text)
            self.assertEqual(path.name, "honcho-abc_123.md")
            rewritten = {**CONCLUSION, "content": "a different sentence"}
            again = promote.promote(config, directory, rewritten)
            self.assertEqual(again, path)
            self.assertIn("one journal line at a time", path.read_text(encoding="utf-8"))
            path.write_text("notes without the id\n", encoding="utf-8")
            replaced = promote.promote(config, directory, CONCLUSION)
            self.assertEqual(replaced, path)
            self.assertIn("honcho_conclusion_id: abc_123", path.read_text(encoding="utf-8"))

    def test_alternate_peer_keys_and_unsafe_id(self) -> None:
        conclusion = {
            "id": "id/with space",
            "content": "kept",
            "observer": "agent-grok",
            "observed": "agent-grok",
        }
        text = promote.render_topic(conclusion)
        self.assertIn("Observer `agent-grok`, observed `agent-grok`.", text)
        bare = promote.render_topic({"id": "only", "content": "kept"})
        self.assertIn("Observer ``, observed ``.", bare)
        self.assertEqual(
            promote.topic_path(Path("/tmp"), "id/with space").name,
            "honcho-id-with-space.md",
        )

    def test_rejects_a_conclusion_without_id_or_content(self) -> None:
        with self.assertRaises(promote.PromotionError):
            promote.render_topic({"content": "x"})
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(promote.PromotionError):
                promote.promote(
                    "[memory]\nenabled = true\n",
                    Path(root),
                    {"id": "only-id"},
                )


if __name__ == "__main__":
    unittest.main()
