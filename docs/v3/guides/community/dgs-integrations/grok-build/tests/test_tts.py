"""Branch coverage for src/tts.

No Grok process is started. os.kill and process creation are mocked, and
every TTS path is pointed at a temporary directory. The live Kokoro daemon
is not signaled.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import queue
import runpy
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import warnings
import wave
from pathlib import Path
from unittest import mock

import tts
import tts.__main__ as tts_main
import tts.command as command
import tts.daemon as daemon
import tts.paths as paths
import tts.piper as piper
import tts.speak as speak
import tts.toggle as toggle
import tts.volume as volume

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "tts"
ENV_KEYS = (
    "GROK_TTS_DIR",
    "GROK_TTS_CONFIG",
    "GROK_TTS_LOG",
    "GROK_TTS_STATE_DIR",
    "GROK_TTS_PID_FILE",
    "GROK_TTS_SOCKET_FILE",
    "GROK_TTS_DEVICE_FILE",
    "GROK_TTS_DAEMON_LOG",
    "GROK_TTS_DAEMON",
    "GROK_TTS_DAEMON_PYTHON",
    "GROK_TTS_SPEAK_SH",
    "GROK_TTS_PIPER_PYTHON",
    "GROK_TTS_PIPER_SPEAK",
    "GROK_TTS_PIPER_ROOT",
    "GROK_TTS_PIPER_VOICES",
    "GROK_TTS_PIPER_LOCK",
    "GROK_TTS_TOGGLE",
    "GROK_TTS_DRY_RUN",
    "GROK_TTS_WAV_DIR",
    "KOKORO_DEVICE",
)
LIVE_FILES = (
    Path("/tmp/pi-tts-server.pid"),
    Path("/tmp/pi-tts-server.sock"),
    Path("/tmp/pi-tts-server.device"),
    Path("/tmp/kokoro-daemon.log"),
    Path.home() / ".grok" / "tts" / "config.json",
    Path.home() / ".grok" / "tts" / "speak.log",
)
_LIVE_STAT: dict[Path, tuple[int, int, int] | None] = {}


def setUpModule() -> None:
    for path in LIVE_FILES:
        if path.exists():
            st = path.stat()
            _LIVE_STAT[path] = (st.st_mtime_ns, st.st_size, st.st_ino)
        else:
            _LIVE_STAT[path] = None


def tearDownModule() -> None:
    for path, before in _LIVE_STAT.items():
        if before is None:
            if path.exists():
                raise AssertionError(f"{path} was created by the tests")
            continue
        st = path.stat()
        now = (st.st_mtime_ns, st.st_size, st.st_ino)
        if now != before:
            raise AssertionError(f"{path} was changed by the tests")


class Clock:
    """A monotonic clock that jumps forward when something sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class DefaultPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = {key: os.environ.pop(key, None) for key in ENV_KEYS}

    def tearDown(self) -> None:
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_defaults_point_at_the_live_grok_and_pi_paths(self) -> None:
        home = Path.home()
        pi = home / ".pi" / "agent" / "skills" / "voice-response" / "scripts"
        self.assertEqual(paths.tts_dir(), home / ".grok" / "tts")
        self.assertEqual(paths.config_path(), home / ".grok" / "tts" / "config.json")
        self.assertEqual(paths.log_path(), home / ".grok" / "tts" / "speak.log")
        self.assertEqual(paths.state_dir(), home / ".grok" / "tts" / "state")
        self.assertEqual(paths.pid_file(), Path("/tmp/pi-tts-server.pid"))
        self.assertEqual(paths.socket_file(), Path("/tmp/pi-tts-server.sock"))
        self.assertEqual(paths.device_file(), Path("/tmp/pi-tts-server.device"))
        self.assertEqual(paths.wav_dir(), Path("/tmp"))
        self.assertEqual(paths.daemon_log(), Path("/tmp/kokoro-daemon.log"))
        self.assertEqual(paths.daemon_script(), pi / "tts-daemon.py")
        self.assertEqual(paths.speak_sh(), pi / "speak.sh")
        self.assertEqual(paths.daemon_python(), home / ".pi" / ".venv" / "bin" / "python3")
        self.assertEqual(
            paths.piper_python(),
            home / ".grok" / "tts" / "piper" / ".venv" / "bin" / "python3",
        )
        self.assertEqual(paths.piper_script(), PACKAGE / "piper.py")
        self.assertTrue(paths.piper_script().is_file())
        self.assertEqual(paths.piper_root(), home / ".grok" / "tts" / "piper")
        self.assertEqual(paths.piper_voices(), home / ".grok" / "tts" / "piper" / "voices")
        self.assertEqual(paths.piper_lock(), home / ".grok" / "tts" / "piper" / "speak.lock")
        self.assertEqual(paths.toggle_script(), home / ".grok" / "tts" / "toggle")
        self.assertEqual(piper.piper_root(), home / ".grok" / "tts" / "piper")
        self.assertEqual(piper.speak_log(), home / ".grok" / "tts" / "speak.log")
        self.assertEqual(piper.voices_dir(), home / ".grok" / "tts" / "piper" / "voices")
        self.assertEqual(piper.lock_path(), home / ".grok" / "tts" / "piper" / "speak.lock")

    def test_each_override_replaces_one_path(self) -> None:
        cases = {
            "GROK_TTS_DIR": (paths.tts_dir, Path("/override/dir")),
            "GROK_TTS_CONFIG": (paths.config_path, Path("/override/config.json")),
            "GROK_TTS_LOG": (paths.log_path, Path("/override/speak.log")),
            "GROK_TTS_STATE_DIR": (paths.state_dir, Path("/override/state")),
            "GROK_TTS_PID_FILE": (paths.pid_file, Path("/override/pid")),
            "GROK_TTS_SOCKET_FILE": (paths.socket_file, Path("/override/sock")),
            "GROK_TTS_DEVICE_FILE": (paths.device_file, Path("/override/device")),
            "GROK_TTS_WAV_DIR": (paths.wav_dir, Path("/override/wavs")),
            "GROK_TTS_DAEMON_LOG": (paths.daemon_log, Path("/override/daemon.log")),
            "GROK_TTS_DAEMON": (paths.daemon_script, Path("/override/daemon.py")),
            "GROK_TTS_DAEMON_PYTHON": (paths.daemon_python, Path("/override/python")),
            "GROK_TTS_SPEAK_SH": (paths.speak_sh, Path("/override/speak.sh")),
            "GROK_TTS_PIPER_PYTHON": (paths.piper_python, Path("/override/piper-python")),
            "GROK_TTS_PIPER_SPEAK": (paths.piper_script, Path("/override/piper.py")),
            "GROK_TTS_PIPER_ROOT": (paths.piper_root, Path("/override/piper")),
            "GROK_TTS_PIPER_VOICES": (paths.piper_voices, Path("/override/voices")),
            "GROK_TTS_PIPER_LOCK": (paths.piper_lock, Path("/override/speak.lock")),
            "GROK_TTS_TOGGLE": (paths.toggle_script, Path("/override/toggle")),
        }
        for key, (func, expected) in cases.items():
            with (
                self.subTest(key=key),
                mock.patch.dict(os.environ, {key: str(expected)}),
            ):
                self.assertEqual(func(), expected)

    def test_blank_override_is_ignored(self) -> None:
        with mock.patch.dict(os.environ, {"GROK_TTS_CONFIG": "   "}):
            self.assertEqual(
                paths.config_path(), Path.home() / ".grok" / "tts" / "config.json"
            )
        with mock.patch.dict(os.environ, {"GROK_TTS_PIPER_ROOT": "   "}):
            self.assertEqual(piper.piper_root(), Path.home() / ".grok" / "tts" / "piper")
        with mock.patch.dict(os.environ, {"GROK_TTS_WAV_DIR": "   "}):
            self.assertEqual(paths.wav_dir(), Path("/tmp"))

    def test_directory_override_fills_the_paths_under_it(self) -> None:
        with mock.patch.dict(os.environ, {"GROK_TTS_DIR": "/override/dir"}):
            self.assertEqual(paths.config_path(), Path("/override/dir/config.json"))
            self.assertEqual(paths.log_path(), Path("/override/dir/speak.log"))
            self.assertEqual(paths.state_dir(), Path("/override/dir/state"))
            self.assertEqual(
                paths.piper_python(),
                Path("/override/dir/piper/.venv/bin/python3"),
            )
            self.assertEqual(paths.piper_root(), Path("/override/dir/piper"))
            self.assertEqual(paths.piper_voices(), Path("/override/dir/piper/voices"))
            self.assertEqual(paths.piper_lock(), Path("/override/dir/piper/speak.lock"))
            self.assertEqual(paths.toggle_script(), Path("/override/dir/toggle"))
            self.assertEqual(piper.piper_root(), Path("/override/dir/piper"))
            self.assertEqual(piper.speak_log(), Path("/override/dir/speak.log"))
            self.assertEqual(piper.voices_dir(), Path("/override/dir/piper/voices"))
            self.assertEqual(piper.lock_path(), Path("/override/dir/piper/speak.lock"))

    def test_piper_root_and_log_overrides_win_over_the_directory(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "GROK_TTS_DIR": "/override/dir",
                "GROK_TTS_PIPER_ROOT": "/voices-root",
                "GROK_TTS_LOG": "/other.log",
                "GROK_TTS_PIPER_VOICES": "/voices",
                "GROK_TTS_PIPER_LOCK": "/lock",
            },
        ):
            self.assertEqual(piper.piper_root(), Path("/voices-root"))
            self.assertEqual(piper.speak_log(), Path("/other.log"))
            self.assertEqual(piper.voices_dir(), Path("/voices"))
            self.assertEqual(piper.lock_path(), Path("/lock"))


