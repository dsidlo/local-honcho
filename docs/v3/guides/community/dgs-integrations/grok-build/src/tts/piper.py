#!/usr/bin/env python3
"""Speak one utterance with Piper. A lock keeps turns from overlapping.

This file is self-contained. The Piper virtualenv runs it by path and does
not have the rest of the package on its import path.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

PLAYERS = ("paplay", "pw-play", "aplay")


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def piper_root() -> Path:
    """Return the directory that holds the Piper lock."""
    chosen = _env("GROK_TTS_PIPER_ROOT")
    if chosen:
        return Path(chosen)
    base = _env("GROK_TTS_DIR")
    if base:
        return Path(base) / "piper"
    return Path.home() / ".grok" / "tts" / "piper"


def speak_log() -> Path:
    """Return the speech log."""
    chosen = _env("GROK_TTS_LOG")
    if chosen:
        return Path(chosen)
    base = _env("GROK_TTS_DIR")
    if base:
        return Path(base) / "speak.log"
    return Path.home() / ".grok" / "tts" / "speak.log"


def voices_dir() -> Path:
    """Return the directory of installed Piper voices."""
    chosen = _env("GROK_TTS_PIPER_VOICES")
    if chosen:
        return Path(chosen)
    return piper_root() / "voices"


def lock_path() -> Path:
    """Return the speak lock."""
    chosen = _env("GROK_TTS_PIPER_LOCK")
    if chosen:
        return Path(chosen)
    return piper_root() / "speak.lock"


def log(line: str) -> None:
    """Append one line to the speech log."""
    try:
        with speak_log().open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip() + "\n")
    except OSError:
        pass


def resolve_model(voice: str) -> Path:
    """Return the ``.onnx`` file for a path or an installed voice name."""
    candidate = Path(voice).expanduser()
    if candidate.suffix == ".onnx" and candidate.is_file():
        return candidate
    named = voices_dir() / f"{voice}.onnx"
    if named.is_file():
        return named
    raise FileNotFoundError(voice)


def _resolves_to(path_str: str, target: Path) -> bool:
    """Return whether ``path_str`` is ``target`` after resolving links."""
    if not path_str:
        return False
    try:
        return Path(path_str).resolve() == target
    except OSError:
        return False


def _hides_piper(entry: str, script_dir: Path) -> bool:
    """Return whether a ``sys.path`` entry would import this script as Piper."""
    if entry == "":
        try:
            entry = str(Path.cwd())
        except OSError:
            return False
    return _resolves_to(entry, script_dir)


def _import_piper_library():
    """Import the installed Piper package, not this script.

    Running this file puts its directory first on ``sys.path``. The file is
    named ``piper.py``, so a plain ``import piper`` loads the script again
    and ``PiperVoice`` is missing.
    """
    import importlib

    script = Path(__file__).resolve()
    saved_path = sys.path[:]
    sys.path[:] = [entry for entry in saved_path if not _hides_piper(entry, script.parent)]
    previous = sys.modules.get("piper")
    popped = False
    if previous is not None and _resolves_to(str(getattr(previous, "__file__", "") or ""), script):
        sys.modules.pop("piper", None)
        popped = True
    try:
        module = importlib.import_module("piper")
    finally:
        sys.path[:] = saved_path
    if _resolves_to(str(getattr(module, "__file__", "") or ""), script):
        if popped:
            sys.modules.pop("piper", None)
        raise ImportError("Piper library is shadowed by the speaker script")
    return module.PiperVoice, module.SynthesisConfig


def synthesize(
    text: str,
    model: Path,
    length_scale: float,
    dest: str,
    volume: float = 0.8,
) -> None:
    """Write one utterance to ``dest``.

    ``volume`` is Piper's sample multiplier. 0.8 is about twenty percent
    under its full-scale default of 1.0.
    """
    PiperVoice, SynthesisConfig = _import_piper_library()

    voice = PiperVoice.load(model)
    config = SynthesisConfig(length_scale=length_scale, volume=volume)
    with wave.open(dest, "wb") as wav:
        voice.synthesize_wav(text, wav, syn_config=config)


def play(path: str) -> None:
    """Play a wav with the first available player."""
    for player in PLAYERS:
        binary = shutil.which(player)
        if not binary:
            continue
        result = subprocess.run([binary, path], check=False)
        if result.returncode == 0:
            return
        log(f"piper player-failed {player} code={result.returncode}")
    log("piper no-player")


def main() -> None:
    """Speak the text given on the command line."""
    parser = argparse.ArgumentParser(description="Speak text with Piper")
    parser.add_argument("--voice", required=True)
    parser.add_argument("--length-scale", type=float, default=1.0)
    parser.add_argument("--volume", type=float, default=0.8)
    parser.add_argument("--output", default="")
    parser.add_argument("--no-play", action="store_true")
    parser.add_argument("text")
    args = parser.parse_args()
    text = args.text.strip()
    if len(text) < 2:
        return
    scale = args.length_scale if args.length_scale > 0 else 1.0
    volume = args.volume if args.volume > 0 else 0.8
    model = resolve_model(args.voice)
    piper_root().mkdir(parents=True, exist_ok=True)
    lock = lock_path().open("a", encoding="utf-8")
    fcntl.flock(lock, fcntl.LOCK_EX)
    dest = ""
    try:
        if args.output:
            dest = args.output
            synthesize(text, model, scale, dest, volume)
        else:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                dest = handle.name
            synthesize(text, model, scale, dest, volume)
            if not args.no_play:
                play(dest)
        log(f"piper spoke chars={len(text)} voice={args.voice}")
    finally:
        if dest and not args.output:
            Path(dest).unlink(missing_ok=True)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"piper error {exc}")
        print(f"piper error {exc}", file=sys.stderr)
        sys.exit(1)
