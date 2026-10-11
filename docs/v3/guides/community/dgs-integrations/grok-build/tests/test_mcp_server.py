"""Branch coverage for src/honcho/mcp_server.py. Honcho is mocked."""

from __future__ import annotations

import io
import json
import os
import runpy
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "honcho"))

import common  # noqa: E402
import mcp_server  # noqa: E402

MCP_PATH = os.path.join(os.path.dirname(__file__), "..", "src", "honcho", "mcp_server.py")


class McpTestCase(unittest.TestCase):
    def setUp(self) -> None:
        common.reset_caches()

    def tearDown(self) -> None:
        common.reset_caches()


class DetectTests(McpTestCase):
    def test_explicit_workspace_wins(self) -> None:
        self.assertEqual(mcp_server.detect({"workspace": "  Local Honcho "}), "local-honcho")

    def test_blank_explicit_uses_cwd_argument(self) -> None:
        with mock.patch.object(common, "workspace_from_dir", return_value="from-cwd") as detect:
            self.assertEqual(mcp_server.detect({"workspace": "   ", "cwd": "/repo"}), "from-cwd")
            detect.assert_called_once_with("/repo")

    def test_missing_cwd_uses_the_process_directory(self) -> None:
        with mock.patch.object(common, "workspace_from_dir", return_value="here") as detect:
            with mock.patch.object(mcp_server.os, "getcwd", return_value="/proc-cwd"):
                self.assertEqual(mcp_server.detect({"cwd": "  "}), "here")
                self.assertEqual(mcp_server.detect({"workspace": 12}), "here")
        self.assertEqual(detect.call_args_list[0].args[0], "/proc-cwd")
        self.assertEqual(detect.call_args_list[1].args[0], "/proc-cwd")


