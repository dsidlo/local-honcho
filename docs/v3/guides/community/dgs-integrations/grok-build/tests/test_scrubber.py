"""Branch coverage for src/scrubber. No Grok process and no Honcho server."""

from __future__ import annotations

import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import scrubber  # noqa: E402
import scrubber.__main__ as scrubber_main  # noqa: E402
from scrubber.hook import (  # noqa: E402
    control_mode,
    event_name,
    handle,
    hook_config,
    main,
    session_id_from,
)
from scrubber.patterns import (  # noqa: E402
    REDACTED,
    contains_secret,
    scrub,
    scrub_tree,
)
from scrubber.state import (  # noqa: E402
    legacy_disabled_file,
    off_marker,
    redaction_enabled,
    reset_session,
    set_redaction,
    state_dir,
)

_SK = "sk-" + "a" * 20
_SK_PROJ = "sk-proj-" + "b" * 20
_SK_ANT = "sk-ant-" + "c" * 20
_GHP = "ghp_" + "d" * 20
_GHO = "gho_" + "e" * 20
_GHU = "ghu_" + "f" * 20
_GHS = "ghs_" + "g" * 20
_GHR = "ghr_" + "h" * 20
_GITHUB_PAT = "github_pat_" + "i" * 22
_AKIA = "AKIA" + "J" * 16
_SLACK = "xoxb-" + "k" * 10
_HF = "hf_" + "m" * 30
_XAI = "xai-" + "n" * 20
_GEMINI = "AIza" + "o" * 35
_BEARER = "Bearer " + "p" * 25
_JWT = "eyJ" + "q" * 10 + "." + "r" * 10 + "." + "s" * 10
_STATE = tempfile.TemporaryDirectory()
os.environ["SCRUBBER_STATE_DIR"] = _STATE.name
_SESSION = "sess-1"
_OTHER_SESSION = "sess-2"

_PEM_BODY = "***REMOVED***"
_PEM = "-----BEGIN PRIVATE KEY-----\n" + _PEM_BODY + "\n-----END PRIVATE KEY-----"


