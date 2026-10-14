"""Locations Grok TTS reads and writes.

Each function reads its environment variable when it is called. Unset
variables mean the live Grok and Pi paths. Tests point them at a temporary
directory so the running Kokoro daemon is left alone.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    return Path(value)


def tts_dir() -> Path:
    """Return the Grok TTS directory."""
    return _env_path("GROK_TTS_DIR") or (Path.home() / ".grok" / "tts")


def config_path() -> Path:
    """Return the TTS config file."""
    return _env_path("GROK_TTS_CONFIG") or (tts_dir() / "config.json")


def log_path() -> Path:
    """Return the speech log."""
    return _env_path("GROK_TTS_LOG") or (tts_dir() / "speak.log")


def state_dir() -> Path:
    """Return the directory that remembers spoken turns."""
    return _env_path("GROK_TTS_STATE_DIR") or (tts_dir() / "state")


def pid_file() -> Path:
    """Return the Kokoro daemon pid file."""
    return _env_path("GROK_TTS_PID_FILE") or Path("/tmp/pi-tts-server.pid")


def socket_file() -> Path:
    """Return the Kokoro daemon socket."""
    return _env_path("GROK_TTS_SOCKET_FILE") or Path("/tmp/pi-tts-server.sock")


def device_file() -> Path:
    """Return the file that records the daemon's live device."""
    return _env_path("GROK_TTS_DEVICE_FILE") or Path("/tmp/pi-tts-server.device")


def wav_dir() -> Path:
    """Return the directory where the Kokoro daemon writes utterance files."""
    return _env_path("GROK_TTS_WAV_DIR") or Path("/tmp")


def daemon_log() -> Path:
    """Return the Kokoro daemon log."""
    return _env_path("GROK_TTS_DAEMON_LOG") or Path("/tmp/kokoro-daemon.log")


def daemon_script() -> Path:
    """Return the Pi Kokoro launcher. The implementation is ``tts.daemon``."""
    return _env_path("GROK_TTS_DAEMON") or (
        Path.home()
        / ".pi"
        / "agent"
        / "skills"
        / "voice-response"
        / "scripts"
        / "tts-daemon.py"
    )


def daemon_python() -> Path:
    """Return the interpreter that can import Kokoro."""
    return _env_path("GROK_TTS_DAEMON_PYTHON") or (
        Path.home() / ".pi" / ".venv" / "bin" / "python3"
    )


def speak_sh() -> Path:
    """Return the Pi script that sends one line to the Kokoro daemon."""
    return _env_path("GROK_TTS_SPEAK_SH") or (
        Path.home()
        / ".pi"
        / "agent"
        / "skills"
        / "voice-response"
        / "scripts"
        / "speak.sh"
    )


def piper_python() -> Path:
    """Return the interpreter that has Piper installed."""
    return _env_path("GROK_TTS_PIPER_PYTHON") or (
        tts_dir() / "piper" / ".venv" / "bin" / "python3"
    )


def piper_script() -> Path:
    """Return the Piper speaker. The Piper venv runs this file by path."""
    return _env_path("GROK_TTS_PIPER_SPEAK") or (
        Path(__file__).resolve().parent / "piper.py"
    )


def piper_root() -> Path:
    """Return the directory that holds Piper voices and the speak lock."""
    return _env_path("GROK_TTS_PIPER_ROOT") or (tts_dir() / "piper")


def piper_voices() -> Path:
    """Return the Piper voice directory."""
    return _env_path("GROK_TTS_PIPER_VOICES") or (piper_root() / "voices")


def piper_lock() -> Path:
    """Return the lock that keeps Piper turns from overlapping."""
    return _env_path("GROK_TTS_PIPER_LOCK") or (piper_root() / "speak.lock")


def toggle_script() -> Path:
    """Return the toggle command used by the slash-command hook."""
    return _env_path("GROK_TTS_TOGGLE") or (tts_dir() / "toggle")
