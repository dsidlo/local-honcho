"""Speak a finished Grok turn.

The engine comes from config.json. "kokoro" (the default) uses the Pi Kokoro
daemon. "piper" uses a local Piper voice. SessionStart warms Kokoro only when
that engine is selected. Stop speaks lastAssistantMessage when the turn
actually ended. The hook returns immediately; playback happens in the child.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys

from tts import paths

# tts_guard binds its log path at import time, so the runtime state
# location must be chosen before the scrub modules load.
os.environ.setdefault("TTS_GUARD_LOG", str(paths.tts_dir() / "guard.log"))
from tts import tts_guard, tts_piper_normalize, tts_sanitize  # noqa: E402
from tts.volume import PIPER_DEFAULT as DEFAULT_PIPER_VOLUME
from tts.volume import level, multiplier

DEFAULT_PIPER_VOICE = "en_GB-jenny_dioco-medium"
ENGINES = ("kokoro", "piper")

THINKING = re.compile(r"<thinking>[\s\S]*?</thinking>", re.IGNORECASE)
FENCE = re.compile(r"```[\s\S]*?```")
INLINE = re.compile(r"`([^`]*)`")
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
URL = re.compile(r"https?://\S+")
HEADING = re.compile(r"^#{1,6}\s*", re.MULTILINE)
HTML = re.compile(r"<[^>]+>")
BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
NUMBERED = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
EMPHASIS = re.compile(r"[*_]{1,3}")
CLIP = re.compile(r"\s*… \[\+\d+ chars\]\s*$")
SPACE = re.compile(r"\s+")


def load_config() -> dict:
    """Return the speech config, with defaults for anything unreadable."""
    cfg = {
        "enabled": True,
        "maxTextLength": 500,
        "engine": "kokoro",
        "voice": "",
        "speed": "",
        "piper": {
            "voice": DEFAULT_PIPER_VOICE,
            "lengthScale": 1.0,
            "volume": DEFAULT_PIPER_VOLUME,
        },
    }
    try:
        stored = json.loads(paths.config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return cfg
    if not isinstance(stored, dict):
        return cfg
    for key in ("enabled", "maxTextLength", "voice", "speed"):
        if key in stored:
            cfg[key] = stored[key]
    try:
        cfg["maxTextLength"] = max(40, int(cfg["maxTextLength"]))
    except (TypeError, ValueError):
        cfg["maxTextLength"] = 500
    cfg["enabled"] = bool(cfg["enabled"])
    cfg["voice"] = str(cfg.get("voice") or "")
    cfg["speed"] = str(cfg.get("speed") or "")
    engine = str(stored.get("engine") or "kokoro").strip().lower()
    cfg["engine"] = engine if engine in ENGINES else "kokoro"
    piper = stored.get("piper")
    if isinstance(piper, dict):
        voice = str(piper.get("voice") or "").strip()
        if voice:
            cfg["piper"]["voice"] = voice
        if "lengthScale" in piper:
            try:
                scale = float(piper["lengthScale"])
            except (TypeError, ValueError):
                scale = 1.0
            if scale > 0:
                cfg["piper"]["lengthScale"] = scale
        if "volume" in piper:
            cfg["piper"]["volume"] = level(piper["volume"], DEFAULT_PIPER_VOLUME)
    return cfg


def log(line: str) -> None:
    """Append one line to the speech log."""
    try:
        paths.tts_dir().mkdir(parents=True, exist_ok=True)
        with paths.log_path().open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip() + "\n")
    except OSError:
        pass


def prepare_text(text: str, limit: int) -> str:
    """Turn a model reply into a short sentence for speech."""
    if not text:
        return ""
    text = CLIP.sub("", text)
    text = THINKING.sub(" ", text)
    text = FENCE.sub(" ", text)
    text = IMAGE.sub(" ", text)
    text = LINK.sub(r"\1", text)
    text = INLINE.sub(r"\1", text)
    text = URL.sub(" ", text)
    text = HEADING.sub("", text)
    text = HTML.sub(" ", text)
    text = BULLET.sub("", text)
    text = NUMBERED.sub("", text)
    text = EMPHASIS.sub("", text)
    text = SPACE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(".,;:")
    if len(cut) < limit // 2:
        cut = text[:limit].rstrip(".,;:")
    return cut + "..."


def daemon_running() -> bool:
    """Return whether the Kokoro daemon pid and socket are live."""
    if not paths.pid_file().is_file() or not paths.socket_file().is_socket():
        return False
    try:
        pid = int(paths.pid_file().read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def prewarm() -> None:
    """Start the Kokoro daemon when it is not already running."""
    if daemon_running():
        log("prewarm already-running")
        return
    if not paths.daemon_script().is_file() or not paths.daemon_python().is_file():
        log("prewarm missing-daemon")
        return
    for stale in (paths.pid_file(), paths.socket_file()):
        with contextlib.suppress(OSError):
            stale.unlink()
    subprocess.Popen(
        [str(paths.daemon_python()), str(paths.daemon_script())],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    log("prewarm started")


def already_spoken(session_id: str, text: str) -> bool:
    """Return whether this session already spoke ``text``."""
    if not session_id:
        return False
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:120]
    path = paths.state_dir() / safe
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        if path.is_file() and path.read_text(encoding="utf-8").strip() == digest:
            return True
        paths.state_dir().mkdir(parents=True, exist_ok=True)
        path.write_text(digest + "\n", encoding="utf-8")
    except OSError:
        return False
    return False


def kokoro_command(text: str, cfg: dict) -> list[str]:
    """Build the command that sends one line to Kokoro."""
    command = ["bash", str(paths.speak_sh()), text]
    if cfg["voice"]:
        command.append(cfg["voice"])
        command.append(cfg["speed"] or "1.6")
    elif cfg["speed"]:
        command.extend(["", cfg["speed"]])
    return command


def piper_command(text: str, cfg: dict) -> list[str]:
    """Build the command that speaks one line with Piper."""
    piper = cfg.get("piper") or {}
    voice = str(piper.get("voice") or DEFAULT_PIPER_VOICE)
    try:
        scale = float(piper.get("lengthScale", 1.0))
    except (TypeError, ValueError):
        scale = 1.0
    if scale <= 0:
        scale = 1.0
    gain = multiplier(piper.get("volume", DEFAULT_PIPER_VOLUME), DEFAULT_PIPER_VOLUME)
    return [
        str(paths.piper_python()),
        str(paths.piper_script()),
        "--voice",
        voice,
        "--length-scale",
        str(scale),
        "--volume",
        str(gain),
        "--",
        text,
    ]


def scrub_for_piper(text: str) -> str:
    """Run the pi-mono voice-response scrub chain over Piper-bound text.

    Same modules pi-mono's speak.sh runs before its engines (verbatim
    copies of tts_sanitize.py, tts_guard.py, tts_piper_normalize.py):
    sanitize strips symbol noise, guard risk-scores and rewrites
    engine-hostile fragments (paths, KEY=VALUE flags, hash tokens,
    hyphen compounds espeak would fuse - "pi-mono" becomes "pi mono")
    and digests or drops the rest, normalize fixes espeak terminations
    and units. The chain never raises; guard.log records every decision.
    """

    chars_in = len(text)
    text = tts_sanitize.sanitize(text)
    text, report = tts_guard.guard(text)
    normalized = tts_piper_normalize.piper_normalize(text)
    if normalized.strip():
        text = normalized
    log(
        f"scrub piper chars_in={chars_in} chars_out={len(text)} "
        f"guard_mode={report.get('mode', '?')}"
    )
    return text


def speak(text: str, cfg: dict) -> None:
    """Start playback in a child process and return."""
    if cfg.get("engine") == "piper":
        if not paths.piper_python().is_file() or not paths.piper_script().is_file():
            log("speak piper missing")
            return
        text = scrub_for_piper(text)
        if len(text.strip()) < 2:
            log("speak piper nothing-to-say")
            return
        command = piper_command(text, cfg)
        label = f"speak piper queued chars={len(text)}"
    else:
        if not paths.speak_sh().is_file():
            log("speak missing-script")
            return
        command = kokoro_command(text, cfg)
        label = f"speak queued chars={len(text)}"
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    log(label)


def handle(event: dict, cfg: dict, dry_run: bool = False) -> str | None:
    """Speak one hook event. A dry run returns the text instead."""
    name = event.get("hook_event_name") or ""
    camel = event.get("hookEventName") or ""
    if name == "SessionStart" or camel == "session_start":
        if cfg["enabled"] and not dry_run:
            if cfg.get("engine", "kokoro") == "kokoro":
                prewarm()
            else:
                log(f"prewarm skipped engine={cfg.get('engine')}")
        return None
    if name not in ("Stop", "") and camel not in ("stop", ""):
        return None
    if name and name != "Stop" and camel != "stop":
        return None
    if event.get("subagentType"):
        return None
    reason = event.get("reason") or event.get("stopReason") or ""
    if reason != "end_turn":
        return None
    spoken = prepare_text(
        event.get("lastAssistantMessage") or "", cfg["maxTextLength"]
    )
    if len(spoken) < 2:
        return None
    if already_spoken(str(event.get("sessionId") or ""), spoken):
        log("speak skipped duplicate")
        return None
    if dry_run:
        return spoken
    if cfg["enabled"]:
        speak(spoken, cfg)
    else:
        log("speak skipped disabled")
    return None


def self_test() -> None:
    """Check text cleanup, dedupe, and the two speaker commands."""
    sample = prepare_text(
        "<thinking>hidden</thinking>\n# Title\nSee [docs](https://example.com).\n"
        "Use `rg` here.\n```\ncode\n```\nhttps://example.com/x\n- item one\n"
        "1. item two",
        500,
    )
    assert "hidden" not in sample
    assert "Title" in sample
    assert "docs" in sample
    assert "rg" in sample
    assert "code" not in sample
    assert "example.com" not in sample
    assert "item one" in sample and "item two" in sample
    long = prepare_text("word " * 200, 80)
    assert long.endswith("...") and len(long) <= 83
    assert prepare_text("```\nonly code\n```", 500) == ""
    assert prepare_text("done … [+120 chars]", 500) == "done"

    state = paths.state_dir() / "self-test-session"
    with contextlib.suppress(OSError):
        state.unlink()
    payload = {
        "hook_event_name": "Stop",
        "reason": "end_turn",
        "sessionId": "self-test-session",
        "lastAssistantMessage": "Hello **there**.",
    }
    cfg = {"enabled": True, "maxTextLength": 500, "voice": "", "speed": ""}
    spoken = handle(payload, cfg, dry_run=True)
    assert spoken == "Hello there."
    again = handle(
        {
            "hook_event_name": "Stop",
            "reason": "end_turn",
            "sessionId": "self-test-session",
            "lastAssistantMessage": "Hello **there**.",
        },
        cfg,
        dry_run=True,
    )
    assert again is None
    skipped = handle(
        {
            "hook_event_name": "Stop",
            "reason": "shutdown",
            "sessionId": "self-test-other",
            "lastAssistantMessage": "Do not speak this.",
        },
        {"enabled": True, "maxTextLength": 500, "voice": "", "speed": ""},
        dry_run=True,
    )
    assert skipped is None
    with contextlib.suppress(OSError):
        state.unlink()

    kokoro_cfg = {
        "enabled": True,
        "maxTextLength": 500,
        "engine": "kokoro",
        "voice": "bf_emma",
        "speed": "1.25",
        "piper": {
            "voice": DEFAULT_PIPER_VOICE,
            "lengthScale": 1.0,
            "volume": DEFAULT_PIPER_VOLUME,
        },
    }
    kokoro = kokoro_command("Hello there.", kokoro_cfg)
    assert kokoro[:3] == ["bash", str(paths.speak_sh()), "Hello there."]
    assert kokoro[3:] == ["bf_emma", "1.25"]
    piper_cfg = dict(kokoro_cfg)
    piper_cfg["engine"] = "piper"
    piper_cfg["piper"] = {"voice": "en_GB-alba-medium", "lengthScale": 0.9}
    built = piper_command("Hello there.", piper_cfg)
    assert built[0] == str(paths.piper_python())
    assert built[1] == str(paths.piper_script())
    assert built[built.index("--voice") + 1] == "en_GB-alba-medium"
    assert built[built.index("--length-scale") + 1] == "0.9"
    assert built[built.index("--volume") + 1] == "0.8"
    assert built[-1] == "Hello there."
    scrubbed = scrub_for_piper("**pi-mono** now! 1f9472f.")
    assert "pi mono" in scrubbed and "pi-mono" not in scrubbed
    assert "1f9472f" not in scrubbed
    assert scrub_for_piper("done at 20C").endswith("degrees Celsius.")
    assert scrub_for_piper("```code```") == "code omitted."
    kept = load_config()
    assert kept["engine"] in ENGINES
    print("self-test ok")


def main() -> None:
    """Read one hook event from stdin and speak when it is a finished turn."""
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        self_test()
        return
    raw = sys.stdin.read()
    if not raw.strip():
        return
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        log("bad json")
        return
    if not isinstance(event, dict):
        return
    handle(event, load_config(), dry_run=os.environ.get("GROK_TTS_DRY_RUN") == "1")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"error {exc}")
        sys.exit(0)