class ToolTests(McpTestCase):
    def test_search(self) -> None:
        self.assertEqual(mcp_server.tool_search({"query": "  "}), "error: empty query")
        with mock.patch.object(common, "ensure_workspace") as ensure:
            with mock.patch.object(common, "api", return_value=[]) as api:
                self.assertEqual(
                    mcp_server.tool_search({"query": "voice", "workspace": "local-honcho"}),
                    "no results",
                )
        ensure.assert_called_once_with("local-honcho")
        self.assertEqual(api.call_args.args[0], "/workspaces/local-honcho/search")

        items = [
            {
                "created_at": "2026-10-11T00:00:00",
                "peer_id": "dsidlo",
                "metadata": {"type": "prompt"},
                "content": "a" * 400,
            },
            {"peer_id": "agent-grok", "metadata": None, "content": "final"},
        ]
        with mock.patch.object(common, "ensure_workspace"):
            with mock.patch.object(common, "api", return_value={"items": items}):
                text = mcp_server.tool_search({"query": "voice", "workspace": "ws", "limit": 100})
        self.assertIn("peer=dsidlo type=prompt", text)
        self.assertIn("type=?", text)
        self.assertLessEqual(text.count("---") + 1, 25)
        self.assertLess(len(text.split("---")[0]), 400)

        with mock.patch.object(common, "ensure_workspace"):
            with mock.patch.object(common, "api", return_value={"items": []}):
                self.assertEqual(
                    mcp_server.tool_search({"query": "voice", "workspace": "ws"}),
                    "no results",
                )

    def test_conclusions(self) -> None:
        self.assertEqual(mcp_server.tool_conclusions({"query": ""}), "error: empty query")

        def hybrid(path, payload=None, method="POST", timeout=60):
            if path.endswith("/documents/search"):
                return [
                    {"created_at": "2026-01-02T00:00:00", "content": "c" * 1600},
                ]
            raise AssertionError(path)

        calls: list[tuple[str, dict[str, object]]] = []

        def recording(path, payload=None, method="POST", timeout=60):
            calls.append((path, payload or {}))
            return hybrid(path, payload, method, timeout)

        with mock.patch.object(common, "ensure_workspace"):
            with mock.patch.object(common, "api", recording):
                text = mcp_server.tool_conclusions(
                    {
                        "query": "voice",
                        "workspace": "local-honcho",
                        "observer": "agent-grok",
                        "observed": "dsidlo",
                        "limit": 100,
                    }
                )
        self.assertTrue(text.startswith("[2026-01-02]"))
        self.assertLessEqual(len(text.split("] ", 1)[1]), 1500)
        body = calls[0][1]
        self.assertEqual(body["method"], "rrf")
        self.assertEqual(body["top_k"], 25)

        fallback_calls: list[dict[str, object]] = []

        def fallback(path, payload=None, method="POST", timeout=60):
            if path.endswith("/documents/search"):
                raise common.HonchoError("missing", status=404)
            if path.endswith("/conclusions/query"):
                fallback_calls.append(payload or {})
                return {"items": [{"created_at": "2026-03-01", "content": "from query"}]}
            raise AssertionError(path)

        with mock.patch.object(common, "ensure_workspace"):
            with mock.patch.object(common, "api", fallback):
                text = mcp_server.tool_conclusions({"query": "voice", "workspace": "ws"})
        self.assertIn("from query", text)
        self.assertEqual(fallback_calls[0]["filters"]["observer"], common.AGENT_PEER)
        self.assertEqual(fallback_calls[0]["filters"]["observed"], common.AGENT_PEER)

        with mock.patch.object(common, "ensure_workspace"):
            with mock.patch.object(common, "api", return_value=[]):
                self.assertEqual(
                    mcp_server.tool_conclusions({"query": "voice", "workspace": "ws"}),
                    "no conclusions",
                )

        with mock.patch.object(common, "ensure_workspace"):
            with mock.patch.object(
                common, "api", side_effect=common.HonchoError("down", status=503)
            ):
                with self.assertRaises(common.HonchoError):
                    mcp_server.tool_conclusions({"query": "voice", "workspace": "ws"})

    def test_ask(self) -> None:
        self.assertEqual(mcp_server.tool_ask({"query": " "}), "error: empty query")
        self.assertIn(
            "reasoning_level",
            mcp_server.tool_ask({"query": "where", "reasoning_level": "huge", "workspace": "ws"}),
        )
        with mock.patch.object(common, "ensure_workspace"):
            with mock.patch.object(
                common, "api", return_value={"content": "  left off at the tailer  "}
            ) as api:
                self.assertEqual(
                    mcp_server.tool_ask(
                        {
                            "query": "where",
                            "workspace": "local-honcho",
                            "peer": "agent-grok",
                            "target": " dsidlo ",
                            "reasoning_level": "minimal",
                        }
                    ),
                    "  left off at the tailer  ",
                )
        payload = api.call_args.args[1]
        self.assertEqual(payload["target"], "dsidlo")
        self.assertEqual(payload["reasoning_level"], "minimal")
        self.assertEqual(payload["conclusion_search"], "rrf")
        self.assertIn("/peers/agent-grok/chat", api.call_args.args[0])

        with mock.patch.object(common, "ensure_workspace"):
            with mock.patch.object(common, "api", return_value={"content": "self model"}) as api:
                self.assertEqual(
                    mcp_server.tool_ask({"query": "self", "workspace": "ws"}),
                    "self model",
                )
        self.assertNotIn("target", api.call_args.args[1])
        self.assertEqual(api.call_args.args[1]["conclusion_search"], "rrf")

        with mock.patch.object(common, "ensure_workspace"):
            with mock.patch.object(common, "api", return_value={"content": "   "}) as api:
                text = mcp_server.tool_ask({"query": "self", "workspace": "ws", "target": ""})
        self.assertNotIn("target", api.call_args.args[1])
        self.assertIn("{", text)

        with mock.patch.object(common, "ensure_workspace"):
            with mock.patch.object(common, "api", return_value=["nope"]) as api:
                text = mcp_server.tool_ask({"query": "self", "workspace": "ws", "target": None})
        self.assertNotIn("target", api.call_args.args[1])
        self.assertIn("nope", text)

    def test_store(self) -> None:
        self.assertEqual(mcp_server.tool_store({"text": "  "}), "error: empty text")
        secret = "sk-" + "a" * 24
        calls = []

        def fake_api(path, payload=None, method="POST", timeout=60):
            calls.append((path, payload))
            return {}

        with mock.patch.object(common, "ensure_workspace"):
            with mock.patch.object(common, "ensure_session"):
                with mock.patch.object(common, "api", fake_api):
                    reply = mcp_server.tool_store(
                        {
                            "text": f"note {secret}",
                            "kind": "prompt",
                            "workspace": "local-honcho",
                            "session": "My Session",
                        }
                    )
        self.assertIn("as " + common.USER_PEER, reply)
        self.assertIn("my-session", reply)
        stored = calls[-1][1]["messages"][0]
        self.assertNotIn(secret, stored["content"])
        self.assertEqual(stored["peer_id"], common.USER_PEER)
        self.assertEqual(stored["metadata"]["type"], "prompt")

        calls.clear()
        with mock.patch.object(common, "ensure_workspace"):
            with mock.patch.object(common, "ensure_session") as ensure_session:
                with mock.patch.object(common, "api", fake_api):
                    reply = mcp_server.tool_store(
                        {"text": "word " * 6000, "workspace": "local-honcho"}
                    )
        self.assertGreater(len(calls), 1)
        self.assertIn(common.AGENT_PEER, reply)
        ensure_session.assert_called_with("local-honcho", "grok-manual")


