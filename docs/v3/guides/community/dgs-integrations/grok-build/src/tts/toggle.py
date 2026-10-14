"""Turn Grok's spoken replies on or off, and choose the speech engine.

Usage:
  toggle [on|off|toggle|status]
  toggle engine [kokoro|piper]
  toggle device [cpu|gpu]
  toggle voices
  toggle piper-voice <name>
  toggle volume [1-10]

Changing Kokoro's device restarts the Kokoro daemon. The loaded model stays
on the device chosen when that process started.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time

from tts import paths
from tts.volume import KOKORO_DEFAULT, PIPER_DEFAULT, level

ENGINES = ("kokoro", "piper")
DEFAULT_PIPER = {
    "voice": "en_GB-jenny_dioco-medium",
    "lengthScale": 1.0,
    "volume": PIPER_DEFAULT,
}
DEFAULT_KOKORO = {"device": "gpu", "volume": KOKORO_DEFAULT}


def normalize_device(value: object) -> str:
    """Return ``cpu`` or ``gpu``. ``cuda`` is stored as ``gpu``."""
    choice = str(value or "").strip().lower()
    if choice == "cpu":
        return "cpu"
    if choice in ("gpu", "cuda"):
        return "gpu"
    return ""


def load() -> dict:
    """Return the config, filling any missing speech fields."""
    try:
        data = json.loads(paths.config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("enabled", True)
    data.setdefault("maxTextLength", 500)
    data.setdefault("voice", "")
    data.setdefault("speed", "")
    piper = data.get("piper")
    merged_piper = dict(DEFAULT_PIPER)
    if isinstance(piper, dict):
        merged_piper.update(piper)
    if not str(merged_piper.get("voice") or "").strip():
        merged_piper["voice"] = DEFAULT_PIPER["voice"]
    merged_piper["volume"] = level(merged_piper.get("volume"), PIPER_DEFAULT)
    data["piper"] = merged_piper
    kokoro = data.get("kokoro")
    merged_kokoro = dict(DEFAULT_KOKORO)
    if isinstance(kokoro, dict):
        merged_kokoro.update(kokoro)
    device = normalize_device(merged_kokoro.get("device"))
    merged_kokoro["device"] = device or DEFAULT_KOKORO["device"]
    merged_kokoro["volume"] = level(merged_kokoro.get("volume"), KOKORO_DEFAULT)
    data["kokoro"] = merged_kokoro
    engine = str(data.get("engine") or "kokoro").strip().lower()
    data["engine"] = engine if engine in ENGINES else "kokoro"
    return data


def save(data: dict) -> None:
    """Write the config."""
    paths.config_path().parent.mkdir(parents=True, exist_ok=True)
    paths.config_path().write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )


def status_line(data: dict) -> str:
    """Return one line describing the current speech settings."""
    state = "on" if data["enabled"] else "off"
    if data["engine"] == "piper":
        piper = data["piper"]
        return (
            f"{state} engine=piper voice={piper['voice']} "
            f"lengthScale={piper['lengthScale']} volume={piper['volume']}"
        )
    return (
        f"{state} engine=kokoro voice={data['voice']} speed={data['speed']} "
        f"device={data['kokoro']['device']} volume={data['kokoro']['volume']}"
    )


def volume_line(data: dict) -> str:
    """Return the current engine's volume and the other engine's saved level."""
    current = data["engine"]
    other = "kokoro" if current == "piper" else "piper"
    return (
        f"{current} volume={data[current]['volume']} "
        f"({other} volume={data[other]['volume']})"
    )


def daemon_pid() -> int | None:
    """Return the live daemon pid, or None when it is not running."""
    try:
        pid = int(paths.pid_file().read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    return pid


def stop_daemon() -> None:
    """Stop the Kokoro daemon and remove its pid, socket, and device files."""
    pid = daemon_pid()
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pid = None
    if pid is not None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.1)
        else:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
            time.sleep(0.2)
    for path in (paths.pid_file(), paths.socket_file(), paths.device_file()):
        with contextlib.suppress(OSError):
            path.unlink()


def start_daemon() -> None:
    """Start the Kokoro daemon and truncate its log."""
    python = paths.daemon_python()
    script = paths.daemon_script()
    if not python.is_file() or not script.is_file():
        raise RuntimeError(f"missing daemon: {python} {script}")
    with paths.daemon_log().open("w", encoding="utf-8") as handle:
        subprocess.Popen(
            [str(python), str(script)],
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def wait_ready(old_pid: int | None, timeout: int = 60) -> tuple[int | None, str]:
    """Wait until a new daemon has written its socket and device."""
    deadline = time.monotonic() + timeout
    saw_new = False
    while time.monotonic() < deadline:
        pid = daemon_pid()
        if pid and pid != old_pid:
            saw_new = True
            if paths.socket_file().exists() and paths.device_file().is_file():
                live = paths.device_file().read_text(encoding="utf-8").strip().lower()
                return pid, live
        elif saw_new and pid is None:
            return None, ""
        time.sleep(0.2)
    return None, ""


def log_tail() -> str:
    """Return the last 20 lines of the daemon log."""
    try:
        lines = paths.daemon_log().read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-20:])


def restart_daemon() -> tuple[int | None, str]:
    """Stop the daemon, start it, and wait for the new process."""
    old_pid = daemon_pid()
    stop_daemon()
    start_daemon()
    return wait_ready(old_pid)


def live_choice() -> str:
    """Return the running device as ``cpu`` or ``gpu``."""
    try:
        live = paths.device_file().read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""
    if live == "cpu":
        return "cpu"
    if live == "cuda":
        return "gpu"
    return ""


def switch_device(data: dict, choice: str) -> None:
    """Save ``choice`` and restart Kokoro when the device actually changes."""
    previous = data["kokoro"]["device"]
    live = live_choice()
    if choice == previous and live in ("", choice):
        save(data)
        print(f"already on {choice}")
        print(status_line(data))
        return
    data["kokoro"]["device"] = choice
    save(data)
    expect = "cpu" if choice == "cpu" else "cuda"
    try:
        pid, live = restart_daemon()
    except RuntimeError as exc:
        data["kokoro"]["device"] = previous
        save(data)
        print(f"Kokoro daemon was not restarted: {exc}", file=sys.stderr)
        sys.exit(1)
    if pid and live == expect:
        print(f"restarted on {live} pid={pid}")
        print(status_line(data))
        return
    detail = log_tail()
    data["kokoro"]["device"] = previous
    save(data)
    restored = ""
    try:
        restore_pid, _restore_live = restart_daemon()
        if restore_pid:
            restored = f" The daemon was restored to {previous}."
    except RuntimeError:
        restore_pid = None
    print(f"Kokoro did not come up on {choice}.{restored}", file=sys.stderr)
    if detail:
        print(detail, file=sys.stderr)
    if not restored and restore_pid is None:
        print("The previous daemon did not come back either.", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    """Apply one toggle command from ``sys.argv``."""
    args = sys.argv[1:]
    data = load()
    if not args or args[0] == "status":
        print(status_line(data))
        return
    if args[0] in ("on", "off", "toggle") and len(args) == 1:
        if args[0] == "on":
            data["enabled"] = True
        elif args[0] == "off":
            data["enabled"] = False
        else:
            data["enabled"] = not bool(data["enabled"])
        save(data)
        print(status_line(data))
        return
    if args[0] == "engine":
        if len(args) == 1:
            print(status_line(data))
            return
        choice = args[1].strip().lower()
        if len(args) != 2 or choice not in ENGINES:
            print("Usage: toggle engine [kokoro|piper]", file=sys.stderr)
            sys.exit(2)
        data["engine"] = choice
        save(data)
        print(status_line(data))
        return
    if args[0] == "device":
        if len(args) == 1:
            print(status_line(data))
            return
        choice = normalize_device(args[1] if len(args) == 2 else "")
        if not choice:
            print("Usage: toggle device [cpu|gpu]", file=sys.stderr)
            sys.exit(2)
        switch_device(data, choice)
        return
    if args[0] == "voices" and len(args) == 1:
        names = sorted(path.stem for path in paths.piper_voices().glob("*.onnx"))
        print("\n".join(names) if names else "(no piper voices installed)")
        return
    if args[0] == "piper-voice" and len(args) == 2 and args[1].strip():
        data["piper"]["voice"] = args[1].strip()
        save(data)
        print(status_line(data))
        return
    if args[0] == "volume":
        if len(args) == 1:
            print(volume_line(data))
            return
        chosen = args[1].strip() if len(args) == 2 else ""
        number = int(chosen) if chosen.isdigit() else 0
        if 1 <= number <= 10:
            data[data["engine"]]["volume"] = number
            save(data)
            print(volume_line(data))
            return
        print("Usage: toggle volume [1-10]", file=sys.stderr)
        sys.exit(2)
    print(
        "Usage: toggle [on|off|toggle|status|engine|device|voices|"
        "piper-voice|volume]",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