class TemplateTests(unittest.TestCase):
    def test_config_template_selects_kokoro_on_the_gpu(self) -> None:
        data = json.loads((PACKAGE / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(data["engine"], "kokoro")
        self.assertEqual(data["voice"], "bf_emma")
        self.assertEqual(data["speed"], "1.25")
        self.assertTrue(data["enabled"])
        self.assertEqual(data["kokoro"]["device"], "gpu")
        self.assertEqual(data["piper"]["voice"], "en_GB-jenny_dioco-medium")
        self.assertEqual(data["piper"]["lengthScale"], 1.0)
        self.assertEqual(data["piper"]["volume"], 8)
        self.assertEqual(data["kokoro"]["volume"], 10)

    def test_command_files_carry_the_control_markers(self) -> None:
        markers = {
            "tts.md": "tts-control: status",
            "tts-kokoro.md": "tts-control: engine kokoro",
            "tts-piper.md": "tts-control: engine piper",
            "tts-cpu.md": "tts-control: device cpu",
            "tts-gpu.md": "tts-control: device gpu",
            "tts-volume.md": "tts-control: volume $ARGUMENTS",
        }
        for name, marker in markers.items():
            text = (PACKAGE / "commands" / name).read_text(encoding="utf-8")
            self.assertIn("disable-model-invocation: true", text)
            self.assertIn("user-invocable: true", text)
            self.assertIn(marker, text)

    def test_package_docstring_is_present(self) -> None:
        self.assertIn("Kokoro", tts.__doc__ or "")


def _sample_config() -> dict:
    return {
        "enabled": True,
        "maxTextLength": 500,
        "engine": "kokoro",
        "voice": "bf_emma",
        "speed": "1.25",
        "piper": {"voice": "en_GB-jenny_dioco-medium", "lengthScale": 1.0},
        "kokoro": {"device": "gpu"},
    }


class TempTTS(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.saved = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(
            {
                "GROK_TTS_DIR": str(self.root / "tts"),
                "GROK_TTS_CONFIG": str(self.root / "tts" / "config.json"),
                "GROK_TTS_LOG": str(self.root / "tts" / "speak.log"),
                "GROK_TTS_STATE_DIR": str(self.root / "tts" / "state"),
                "GROK_TTS_PID_FILE": str(self.root / "pid"),
                "GROK_TTS_SOCKET_FILE": str(self.root / "sock"),
                "GROK_TTS_DEVICE_FILE": str(self.root / "device"),
                "GROK_TTS_WAV_DIR": str(self.root / "wavs"),
                "GROK_TTS_DAEMON_LOG": str(self.root / "daemon.log"),
                "GROK_TTS_DAEMON": str(self.root / "tts-daemon.py"),
                "GROK_TTS_DAEMON_PYTHON": str(self.root / "python3"),
                "GROK_TTS_SPEAK_SH": str(self.root / "speak.sh"),
                "GROK_TTS_PIPER_PYTHON": str(self.root / "piper-python"),
                "GROK_TTS_PIPER_SPEAK": str(self.root / "piper-speak.py"),
                "GROK_TTS_PIPER_ROOT": str(self.root / "piper"),
                "GROK_TTS_PIPER_VOICES": str(self.root / "piper" / "voices"),
                "GROK_TTS_PIPER_LOCK": str(self.root / "piper" / "speak.lock"),
                "GROK_TTS_TOGGLE": str(self.root / "toggle"),
            }
        )
        (self.root / "tts").mkdir()
        (self.root / "piper" / "voices").mkdir(parents=True)
        (self.root / "wavs").mkdir()
        for func in (
            paths.tts_dir,
            paths.config_path,
            paths.log_path,
            paths.state_dir,
            paths.pid_file,
            paths.socket_file,
            paths.device_file,
            paths.wav_dir,
            paths.daemon_log,
            paths.daemon_script,
            paths.daemon_python,
            paths.speak_sh,
            paths.piper_python,
            paths.piper_script,
            paths.piper_root,
            paths.piper_voices,
            paths.piper_lock,
            paths.toggle_script,
        ):
            self.assertTrue(str(func()).startswith(str(self.root)), func.__name__)
        self.write_config(_sample_config())

    def tearDown(self) -> None:
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def write_config(self, data: dict) -> None:
        path = paths.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def write_raw_config(self, text: str) -> None:
        path = paths.config_path()
        if path.is_dir():
            path.rmdir()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def touch(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")

    def bind_socket(self, path: Path) -> socket.socket:
        if path.exists():
            path.unlink()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(path))
        self.addCleanup(sock.close)
        return sock


class SpeakTests(TempTTS):
    def setUp(self) -> None:
        super().setUp()
        popen_patch = mock.patch("tts.speak.subprocess.Popen")
        self.popen = popen_patch.start()
        self.addCleanup(popen_patch.stop)
        kill_patch = mock.patch("tts.speak.os.kill", return_value=None)
        self.kill = kill_patch.start()
        self.addCleanup(kill_patch.stop)

    def tearDown(self) -> None:
        for call in self.kill.call_args_list:
            self.assertLess(call.args[0], 1000)
        super().tearDown()

    def test_load_config_repairs_bad_values(self) -> None:
        path = paths.config_path()
        path.unlink()
        missing = speak.load_config()
        self.assertEqual(missing["engine"], "kokoro")
        self.assertEqual(missing["piper"]["voice"], speak.DEFAULT_PIPER_VOICE)
        self.assertEqual(missing["piper"]["volume"], 8)
        self.write_raw_config("{")
        self.assertEqual(speak.load_config()["maxTextLength"], 500)
        self.write_raw_config("[]")
        self.assertTrue(speak.load_config()["enabled"])
        path.unlink()
        path.mkdir()
        self.assertEqual(speak.load_config()["engine"], "kokoro")
        path.rmdir()
        self.write_config(
            {
                "enabled": 0,
                "maxTextLength": "nope",
                "voice": None,
                "speed": None,
                "engine": "  NOPE  ",
                "piper": ["not-a-dict"],
            }
        )
        repaired = speak.load_config()
        self.assertFalse(repaired["enabled"])
        self.assertEqual(repaired["maxTextLength"], 500)
        self.assertEqual(repaired["voice"], "")
        self.assertEqual(repaired["engine"], "kokoro")
        self.assertEqual(repaired["piper"]["voice"], speak.DEFAULT_PIPER_VOICE)
        self.write_config(
            {
                "enabled": 1,
                "maxTextLength": None,
                "voice": "bf_emma",
                "speed": "1.25",
                "engine": "  Piper ",
                "piper": {"voice": "   ", "lengthScale": None},
            }
        )
        scaled = speak.load_config()
        self.assertTrue(scaled["enabled"])
        self.assertEqual(scaled["maxTextLength"], 500)
        self.assertEqual(scaled["engine"], "piper")
        self.assertEqual(scaled["piper"]["voice"], speak.DEFAULT_PIPER_VOICE)
        self.assertEqual(scaled["piper"]["lengthScale"], 1.0)
        self.write_config(
            {
                "maxTextLength": 10,
                "engine": "kokoro",
                "piper": {"voice": "alice", "lengthScale": "fast"},
            }
        )
        short = speak.load_config()
        self.assertEqual(short["maxTextLength"], 40)
        self.assertEqual(short["piper"]["voice"], "alice")
        self.assertEqual(short["piper"]["lengthScale"], 1.0)
        self.write_config({"piper": {"lengthScale": 0}})
        self.assertEqual(speak.load_config()["piper"]["lengthScale"], 1.0)
        self.write_config({"piper": {"lengthScale": -1}})
        self.assertEqual(speak.load_config()["piper"]["lengthScale"], 1.0)
        self.write_config({"piper": {"lengthScale": 0.5}, "maxTextLength": 80})
        kept = speak.load_config()
        self.assertEqual(kept["piper"]["lengthScale"], 0.5)
        self.assertEqual(kept["maxTextLength"], 80)
        self.write_config({"piper": {"voice": "alice"}})
        named = speak.load_config()
        self.assertEqual(named["piper"]["voice"], "alice")
        self.assertEqual(named["piper"]["lengthScale"], 1.0)
        self.assertEqual(named["piper"]["volume"], 8)
        self.write_config({"piper": {"volume": 0.5}})
        self.assertEqual(speak.load_config()["piper"]["volume"], 5)
        self.write_config({"piper": {"volume": "loud"}})
        self.assertEqual(speak.load_config()["piper"]["volume"], 8)
        self.write_config({"piper": {"volume": 0}})
        self.assertEqual(speak.load_config()["piper"]["volume"], 8)
        self.write_config({"piper": {"volume": -0.2}})
        self.assertEqual(speak.load_config()["piper"]["volume"], 8)
        self.write_config({"piper": {"volume": 3}})
        self.assertEqual(speak.load_config()["piper"]["volume"], 3)
        self.write_config({"piper": {"volume": 11}})
        self.assertEqual(speak.load_config()["piper"]["volume"], 8)
        self.write_config({"piper": {"volume": 7.5}})
        self.assertEqual(speak.load_config()["piper"]["volume"], 7.5)
        self.write_config({"engine": "kokoro"})
        partial = speak.load_config()
        self.assertTrue(partial["enabled"])
        self.assertEqual(partial["maxTextLength"], 500)

    def test_log_appends_and_swallows_oserror(self) -> None:
        speak.log("  hello  ")
        self.assertEqual(paths.log_path().read_text(encoding="utf-8"), "  hello\n")
        blocked = self.root / "not-a-dir"
        blocked.write_text("x", encoding="utf-8")
        with mock.patch.dict(os.environ, {"GROK_TTS_DIR": str(blocked)}):
            speak.log("hidden")

    def test_prepare_text_strips_markup_and_clips(self) -> None:
        self.assertEqual(speak.prepare_text("", 40), "")
        self.assertEqual(speak.prepare_text("done … [+120 chars]", 500), "done")
        sample = speak.prepare_text(
            "<thinking>hidden</thinking>\n# Title\n"
            "See [docs](https://example.com) and `rg`.\n"
            "```\ncode\n```\nhttps://example.com/x\n"
            "- one\n* two\n+ three\n1. four\n2) five\n"
            "**bold** and _em_ <b>tag</b> ![pic](http://img)",
            500,
        )
        self.assertNotIn("hidden", sample)
        self.assertNotIn("example.com", sample)
        self.assertNotIn("code", sample)
        self.assertNotIn("*", sample)
        self.assertNotIn("<", sample)
        for word in ("Title", "docs", "rg", "one", "two", "three", "four", "five"):
            self.assertIn(word, sample)
        self.assertIn("bold", sample)
        self.assertIn("em", sample)
        self.assertIn("tag", sample)
        self.assertEqual(speak.prepare_text("exact", 5), "exact")
        self.assertEqual(
            speak.prepare_text("alpha beta gamma delta", 12), "alpha beta..."
        )
        self.assertEqual(speak.prepare_text("hello world.,;: extra", 16), "hello world...")
        self.assertEqual(speak.prepare_text("hi .,;:qqqqqq", 7), "hi ...")

    def test_daemon_running_checks_pid_and_socket(self) -> None:
        self.assertFalse(speak.daemon_running())
        paths.pid_file().write_text("12\n", encoding="utf-8")
        paths.socket_file().write_text("", encoding="utf-8")
        self.assertFalse(speak.daemon_running())
        paths.socket_file().unlink()
        self.bind_socket(paths.socket_file())
        paths.pid_file().write_text("nope\n", encoding="utf-8")
        self.assertFalse(speak.daemon_running())
        paths.pid_file().write_text("12\n", encoding="utf-8")
        self.kill.side_effect = OSError("dead")
        self.assertFalse(speak.daemon_running())
        self.kill.side_effect = None
        self.kill.return_value = None
        self.assertTrue(speak.daemon_running())

    def test_prewarm_skips_starts_and_clears_stale_files(self) -> None:
        paths.pid_file().write_text("12\n", encoding="utf-8")
        self.bind_socket(paths.socket_file())
        speak.prewarm()
        self.assertIn("already-running", paths.log_path().read_text(encoding="utf-8"))
        self.popen.assert_not_called()

        paths.pid_file().unlink()
        paths.socket_file().unlink()
        paths.log_path().unlink()
        speak.prewarm()
        self.assertIn("missing-daemon", paths.log_path().read_text(encoding="utf-8"))
        self.touch(paths.daemon_script())
        paths.log_path().unlink()
        speak.prewarm()
        self.assertIn("missing-daemon", paths.log_path().read_text(encoding="utf-8"))

        self.touch(paths.daemon_python())
        paths.pid_file().write_text("stale\n", encoding="utf-8")
        paths.socket_file().mkdir()
        speak.prewarm()
        self.assertFalse(paths.pid_file().exists())
        self.assertTrue(paths.socket_file().is_dir())
        self.popen.assert_called_once()
        self.assertIn("prewarm started", paths.log_path().read_text(encoding="utf-8"))
        command = self.popen.call_args.args[0]
        self.assertEqual(
            command, [str(paths.daemon_python()), str(paths.daemon_script())]
        )
        self.assertTrue(self.popen.call_args.kwargs["start_new_session"])

    def test_already_spoken_remembers_one_digest(self) -> None:
        self.assertFalse(speak.already_spoken("", "hello"))
        self.assertFalse(speak.already_spoken("sess", "hello"))
        self.assertTrue(speak.already_spoken("sess", "hello"))
        self.assertFalse(speak.already_spoken("sess", "different"))
        session = "a/b c" + ("!" * 200)
        self.assertFalse(speak.already_spoken(session, "hello"))
        names = [path.name for path in paths.state_dir().iterdir()]
        safe = [name for name in names if name.startswith("a_b_c")]
        self.assertEqual(len(safe), 1)
        self.assertLessEqual(len(safe[0]), 120)
        self.assertNotIn("/", safe[0])
        with mock.patch.object(Path, "read_text", side_effect=OSError("denied")):
            self.assertFalse(speak.already_spoken("sess", "different"))
        state = paths.state_dir()
        for child in state.iterdir():
            child.unlink()
        state.rmdir()
        state.write_text("blocked", encoding="utf-8")
        self.assertFalse(speak.already_spoken("fresh", "hello"))

    def test_speaker_commands_include_voice_and_speed(self) -> None:
        voiced = speak.kokoro_command("hi", {"voice": "bf_emma", "speed": "1.25"})
        self.assertEqual(voiced[:3], ["bash", str(paths.speak_sh()), "hi"])
        self.assertEqual(voiced[3:], ["bf_emma", "1.25"])
        default_speed = speak.kokoro_command("hi", {"voice": "bf_emma", "speed": ""})
        self.assertEqual(default_speed[-1], "1.6")
        speed_only = speak.kokoro_command("hi", {"voice": "", "speed": "1.1"})
        self.assertEqual(speed_only[-2:], ["", "1.1"])
        plain = speak.kokoro_command("hi", {"voice": "", "speed": ""})
        self.assertEqual(len(plain), 3)

        missing = speak.piper_command("hi", {})
        self.assertIn(speak.DEFAULT_PIPER_VOICE, missing)
        self.assertIn("1.0", missing)
        self.assertEqual(missing[missing.index("--volume") + 1], "0.8")
        for scale in ("nope", None, 0, -2):
            built = speak.piper_command("hi", {"piper": {"lengthScale": scale}})
            self.assertIn("1.0", built)
        for bad in ("nope", None, 0, -2):
            quiet = speak.piper_command("hi", {"piper": {"volume": bad}})
            self.assertEqual(quiet[quiet.index("--volume") + 1], "0.8")
        custom = speak.piper_command(
            "hi", {"piper": {"voice": "alice", "lengthScale": 0.8, "volume": 4}}
        )
        legacy = speak.piper_command("hi", {"piper": {"volume": 0.5}})
        self.assertEqual(legacy[legacy.index("--volume") + 1], "0.5")
        quieter = speak.piper_command("hi", {"piper": {"volume": 7.5}})
        self.assertEqual(quieter[quieter.index("--volume") + 1], "0.75")
        self.assertEqual(custom[0], str(paths.piper_python()))
        self.assertEqual(custom[1], str(paths.piper_script()))
        self.assertIn("alice", custom)
        self.assertIn("0.8", custom)
        self.assertEqual(custom[custom.index("--volume") + 1], "0.4")
        self.assertEqual(custom[-1], "hi")

    def test_speak_starts_the_selected_engine(self) -> None:
        speak.speak("hello", {"engine": "piper"})
        self.assertIn("piper missing", paths.log_path().read_text(encoding="utf-8"))
        self.popen.assert_not_called()
        self.touch(paths.piper_python())
        paths.log_path().write_text("", encoding="utf-8")
        speak.speak("hello", {"engine": "piper"})
        self.assertIn("piper missing", paths.log_path().read_text(encoding="utf-8"))
        self.touch(paths.piper_script())
        paths.log_path().write_text("", encoding="utf-8")
        speak.speak("hello", {"engine": "piper", "piper": {"voice": "alice"}})
        self.assertIn(
            "speak piper queued chars=5", paths.log_path().read_text(encoding="utf-8")
        )
        self.popen.assert_called_once()

        self.popen.reset_mock()
        paths.log_path().write_text("", encoding="utf-8")
        speak.speak("hello", {"engine": "kokoro", "voice": "", "speed": ""})
        self.assertIn("missing-script", paths.log_path().read_text(encoding="utf-8"))
        self.touch(paths.speak_sh())
        paths.log_path().write_text("", encoding="utf-8")
        speak.speak("hello", {"voice": "bf_emma", "speed": "1.25"})
        self.assertIn("speak queued chars=5", paths.log_path().read_text(encoding="utf-8"))
        self.assertEqual(self.popen.call_args.args[0][0], "bash")
        self.assertTrue(self.popen.call_args.kwargs["start_new_session"])

    def _cfg(self, **overrides: object) -> dict:
        cfg = {
            "enabled": True,
            "maxTextLength": 500,
            "engine": "kokoro",
            "voice": "",
            "speed": "",
        }
        cfg.update(overrides)
        return cfg

    def _stop(self, **overrides: object) -> dict:
        event = {
            "hook_event_name": "Stop",
            "reason": "end_turn",
            "sessionId": "s1",
            "lastAssistantMessage": "Hello there.",
        }
        event.update(overrides)
        return event

    def test_handle_session_start_warms_only_kokoro(self) -> None:
        speak.handle({"hook_event_name": "SessionStart"}, self._cfg())
        self.assertIn("missing-daemon", paths.log_path().read_text(encoding="utf-8"))
        paths.log_path().write_text("", encoding="utf-8")
        speak.handle({"hookEventName": "session_start"}, self._cfg())
        self.assertIn("missing-daemon", paths.log_path().read_text(encoding="utf-8"))
        paths.log_path().unlink()
        speak.handle(
            {"hook_event_name": "SessionStart", "hookEventName": "other"},
            self._cfg(enabled=False),
        )
        self.assertFalse(paths.log_path().exists())
        speak.handle({"hook_event_name": "SessionStart"}, self._cfg(), dry_run=True)
        self.assertFalse(paths.log_path().exists())
        speak.handle({"hook_event_name": "SessionStart"}, self._cfg(engine="piper"))
        self.assertIn(
            "prewarm skipped engine=piper", paths.log_path().read_text(encoding="utf-8")
        )
        paths.log_path().unlink()
        no_engine = self._cfg()
        del no_engine["engine"]
        speak.handle({"hookEventName": "session_start"}, no_engine)
        self.assertIn("missing-daemon", paths.log_path().read_text(encoding="utf-8"))

    def test_handle_ignores_other_events_and_speaks_a_finished_turn(self) -> None:
        ignored = {
            "hook_event_name": "Other",
            "hookEventName": "nope",
            "reason": "end_turn",
            "lastAssistantMessage": "Hello there.",
        }
        self.assertIsNone(speak.handle(ignored, self._cfg(), dry_run=True))
        self.assertIsNone(
            speak.handle(
                {"hook_event_name": "Other", "reason": "end_turn"},
                self._cfg(),
                dry_run=True,
            )
        )
        camel = {
            "hookEventName": "other",
            "reason": "end_turn",
            "sessionId": "camel",
            "lastAssistantMessage": "Hello there.",
        }
        self.assertEqual(speak.handle(camel, self._cfg(), dry_run=True), "Hello there.")
        named = self._stop(hookEventName="nope", sessionId="named")
        self.assertEqual(speak.handle(named, self._cfg(), dry_run=True), "Hello there.")
        via_camel = {
            "hook_event_name": "Other",
            "hookEventName": "stop",
            "reason": "end_turn",
            "sessionId": "via",
            "lastAssistantMessage": "Hello there.",
        }
        self.assertEqual(
            speak.handle(via_camel, self._cfg(), dry_run=True), "Hello there."
        )
        self.assertIsNone(
            speak.handle(self._stop(subagentType="explore"), self._cfg(), dry_run=True)
        )
        self.assertIsNone(
            speak.handle(self._stop(reason="shutdown"), self._cfg(), dry_run=True)
        )
        self.assertIsNone(speak.handle(self._stop(reason=""), self._cfg(), dry_run=True))
        from_alias = self._stop(reason="", stopReason="end_turn", sessionId="alias")
        self.assertEqual(
            speak.handle(from_alias, self._cfg(), dry_run=True), "Hello there."
        )
        self.assertIsNone(
            speak.handle(
                self._stop(lastAssistantMessage="x", sessionId="short"),
                self._cfg(),
                dry_run=True,
            )
        )
        self.assertEqual(
            speak.handle(self._stop(sessionId="dry"), self._cfg(), dry_run=True),
            "Hello there.",
        )
        self.popen.assert_not_called()
        self.assertIsNone(
            speak.handle(self._stop(sessionId="dry"), self._cfg(), dry_run=True)
        )
        self.assertIn("duplicate", paths.log_path().read_text(encoding="utf-8"))
        self.touch(paths.speak_sh())
        self.assertIsNone(speak.handle(self._stop(sessionId="live"), self._cfg()))
        self.popen.assert_called_once()
        self.popen.reset_mock()
        paths.log_path().write_text("", encoding="utf-8")
        self.assertIsNone(
            speak.handle(self._stop(sessionId="off"), self._cfg(enabled=False))
        )
        self.popen.assert_not_called()
        self.assertIn("disabled", paths.log_path().read_text(encoding="utf-8"))

    def test_self_test_and_main_cover_stdin_paths(self) -> None:
        stdout = io.StringIO()
        with mock.patch("sys.argv", ["speak", "--self-test"]), mock.patch(
            "sys.stdout", stdout
        ):
            speak.main()
        self.assertIn("self-test ok", stdout.getvalue())

        def run(raw: str, argv: list[str] | None = None) -> None:
            with (
                mock.patch("sys.stdin", io.StringIO(raw)),
                mock.patch("sys.argv", argv or ["speak"]),
                mock.patch("sys.stdout", io.StringIO()),
            ):
                speak.main()

        run("")
        run("   \n")
        run("{")
        self.assertIn("bad json", paths.log_path().read_text(encoding="utf-8"))
        run("[]")
        run("1")
        self.touch(paths.speak_sh())
        os.environ["GROK_TTS_DRY_RUN"] = "1"
        run(json.dumps(self._stop(sessionId="main-dry")))
        self.popen.assert_not_called()
        os.environ["GROK_TTS_DRY_RUN"] = "0"
        run(json.dumps(self._stop(sessionId="main-live")))
        self.popen.assert_called()
        self.write_config({"enabled": False, "engine": "kokoro"})
        self.popen.reset_mock()
        run(json.dumps(self._stop(sessionId="main-off")))
        self.popen.assert_not_called()
        self.write_config(_sample_config())
        run(json.dumps({"hook_event_name": "SessionStart"}))
        self.assertIn("missing-daemon", paths.log_path().read_text(encoding="utf-8"))

    def test_module_guard_swallows_a_read_error(self) -> None:
        class Boom:
            def read(self) -> str:
                raise RuntimeError("broken")

        with (
            mock.patch("sys.stdin", io.StringIO("")),
            mock.patch("sys.argv", ["speak"]),
            mock.patch("sys.stdout", io.StringIO()),
        ):
            runpy.run_module("tts.speak", run_name="__main__")
        with (
            mock.patch("sys.stdin", Boom()),
            mock.patch("sys.argv", ["speak"]),
            mock.patch("sys.stdout", io.StringIO()),
            self.assertRaises(SystemExit) as caught,
        ):
            runpy.run_module("tts.speak", run_name="__main__")
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("error broken", paths.log_path().read_text(encoding="utf-8"))


class ToggleTests(TempTTS):
    def setUp(self) -> None:
        super().setUp()
        self.clock = Clock()
        self.kills: list[tuple[int, int]] = []
        self.alive: set[int] = set()
        self.term_fails = False
        self.kill_fails = False
        self.immortal = False
        for target, value in (
            ("tts.toggle.time.sleep", self.clock.sleep),
            ("tts.toggle.time.monotonic", self.clock.monotonic),
            ("tts.toggle.os.kill", self.fake_kill),
        ):
            patch = mock.patch(target, value)
            patch.start()
            self.addCleanup(patch.stop)
        popen_patch = mock.patch("tts.toggle.subprocess.Popen")
        self.popen = popen_patch.start()
        self.addCleanup(popen_patch.stop)

    def tearDown(self) -> None:
        for pid, _sig in self.kills:
            self.assertLess(pid, 1000)
        super().tearDown()

    def fake_kill(self, pid: int, sig: int) -> None:
        self.kills.append((pid, sig))
        if sig == 0 and pid not in self.alive:
            raise OSError("not running")
        if sig == signal.SIGTERM:
            if self.term_fails:
                raise OSError("term")
            if not self.immortal:
                self.alive.discard(pid)
            return
        if sig == signal.SIGKILL:
            if self.kill_fails:
                raise OSError("kill")
            self.alive.discard(pid)

    def invoke(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["toggle", *args]),
            mock.patch("sys.stdout", stdout),
            mock.patch("sys.stderr", stderr),
        ):
            try:
                toggle.main()
            except SystemExit as exc:
                code = 0 if exc.code is None else int(exc.code)
            else:
                code = 0
        return code, stdout.getvalue(), stderr.getvalue()

    def test_normalize_device(self) -> None:
        self.assertEqual(toggle.normalize_device(None), "")
        self.assertEqual(toggle.normalize_device(0), "")
        self.assertEqual(toggle.normalize_device("  CPU "), "cpu")
        self.assertEqual(toggle.normalize_device("cuda"), "gpu")
        self.assertEqual(toggle.normalize_device("GPU"), "gpu")
        self.assertEqual(toggle.normalize_device("tpu"), "")

    def test_load_repairs_bad_files_and_save_roundtrips(self) -> None:
        path = paths.config_path()
        path.unlink()
        self.assertEqual(toggle.load()["engine"], "kokoro")
        self.write_raw_config("{")
        self.assertEqual(toggle.load()["kokoro"]["device"], "gpu")
        self.write_raw_config("null")
        self.assertTrue(toggle.load()["enabled"])
        path.unlink()
        path.mkdir()
        self.assertEqual(toggle.load()["piper"]["voice"], toggle.DEFAULT_PIPER["voice"])
        path.rmdir()
        self.write_config(
            {
                "enabled": False,
                "voice": "bf_emma",
                "speed": "1.25",
                "engine": "  NOPE ",
                "piper": {"voice": "   "},
                "kokoro": {"device": "cuda"},
            }
        )
        data = toggle.load()
        self.assertFalse(data["enabled"])
        self.assertEqual(data["engine"], "kokoro")
        self.assertEqual(data["piper"]["voice"], toggle.DEFAULT_PIPER["voice"])
        self.assertEqual(data["kokoro"]["device"], "gpu")
        self.write_config(
            {
                "engine": "Piper",
                "piper": ["nope"],
                "kokoro": ["nope"],
            }
        )
        data = toggle.load()
        self.assertEqual(data["engine"], "piper")
        self.assertEqual(data["kokoro"]["device"], "gpu")
        self.write_config({"engine": "kokoro", "kokoro": {"device": "cpu"}})
        self.assertEqual(toggle.load()["kokoro"]["device"], "cpu")
        path.unlink()
        path.parent.rmdir()
        toggle.save({"enabled": True, "engine": "kokoro"})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["enabled"], True)

    def test_status_line_names_the_active_engine(self) -> None:
        data = toggle.load()
        self.assertIn("on engine=kokoro", toggle.status_line(data))
        self.assertIn("device=gpu", toggle.status_line(data))
        data["enabled"] = False
        data["engine"] = "piper"
        data["piper"]["voice"] = "alice"
        data["piper"]["lengthScale"] = 0.7
        self.assertEqual(
            toggle.status_line(data),
            "off engine=piper voice=alice lengthScale=0.7 volume=8",
        )
        self.assertIn("volume=10", toggle.status_line(toggle.load()))

    def test_main_switches_enabled_engine_and_voice(self) -> None:
        code, out, _err = self.invoke()
        self.assertEqual(code, 0)
        self.assertIn("engine=kokoro", out)
        code, out, _err = self.invoke("status", "extra")
        self.assertEqual(code, 0)
        self.assertIn("on engine=kokoro", out)
        code, out, _err = self.invoke("off")
        self.assertIn("off engine=kokoro", out)
        self.assertFalse(toggle.load()["enabled"])
        code, out, _err = self.invoke("off")
        self.assertFalse(toggle.load()["enabled"])
        code, out, _err = self.invoke("on")
        self.assertTrue(toggle.load()["enabled"])
        self.invoke("on")
        self.assertTrue(toggle.load()["enabled"])
        self.invoke("toggle")
        self.assertFalse(toggle.load()["enabled"])
        self.invoke("toggle")
        self.assertTrue(toggle.load()["enabled"])
        code, _out, err = self.invoke("toggle", "extra")
        self.assertEqual(code, 2)
        self.assertIn("Usage: toggle [", err)

        code, out, _err = self.invoke("engine")
        self.assertIn("engine=kokoro", out)
        code, out, _err = self.invoke("engine", "Piper")
        self.assertEqual(code, 0)
        self.assertIn("engine=piper", out)
        self.assertEqual(toggle.load()["engine"], "piper")
        code, out, _err = self.invoke("engine", "kokoro")
        self.assertIn("engine=kokoro", out)
        code, _out, err = self.invoke("engine", "nope")
        self.assertEqual(code, 2)
        self.assertIn("toggle engine", err)
        code, _out, err = self.invoke("engine", "kokoro", "extra")
        self.assertEqual(code, 2)

        self.invoke("piper-voice", "alice")
        self.assertEqual(toggle.load()["piper"]["voice"], "alice")
        code, _out, err = self.invoke("piper-voice", "   ")
        self.assertEqual(code, 2)
        code, _out, err = self.invoke("piper-voice")
        self.assertEqual(code, 2)
        code, _out, err = self.invoke("piper-voice", "alice", "extra")
        self.assertEqual(code, 2)

        (paths.piper_voices() / "jenny.onnx").write_bytes(b"m")
        (paths.piper_voices() / "amy.onnx").write_bytes(b"m")
        (paths.piper_voices() / "notes.onnx.json").write_text("{}", encoding="utf-8")
        (paths.piper_voices() / "readme.txt").write_text("x", encoding="utf-8")
        code, out, _err = self.invoke("voices")
        self.assertEqual(out.strip().splitlines(), ["amy", "jenny"])
        for child in paths.piper_voices().iterdir():
            child.unlink()
        code, out, _err = self.invoke("voices")
        self.assertIn("(no piper voices installed)", out)
        paths.piper_voices().rmdir()
        code, out, _err = self.invoke("voices")
        self.assertIn("(no piper voices installed)", out)
        code, _out, err = self.invoke("voices", "extra")
        self.assertEqual(code, 2)
        code, _out, err = self.invoke("nope")
        self.assertEqual(code, 2)
        self.assertIn("piper-voice", err)

        code, out, _err = self.invoke("volume")
        self.assertEqual(code, 0)
        self.assertIn("kokoro volume=10", out)
        self.assertIn("piper volume=8", out)
        code, out, _err = self.invoke("volume", "4")
        self.assertEqual(code, 0)
        self.assertEqual(toggle.load()["kokoro"]["volume"], 4)
        self.assertEqual(toggle.load()["piper"]["volume"], 8)
        self.assertIn("kokoro volume=4", out)
        self.invoke("engine", "piper")
        code, out, _err = self.invoke("volume", "2")
        self.assertEqual(toggle.load()["piper"]["volume"], 2)
        self.assertEqual(toggle.load()["kokoro"]["volume"], 4)
        self.assertIn("piper volume=2", out)
        self.assertIn("kokoro volume=4", out)
        self.write_config({"engine": "piper", "piper": {"volume": 0.8}})
        self.assertEqual(toggle.load()["piper"]["volume"], 8)
        self.write_config({"kokoro": {"volume": 0.5}})
        self.assertEqual(toggle.load()["kokoro"]["volume"], 5)
        for bad in ("0", "11", "loud", "7.5"):
            code, _out, err = self.invoke("volume", bad)
            self.assertEqual(code, 2, bad)
            self.assertIn("toggle volume", err)
        code, _out, err = self.invoke("volume", "4", "extra")
        self.assertEqual(code, 2)
        self.assertIn("toggle volume", err)

    def test_daemon_pid_and_stop(self) -> None:
        self.assertIsNone(toggle.daemon_pid())
        paths.pid_file().write_text("nope\n", encoding="utf-8")
        self.assertIsNone(toggle.daemon_pid())
        paths.pid_file().write_text("12\n", encoding="utf-8")
        self.assertIsNone(toggle.daemon_pid())
        self.alive.add(12)
        self.assertEqual(toggle.daemon_pid(), 12)

        self.term_fails = True
        toggle.stop_daemon()
        self.assertIn((12, signal.SIGTERM), self.kills)
        self.assertNotIn((12, signal.SIGKILL), self.kills)
        self.assertFalse(paths.pid_file().exists())

        self.term_fails = False
        self.immortal = True
        self.alive.add(50)
        paths.pid_file().write_text("50\n", encoding="utf-8")
        paths.socket_file().write_text("", encoding="utf-8")
        paths.device_file().write_text("cpu\n", encoding="utf-8")
        toggle.stop_daemon()
        self.assertIn((50, signal.SIGKILL), self.kills)
        self.assertFalse(paths.socket_file().exists())
        self.assertGreaterEqual(self.clock.now, 10)

        self.kills.clear()
        self.immortal = True
        self.kill_fails = True
        self.alive.add(50)
        paths.pid_file().write_text("50\n", encoding="utf-8")
        toggle.stop_daemon()
        self.assertIn((50, signal.SIGKILL), self.kills)

    def test_start_and_restart_and_log_tail(self) -> None:
        with self.assertRaises(RuntimeError) as missing:
            toggle.start_daemon()
        self.assertIn("missing daemon", str(missing.exception))
        self.touch(paths.daemon_python())
        with self.assertRaises(RuntimeError):
            toggle.start_daemon()
        self.touch(paths.daemon_script())
        paths.daemon_python().unlink()
        with self.assertRaises(RuntimeError):
            toggle.start_daemon()
        self.touch(paths.daemon_python())
        paths.daemon_log().write_text("old\n", encoding="utf-8")

        def popen(cmd: list[str], **kwargs: object) -> mock.Mock:
            handle = kwargs["stdout"]
            assert hasattr(handle, "write")
            handle.write("ready\n")
            paths.pid_file().write_text("222\n", encoding="utf-8")
            paths.socket_file().write_text("", encoding="utf-8")
            paths.device_file().write_text("CUDA\n", encoding="utf-8")
            self.alive.add(222)
            return mock.Mock()

        self.popen.side_effect = popen
        self.alive.add(111)
        paths.pid_file().write_text("111\n", encoding="utf-8")
        pid, live = toggle.restart_daemon()
        self.assertEqual((pid, live), (222, "cuda"))
        self.assertNotIn("old", paths.daemon_log().read_text(encoding="utf-8"))
        self.assertIn("ready", paths.daemon_log().read_text(encoding="utf-8"))
        self.popen.assert_called()
        self.assertTrue(self.popen.call_args.kwargs["start_new_session"])
        self.assertIs(self.popen.call_args.kwargs["stderr"], subprocess.STDOUT)

        paths.daemon_log().unlink()
        self.assertEqual(toggle.log_tail(), "")
        paths.daemon_log().write_text("", encoding="utf-8")
        self.assertEqual(toggle.log_tail(), "")
        lines = [f"L{i}" for i in range(25)]
        paths.daemon_log().write_text("\n".join(lines) + "\n", encoding="utf-8")
        tail = toggle.log_tail().splitlines()
        self.assertEqual(tail[0], "L5")
        self.assertEqual(tail[-1], "L24")
        self.assertEqual(len(tail), 20)
        paths.daemon_log().write_bytes(b"ok\xff\n")
        self.assertIn("ok", toggle.log_tail())
        paths.daemon_log().unlink()
        paths.daemon_log().mkdir()
        self.assertEqual(toggle.log_tail(), "")

    def test_wait_ready_covers_timeout_and_a_died_process(self) -> None:
        with mock.patch.object(toggle, "daemon_pid", return_value=5):
            self.assertEqual(toggle.wait_ready(5, timeout=1), (None, ""))
        paths.socket_file().write_text("", encoding="utf-8")
        paths.device_file().write_text("CUDA\n", encoding="utf-8")
        with mock.patch.object(toggle, "daemon_pid", return_value=8):
            self.assertEqual(toggle.wait_ready(1), (8, "cuda"))
        paths.device_file().unlink()
        with mock.patch.object(toggle, "daemon_pid", return_value=8):
            self.assertEqual(toggle.wait_ready(1, timeout=1), (None, ""))
        paths.socket_file().unlink()
        with mock.patch.object(toggle, "daemon_pid", side_effect=[8, None]):
            self.assertEqual(toggle.wait_ready(1, timeout=5), (None, ""))
        with mock.patch.object(toggle, "daemon_pid", return_value=None):
            self.assertEqual(toggle.wait_ready(None, timeout=1), (None, ""))

    def test_live_choice_and_switch_device(self) -> None:
        self.assertEqual(toggle.live_choice(), "")
        paths.device_file().write_text("CPU\n", encoding="utf-8")
        self.assertEqual(toggle.live_choice(), "cpu")
        paths.device_file().write_text("cuda\n", encoding="utf-8")
        self.assertEqual(toggle.live_choice(), "gpu")
        paths.device_file().write_text("mps\n", encoding="utf-8")
        self.assertEqual(toggle.live_choice(), "")
        paths.device_file().unlink()
        paths.device_file().mkdir()
        self.assertEqual(toggle.live_choice(), "")
        paths.device_file().rmdir()

        code, out, err = self.invoke("device")
        self.assertEqual(code, 0)
        self.assertIn("device=gpu", out)
        code, _out, err = self.invoke("device", "tpu")
        self.assertEqual(code, 2)
        self.assertIn("toggle device", err)
        code, _out, err = self.invoke("device", "cpu", "extra")
        self.assertEqual(code, 2)
        code, out, _err = self.invoke("device", "gpu")
        self.assertIn("already on gpu", out)
        code, out, _err = self.invoke("device", "cuda")
        self.assertIn("already on gpu", out)
        paths.device_file().write_text("cuda\n", encoding="utf-8")
        code, out, _err = self.invoke("device", "gpu")
        self.assertIn("already on gpu", out)

        paths.device_file().write_text("cpu\n", encoding="utf-8")
        with mock.patch.object(toggle, "restart_daemon", return_value=(9, "cuda")):
            code, out, err = self.invoke("device", "gpu")
        self.assertEqual(code, 0)
        self.assertIn("restarted on cuda pid=9", out)
        self.assertEqual(toggle.load()["kokoro"]["device"], "gpu")

        self.write_config(_sample_config())
        with mock.patch.object(toggle, "restart_daemon", return_value=(15, "cpu")):
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                toggle.switch_device(toggle.load(), "cpu")
        self.assertIn("restarted on cpu pid=15", stdout.getvalue())
        self.assertEqual(toggle.load()["kokoro"]["device"], "cpu")

        self.write_config(_sample_config())
        paths.daemon_log().write_text("\n".join(f"L{i}" for i in range(25)), encoding="utf-8")

        def restore_ok() -> tuple[int | None, str]:
            restore_ok.calls += 1
            if restore_ok.calls == 1:
                return None, ""
            return 77, "cpu"

        restore_ok.calls = 0
        stderr = io.StringIO()
        with (
            mock.patch.object(toggle, "restart_daemon", side_effect=restore_ok),
            mock.patch("sys.stderr", stderr),
            self.assertRaises(SystemExit) as caught,
        ):
            toggle.switch_device(toggle.load(), "cpu")
        self.assertEqual(caught.exception.code, 1)
        err = stderr.getvalue()
        self.assertIn("Kokoro did not come up on cpu.", err)
        self.assertIn("The daemon was restored to gpu.", err)
        self.assertIn("L24", err)
        self.assertNotIn("did not come back", err)
        self.assertEqual(toggle.load()["kokoro"]["device"], "gpu")

        if paths.daemon_log().exists():
            paths.daemon_log().unlink()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                toggle,
                "restart_daemon",
                side_effect=[(4, "cuda"), RuntimeError("gone")],
            ),
            mock.patch("sys.stderr", stderr),
            self.assertRaises(SystemExit) as caught,
        ):
            toggle.switch_device(toggle.load(), "cpu")
        self.assertEqual(caught.exception.code, 1)
        err = stderr.getvalue()
        self.assertIn("did not come back", err)
        self.assertNotIn("L0", err)
        self.assertEqual(toggle.load()["kokoro"]["device"], "gpu")

        paths.daemon_log().write_text("boom\n", encoding="utf-8")
        stderr = io.StringIO()
        with (
            mock.patch.object(
                toggle, "restart_daemon", side_effect=[(4, "cuda"), (None, "")]
            ),
            mock.patch("sys.stderr", stderr),
            self.assertRaises(SystemExit) as caught,
        ):
            toggle.switch_device(toggle.load(), "cpu")
        err = stderr.getvalue()
        self.assertIn("boom", err)
        self.assertIn("did not come back", err)
        self.assertEqual(caught.exception.code, 1)

        code, _out, err = self.invoke("device", "cpu")
        self.assertEqual(code, 1)
        self.assertIn("was not restarted", err)
        self.assertIn("missing daemon", err)
        self.assertEqual(toggle.load()["kokoro"]["device"], "gpu")

    def test_module_guard(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch("sys.argv", ["toggle", "status"]),
            mock.patch("sys.stdout", stdout),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            runpy.run_module("tts.toggle", run_name="__main__")
        self.assertIn("engine=kokoro", stdout.getvalue())
        with (
            mock.patch("sys.argv", ["toggle", "nope"]),
            mock.patch("sys.stdout", io.StringIO()),
            mock.patch("sys.stderr", io.StringIO()),
            self.assertRaises(SystemExit) as caught,
        ):
            runpy.run_module("tts.toggle", run_name="__main__")
        self.assertEqual(caught.exception.code, 2)


class _FakeConfig:
    def __init__(self, length_scale: float, volume: float = 1.0) -> None:
        self.length_scale = length_scale
        self.volume = volume


class _FakeVoice:
    def __init__(self, model: Path) -> None:
        self.model = model
        self.last: tuple[str, _FakeConfig] | None = None

    def synthesize_wav(self, text: str, wav: wave.Wave_write, syn_config: _FakeConfig) -> None:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\x00\x00" * 8)
        self.last = (text, syn_config)


class _FakePiper:
    def __init__(self) -> None:
        self.loaded: _FakeVoice | None = None
        outer = self

        class PiperVoice:
            @staticmethod
            def load(model: Path) -> _FakeVoice:
                voice = _FakeVoice(model)
                outer.loaded = voice
                return voice

        self.PiperVoice = PiperVoice
        self.SynthesisConfig = _FakeConfig


class PiperTests(TempTTS):
    def setUp(self) -> None:
        super().setUp()
        which_patch = mock.patch("tts.piper.shutil.which", return_value=None)
        self.which = which_patch.start()
        self.addCleanup(which_patch.stop)
        run_patch = mock.patch("tts.piper.subprocess.run")
        self.run = run_patch.start()
        self.addCleanup(run_patch.stop)
        self.fake = _FakePiper()
        self._old_piper = sys.modules.get("piper")
        sys.modules["piper"] = self.fake  # type: ignore[assignment]
        self.addCleanup(self._restore_piper)

    def _restore_piper(self) -> None:
        if self._old_piper is None:
            sys.modules.pop("piper", None)
        else:
            sys.modules["piper"] = self._old_piper

    def _voice(self, name: str = "jenny") -> None:
        (piper.voices_dir() / f"{name}.onnx").write_bytes(b"model")

    def test_log_appends_and_swallows_oserror(self) -> None:
        piper.log("  hi  ")
        self.assertEqual(piper.speak_log().read_text(encoding="utf-8"), "  hi\n")
        blocked = self.root / "log-dir"
        blocked.mkdir()
        with mock.patch.dict(os.environ, {"GROK_TTS_LOG": str(blocked)}):
            piper.log("hidden")

    def test_resolve_model_accepts_a_file_or_a_voice_name(self) -> None:
        explicit = self.root / "explicit.onnx"
        explicit.write_bytes(b"model")
        self.assertEqual(piper.resolve_model(str(explicit)), explicit)
        self._voice("jenny")
        self.assertEqual(piper.resolve_model("jenny"), piper.voices_dir() / "jenny.onnx")
        with self.assertRaises(FileNotFoundError):
            piper.resolve_model("missing")
        with self.assertRaises(FileNotFoundError):
            piper.resolve_model(str(self.root / "missing.onnx"))

    def test_import_uses_the_installed_library_not_this_script(self) -> None:
        import types

        site = self.root / "site"
        package = site / "piper"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(
            "PiperVoice = 'library-voice'\nSynthesisConfig = 'library-config'\n",
            encoding="utf-8",
        )
        script = Path(piper.__file__).resolve()
        shadowed = types.ModuleType("piper")
        shadowed.__file__ = str(script)
        saved_path = sys.path[:]
        saved_mod = sys.modules.get("piper")
        try:
            sys.modules["piper"] = shadowed
            sys.path = [str(script.parent), str(site), *saved_path]
            voice, config = piper._import_piper_library()
        finally:
            sys.path[:] = saved_path
            if saved_mod is None:
                sys.modules.pop("piper", None)
            else:
                sys.modules["piper"] = saved_mod
        self.assertEqual((voice, config), ("library-voice", "library-config"))

        self.assertFalse(piper._resolves_to("", script))
        self.assertFalse(piper._hides_piper(str(self.root), script.parent))
        self.assertTrue(piper._hides_piper(str(script.parent), script.parent))
        with mock.patch("tts.piper.Path.cwd", return_value=script.parent):
            self.assertTrue(piper._hides_piper("", script.parent))
        with mock.patch("tts.piper.Path.cwd", return_value=self.root):
            self.assertFalse(piper._hides_piper("", script.parent))
        with mock.patch("tts.piper.Path.cwd", side_effect=OSError("cwd")):
            self.assertFalse(piper._hides_piper("", script.parent))

        real_resolve = Path.resolve

        def explode(path: Path, *args: object, **kwargs: object) -> Path:
            if str(path) == "/unreadable":
                raise OSError("unreadable")
            return real_resolve(path, *args, **kwargs)

        with mock.patch.object(Path, "resolve", explode):
            self.assertFalse(piper._resolves_to("/unreadable", script))

        stuck = types.ModuleType("piper")
        stuck.__file__ = str(script)
        sys.modules["piper"] = stuck
        try:
            with (
                mock.patch("importlib.import_module", return_value=stuck),
                self.assertRaises(ImportError),
            ):
                piper._import_piper_library()
            self.assertNotIn("piper", sys.modules)
        finally:
            sys.modules["piper"] = self.fake  # type: ignore[assignment]

        # The installed name is still the test fake, so a shadowed import
        # must raise without removing that fake.
        returned = types.ModuleType("piper")
        returned.__file__ = str(script)
        try:
            with (
                mock.patch("importlib.import_module", return_value=returned),
                self.assertRaises(ImportError),
            ):
                piper._import_piper_library()
            self.assertIs(sys.modules.get("piper"), self.fake)
        finally:
            sys.modules["piper"] = self.fake  # type: ignore[assignment]

    def test_synthesize_writes_a_wav(self) -> None:
        dest = self.root / "out.wav"
        model = self.root / "explicit.onnx"
        model.write_bytes(b"model")
        piper.synthesize("hello", model, 0.5, str(dest))
        assert self.fake.loaded is not None
        assert self.fake.loaded.last is not None
        self.assertEqual(self.fake.loaded.last[0], "hello")
        self.assertEqual(self.fake.loaded.last[1].length_scale, 0.5)
        self.assertEqual(self.fake.loaded.last[1].volume, 0.8)
        self.assertGreater(dest.stat().st_size, 0)

    def test_play_tries_each_player(self) -> None:
        piper.play(str(self.root / "out.wav"))
        self.run.assert_not_called()
        self.assertIn("no-player", piper.speak_log().read_text(encoding="utf-8"))

        def which(name: str) -> str | None:
            if name == "paplay":
                return None
            return f"/usr/bin/{name}"

        def run(cmd: list[str], check: bool) -> subprocess.CompletedProcess[str]:
            code = 0 if cmd[0].endswith("aplay") else 4
            return subprocess.CompletedProcess(cmd, code)

        piper.speak_log().write_text("", encoding="utf-8")
        with (
            mock.patch("tts.piper.shutil.which", side_effect=which),
            mock.patch("tts.piper.subprocess.run", side_effect=run),
        ):
            piper.play(str(self.root / "out.wav"))
        text = piper.speak_log().read_text(encoding="utf-8")
        self.assertIn("player-failed pw-play code=4", text)
        self.assertNotIn("no-player", text)

        def fail(cmd: list[str], check: bool) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 1)

        piper.speak_log().write_text("", encoding="utf-8")
        with (
            mock.patch("tts.piper.shutil.which", return_value="/usr/bin/player"),
            mock.patch("tts.piper.subprocess.run", side_effect=fail) as failed,
        ):
            piper.play(str(self.root / "out.wav"))
        self.assertEqual(failed.call_count, 3)
        text = piper.speak_log().read_text(encoding="utf-8")
        self.assertIn("no-player", text)
        self.assertEqual(text.count("player-failed"), 3)

    def _run_main(self, argv: list[str]) -> None:
        with mock.patch("sys.argv", argv):
            piper.main()

    def test_main_speaks_keeps_output_and_cleans_up(self) -> None:
        self._voice()
        self._run_main(["piper", "--voice", "jenny", "a"])
        self.assertFalse(piper.speak_log().exists())

        dest = self.root / "kept.wav"
        self._run_main(
            ["piper", "--voice", "jenny", "--length-scale", "0.5", "--output", str(dest), "hello"]
        )
        self.assertTrue(dest.is_file())
        assert self.fake.loaded is not None
        assert self.fake.loaded.last is not None
        self.assertEqual(self.fake.loaded.last[1].length_scale, 0.5)
        self.assertEqual(self.fake.loaded.last[1].volume, 0.8)
        self.assertIn("piper spoke chars=5", piper.speak_log().read_text(encoding="utf-8"))

        self.fake.loaded = None
        self._run_main(
            ["piper", "--voice", "jenny", "--length-scale", "0", "--no-play", "hello"]
        )
        assert self.fake.loaded is not None
        assert self.fake.loaded.last is not None
        self.assertEqual(self.fake.loaded.last[1].length_scale, 1.0)
        self.assertEqual(self.fake.loaded.last[1].volume, 0.8)

        self._run_main(
            ["piper", "--voice", "jenny", "--volume", "0", "--no-play", "hello"]
        )
        assert self.fake.loaded is not None
        assert self.fake.loaded.last is not None
        self.assertEqual(self.fake.loaded.last[1].volume, 0.8)
        self._run_main(
            ["piper", "--voice", "jenny", "--volume", "0.5", "--no-play", "hello"]
        )
        assert self.fake.loaded.last is not None
        self.assertEqual(self.fake.loaded.last[1].volume, 0.5)

        names: list[str] = []
        real = tempfile.NamedTemporaryFile

        def spy(*args: object, **kwargs: object) -> object:
            handle = real(*args, **kwargs)  # type: ignore[arg-type]
            names.append(handle.name)
            return handle

        with mock.patch("tts.piper.tempfile.NamedTemporaryFile", side_effect=spy):
            self._run_main(["piper", "--voice", "jenny", "--no-play", "hello"])
        self.assertEqual(len(names), 1)
        self.assertFalse(Path(names[0]).exists())

        names.clear()
        self.which.side_effect = lambda name: f"/usr/bin/{name}"
        self.run.return_value = subprocess.CompletedProcess(["paplay"], 0)
        with mock.patch("tts.piper.tempfile.NamedTemporaryFile", side_effect=spy):
            self._run_main(["piper", "--voice", "jenny", "hello there"])
        self.run.assert_called()
        self.assertFalse(Path(names[0]).exists())

        def explode(text: str, wav: wave.Wave_write, syn_config: _FakeConfig) -> None:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            raise RuntimeError("bad synth")

        assert self.fake.loaded is not None
        self.fake.loaded.synthesize_wav = explode  # type: ignore[method-assign]
        # The next load() returns a new voice. Make that one explode too.
        original_load = self.fake.PiperVoice.load

        def load(model: Path) -> _FakeVoice:
            voice = original_load(model)
            voice.synthesize_wav = explode  # type: ignore[method-assign]
            return voice

        self.fake.PiperVoice.load = staticmethod(load)  # type: ignore[method-assign]
        names.clear()
        with (
            mock.patch("tts.piper.tempfile.NamedTemporaryFile", side_effect=spy),
            self.assertRaises(RuntimeError),
        ):
            self._run_main(["piper", "--voice", "jenny", "hello"])
        self.assertFalse(Path(names[0]).exists())

        with (
            mock.patch(
                "tts.piper.tempfile.NamedTemporaryFile",
                side_effect=OSError("temp"),
            ),
            self.assertRaises(OSError),
        ):
            self._run_main(["piper", "--voice", "jenny", "hello"])

    def test_module_guard(self) -> None:
        with (
            mock.patch("sys.argv", ["piper", "--voice", "jenny", "x"]),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            runpy.run_module("tts.piper", run_name="__main__")
        stderr = io.StringIO()
        with (
            mock.patch("sys.argv", ["piper", "--voice", "missing-voice", "hello"]),
            mock.patch("sys.stderr", stderr),
            self.assertRaises(SystemExit) as caught,
        ):
            runpy.run_module("tts.piper", run_name="__main__")
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("piper error", stderr.getvalue())
        self.assertIn("piper error", piper.speak_log().read_text(encoding="utf-8"))
        with (
            mock.patch("sys.argv", ["piper"]),
            mock.patch("sys.stderr", io.StringIO()),
            self.assertRaises(SystemExit) as caught,
        ):
            runpy.run_module("tts.piper", run_name="__main__")
        self.assertEqual(caught.exception.code, 2)


class VolumeTests(unittest.TestCase):
    def test_level_accepts_the_scale_and_a_legacy_multiplier(self) -> None:
        self.assertEqual(volume.level(True, 8), 8)
        self.assertEqual(volume.level(4, 8), 4)
        self.assertEqual(volume.level(0, 8), 8)
        self.assertEqual(volume.level(11, 8), 8)
        self.assertEqual(volume.level(8.0, 8), 8)
        self.assertEqual(volume.level(1.0, 8), 10)
        self.assertEqual(volume.level(11.0, 8), 8)
        self.assertEqual(volume.level(0.8, 8), 8)
        self.assertEqual(volume.level(0.04, 8), 1)
        self.assertEqual(volume.level(-0.2, 8), 8)
        self.assertEqual(volume.level(1.5, 8), 1.5)
        self.assertEqual(volume.level(7.5, 8), 7.5)
        self.assertEqual(volume.level(10.5, 8), 8)
        self.assertEqual(volume.level(" 4 ", 8), 4)
        self.assertEqual(volume.level("0.8", 8), 8)
        self.assertEqual(volume.level("", 8), 8)
        self.assertEqual(volume.level("loud", 8), 8)
        self.assertEqual(volume.level(None, 8), 8)
        self.assertEqual(volume.multiplier(4, 8), 0.4)
        self.assertEqual(volume.engine_multiplier(None, "kokoro", 10), 1.0)
        self.assertEqual(volume.engine_multiplier({"kokoro": []}, "kokoro", 10), 1.0)
        self.assertEqual(volume.engine_multiplier({"kokoro": {}}, "kokoro", 10), 1.0)
        self.assertEqual(
            volume.engine_multiplier({"piper": {"volume": 8}}, "piper", 8), 0.8
        )


class CommandTests(TempTTS):
    def setUp(self) -> None:
        super().setUp()
        self.proc = mock.Mock()
        self.proc.returncode = 0
        self.proc.stdout = "on engine=kokoro voice=bf_emma speed=1.25 device=gpu"
        self.proc.stderr = ""
        run_patch = mock.patch("tts.command.subprocess.run", return_value=self.proc)
        self.run = run_patch.start()
        self.addCleanup(run_patch.stop)

    def test_event_and_prompt_fields(self) -> None:
        self.assertEqual(command.event_name({}), "")
        self.assertEqual(
            command.event_name({"hook_event_name": "User_Prompt_Submit"}),
            "userpromptsubmit",
        )
        self.assertEqual(
            command.event_name({"hookEventName": "UserPromptSubmit"}),
            "userpromptsubmit",
        )
        self.assertEqual(
            command.event_name(
                {"hook_event_name": "Stop", "hookEventName": "UserPromptSubmit"}
            ),
            "stop",
        )
        self.assertEqual(command.prompt_text({"prompt": "/tts"}), "/tts")
        self.assertEqual(command.prompt_text({"prompt": 1, "userPrompt": "/tts"}), "/tts")
        self.assertIsNone(command.prompt_text({"prompt": 1}))
        self.assertIsNone(command.prompt_text({}))

    def test_control_action_parses_slash_commands_and_markers(self) -> None:
        cases = {
            "/tts": ("status", ""),
            "  /user:tts-kokoro  ": ("engine", "kokoro"),
            "/tts-piper": ("engine", "piper"),
            "/tts-cpu": ("device", "cpu"),
            "/TTS-GPU": ("device", "gpu"),
            "/tts-status": ("status", ""),
            "/tts-volume": ("volume", ""),
            "/tts-volume 4": ("volume", "4"),
            "/TTS-VOLUME 08": ("volume", "08"),
            "/user:tts-volume 7": ("volume", "7"),
            "/tts-volume 1 2": ("volume", "1 2"),
            "/tts volume": ("volume", ""),
            "/tts volume 9": ("volume", "9"),
            "/tts volume $ARGUMENTS": ("volume", ""),
            "/foo:tts": ("status", ""),
            "/tts engine piper": ("engine", "piper"),
            "/tts ENGINE kokoro": ("engine", "kokoro"),
            "/tts device cuda": ("device", "gpu"),
            "/tts device CPU": ("device", "cpu"),
            "tts-control: status": ("status", ""),
            "tts-control: engine kokoro": ("engine", "kokoro"),
            "tts-control: device gpu": ("device", "gpu"),
            "tts-control: device cuda": ("device", "gpu"),
            "tts-control: volume": ("volume", ""),
            "tts-control: volume 6": ("volume", "6"),
            "tts-control: volume $ARGUMENTS": ("volume", ""),
            "tts-control: volume 1 2": ("volume", "invalid"),
            "hello\ntts-control: status\ntts-control: device cpu": ("device", "cpu"),
            "please run /tts-gpu later": None,
            "/redact": None,
            "/tts-foo": None,
            "/tts engine": None,
            "hello": None,
        }
        for prompt, expected in cases.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(command.control_action(prompt), expected)
        long_prompt = ("note " * 500) + "\ntts-control: device gpu"
        self.assertGreater(len(long_prompt), 2000)
        self.assertIsNone(command.control_action(long_prompt))
        self.assertIsNone(command._action_from_body("nope"))
        self.assertIsNone(command._action_from_body("engine"))
        self.assertIsNone(command._action_from_body("status extra"))
        self.assertEqual(command._action_from_body("STATUS"), ("status", ""))
        self.assertEqual(command._action_from_body("device cuda"), ("device", "gpu"))
        self.assertEqual(command._action_from_body("engine kokoro"), ("engine", "kokoro"))
        self.assertEqual(command._action_from_body("volume"), ("volume", ""))
        self.assertEqual(command._action_from_body("volume 3"), ("volume", "3"))
        self.assertEqual(command._action_from_body("volume 1 2"), ("volume", "invalid"))
        self.assertEqual(command._volume_text("  $Arguments "), "")

    def test_current_engine_falls_back_to_kokoro(self) -> None:
        path = paths.config_path()
        path.unlink()
        self.assertEqual(command.current_engine(), "kokoro")
        self.write_raw_config("{")
        self.assertEqual(command.current_engine(), "kokoro")
        self.write_raw_config("[]")
        self.assertEqual(command.current_engine(), "kokoro")
        path.unlink()
        path.mkdir()
        self.assertEqual(command.current_engine(), "kokoro")
        path.rmdir()
        for raw, expected in (
            ({}, "kokoro"),
            ({"engine": ""}, "kokoro"),
            ({"engine": "nope"}, "kokoro"),
            ({"engine": " Piper "}, "piper"),
            ({"engine": "kokoro"}, "kokoro"),
        ):
            self.write_config(raw)
            self.assertEqual(command.current_engine(), expected)

    def test_plan_refuses_a_device_change_for_piper(self) -> None:
        self.assertEqual(command.plan(("status", ""), "kokoro"), ("toggle", ["status"]))
        self.assertEqual(
            command.plan(("engine", "piper"), "kokoro"), ("toggle", ["engine", "piper"])
        )
        self.assertEqual(
            command.plan(("engine", "kokoro"), "piper"), ("toggle", ["engine", "kokoro"])
        )
        self.assertEqual(
            command.plan(("device", "gpu"), "kokoro"), ("toggle", ["device", "gpu"])
        )
        self.assertEqual(
            command.plan(("device", "cpu"), "kokoro"), ("toggle", ["device", "cpu"])
        )
        kind, text = command.plan(("device", "gpu"), "piper")
        self.assertEqual(kind, "message")
        self.assertEqual(
            text,
            "Piper runs on the CPU only. The GPU was not selected. "
            "Switch to Kokoro with /tts-kokoro, then run /tts-gpu.",
        )
        _kind, cpu_text = command.plan(("device", "cpu"), "piper")
        self.assertEqual(
            cpu_text, "Piper runs on the CPU only. This engine has no device to change."
        )
        _kind, usage = command.plan(("engine", "nope"), "kokoro")
        self.assertEqual(
            usage,
            "Usage: /tts, /tts-kokoro, /tts-piper, /tts-cpu, /tts-gpu, "
            "or /tts-volume [1-10].",
        )
        self.assertEqual(command.plan(("volume", ""), "piper"), ("toggle", ["volume"]))
        self.assertEqual(
            command.plan(("volume", "04"), "kokoro"), ("toggle", ["volume", "4"])
        )
        self.assertEqual(
            command.plan(("volume", "$ARGUMENTS"), "piper"), ("toggle", ["volume"])
        )
        kind, text = command.plan(("volume", "0"), "piper")
        self.assertEqual(kind, "message")
        self.assertIn("1-10", str(text))
        kind, text = command.plan(("volume", "11"), "piper")
        self.assertIn("1-10", str(text))
        kind, text = command.plan(("volume", "loud"), "piper")
        self.assertIn("1-10", str(text))
        kind, text = command.plan(("volume", "1 2"), "piper")
        self.assertIn("1-10", str(text))
        _kind, usage = command.plan(("device", "tpu"), "kokoro")
        self.assertIn("Usage", str(usage))
        _kind, usage = command.plan(("nope", ""), "kokoro")
        self.assertIn("Usage", str(usage))

    def test_run_toggle_formats_both_streams(self) -> None:
        cases = (
            (1, "out", "err", "err"),
            (1, "out", "", "out"),
            (1, "", "", "The TTS change failed (1)."),
            (1, None, None, "The TTS change failed (1)."),
            (0, "out", "err", "out\nerr"),
            (0, "out", "", "out"),
            (0, "", "err", "err"),
            (0, None, "err", "err"),
            (0, "", "", "TTS was updated."),
            (0, "  ", "  ", "TTS was updated."),
        )
        for code, out, err, expected in cases:
            self.proc.returncode = code
            self.proc.stdout = out
            self.proc.stderr = err
            self.assertEqual(command.run_toggle(["status"]), expected)
        args = self.run.call_args.args[0]
        self.assertEqual(args, [str(paths.toggle_script()), "status"])

    def test_perform_and_handle_block_only_tts_commands(self) -> None:
        blocked = command.handle(
            {"hook_event_name": "UserPromptSubmit", "prompt": "/tts"}
        )
        assert blocked is not None
        self.assertEqual(blocked["decision"], "block")
        self.assertNotIn("CPU only", str(blocked["reason"]))
        self.proc.stdout = "on engine=piper voice=jenny lengthScale=1.0"
        blocked = command.handle(
            {"hookEventName": "user_prompt_submit", "prompt": "/tts-piper"}
        )
        assert blocked is not None
        self.assertTrue(
            str(blocked["reason"]).endswith("\nPiper runs on the CPU only.")
        )
        self.run.reset_mock()
        self.write_config({"engine": "piper"})
        blocked = command.handle(
            {"hook_event_name": "UserPromptSubmit", "prompt": "/tts-gpu"}
        )
        assert blocked is not None
        self.assertIn("was not selected", str(blocked["reason"]))
        self.run.assert_not_called()
        blocked = command.handle(
            {"hook_event_name": "UserPromptSubmit", "prompt": "/tts-cpu"}
        )
        assert blocked is not None
        self.assertIn("no device to change", str(blocked["reason"]))
        self.run.assert_not_called()
        self.assertIsNone(
            command.handle({"hook_event_name": "Stop", "prompt": "/tts"})
        )
        self.assertIsNone(
            command.handle({"hook_event_name": "UserPromptSubmit", "prompt": "hello"})
        )
        self.assertIsNone(command.handle({"hook_event_name": "UserPromptSubmit"}))
        self.assertIsNone(
            command.handle(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "hello",
                    "userPrompt": "/tts",
                }
            )
        )
        blocked = command.handle(
            {
                "hook_event_name": "UserPromptSubmit",
                "prompt": 1,
                "userPrompt": "/tts-status",
            }
        )
        self.assertIsNotNone(blocked)
        self.run.reset_mock()
        blocked = command.handle(
            {"hook_event_name": "UserPromptSubmit", "prompt": "/tts-volume"}
        )
        assert blocked is not None
        self.assertEqual(self.run.call_args.args[0][-1:], ["volume"])
        self.run.reset_mock()
        blocked = command.handle(
            {"hook_event_name": "UserPromptSubmit", "prompt": "/tts-volume 6"}
        )
        assert blocked is not None
        self.assertEqual(self.run.call_args.args[0][-2:], ["volume", "6"])
        self.run.reset_mock()
        blocked = command.handle(
            {"hook_event_name": "UserPromptSubmit", "prompt": "/tts-volume 0"}
        )
        assert blocked is not None
        self.assertIn("1-10", str(blocked["reason"]))
        self.run.assert_not_called()

    def test_main_prints_a_block_or_nothing(self) -> None:
        def run(raw: str, argv: list[str] | None = None) -> tuple[int, str]:
            stdout = io.StringIO()
            with (
                mock.patch("sys.stdin", io.StringIO(raw)),
                mock.patch("sys.stdout", stdout),
                mock.patch("sys.argv", argv or ["command"]),
            ):
                try:
                    command.main()
                except SystemExit as exc:
                    code = 0 if exc.code is None else int(exc.code)
                else:
                    code = 0
            return code, stdout.getvalue()

        stdout = io.StringIO()
        with mock.patch("sys.argv", ["command", "--self-test"]), mock.patch(
            "sys.stdout", stdout
        ):
            command.main()
        self.assertIn("self-test ok", stdout.getvalue())
        code, out = run("")
        self.assertEqual((code, out), (0, ""))
        code, out = run("   ")
        self.assertEqual(out, "")
        code, out = run("{")
        self.assertEqual(out, "")
        code, out = run("[]")
        self.assertEqual(out, "")
        code, out = run(json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "hi"}))
        self.assertEqual(out, "")
        code, out = run(
            json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "/tts"})
        )
        parsed = json.loads(out)
        self.assertEqual(parsed["decision"], "block")
        self.assertIn("engine=kokoro", parsed["reason"])
        with mock.patch.object(command, "handle", side_effect=RuntimeError("boom")):
            _code, out = run(
                json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "/tts"})
            )
        parsed = json.loads(out)
        self.assertEqual(parsed["reason"], "The TTS command failed: boom")

    def test_module_guard(self) -> None:
        with (
            mock.patch("sys.stdin", io.StringIO("")),
            mock.patch("sys.argv", ["command"]),
            mock.patch("sys.stdout", io.StringIO()),
        ):
            runpy.run_module("tts.command", run_name="__main__")

        class Boom:
            def read(self) -> str:
                raise RuntimeError("broken")

        with (
            mock.patch("sys.stdin", Boom()),
            mock.patch("sys.argv", ["command"]),
            mock.patch("sys.stdout", io.StringIO()),
            self.assertRaises(SystemExit) as caught,
        ):
            runpy.run_module("tts.command", run_name="__main__")
        self.assertEqual(caught.exception.code, 0)