class MainTests(McpTestCase):
    def run_requests(self, requests: list[dict | str]) -> list[dict]:
        raw = []
        for item in requests:
            raw.append(item if isinstance(item, str) else json.dumps(item))
        stdin = io.StringIO("\n".join(raw) + "\n")
        stdout = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin):
            with mock.patch.object(sys, "stdout", stdout):
                mcp_server.main()
        messages = []
        for line in stdout.getvalue().splitlines():
            if line.strip():
                messages.append(json.loads(line))
        return messages

    def test_protocol_surface(self) -> None:
        def fake_api(path, payload=None, method="POST", timeout=60):
            if path.endswith("/search"):
                return [{"content": "hit", "peer_id": "dsidlo", "metadata": {}}]
            raise common.HonchoError("down", status=503)

        requests: list[dict | str] = [
            "",
            "not-json",
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "method": "notifications/progress"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "honcho_search", "arguments": {"query": "voice", "workspace": "ws"}},
            },
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": None},
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "honcho_search", "arguments": None},
            },
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "honcho_ask", "arguments": {"query": "where", "workspace": "default"}},
            },
            {"jsonrpc": "2.0", "id": 7, "method": "ping"},
            {"jsonrpc": "2.0", "id": 8, "method": "resources/list"},
            {"jsonrpc": "2.0", "id": 9, "method": "prompts/list"},
            {"jsonrpc": "2.0", "id": 10, "method": "unknown/method"},
            {"jsonrpc": "2.0", "method": "unknown/method"},
        ]
        with mock.patch.object(common, "ensure_workspace"):
            with mock.patch.object(common, "api", fake_api):
                messages = self.run_requests(requests)
        by_id = {message["id"]: message for message in messages if "id" in message}
        self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "grok-honcho")
        self.assertEqual(by_id[1]["result"]["protocolVersion"], mcp_server.PROTOCOL_VERSION)
        names = [tool["name"] for tool in by_id[2]["result"]["tools"]]
        self.assertEqual(
            names,
            ["honcho_search", "honcho_conclusions", "honcho_ask", "honcho_store"],
        )
        self.assertIn("hit", by_id[3]["result"]["content"][0]["text"])
        self.assertEqual(by_id[4]["error"]["code"], -32602)
        self.assertIn("empty query", by_id[5]["result"]["content"][0]["text"])
        self.assertTrue(by_id[6]["result"]["isError"])
        self.assertEqual(by_id[7]["result"], {})
        self.assertEqual(by_id[8]["result"], {"resources": []})
        self.assertEqual(by_id[9]["result"], {"prompts": []})
        self.assertEqual(by_id[10]["error"]["code"], -32601)
        self.assertNotIn(None, by_id)

    def test_send_flushes_stdout(self) -> None:
        stdout = io.StringIO()
        with mock.patch.object(sys, "stdout", stdout):
            mcp_server.send({"ok": True})
        self.assertEqual(json.loads(stdout.getvalue()), {"ok": True})

    def test_module_runs_as_main(self) -> None:
        with mock.patch.object(sys, "stdin", io.StringIO("")):
            with mock.patch.object(sys, "stdout", io.StringIO()):
                runpy.run_path(MCP_PATH, run_name="__main__")


if __name__ == "__main__":
    unittest.main()