def _run(event: dict[str, object]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(io.StringIO(json.dumps(event)), stdout, stderr)
    return code, stdout.getvalue(), stderr.getvalue()


class PackageTests(unittest.TestCase):
    def test_public_names_match_the_functions(self) -> None:
        self.assertIs(scrubber.scrub, scrub)
        self.assertIs(scrubber.scrub_tree, scrub_tree)
        self.assertIs(scrubber.contains_secret, contains_secret)
        self.assertEqual(scrubber.REDACTED, REDACTED)
        self.assertEqual(
            scrubber.__all__,
            ["REDACTED", "contains_secret", "scrub", "scrub_tree"],
        )


class TokenPatternTests(unittest.TestCase):
    def test_every_token_shape_is_removed(self) -> None:
        samples = [
            _PEM,
            "-----BEGIN RSA PRIVATE KEY-----",
            "postgresql+psycopg://user:secret@db.example/honcho",
            "https://user:secret@example.com/v1",
            "appuser:s3cret@localhost:5432",
            "appuser:s3cret@127.0.0.1:5433",
            _SK_PROJ,
            _SK_ANT,
            _SK,
            _GITHUB_PAT,
            _GHP,
            _GHO,
            _GHU,
            _GHS,
            _GHR,
            _AKIA,
            _SLACK,
            _HF,
            _XAI,
            _GEMINI,
            _BEARER,
            "bearer " + "p" * 25,
            _JWT,
        ]
        redacted = scrub("\n".join(samples))
        for sample in samples:
            with self.subTest(sample=sample[:24]):
                self.assertNotIn(sample, redacted)
        self.assertNotIn(_PEM_BODY, redacted)
        self.assertIn(REDACTED, redacted)
        self.assertIn("db.example/honcho", redacted)

    def test_near_misses_stay(self) -> None:
        samples = [
            "sk-" + "a" * 19,
            "sk-proj-" + "b" * 19,
            "sk-ant-" + "c" * 19,
            "ghp_" + "d" * 19,
            "github_pat_" + "i" * 21,
            "AKIA" + "J" * 15,
            "xoxb-" + "k" * 9,
            "hf_" + "m" * 29,
            "xai-" + "n" * 19,
            "AIza" + "o" * 34,
            "Bearer " + "p" * 24,
            "eyJ" + "q" * 9 + "." + "r" * 10 + "." + "s" * 10,
            "https://example.com/path",
            "http://localhost:8300/health",
            "user@example.com",
            "user:s3cret@db.example:5432",
            "the password is hunter22",
            "-----BEGIN PUBLIC KEY-----",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(scrub(sample), sample)
                self.assertFalse(contains_secret(sample))

    def test_second_pass_is_a_no_op(self) -> None:
        once = scrub(f"prefix {_SK} suffix")
        self.assertEqual(scrub(once), once)
        self.assertFalse(contains_secret(once))


class AssignmentTests(unittest.TestCase):
    def test_value_is_replaced_and_the_name_stays(self) -> None:
        samples = {
            'password="hunter22"': 'password="[REDACTED]"',
            "password = 'hunter22'": "password = '[REDACTED]'",
            "api_key=hunter22": "api_key=[REDACTED]",
            "api-key: hunter22": "api-key: [REDACTED]",
            "OPENAI_API_KEY=hunter22": "OPENAI_API_KEY=[REDACTED]",
            "client_secret=hunter22": "client_secret=[REDACTED]",
            "PGPASSWORD=hunter22": "PGPASSWORD=[REDACTED]",
            "secret_key: hunter22": "secret_key: [REDACTED]",
            "access_key=hunter22": "access_key=[REDACTED]",
            "private-key=hunter22": "private-key=[REDACTED]",
            'token: "hunter 22"': 'token: "[REDACTED]"',
            "passwd=hunter22": "passwd=[REDACTED]",
        }
        for raw, expected in samples.items():
            with self.subTest(raw=raw):
                self.assertEqual(scrub(raw), expected)
                self.assertNotIn("hunter", scrub(raw))

    def test_short_safe_and_unrelated_values_stay(self) -> None:
        samples = [
            "password=short",
            "token=1234567",
            "secret=None",
            "secret=false",
            "secret=null",
            "password=changeme",
            "password=placeholder",
            "password=your-api-key",
            "password=YOUR_API_KEY",
            "API_KEY=OTHER_KEY",
            "api_key: str",
            "max_tokens=12345678",
            "api_key_env=LLM_VLLM_API_KEY",
            "token_count=12345678",
            "mode=hunter22",
            "password=[REDACTED]",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(scrub(sample), sample)

    def test_eight_character_value_is_redacted(self) -> None:
        self.assertEqual(scrub("token=12345678"), "token=[REDACTED]")

    def test_assigned_key_shape_is_removed_once(self) -> None:
        self.assertEqual(scrub(f"api_key={_SK}"), "api_key=[REDACTED]")

    def test_two_assignments_on_one_line(self) -> None:
        raw = "password=hunter22, token=hunter22"
        self.assertEqual(scrub(raw), "password=[REDACTED], token=[REDACTED]")


class TreeTests(unittest.TestCase):
    def test_string_and_clean_string(self) -> None:
        scrubbed, changed = scrub_tree(f"see {_SK}")
        self.assertTrue(changed)
        self.assertNotIn(_SK, scrubbed)
        same, changed = scrub_tree("hello")
        self.assertEqual(same, "hello")
        self.assertFalse(changed)

    def test_list_mixes_clean_and_dirty_items(self) -> None:
        scrubbed, changed = scrub_tree(["ok", 3, f"key {_SK}", None, True])
        self.assertTrue(changed)
        self.assertEqual(scrubbed[0], "ok")
        self.assertEqual(scrubbed[1], 3)
        self.assertNotIn(_SK, scrubbed[2])
        self.assertIsNone(scrubbed[3])
        self.assertIs(scrubbed[4], True)

    def test_clean_list_and_empty_list(self) -> None:
        scrubbed, changed = scrub_tree(["ok", "still ok"])
        self.assertEqual(scrubbed, ["ok", "still ok"])
        self.assertFalse(changed)
        scrubbed, changed = scrub_tree([])
        self.assertEqual(scrubbed, [])
        self.assertFalse(changed)

    def test_dict_scrubs_values_and_keys(self) -> None:
        scrubbed, changed = scrub_tree(
            {_SK: "plain", "note": f"pwd {_GEMINI}", 7: "number key"}
        )
        self.assertTrue(changed)
        self.assertNotIn(_SK, scrubbed)
        self.assertIn(REDACTED, scrubbed)
        self.assertNotIn(_GEMINI, scrubbed["note"])
        self.assertEqual(scrubbed[7], "number key")

    def test_clean_dict(self) -> None:
        scrubbed, changed = scrub_tree({"note": "plain", 1: "x"})
        self.assertEqual(scrubbed, {"note": "plain", 1: "x"})
        self.assertFalse(changed)

    def test_scalar_passes_through(self) -> None:
        for value in (None, 1, 1.5, True, False):
            with self.subTest(value=value):
                scrubbed, changed = scrub_tree(value)
                self.assertIs(scrubbed, value)
                self.assertFalse(changed)


class HookEventTests(unittest.TestCase):
    def test_event_name_folds_both_fields(self) -> None:
        self.assertEqual(event_name({"hook_event_name": "PostToolUse"}), "posttooluse")
        self.assertEqual(
            event_name({"hookEventName": "post_tool_use_failure"}),
            "posttoolusefailure",
        )
        self.assertEqual(event_name({}), "")

    def test_clean_tool_result_writes_nothing(self) -> None:
        code, out, err = _run({"hook_event_name": "PostToolUse", "toolResult": "hello"})
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertEqual(err, "")
        self.assertIsNone(
            handle({"hook_event_name": "PostToolUse", "toolResult": "hello"})
        )

    def test_string_tool_result_is_replaced(self) -> None:
        code, out, err = _run(
            {
                "hookEventName": "post_tool_use",
                "toolResult": f"file has {_SK}",
            }
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        payload = json.loads(out)
        text = payload["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertNotIn(_SK, text)
        self.assertNotIn(_SK, out)
        self.assertIn(REDACTED, text)

    def test_structured_tool_result_is_returned_as_text(self) -> None:
        code, out, _err = _run(
            {
                "hook_event_name": "PostToolUse",
                "toolResult": {
                    "type": "Bash",
                    "output_for_prompt": f"export {_SK}",
                    "exit_code": 0,
                },
            }
        )
        text = json.loads(out)["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIsInstance(text, str)
        self.assertNotIn(_SK, text)
        self.assertIn("Bash", text)
        self.assertEqual(code, 0)

    def test_list_tool_result(self) -> None:
        response = handle({"hook_event_name": "PostToolUse", "toolResult": ["ok", _SK]})
        self.assertIsNotNone(response)
        text = response["hookSpecificOutput"]["updatedToolOutput"]
        self.assertNotIn(_SK, text)

    def test_tool_result_wins_over_the_alias(self) -> None:
        response = handle(
            {
                "hook_event_name": "PostToolUse",
                "toolResult": "clean",
                "tool_response": _SK,
            }
        )
        self.assertIsNone(response)

    def test_tool_response_alias_is_used_when_it_is_the_only_result(self) -> None:
        response = handle({"hook_event_name": "PostToolUse", "tool_response": _SK})
        self.assertNotIn(_SK, response["hookSpecificOutput"]["updatedToolOutput"])

    def test_tool_result_snake_alias(self) -> None:
        response = handle({"hook_event_name": "PostToolUse", "tool_result": _GEMINI})
        self.assertNotIn(_GEMINI, response["hookSpecificOutput"]["updatedToolOutput"])

    def test_missing_result_is_left_alone(self) -> None:
        self.assertIsNone(handle({"hook_event_name": "PostToolUse"}))

    def test_none_result_is_left_alone(self) -> None:
        self.assertIsNone(
            handle({"hook_event_name": "PostToolUse", "toolResult": None})
        )

    def test_unknown_event_is_ignored(self) -> None:
        self.assertIsNone(
            handle({"hook_event_name": "Notification", "toolResult": _SK})
        )


class FailureHookTests(unittest.TestCase):
    def test_dirty_error_is_replaced(self) -> None:
        response = handle(
            {"hook_event_name": "PostToolUseFailure", "error": f"boom {_SK}"}
        )
        output = response["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PostToolUseFailure")
        self.assertNotIn(_SK, output["updatedToolOutput"])

    def test_clean_error_is_left_alone(self) -> None:
        self.assertIsNone(
            handle({"hook_event_name": "PostToolUseFailure", "error": "boom"})
        )

    def test_dirty_error_drops_a_clean_result(self) -> None:
        response = handle(
            {
                "hook_event_name": "PostToolUseFailure",
                "error": f"boom {_SK}",
                "toolResult": "plain detail",
            }
        )
        text = response["hookSpecificOutput"]["updatedToolOutput"]
        self.assertNotIn(_SK, text)
        self.assertNotIn("plain detail", text)

    def test_clean_error_plus_dirty_result(self) -> None:
        response = handle(
            {
                "hookEventName": "post_tool_use_failure",
                "error": "boom",
                "toolResult": {"output": _JWT},
            }
        )
        text = response["hookSpecificOutput"]["updatedToolOutput"]
        self.assertTrue(text.startswith("boom\n"))
        self.assertNotIn(_JWT, text)

    def test_non_string_error_uses_the_result(self) -> None:
        response = handle(
            {
                "hook_event_name": "PostToolUseFailure",
                "error": 500,
                "tool_result": f"failed {_HF}",
            }
        )
        text = response["hookSpecificOutput"]["updatedToolOutput"]
        self.assertNotIn(_HF, text)
        self.assertNotIn("500", text)

    def test_clean_result_with_no_error_string_is_left_alone(self) -> None:
        self.assertIsNone(
            handle(
                {
                    "hook_event_name": "PostToolUseFailure",
                    "error": None,
                    "toolResult": "plain",
                }
            )
        )

    def test_empty_failure_is_left_alone(self) -> None:
        self.assertIsNone(handle({"hook_event_name": "PostToolUseFailure"}))


class PromptHookTests(unittest.TestCase):
    def test_secret_prompt_is_blocked_without_echoing_it(self) -> None:
        code, out, err = _run(
            {"hook_event_name": "UserPromptSubmit", "prompt": f"key is {_SK}"}
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        payload = json.loads(out)
        self.assertEqual(payload["decision"], "block")
        self.assertNotIn(_SK, out)
        self.assertIn("was not sent", payload["reason"])

    def test_clean_prompt_is_allowed(self) -> None:
        self.assertIsNone(
            handle(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "Where did we leave off?",
                }
            )
        )

    def test_user_prompt_alias_is_blocked(self) -> None:
        response = handle(
            {"hook_event_name": "UserPromptSubmit", "userPrompt": _GEMINI}
        )
        self.assertEqual(response["decision"], "block")
        self.assertNotIn(_GEMINI, json.dumps(response))

    def test_non_string_prompt_falls_through_to_the_alias(self) -> None:
        response = handle(
            {
                "hook_event_name": "UserPromptSubmit",
                "prompt": 12,
                "userPrompt": "password=hunter22",
            }
        )
        self.assertEqual(response["decision"], "block")

    def test_missing_prompt_is_allowed(self) -> None:
        self.assertIsNone(handle({"hook_event_name": "UserPromptSubmit"}))
        self.assertIsNone(
            handle(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": None,
                    "userPrompt": None,
                }
            )
        )

    def test_secret_in_another_field_does_not_block(self) -> None:
        self.assertIsNone(
            handle(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "hello",
                    "cwd": _SK,
                }
            )
        )


class MainTests(unittest.TestCase):
    def test_empty_and_whitespace_input(self) -> None:
        for raw in ("", "   \n"):
            with self.subTest(raw=raw):
                stdout = io.StringIO()
                stderr = io.StringIO()
                code = main(io.StringIO(raw), stdout, stderr)
                self.assertEqual(code, 0)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")

    def test_malformed_input_does_not_echo_the_body(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = main(io.StringIO("not-json " + _SK), stdout, stderr)
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn(_SK, stderr.getvalue())
        self.assertIn("malformed", stderr.getvalue())

    def test_non_object_json(self) -> None:
        for raw in ("[]", '"hi"', "1", "null"):
            with self.subTest(raw=raw):
                stdout = io.StringIO()
                stderr = io.StringIO()
                code = main(io.StringIO(raw), stdout, stderr)
                self.assertEqual(code, 0)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("not an object", stderr.getvalue())

    def test_read_failure_is_fail_open(self) -> None:
        class Boom:
            def read(self) -> str:
                raise OSError("unavailable")

        stdout = io.StringIO()
        stderr = io.StringIO()
        code = main(Boom(), stdout, stderr)  # type: ignore[arg-type]
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("failed to read", stderr.getvalue())

    def test_handler_failure_is_fail_open_and_does_not_echo(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("scrubber.hook.handle", side_effect=RuntimeError(_SK)):
            code = main(
                io.StringIO(json.dumps({"hook_event_name": "PostToolUse"})),
                stdout,
                stderr,
            )
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn(_SK, stderr.getvalue())
        self.assertIn("failed to scrub", stderr.getvalue())

    def test_default_streams_are_stdin_and_stdout(self) -> None:
        with (
            mock.patch("sys.stdin", io.StringIO("")),
            mock.patch("sys.stdout", io.StringIO()) as stdout,
            mock.patch("sys.stderr", io.StringIO()) as stderr,
        ):
            code = main()
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")


class HookConfigTests(unittest.TestCase):
    def test_config_runs_the_package_on_the_scrubber_events(self) -> None:
        src = "/tmp/scrubber-src"
        document = hook_config(src, python="/usr/bin/python3")
        command = f"PYTHONPATH={src} /usr/bin/python3 -m scrubber"
        self.assertEqual(
            set(document["hooks"]),
            {
                "SessionStart",
                "PostToolUse",
                "PostToolUseFailure",
                "UserPromptSubmit",
            },
        )
        for name, groups in document["hooks"].items():
            with self.subTest(name=name):
                handler = groups[0]["hooks"][0]
                self.assertEqual(handler["type"], "command")
                self.assertEqual(handler["command"], command)
                self.assertEqual(handler["timeout"], 5)


class ModuleEntryTests(unittest.TestCase):
    def test_entry_uses_main(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO("")),
            mock.patch("sys.stdout", stdout),
            mock.patch("sys.stderr", stderr),
        ):
            self.assertEqual(scrubber_main.entry(), 0)
        self.assertEqual(stdout.getvalue(), "")

    def test_module_guard_exits_zero(self) -> None:
        with (
            mock.patch("sys.stdin", io.StringIO("")),
            mock.patch("sys.stdout", io.StringIO()),
            mock.patch("sys.stderr", io.StringIO()),
            self.assertRaises(SystemExit) as caught,
        ):
            runpy.run_module("scrubber", run_name="__main__")
        self.assertEqual(caught.exception.code, 0)


class RedactionSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["SCRUBBER_STATE_DIR"] = _STATE.name
        self.assertTrue(reset_session(_SESSION))
        self.assertTrue(reset_session(_OTHER_SESSION))

    def test_missing_switch_leaves_redaction_on(self) -> None:
        self.assertTrue(redaction_enabled(_SESSION))
        self.assertFalse(off_marker(_SESSION).exists())

    def test_switch_file_turns_redaction_off_and_on(self) -> None:
        self.assertTrue(set_redaction(False, _SESSION))
        self.assertFalse(redaction_enabled(_SESSION))
        self.assertEqual(off_marker(_SESSION).read_text(encoding="utf-8"), "off\n")
        self.assertTrue(set_redaction(True, _SESSION))
        self.assertTrue(redaction_enabled(_SESSION))

    def test_invalid_session_id_cannot_turn_redaction_off(self) -> None:
        self.assertFalse(set_redaction(False, None))
        self.assertFalse(set_redaction(False, "bad id"))
        self.assertFalse(set_redaction(False, "../etc"))
        self.assertTrue(redaction_enabled(None))
        self.assertTrue(redaction_enabled("bad id"))
        self.assertFalse(off_marker(_SESSION).exists())
        self.assertFalse((state_dir() / "etc").exists())

    def test_default_directory_is_under_grok(self) -> None:
        previous = os.environ.pop("SCRUBBER_STATE_DIR")
        try:
            self.assertEqual(state_dir(), Path.home() / ".grok" / "scrubber")
        finally:
            os.environ["SCRUBBER_STATE_DIR"] = previous

    def test_unreadable_switch_leaves_redaction_on(self) -> None:
        with mock.patch("scrubber.state.Path.is_file", side_effect=OSError("denied")):
            self.assertTrue(redaction_enabled(_SESSION))

    def test_unlink_error_reports_failure(self) -> None:
        directory = off_marker(_SESSION)
        directory.mkdir(parents=True)
        try:
            self.assertFalse(set_redaction(True, _SESSION))
        finally:
            directory.rmdir()

    def test_write_error_reports_failure(self) -> None:
        handle_no, blocked = tempfile.mkstemp()
        os.close(handle_no)
        os.environ["SCRUBBER_STATE_DIR"] = blocked
        try:
            self.assertFalse(set_redaction(False, _SESSION))
        finally:
            os.unlink(blocked)

    def test_slash_commands_and_qualified_names(self) -> None:
        self.assertEqual(control_mode("/redact-off"), "off")
        self.assertEqual(control_mode("  /USER:redact-on  "), "on")
        self.assertEqual(control_mode("/redact"), "status")
        self.assertEqual(control_mode("  /user:redact  "), "status")
        self.assertEqual(control_mode("/USER:redact"), "status")
        self.assertIsNone(control_mode("/redact-status"))
        self.assertIsNone(control_mode("please run /redact-off later"))

    def test_redact_reports_status_without_changing_the_switch(self) -> None:
        response = handle(
            {
                "hook_event_name": "UserPromptSubmit",
                "sessionId": _SESSION,
                "prompt": "/redact",
            }
        )
        self.assertEqual(response["decision"], "block")
        self.assertIn("Secret redaction is on", response["reason"])
        self.assertFalse(off_marker(_SESSION).exists())

        set_redaction(False, _SESSION)
        before = off_marker(_SESSION).read_text(encoding="utf-8")
        response = handle(
            {
                "hook_event_name": "UserPromptSubmit",
                "sessionId": _SESSION,
                "prompt": "/user:redact",
            }
        )
        self.assertEqual(response["decision"], "block")
        self.assertIn("Secret redaction is off", response["reason"])
        self.assertIn("/redact-on", response["reason"])
        self.assertIn("next session", response["reason"])
        self.assertEqual(off_marker(_SESSION).read_text(encoding="utf-8"), before)
        self.assertFalse(redaction_enabled(_SESSION))

    def test_status_marker_does_not_call_the_switch(self) -> None:
        command = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "scrubber"
            / "commands"
            / "redact.md"
        )
        with mock.patch("scrubber.hook.set_redaction") as setter:
            response = handle(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": command.read_text(encoding="utf-8"),
                }
            )
        setter.assert_not_called()
        self.assertEqual(response["decision"], "block")
        self.assertIn("Secret redaction is on", response["reason"])
        self.assertFalse(off_marker(_SESSION).exists())

        set_redaction(False, _SESSION)
        with mock.patch("scrubber.hook.set_redaction") as setter:
            response = handle(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "sessionId": f"  {_SESSION}  ",
                    "prompt": "scrubber-control: status",
                }
            )
        setter.assert_not_called()
        self.assertIn("Secret redaction is off", response["reason"])
        self.assertFalse(redaction_enabled(_SESSION))

    def test_command_file_turns_redaction_off(self) -> None:
        command = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "scrubber"
            / "commands"
            / "redact-off.md"
        )
        response = handle(
            {
                "hook_event_name": "UserPromptSubmit",
                "sessionId": _SESSION,
                "prompt": command.read_text(encoding="utf-8"),
            }
        )
        self.assertEqual(response["decision"], "block")
        self.assertIn("Secret redaction is off", response["reason"])
        self.assertIn("/redact-on", response["reason"])
        self.assertFalse(redaction_enabled(_SESSION))

    def test_redact_on_blocks_with_the_on_message(self) -> None:
        set_redaction(False, _SESSION)
        code, out, err = _run(
            {
                "hook_event_name": "UserPromptSubmit",
                "sessionId": _SESSION,
                "prompt": "/redact-on",
            }
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        payload = json.loads(out)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("Secret redaction is on", payload["reason"])
        self.assertTrue(redaction_enabled(_SESSION))

    def test_last_marker_wins(self) -> None:
        prompt = "scrubber-control: off\nscrubber-control: on\n"
        response = handle(
            {
                "hook_event_name": "UserPromptSubmit",
                "sessionId": _SESSION,
                "prompt": prompt,
            }
        )
        self.assertIn("Secret redaction is on", response["reason"])
        self.assertTrue(redaction_enabled(_SESSION))

    def test_long_prompt_is_not_a_command(self) -> None:
        prompt = ("note " * 500) + "\nscrubber-control: off"
        self.assertGreater(len(prompt.strip()), 2000)
        self.assertIsNone(control_mode(prompt))
        self.assertIsNone(
            handle({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
        )
        self.assertTrue(redaction_enabled(_SESSION))

    def test_failed_write_blocks_without_changing_state(self) -> None:
        with mock.patch("scrubber.hook.set_redaction", return_value=False):
            response = handle(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "sessionId": _SESSION,
                    "prompt": "/redact-off",
                }
            )
        self.assertIn("was not changed", response["reason"])
        self.assertTrue(redaction_enabled(_SESSION))

    def test_off_without_a_session_id_does_not_change_state(self) -> None:
        response = handle(
            {"hook_event_name": "UserPromptSubmit", "prompt": "/redact-off"}
        )
        self.assertIn("was not changed", response["reason"])
        self.assertFalse(off_marker(_SESSION).exists())
        self.assertTrue(redaction_enabled(_SESSION))

    def test_disabled_switch_lets_secrets_through(self) -> None:
        set_redaction(False, _SESSION)
        self.assertIsNone(
            handle(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "sessionId": _SESSION,
                    "prompt": "key is " + _SK,
                }
            )
        )
        self.assertIsNone(
            handle(
                {
                    "hook_event_name": "PostToolUse",
                    "sessionId": _SESSION,
                    "toolResult": _SK,
                }
            )
        )
        self.assertIsNone(
            handle(
                {
                    "hook_event_name": "PostToolUseFailure",
                    "sessionId": _SESSION,
                    "error": "boom " + _SK,
                }
            )
        )

    def test_another_session_stays_on(self) -> None:
        set_redaction(False, _SESSION)
        response = handle(
            {
                "hook_event_name": "PostToolUse",
                "sessionId": _OTHER_SESSION,
                "toolResult": _SK,
            }
        )
        self.assertNotIn(_SK, response["hookSpecificOutput"]["updatedToolOutput"])
        self.assertFalse(redaction_enabled(_SESSION))
        self.assertTrue(redaction_enabled(_OTHER_SESSION))

    def test_session_id_fields(self) -> None:
        self.assertEqual(session_id_from({"sessionId": _SESSION}), _SESSION)
        self.assertEqual(session_id_from({"session_id": _SESSION}), _SESSION)
        self.assertEqual(
            session_id_from({"sessionId": _OTHER_SESSION, "session_id": _SESSION}),
            _OTHER_SESSION,
        )
        self.assertIsNone(session_id_from({"sessionId": 12, "session_id": _SESSION}))
        self.assertIsNone(session_id_from({"sessionId": "bad/id"}))
        self.assertIsNone(session_id_from({}))

    def test_session_start_turns_redaction_on_for_that_session(self) -> None:
        set_redaction(False, _SESSION)
        set_redaction(False, _OTHER_SESSION)
        legacy_disabled_file().write_text("off\n", encoding="utf-8")
        code, out, err = _run(
            {
                "hook_event_name": "SessionStart",
                "sessionId": _SESSION,
                "toolResult": _SK,
            }
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertEqual(err, "")
        self.assertNotIn(_SK, out)
        self.assertTrue(redaction_enabled(_SESSION))
        self.assertFalse(off_marker(_SESSION).exists())
        self.assertFalse(legacy_disabled_file().exists())
        self.assertFalse(redaction_enabled(_OTHER_SESSION))

    def test_session_start_uses_the_snake_case_event_name(self) -> None:
        set_redaction(False, _SESSION)
        self.assertIsNone(
            handle({"hookEventName": "session_start", "session_id": _SESSION})
        )
        self.assertTrue(redaction_enabled(_SESSION))

    def test_session_start_removes_a_legacy_switch_without_a_session_id(self) -> None:
        legacy_disabled_file().write_text("off\n", encoding="utf-8")
        self.assertTrue(reset_session(None))
        self.assertFalse(legacy_disabled_file().exists())
        self.assertFalse(off_marker(_SESSION).exists())

    def test_session_start_reports_a_marker_that_cannot_be_removed(self) -> None:
        marker = off_marker(_SESSION)
        marker.mkdir(parents=True)
        legacy_disabled_file().write_text("off\n", encoding="utf-8")
        try:
            self.assertFalse(reset_session(_SESSION))
            self.assertTrue(marker.is_dir())
            self.assertFalse(legacy_disabled_file().exists())
        finally:
            marker.rmdir()

    def test_session_start_reports_a_legacy_switch_that_cannot_be_removed(self) -> None:
        legacy = legacy_disabled_file()
        legacy.mkdir()
        try:
            self.assertFalse(reset_session(None))
            self.assertTrue(legacy.is_dir())
        finally:
            legacy.rmdir()


if __name__ == "__main__":
    unittest.main()