class ModuleEntryTests(TempTTS):
    def test_entry_returns_zero_when_main_fails(self) -> None:
        with (
            mock.patch("sys.stdin", io.StringIO("")),
            mock.patch("sys.argv", ["tts"]),
            mock.patch("sys.stdout", io.StringIO()),
        ):
            self.assertEqual(tts_main.entry(), 0)
        with mock.patch.object(tts_main, "main", side_effect=RuntimeError("nope")):
            self.assertEqual(tts_main.entry(), 0)
        self.assertIn("error nope", paths.log_path().read_text(encoding="utf-8"))

    def test_module_guard_exits_zero(self) -> None:
        with (
            mock.patch("sys.stdin", io.StringIO("")),
            mock.patch("sys.argv", ["tts"]),
            mock.patch("sys.stdout", io.StringIO()),
            mock.patch("sys.stderr", io.StringIO()),
            self.assertRaises(SystemExit) as caught,
        ):
            runpy.run_module("tts", run_name="__main__")
        self.assertEqual(caught.exception.code, 0)


class FakeConn:
    """A socket that replays one request and records the daemon's reply."""

    def __init__(
        self,
        payload: bytes,
        *,
        body_slice: int | None = None,
        empty_on_read: int | None = None,
    ) -> None:
        self.payload = payload
        self.offset = 0
        self.sent = b""
        self.closed = False
        self.reads = 0
        self.body_slice = body_slice
        self.empty_on_read = empty_on_read

    def recv(self, size: int) -> bytes:
        self.reads += 1
        if self.empty_on_read == self.reads:
            return b""
        if self.offset >= len(self.payload):
            return b""
        take = size
        if self.body_slice is not None and self.offset >= 4:
            take = min(size, self.body_slice)
        chunk = self.payload[self.offset : self.offset + take]
        self.offset += len(chunk)
        return chunk

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def close(self) -> None:
        self.closed = True


