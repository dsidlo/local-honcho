#!/usr/bin/env python3
"""Speak one utterance with Piper. A lock keeps turns from overlapping.

This file is self-contained. The Piper virtualenv runs it by path and does
not have the rest of the package on its import path.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import wave
from pathlib import Path

PLAYERS = ("paplay", "pw-play", "aplay")

# Piper's VITS decoders degrade on long single phoneme streams: synthesis
# past roughly one hundred phonemes can collapse into hallucinated or
# phonetically mushy audio on fragile voice models (verified with whisper
# round-trips against en_US-ryan-medium: a 159-phoneme sentence rendered
# as garbage while the same text split at clause boundaries rendered
# word-perfect; multi-clause calls collapse too, so each chunk is ONE
# espeak clause; and a 134-char comma-free clause, 163 phonemes, collapsed
# BOTH as one call and as a 120-char word-split chunk, while every
# single-clause chunk up to 67 chars in the same session decoded clean).
# Chunks must stay comfortably below that ceiling.
MAX_CHUNK_CHARS = 64
# Silence inserted between synthesized chunks, in seconds.
GAP_SECONDS = 0.1
# Clause breaks: whitespace after punctuation — commas included, because
# espeak treats a comma as a clause and one clause per synthesis call is
# the empirically stable unit. Filenames like "main.py" have no trailing
# whitespace, so they stay intact.
CLAUSE_SPLIT = re.compile(r"(?<=[.!?;:,])\s+")


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


def split_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split text into one-espeak-clause chunks the decoder renders cleanly.

    Every whitespace after sentence punctuation AND after a comma starts a
    new chunk (the punctuation stays attached, preserving the prosodic
    pause before the inter-chunk gap). Chunks are deliberately NOT merged
    across clause boundaries: multi-clause synthesis calls are where the
    VITS decoder collapses on fragile voice models. A clause longer than
    ``max_chars`` (comma-free slab, e.g. code) is hard-split at word
    boundaries.
    """
    text = text.strip()
    if not text:
        return [""]
    pieces: list[str] = []
    for clause in CLAUSE_SPLIT.split(text):
        clause = clause.strip()
        if not clause:
            continue
        if len(clause) <= max_chars:
            pieces.append(clause)
            continue
        run = ""
        for word in clause.split():
            piece = f"{run} {word}".strip()
            if len(piece) > max_chars and run:
                pieces.append(run)
                run = word
            else:
                run = piece
        if run:
            pieces.append(run)
    return pieces


def synthesize(
    text: str,
    model: Path,
    length_scale: float,
    dest: str,
    volume: float = 0.8,
) -> None:
    """Write one utterance to ``dest``.

    The text is split at clause boundaries (see :func:`split_text`) and
    each chunk is synthesized separately, then assembled into one wav with
    a short gap between chunks. Long single phoneme streams otherwise
    collapse into gibberish on some voice models.

    Each chunk is rendered to its own temp wav because ``synthesize_wav``
    sets the WAV format on every call, which the strict ``wave`` module of
    Python 3.12.4+ rejects for a writer that already has frames.

    ``volume`` is Piper's sample multiplier. 0.8 is about twenty percent
    under its full-scale default of 1.0.
    """
    PiperVoice, SynthesisConfig = _import_piper_library()

    voice = PiperVoice.load(model)
    config = SynthesisConfig(length_scale=length_scale, volume=volume)
    chunks = [chunk for chunk in split_text(text) if chunk]
    shape: tuple[int, int, int] | None = None
    rendered: list[bytes] = []
    part_paths: list[str] = []
    try:
        for chunk in chunks:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as part:
                part_path = part.name
            part_paths.append(part_path)
            with wave.open(part_path, "wb") as chunk_wav:
                voice.synthesize_wav(chunk, chunk_wav, syn_config=config)
            with wave.open(part_path, "rb") as chunk_wav:
                params = chunk_wav.getparams()
                pcm = chunk_wav.readframes(params.nframes)
            chunk_shape = (params.nchannels, params.sampwidth, params.framerate)
            if shape is None:
                shape = chunk_shape
            elif chunk_shape != shape:
                raise ValueError(f"chunk sample format mismatch: {chunk_shape}")
            rendered.append(pcm)
    finally:
        for part_path in part_paths:
            Path(part_path).unlink(missing_ok=True)
    if not rendered:
        return
    gap = b"\x00\x00" * int(shape[2] * GAP_SECONDS)
    with wave.open(dest, "wb") as wav:
        wav.setnchannels(shape[0])
        wav.setsampwidth(shape[1])
        wav.setframerate(shape[2])
        for index, pcm in enumerate(rendered):
            if index:
                wav.writeframes(gap)
            wav.writeframes(pcm)


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
        trace = traceback.format_exc(limit=6)
        log(f"piper error {exc}\n{trace}")
        print(f"piper error {exc}\n{trace}", file=sys.stderr)
        sys.exit(1)
