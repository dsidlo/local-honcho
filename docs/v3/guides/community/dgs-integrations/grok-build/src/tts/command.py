"""Apply /tts slash commands and keep them out of the model prompt.

UserPromptSubmit can block a prompt. The block reason is shown in the
session and is not sent to the model. Any other prompt is left untouched.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

from tts import paths

_LIMIT = 2000

_SLASH = re.compile(
    r"^/(?:[A-Za-z0-9_-]+:)?tts"
    r"(?:"
    r"-(?P<tail>volume)(?P<rest>(?:[ \t]+\S+)*)"
    r"|-(?P<named>kokoro|piper|cpu|gpu|status)"
    r"|[ \t]+(?P<volcmd>volume)(?P<rest2>(?:[ \t]+\S+)*)"
    r"|[ \t]+(?P<kind>engine|device)[ \t]+(?P<value>kokoro|piper|cpu|gpu|cuda)"
    r")?"
    r"[ \t]*$",
    re.IGNORECASE,
)
_MARKER = re.compile(
    r"^tts-control:\s*(?P<body>status"
    r"|engine[ \t]+(?:kokoro|piper)"
    r"|device[ \t]+(?:cpu|gpu|cuda)"
    r"|volume(?:[ \t]+\S+)*)$",
    re.IGNORECASE,
)

_PIPER_GPU = (
    "Piper runs on the CPU only. The GPU was not selected. "
    "Switch to Kokoro with /tts-kokoro, then run /tts-gpu."
)
_PIPER_CPU = "Piper runs on the CPU only. This engine has no device to change."
_USAGE = (
    "Usage: /tts, /tts-kokoro, /tts-piper, /tts-cpu, /tts-gpu, "
    "or /tts-volume [1-10]."
)
_VOLUME_USAGE = (
    "Usage: /tts-volume [1-10]. 1 is quietest and 10 is full. "
    "The number changes the engine that is selected now."
)


def event_name(event: dict) -> str:
    """Return the hook event name with underscores removed."""
    raw = event.get("hook_event_name") or event.get("hookEventName") or ""
    return str(raw).replace("_", "").lower()


def prompt_text(event: dict) -> str | None:
    """Return the prompt string from either field name."""
    prompt = event.get("prompt")
    if isinstance(prompt, str):
        return prompt
    user_prompt = event.get("userPrompt")
    if isinstance(user_prompt, str):
        return user_prompt
    return None


def _action_from_body(body: str) -> tuple[str, str] | None:
    parts = body.lower().split()
    if parts == ["status"]:
        return ("status", "")
    if parts and parts[0] == "volume":
        if len(parts) == 1:
            return ("volume", "")
        if len(parts) == 2:
            return ("volume", _volume_text(parts[1]))
        return ("volume", "invalid")
    if len(parts) == 2 and parts[0] in ("engine", "device"):
        value = "gpu" if parts[1] == "cuda" else parts[1]
        return (parts[0], value)
    return None


def _volume_text(value: str) -> str:
    """Return a volume argument, treating an unsubstituted placeholder as empty."""
    text = value.strip()
    if text.lower() == "$arguments":
        return ""
    return text


def control_action(prompt: str) -> tuple[str, str] | None:
    """Return ``(kind, value)`` when ``prompt`` is a TTS command."""
    stripped = prompt.strip()
    match = _SLASH.match(stripped)
    if match:
        if match.group("tail") or match.group("volcmd"):
            raw = match.group("rest") or match.group("rest2") or ""
            return ("volume", _volume_text(raw))
        named = match.group("named")
        if named:
            named = named.lower()
            if named == "status":
                return ("status", "")
            if named in ("kokoro", "piper"):
                return ("engine", named)
            return ("device", named)
        kind = match.group("kind")
        if not kind:
            return ("status", "")
        value = match.group("value").lower()
        if value == "cuda":
            value = "gpu"
        return (kind.lower(), value)
    if len(stripped) > _LIMIT:
        return None
    found = None
    for line in stripped.splitlines():
        marker = _MARKER.match(line.strip())
        if marker:
            found = _action_from_body(marker.group("body"))
    return found


def current_engine() -> str:
    """Return the selected engine. Anything unreadable means Kokoro."""
    try:
        data = json.loads(paths.config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "kokoro"
    if not isinstance(data, dict):
        return "kokoro"
    engine = str(data.get("engine") or "kokoro").strip().lower()
    if engine in ("kokoro", "piper"):
        return engine
    return "kokoro"


def plan(action: tuple[str, str], engine: str) -> tuple[str, list[str] | str]:
    """Return ``("toggle", args)`` or ``("message", text)``."""
    kind, value = action
    if kind == "status":
        return ("toggle", ["status"])
    if kind == "volume":
        text = _volume_text(value)
        if not text:
            return ("toggle", ["volume"])
        if text.isdigit() and 1 <= int(text) <= 10:
            return ("toggle", ["volume", str(int(text))])
        return ("message", _VOLUME_USAGE)
    if kind == "engine" and value in ("kokoro", "piper"):
        return ("toggle", ["engine", value])
    if kind == "device" and value in ("cpu", "gpu"):
        if engine == "piper":
            text = _PIPER_GPU if value == "gpu" else _PIPER_CPU
            return ("message", text)
        return ("toggle", ["device", value])
    return ("message", _USAGE)


def run_toggle(args: list[str]) -> str:
    """Run the toggle command and return the text to show the user."""
    proc = subprocess.run(
        [str(paths.toggle_script()), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    text = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return err or text or f"The TTS change failed ({proc.returncode})."
    if err and text:
        return text + "\n" + err
    return text or err or "TTS was updated."


def perform(action: tuple[str, str], engine: str) -> str:
    """Carry out one TTS command and return the user-visible text."""
    kind, args = plan(action, engine)
    if kind == "message":
        return str(args)
    text = run_toggle(list(args))
    if args == ["engine", "piper"]:
        return text + "\n" + "Piper runs on the CPU only."
    return text


def handle(event: dict) -> dict | None:
    """Block a TTS command prompt. Leave every other event alone."""
    if event_name(event) != "userpromptsubmit":
        return None
    prompt = prompt_text(event)
    if prompt is None:
        return None
    action = control_action(prompt)
    if action is None:
        return None
    reason = perform(action, current_engine())
    return {"decision": "block", "reason": reason}


def self_test() -> None:
    """Check command parsing and the Piper device refusal."""
    assert control_action("/tts") == ("status", "")
    assert control_action("  /user:tts-kokoro  ") == ("engine", "kokoro")
    assert control_action("/tts-piper") == ("engine", "piper")
    assert control_action("/tts-cpu") == ("device", "cpu")
    assert control_action("/TTS-GPU") == ("device", "gpu")
    assert control_action("/tts engine piper") == ("engine", "piper")
    assert control_action("/tts device cuda") == ("device", "gpu")
    assert control_action("tts-control: status") == ("status", "")
    assert control_action("tts-control: engine kokoro") == ("engine", "kokoro")
    assert control_action("tts-control: device gpu") == ("device", "gpu")
    assert control_action("/tts-status") == ("status", "")
    assert control_action("/tts-volume") == ("volume", "")
    assert control_action("/tts-volume 4") == ("volume", "4")
    assert control_action("/tts volume 9") == ("volume", "9")
    assert control_action("tts-control: volume 2") == ("volume", "2")
    assert control_action("tts-control: volume $ARGUMENTS") == ("volume", "")
    assert plan(("volume", ""), "piper") == ("toggle", ["volume"])
    assert plan(("volume", "4"), "kokoro") == ("toggle", ["volume", "4"])
    assert plan(("volume", "0"), "piper") == ("message", _VOLUME_USAGE)
    assert control_action("please run /tts-gpu later") is None
    assert control_action("/redact") is None
    long_prompt = ("note " * 500) + "\ntts-control: device gpu"
    assert control_action(long_prompt) is None
    assert plan(("device", "gpu"), "piper") == ("message", _PIPER_GPU)
    assert plan(("device", "cpu"), "piper") == ("message", _PIPER_CPU)
    assert plan(("device", "gpu"), "kokoro") == ("toggle", ["device", "gpu"])
    assert plan(("engine", "piper"), "kokoro") == ("toggle", ["engine", "piper"])
    blocked = handle({"hook_event_name": "UserPromptSubmit", "prompt": "hello"})
    assert blocked is None
    print("self-test ok")


def main() -> None:
    """Read one hook event from stdin and block TTS commands."""
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        self_test()
        return
    raw = sys.stdin.read()
    if not raw.strip():
        return
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(event, dict):
        return
    try:
        result = handle(event)
    except Exception as exc:
        result = {
            "decision": "block",
            "reason": f"The TTS command failed: {exc}",
        }
    if result is None:
        return
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