def _frame(obj: object) -> bytes:
    payload = json.dumps(obj).encode()
    return struct.pack("!I", len(payload)) + payload


def _reply(sent: bytes) -> dict:
    size = struct.unpack("!I", sent[:4])[0]
    if len(sent) != 4 + size:
        raise AssertionError(f"short reply: {len(sent)} != {4 + size}")
    return json.loads(sent[4:])


class DaemonTests(TempTTS):
    """Kokoro daemon tests. Torch, Kokoro, and soundfile stay fake."""

    def setUp(self) -> None:
        super().setUp()
        self._filters = warnings.filters[:]
        self._sigterm = signal.getsignal(signal.SIGTERM)
        self._sigint = signal.getsignal(signal.SIGINT)
        self._cuda = os.environ.get("CUDA_VISIBLE_DEVICES")
        self._hf = os.environ.get("HF_HUB_OFFLINE")
        self._torch = sys.modules.get("torch")
        self._kokoro = sys.modules.get("kokoro")
        self._soundfile = sys.modules.get("soundfile")
        daemon.pipeline = None
        daemon.running = True
        daemon.playback_queue = queue.Queue()
        daemon.playback_thread = None
        daemon.REQUESTED_DEVICE = "gpu"
        kill_patch = mock.patch("tts.daemon.os.kill", return_value=None)
        self.kill = kill_patch.start()
        self.addCleanup(kill_patch.stop)
        pid_patch = mock.patch("tts.daemon.os.getpid", return_value=42)
        pid_patch.start()
        self.addCleanup(pid_patch.stop)

    def tearDown(self) -> None:
        for call in self.kill.call_args_list:
            self.assertLess(call.args[0], 1000)
        signal.signal(signal.SIGTERM, self._sigterm)
        signal.signal(signal.SIGINT, self._sigint)
        warnings.filters[:] = self._filters
        self._restore_env("CUDA_VISIBLE_DEVICES", self._cuda)
        self._restore_env("HF_HUB_OFFLINE", self._hf)
        thread = daemon.playback_thread
        daemon.running = False
        if isinstance(thread, threading.Thread) and thread.is_alive():
            daemon.playback_queue.put(None)
            thread.join(timeout=2)
        daemon.playback_thread = None
        daemon.pipeline = None
        daemon.playback_queue = queue.Queue()
        daemon.running = True
        self.assertIs(sys.modules.get("torch"), self._torch)
        self.assertIs(sys.modules.get("kokoro"), self._kokoro)
        self.assertIs(sys.modules.get("soundfile"), self._soundfile)
        super().tearDown()

    def _restore_env(self, name: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    def _models(
        self, *, cuda: bool = True, fail: str | None = None
    ) -> tuple[dict, mock.Mock, mock.Mock]:
        created: dict = {}

        class Pipeline:
            def __init__(self, lang_code: str, device: str) -> None:
                if fail:
                    raise RuntimeError(fail)
                created["args"] = (lang_code, device)

        kokoro = mock.Mock()
        kokoro.KPipeline = Pipeline
        torch_mod = mock.Mock()
        torch_mod.cuda.is_available.return_value = cuda
        return created, torch_mod, kokoro

    def test_requested_device_prefers_the_environment(self) -> None:
        paths.config_path().unlink()
        with mock.patch.dict(os.environ, {"KOKORO_DEVICE": "cpu"}):
            self.assertEqual(daemon.requested_device(), "cpu")
        with mock.patch.dict(os.environ, {"KOKORO_DEVICE": "  CUDA "}):
            self.assertEqual(daemon.requested_device(), "gpu")
        with mock.patch.dict(os.environ, {"KOKORO_DEVICE": "gpu"}):
            self.assertEqual(daemon.requested_device(), "gpu")

    def test_requested_device_reads_the_config(self) -> None:
        samples = [
            ("cpu", "cpu"),
            ("gpu", "gpu"),
            ("cuda", "gpu"),
            ("  CPU ", "cpu"),
            ("tpu", "gpu"),
            (None, "gpu"),
        ]
        for device, expected in samples:
            with (
                self.subTest(device=device),
                mock.patch.dict(os.environ, {"KOKORO_DEVICE": "   "}),
            ):
                self.write_config({"kokoro": {"device": device}})
                self.assertEqual(daemon.requested_device(), expected)
        self.write_config({"kokoro": {"device": "cpu"}})
        with mock.patch.dict(os.environ, {"KOKORO_DEVICE": "nope"}):
            self.assertEqual(daemon.requested_device(), "cpu")
        self.write_config({})
        self.assertEqual(daemon.requested_device(), "gpu")
        self.write_config({"kokoro": {}})
        self.assertEqual(daemon.requested_device(), "gpu")
        self.write_config({"kokoro": ["cpu"]})
        self.assertEqual(daemon.requested_device(), "gpu")
        self.write_raw_config("[]")
        self.assertEqual(daemon.requested_device(), "gpu")
        self.write_raw_config("{")
        self.assertEqual(daemon.requested_device(), "gpu")
        paths.config_path().unlink()
        self.assertEqual(daemon.requested_device(), "gpu")
        paths.config_path().mkdir()
        self.assertEqual(daemon.requested_device(), "gpu")
        paths.config_path().rmdir()
        self.write_config(_sample_config())
        with mock.patch("tts.daemon.json.loads", side_effect=TypeError("bad")):
            self.assertEqual(daemon.requested_device(), "gpu")
        with mock.patch("tts.daemon.json.loads", side_effect=ValueError("bad")):
            self.assertEqual(daemon.requested_device(), "gpu")

    def test_prepare_runtime_sets_cuda_visibility_before_torch(self) -> None:
        before = sys.modules.get("torch")
        with mock.patch.dict(os.environ, {"KOKORO_DEVICE": "cpu"}):
            self.assertEqual(daemon.prepare_runtime(), "cpu")
            self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "")
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
            self.assertEqual(daemon.REQUESTED_DEVICE, "cpu")
        with mock.patch.dict(os.environ, {"KOKORO_DEVICE": "gpu"}):
            self.assertEqual(daemon.prepare_runtime(), "gpu")
            self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "0")
            self.assertEqual(daemon.REQUESTED_DEVICE, "gpu")
        self.assertIs(sys.modules.get("torch"), before)

    def test_cleanup_removes_runtime_files_and_joins_the_worker(self) -> None:
        class Alive:
            def __init__(self) -> None:
                self.timeout: float | None = None

            def is_alive(self) -> bool:
                return True

            def join(self, timeout: float = 0) -> None:
                self.timeout = timeout

        class Dead:
            def is_alive(self) -> bool:
                return False

            def join(self, timeout: float = 0) -> None:
                raise AssertionError("joined a finished worker")

        paths.pid_file().write_text("42")
        paths.socket_file().write_text("stale")
        paths.device_file().mkdir()
        alive = Alive()
        daemon.playback_thread = alive
        daemon.cleanup()
        self.assertFalse(daemon.running)
        self.assertEqual(alive.timeout, 2.0)
        self.assertIsNone(daemon.playback_queue.get_nowait())
        self.assertFalse(paths.pid_file().exists())
        self.assertFalse(paths.socket_file().exists())
        self.assertTrue(paths.device_file().is_dir())

        daemon.playback_thread = Dead()
        daemon.running = True
        daemon.cleanup()
        self.assertFalse(daemon.running)

        paths.device_file().rmdir()
        daemon.playback_thread = None
        daemon.running = True
        daemon.cleanup()
        self.assertFalse(paths.device_file().exists())
        self.assertFalse(paths.pid_file().exists())

    def test_is_running_checks_the_pid_and_the_socket(self) -> None:
        self.assertFalse(daemon.is_running())
        paths.pid_file().write_text("nope")
        self.assertFalse(daemon.is_running())
        self.assertFalse(paths.pid_file().exists())

        paths.pid_file().write_text("42")
        self.kill.side_effect = OSError("gone")
        self.assertFalse(daemon.is_running())
        self.assertFalse(paths.pid_file().exists())
        self.kill.side_effect = None

        paths.pid_file().write_text("42")
        self.assertFalse(daemon.is_running())
        self.assertTrue(paths.pid_file().exists())
        paths.socket_file().write_bytes(b"")
        self.assertTrue(daemon.is_running())
        self.kill.assert_called_with(42, 0)

    def test_signal_handler_cleans_up_and_exits(self) -> None:
        previous_term = signal.getsignal(signal.SIGTERM)
        previous_int = signal.getsignal(signal.SIGINT)
        paths.pid_file().write_text("42")
        try:
            daemon.setup_signals()
            handler = signal.getsignal(signal.SIGTERM)
            self.assertIs(handler, signal.getsignal(signal.SIGINT))
            with self.assertRaises(SystemExit) as caught:
                handler(signal.SIGTERM, None)
        finally:
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)
        self.assertEqual(caught.exception.code, 0)
        self.assertFalse(daemon.running)
        self.assertFalse(paths.pid_file().exists())

    def test_load_keeps_cpu_when_that_device_was_requested(self) -> None:
        created, torch_mod, kokoro = self._models(cuda=True)
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"KOKORO_DEVICE": "cpu"}),
            mock.patch.dict(sys.modules, {"torch": torch_mod, "kokoro": kokoro}),
            mock.patch("sys.stderr", stderr),
        ):
            self.assertTrue(daemon.load())
            self.assertIs(sys.modules["torch"], torch_mod)
            self.assertIs(sys.modules["kokoro"], kokoro)
            self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "")
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
        self.assertEqual(created["args"], ("a", "cpu"))
        torch_mod.cuda.is_available.assert_not_called()
        self.assertEqual(paths.device_file().read_text(encoding="utf-8"), "cpu\n")
        text = stderr.getvalue()
        self.assertIn("Requested: cpu", text)
        self.assertIn("Device: cpu", text)
        self.assertIn("Loading model...", text)
        self.assertIn("Ready!", text)

    def test_load_uses_cuda_when_the_gpu_is_available(self) -> None:
        created, torch_mod, kokoro = self._models(cuda=True)
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"KOKORO_DEVICE": "gpu"}),
            mock.patch.dict(sys.modules, {"torch": torch_mod, "kokoro": kokoro}),
            mock.patch("sys.stderr", stderr),
        ):
            self.assertTrue(daemon.load())
            self.assertIs(sys.modules["torch"], torch_mod)
            self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "0")
        self.assertEqual(created["args"], ("a", "cuda"))
        torch_mod.cuda.is_available.assert_called_once()
        self.assertEqual(paths.device_file().read_text(encoding="utf-8"), "cuda\n")
        self.assertIn("Device: cuda", stderr.getvalue())
        self.assertIn("Requested: gpu", stderr.getvalue())

    def test_load_falls_back_to_cpu_when_cuda_is_missing(self) -> None:
        created, torch_mod, kokoro = self._models(cuda=False)
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"KOKORO_DEVICE": "gpu"}),
            mock.patch.dict(sys.modules, {"torch": torch_mod, "kokoro": kokoro}),
            mock.patch("sys.stderr", stderr),
        ):
            self.assertTrue(daemon.load())
            self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "0")
        self.assertEqual(created["args"], ("a", "cpu"))
        self.assertEqual(paths.device_file().read_text(encoding="utf-8"), "cpu\n")
        self.assertIn("Requested: gpu", stderr.getvalue())
        self.assertIn("Device: cpu", stderr.getvalue())

    def test_load_reports_a_model_error(self) -> None:
        _created, torch_mod, kokoro = self._models(fail="no weights")
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"KOKORO_DEVICE": "gpu"}),
            mock.patch.dict(sys.modules, {"torch": torch_mod, "kokoro": kokoro}),
            mock.patch("sys.stderr", stderr),
        ):
            self.assertFalse(daemon.load())
            self.assertIs(sys.modules["torch"], torch_mod)
        self.assertIsNone(daemon.pipeline)
        self.assertFalse(paths.device_file().exists())
        text = stderr.getvalue()
        self.assertIn("Error: no weights", text)
        self.assertIn("Traceback", text)

    def test_load_reports_an_import_failure(self) -> None:
        real_import = builtins.__import__

        def guarded(name, globals=None, locals=None, fromlist=(), level=0):
            if name in {"torch", "kokoro"}:
                raise ImportError("blocked")
            return real_import(name, globals, locals, fromlist, level)

        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"KOKORO_DEVICE": "cpu"}),
            mock.patch.dict(sys.modules),
            mock.patch("sys.stderr", stderr),
        ):
            sys.modules.pop("torch", None)
            sys.modules.pop("kokoro", None)
            with mock.patch("builtins.__import__", guarded):
                self.assertFalse(daemon.load())
                self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "")
                self.assertIsNone(sys.modules.get("torch"))
        text = stderr.getvalue()
        self.assertIn("Error: blocked", text)
        self.assertIn("Traceback", text)
        self.assertFalse(paths.device_file().exists())

    def test_generate_without_a_model_does_not_import_torch(self) -> None:
        before = sys.modules.get("torch")
        daemon.pipeline = None
        self.assertEqual(daemon.generate("x", "y", 1.0), (None, "No model"))
        self.assertIs(sys.modules.get("torch"), before)

    def test_generate_writes_a_wav(self) -> None:
        class Pipe:
            def __call__(self, text, voice, speed, split_pattern):
                self.args = (text, voice, speed, split_pattern)
                yield ("g", "p", 4)
                yield ("g", "p", 6)

        pipe = Pipe()
        daemon.pipeline = pipe
        written: dict = {}

        def write(path: str, audio: int, rate: int) -> None:
            written["path"] = path
            written["audio"] = audio
            written["rate"] = rate

        torch_mod = mock.Mock()
        sound = mock.Mock()
        sound.write.side_effect = write
        with mock.patch.dict(sys.modules, {"torch": torch_mod, "soundfile": sound}):
            path, elapsed = daemon.generate("hello", "emma", 1.25)
            self.assertIs(sys.modules["torch"], torch_mod)
            self.assertIs(sys.modules["soundfile"], sound)
        self.assertEqual(pipe.args, ("hello", "emma", 1.25, r"\n+"))
        self.assertEqual(written["audio"], 10)
        self.assertEqual(written["rate"], 24000)
        self.assertEqual(written["path"], path)
        self.assertTrue(str(path).startswith(str(self.root / "wavs")))
        self.assertTrue(Path(path).name.startswith("tts-voice-"))
        self.assertTrue(str(path).endswith(".wav"))
        self.assertIsInstance(elapsed, float)
        self.assertGreaterEqual(elapsed, 0)
        self.write_config({"kokoro": {"volume": 4}})
        with mock.patch.dict(sys.modules, {"torch": torch_mod, "soundfile": sound}):
            daemon.generate("hello", "emma", 1.25)
        self.assertEqual(written["audio"], 4.0)
        self.write_config({"kokoro": {"volume": 10}})
        with mock.patch.dict(sys.modules, {"torch": torch_mod, "soundfile": sound}):
            daemon.generate("hello", "emma", 1.25)
        self.assertEqual(written["audio"], 10)

    def test_kokoro_gain_uses_the_saved_level_or_full_scale(self) -> None:
        self.assertEqual(daemon.kokoro_gain(), 1.0)
        path = paths.config_path()
        path.unlink()
        self.assertEqual(daemon.kokoro_gain(), 1.0)
        self.write_raw_config("{")
        self.assertEqual(daemon.kokoro_gain(), 1.0)
        self.write_raw_config("[]")
        self.assertEqual(daemon.kokoro_gain(), 1.0)
        self.write_config({"kokoro": ["nope"]})
        self.assertEqual(daemon.kokoro_gain(), 1.0)
        self.write_config({"kokoro": {"device": "gpu"}})
        self.assertEqual(daemon.kokoro_gain(), 1.0)
        self.write_config({"kokoro": {"volume": 4}})
        self.assertEqual(daemon.kokoro_gain(), 0.4)
        path.unlink()
        path.mkdir()
        self.assertEqual(daemon.kokoro_gain(), 1.0)
        path.rmdir()

    def test_generate_reports_empty_audio(self) -> None:
        class Pipe:
            def __call__(self, text, voice, speed, split_pattern):
                yield from ()

        daemon.pipeline = Pipe()
        torch_mod = mock.Mock()
        sound = mock.Mock()
        with mock.patch.dict(sys.modules, {"torch": torch_mod, "soundfile": sound}):
            self.assertEqual(daemon.generate("hi", "v", 1.0), (None, "No audio"))
            self.assertIs(sys.modules["torch"], torch_mod)
        sound.write.assert_not_called()

    def test_generate_returns_the_synthesizer_error(self) -> None:
        class Pipe:
            def __call__(self, *args, **kwargs):
                raise RuntimeError("synth")

        daemon.pipeline = Pipe()
        torch_mod = mock.Mock()
        sound = mock.Mock()
        with mock.patch.dict(sys.modules, {"torch": torch_mod, "soundfile": sound}):
            self.assertEqual(daemon.generate("x", "v", 1.0), (None, "synth"))
            self.assertIs(sys.modules["torch"], torch_mod)

    def test_generate_reports_a_missing_audio_library(self) -> None:
        real_import = builtins.__import__

        def guarded(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "soundfile":
                raise ImportError("no soundfile")
            return real_import(name, globals, locals, fromlist, level)

        daemon.pipeline = object()
        torch_mod = mock.Mock()
        with mock.patch.dict(sys.modules, {"torch": torch_mod}):
            sys.modules.pop("soundfile", None)
            with mock.patch("builtins.__import__", guarded):
                result = daemon.generate("hi", "v", 1.0)
                self.assertIs(sys.modules["torch"], torch_mod)
        self.assertEqual(result, (None, "no soundfile"))

    def _run_worker(self, popen: mock.Mock) -> str:
        stderr = io.StringIO()
        with (
            mock.patch("tts.daemon.subprocess.Popen", popen),
            mock.patch("sys.stderr", stderr),
        ):
            daemon.playback_worker()
        return stderr.getvalue()

    def test_playback_worker_plays_and_deletes_the_file(self) -> None:
        audio = self.root / "wavs" / "line.wav"
        audio.write_bytes(b"RIFF")
        daemon.playback_queue.put(str(audio))
        daemon.playback_queue.put(None)
        popen = mock.Mock()
        popen.return_value = mock.Mock()
        text = self._run_worker(popen)
        command = f'aplay "{audio}" 2>/dev/null || paplay "{audio}" 2>/dev/null'
        popen.assert_called_once_with(
            ["bash", "-c", command],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        popen.return_value.wait.assert_called_once()
        self.assertFalse(audio.exists())
        self.assertIn(f"Cleaned up: {audio}", text)

    def test_playback_worker_deletes_the_file_when_playback_fails(self) -> None:
        audio = self.root / "wavs" / "line.wav"
        audio.write_bytes(b"RIFF")
        daemon.playback_queue.put(str(audio))
        daemon.playback_queue.put(None)
        popen = mock.Mock()
        popen.side_effect = RuntimeError("no audio device")
        text = self._run_worker(popen)
        self.assertIn("Playback error: no audio device", text)
        self.assertIn(f"Cleaned up: {audio}", text)
        self.assertFalse(audio.exists())

    def test_playback_worker_reports_a_delete_failure(self) -> None:
        blocked = self.root / "wavs" / "blocked"
        blocked.mkdir()
        daemon.playback_queue.put(str(blocked))
        daemon.playback_queue.put(None)
        popen = mock.Mock()
        popen.return_value = mock.Mock()
        text = self._run_worker(popen)
        self.assertIn(f"Failed to delete {blocked}", text)
        self.assertTrue(blocked.is_dir())

    def test_playback_worker_skips_a_missing_file(self) -> None:
        missing = self.root / "wavs" / "gone.wav"
        daemon.playback_queue.put(str(missing))
        daemon.playback_queue.put(None)
        popen = mock.Mock()
        popen.return_value = mock.Mock()
        text = self._run_worker(popen)
        popen.assert_called_once()
        self.assertNotIn("Cleaned up", text)
        self.assertNotIn("Failed to delete", text)

    def test_playback_worker_handles_an_empty_queue_and_a_worker_error(self) -> None:
        calls: list[float] = []

        def get(timeout: float = 0.5):
            calls.append(timeout)
            if len(calls) > 4:
                daemon.running = False
                return None
            if len(calls) == 1:
                raise queue.Empty
            if len(calls) == 2:
                raise RuntimeError("queue broke")
            return None

        stderr = io.StringIO()
        with (
            mock.patch.object(daemon.playback_queue, "get", get),
            mock.patch("sys.stderr", stderr),
        ):
            daemon.playback_worker()
        self.assertEqual(calls[0], 0.5)
        self.assertIn("Playback worker error: queue broke", stderr.getvalue())

    def test_playback_worker_stops_when_shut_down(self) -> None:
        daemon.playback_queue.put(None)
        popen = mock.Mock()
        self._run_worker(popen)
        popen.assert_not_called()

    def test_playback_worker_returns_when_stopped(self) -> None:
        daemon.running = False

        def get(timeout: float = 0.5):
            raise AssertionError(f"waited {timeout}s while stopped")

        with mock.patch.object(daemon.playback_queue, "get", get):
            daemon.playback_worker()

    def test_start_playback_worker_starts_one_living_thread(self) -> None:
        created: list[FakeThread] = []

        class FakeThread:
            def __init__(self, target, daemon: bool) -> None:
                self.target = target
                self.daemon = daemon
                self.alive = True
                self.started = False
                created.append(self)

            def start(self) -> None:
                self.started = True

            def is_alive(self) -> bool:
                return self.alive

        with mock.patch("tts.daemon.threading.Thread", FakeThread):
            daemon.playback_thread = None
            daemon.start_playback_worker()
            daemon.start_playback_worker()
            created[0].alive = False
            daemon.start_playback_worker()
        self.assertEqual(len(created), 2)
        self.assertTrue(created[0].started)
        self.assertTrue(created[1].started)
        self.assertTrue(created[0].daemon)
        self.assertIs(created[0].target, daemon.playback_worker)
        self.assertIs(daemon.playback_thread, created[1])

    def test_play_queues_the_path(self) -> None:
        daemon.play("line.wav")
        self.assertEqual(daemon.playback_queue.get_nowait(), "line.wav")

    def test_handle_replies_and_queues_playback(self) -> None:
        seen: dict = {}

        def generate(text, voice, speed):
            seen["args"] = (text, voice, speed)
            return str(self.root / "wavs" / "out.wav"), 1.5

        conn = FakeConn(
            _frame({"text": "hi", "voice": "emma", "speed": 1.25}),
            body_slice=1,
        )
        with mock.patch.object(daemon, "generate", generate):
            daemon.handle(conn)
        self.assertTrue(conn.closed)
        self.assertGreater(conn.reads, 2)
        self.assertEqual(seen["args"], ("hi", "emma", 1.25))
        self.assertEqual(
            daemon.playback_queue.get_nowait(),
            str(self.root / "wavs" / "out.wav"),
        )
        self.assertEqual(
            _reply(conn.sent),
            {
                "success": True,
                "file": str(self.root / "wavs" / "out.wav"),
                "time": 1.5,
                "error": None,
            },
        )

    def test_handle_uses_defaults_when_synthesis_fails(self) -> None:
        seen: dict = {}

        def generate(text, voice, speed):
            seen["args"] = (text, voice, speed)
            return None, "No model"

        conn = FakeConn(_frame({}))
        with mock.patch.object(daemon, "generate", generate):
            daemon.handle(conn)
        self.assertTrue(conn.closed)
        self.assertEqual(
            seen["args"], ("Hello", daemon.DEFAULT_VOICE, daemon.DEFAULT_SPEED)
        )
        self.assertTrue(daemon.playback_queue.empty())
        self.assertEqual(
            _reply(conn.sent),
            {
                "success": False,
                "file": None,
                "time": "No model",
                "error": "No model",
            },
        )

    def test_handle_closes_on_a_bad_request(self) -> None:
        empty = FakeConn(b"", empty_on_read=1)
        daemon.handle(empty)
        self.assertTrue(empty.closed)
        self.assertEqual(empty.sent, b"")

        mid = FakeConn(_frame({"text": "hi"}), empty_on_read=2)
        daemon.handle(mid)
        self.assertTrue(mid.closed)
        self.assertEqual(mid.sent, b"")

        short = FakeConn(b"ab")
        daemon.handle(short)
        self.assertTrue(short.closed)
        self.assertEqual(short.sent, b"")

        zero = FakeConn(struct.pack("!I", 0))
        daemon.handle(zero)
        self.assertTrue(zero.closed)
        self.assertEqual(zero.sent, b"")

        body = b"not-json"
        bad = FakeConn(struct.pack("!I", len(body)) + body)
        daemon.handle(bad)
        self.assertTrue(bad.closed)
        self.assertEqual(bad.sent, b"")

        listed = FakeConn(_frame([1]))
        daemon.handle(listed)
        self.assertTrue(listed.closed)
        self.assertEqual(listed.sent, b"")

    def test_run_exits_when_the_daemon_is_already_up(self) -> None:
        paths.pid_file().write_text("42")
        paths.socket_file().write_bytes(b"x")
        stderr = io.StringIO()
        with (
            mock.patch.object(daemon, "load") as load,
            mock.patch("sys.stderr", stderr),
            self.assertRaises(SystemExit) as caught,
        ):
            daemon.run()
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("Already running", stderr.getvalue())
        load.assert_not_called()
        self.kill.assert_called_once_with(42, 0)

    def test_run_cleans_up_when_the_model_fails_to_load(self) -> None:
        with (
            mock.patch.object(daemon, "load", return_value=False),
            mock.patch.object(daemon, "setup_signals") as sigs,
            mock.patch.object(daemon, "start_playback_worker") as start,
            self.assertRaises(SystemExit) as caught,
        ):
            daemon.run()
        self.assertEqual(caught.exception.code, 1)
        sigs.assert_called_once()
        start.assert_not_called()
        self.assertFalse(paths.pid_file().exists())

    def test_run_serves_until_it_stops(self) -> None:
        paths.socket_file().write_text("stale")
        conn = FakeConn(b"")

        class Srv:
            def __init__(self) -> None:
                self.accepts = 0
                self.closed = False
                self.stale_gone = False
                self.pid = ""
                self.family = None
                self.kind = None

            def bind(self, path: str) -> None:
                self.bound = path
                self.stale_gone = not paths.socket_file().exists()
                self.pid = paths.pid_file().read_text(encoding="utf-8")

            def listen(self, backlog: int) -> None:
                self.backlog = backlog

            def settimeout(self, seconds: float) -> None:
                self.timeout = seconds

            def accept(self):
                self.accepts += 1
                if self.accepts == 1:
                    return (conn, None)
                daemon.running = False
                raise TimeoutError

            def close(self) -> None:
                self.closed = True

        srv = Srv()

        def factory(family, kind):
            srv.family = family
            srv.kind = kind
            return srv

        stderr = io.StringIO()
        with (
            mock.patch.object(daemon, "load", return_value=True) as load,
            mock.patch.object(daemon, "setup_signals") as sigs,
            mock.patch.object(daemon, "start_playback_worker") as start,
            mock.patch("tts.daemon.socket.socket", side_effect=factory),
            mock.patch("sys.stderr", stderr),
        ):
            daemon.run()
        self.assertEqual(srv.family, socket.AF_UNIX)
        self.assertEqual(srv.kind, socket.SOCK_STREAM)
        self.assertEqual(srv.backlog, 5)
        self.assertEqual(srv.timeout, 1.0)
        self.assertTrue(srv.stale_gone)
        self.assertEqual(srv.pid, "42")
        self.assertEqual(srv.accepts, 2)
        self.assertTrue(srv.closed)
        self.assertTrue(conn.closed)
        load.assert_called_once()
        sigs.assert_called_once()
        start.assert_called_once()
        text = stderr.getvalue()
        self.assertIn("Playback worker started", text)
        self.assertIn("Listening", text)
        self.assertFalse(paths.pid_file().exists())

    def test_run_closes_when_accept_fails(self) -> None:
        class Srv:
            def __init__(self) -> None:
                self.closed = False

            def bind(self, path: str) -> None:
                self.bound = path

            def listen(self, backlog: int) -> None:
                self.backlog = backlog

            def settimeout(self, seconds: float) -> None:
                self.timeout = seconds

            def accept(self):
                raise OSError("dead")

            def close(self) -> None:
                self.closed = True

        srv = Srv()
        stderr = io.StringIO()
        with (
            mock.patch.object(daemon, "load", return_value=True),
            mock.patch.object(daemon, "setup_signals"),
            mock.patch.object(daemon, "start_playback_worker"),
            mock.patch("tts.daemon.socket.socket", return_value=srv),
            mock.patch("sys.stderr", stderr),
        ):
            daemon.run()
        self.assertTrue(srv.closed)
        self.assertIn("Listening", stderr.getvalue())
        self.assertFalse(paths.pid_file().exists())
        self.assertFalse(paths.socket_file().exists())

    def test_run_reports_a_bind_failure(self) -> None:
        class Srv:
            def bind(self, path: str) -> None:
                raise RuntimeError("no bind")

            def close(self) -> None:
                raise AssertionError("closed a socket that never listened")

        stderr = io.StringIO()
        with (
            mock.patch.object(daemon, "load", return_value=True),
            mock.patch.object(daemon, "setup_signals"),
            mock.patch.object(daemon, "start_playback_worker") as start,
            mock.patch("tts.daemon.socket.socket", return_value=Srv()),
            mock.patch("sys.stderr", stderr),
        ):
            daemon.run()
        start.assert_called_once()
        self.assertIn("Error: no bind", stderr.getvalue())
        self.assertFalse(paths.pid_file().exists())

    def test_stop_signals_the_recorded_pid(self) -> None:
        paths.pid_file().write_text("42\n")
        paths.device_file().write_text("cuda\n")
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            daemon.stop()
        self.kill.assert_called_once_with(42, signal.SIGTERM)
        self.assertEqual(stdout.getvalue().strip(), "Stopped")
        self.assertEqual(paths.pid_file().read_text(encoding="utf-8"), "42\n")
        self.assertTrue(paths.device_file().exists())

    def test_stop_reports_when_nothing_is_running(self) -> None:
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            daemon.stop()
        self.assertEqual(stdout.getvalue().strip(), "Not running")
        self.kill.assert_not_called()

    def test_stop_cleans_up_when_the_pid_cannot_be_signaled(self) -> None:
        paths.pid_file().write_text("nope")
        paths.socket_file().write_text("x")
        daemon.stop()
        self.kill.assert_not_called()
        self.assertFalse(paths.pid_file().exists())
        self.assertFalse(paths.socket_file().exists())

        paths.pid_file().write_text("7")
        self.kill.side_effect = OSError("gone")
        daemon.stop()
        self.kill.assert_called_once_with(7, signal.SIGTERM)
        self.assertFalse(paths.pid_file().exists())

    def test_status_reports_the_running_pid(self) -> None:
        paths.pid_file().write_text("42\n")
        paths.socket_file().write_bytes(b"")
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            daemon.status()
        self.assertEqual(stdout.getvalue().strip(), "Running (PID 42)")
        self.kill.assert_called_with(42, 0)

    def test_status_reports_when_the_daemon_is_down(self) -> None:
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            daemon.status()
        self.assertEqual(stdout.getvalue().strip(), "Not running")

        paths.pid_file().write_text("42")
        self.kill.side_effect = OSError("dead")
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            daemon.status()
        self.assertEqual(stdout.getvalue().strip(), "Not running")
        self.assertFalse(paths.pid_file().exists())

    def test_main_dispatches_every_command(self) -> None:
        with (
            mock.patch.object(daemon, "run") as run,
            mock.patch.object(daemon, "stop") as stop,
            mock.patch.object(daemon, "status") as status,
        ):
            with mock.patch("sys.argv", ["tts-daemon"]):
                daemon.main()
            with mock.patch("sys.argv", ["tts-daemon", "start"]):
                daemon.main()
            self.assertEqual(run.call_count, 2)
            with mock.patch("sys.argv", ["tts-daemon", "stop"]):
                daemon.main()
            stop.assert_called_once()
            with mock.patch("sys.argv", ["tts-daemon", "status"]):
                daemon.main()
            status.assert_called_once()
            stdout = io.StringIO()
            with (
                mock.patch("sys.argv", ["tts-daemon", "frob"]),
                mock.patch("sys.stdout", stdout),
                self.assertRaises(SystemExit) as caught,
            ):
                daemon.main()
            self.assertEqual(caught.exception.code, 1)
            self.assertEqual(stdout.getvalue().strip(), "Unknown: frob")
        self.assertEqual(warnings.filters[0][0], "ignore")

    def test_module_guard(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch("sys.argv", ["tts-daemon.py", "status"]),
            mock.patch("sys.stdout", stdout),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            runpy.run_module("tts.daemon", run_name="__main__")
        self.assertIn("Not running", stdout.getvalue())

        stdout = io.StringIO()
        with (
            mock.patch("sys.argv", ["tts-daemon.py", "bogus"]),
            mock.patch("sys.stdout", stdout),
            mock.patch("sys.stderr", io.StringIO()),
            self.assertRaises(SystemExit) as caught,
        ):
            runpy.run_module("tts.daemon", run_name="__main__")
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("Unknown: bogus", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
