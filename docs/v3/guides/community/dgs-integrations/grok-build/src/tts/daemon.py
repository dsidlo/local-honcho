"""Kokoro TTS daemon. Keeps one model loaded for the life of the process.

Device comes from ``KOKORO_DEVICE``, or from the TTS config key
``kokoro.device``. ``gpu`` is the default. The choice is fixed until this
process starts again. ``load`` applies that choice to the process environment
before torch is imported. Paths are read when a function runs, so tests can
point them away from the live daemon. Each request also reads
``kokoro.volume``, a level from 1 to 10, and scales that utterance.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
import uuid
import warnings

from tts import paths
from tts.volume import KOKORO_DEFAULT, engine_multiplier

DEFAULT_VOICE = "af_heart"
DEFAULT_SPEED = 1.5

pipeline = None
running = True
playback_queue: queue.Queue[str | None] = queue.Queue()
playback_lock = threading.Lock()
playback_thread: threading.Thread | None = None
REQUESTED_DEVICE = "gpu"


def requested_device() -> str:
    """Return ``cpu`` or ``gpu`` for a process that is about to load Kokoro."""
    env = os.environ.get("KOKORO_DEVICE", "").strip().lower()
    if env == "cpu":
        return "cpu"
    if env in ("gpu", "cuda"):
        return "gpu"
    try:
        data = json.loads(paths.config_path().read_text(encoding="utf-8"))
        kokoro = data.get("kokoro") if isinstance(data, dict) else None
        if isinstance(kokoro, dict):
            choice = str(kokoro.get("device") or "").strip().lower()
            if choice == "cpu":
                return "cpu"
            if choice in ("gpu", "cuda"):
                return "gpu"
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return "gpu"


def prepare_runtime() -> str:
    """Fix the device and hide GPUs before torch is imported."""
    global REQUESTED_DEVICE
    REQUESTED_DEVICE = requested_device()
    # An empty value hides every GPU from libraries imported later.
    os.environ["CUDA_VISIBLE_DEVICES"] = "" if REQUESTED_DEVICE == "cpu" else "0"
    os.environ["HF_HUB_OFFLINE"] = "1"
    return REQUESTED_DEVICE


def cleanup() -> None:
    """Stop playback and remove the pid, socket, and device files."""
    global running
    running = False
    playback_queue.put(None)
    if playback_thread and playback_thread.is_alive():
        playback_thread.join(timeout=2.0)
    for path in (paths.pid_file(), paths.socket_file(), paths.device_file()):
        with contextlib.suppress(OSError):
            if path.exists():
                path.unlink()


def is_running() -> bool:
    """Return whether the pid is alive and the socket file is present."""
    pid_path = paths.pid_file()
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return paths.socket_file().exists()
    except Exception:
        cleanup()
        return False


def setup_signals() -> None:
    """Exit cleanly on SIGTERM and SIGINT."""

    def handler(_signum: int, _frame: object) -> None:
        global running
        running = False
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def load() -> bool:
    """Load Kokoro onto the requested device. Torch is imported here."""
    global pipeline
    try:
        prepare_runtime()
        import torch
        from kokoro import KPipeline

        if REQUESTED_DEVICE == "cpu":
            device = "cpu"
        else:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[Daemon] Requested: {REQUESTED_DEVICE}", file=sys.stderr, flush=True)
        print(f"[Daemon] Device: {device}", file=sys.stderr, flush=True)
        print("[Daemon] Loading model...", file=sys.stderr, flush=True)
        pipeline = KPipeline(lang_code="a", device=device)
        paths.device_file().write_text(device + "\n", encoding="utf-8")
        print("[Daemon] Ready!", file=sys.stderr, flush=True)
        return True
    except Exception as exc:
        print(f"[Daemon] Error: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return False


def kokoro_gain() -> float:
    """Return the sample multiplier for Kokoro's saved volume."""
    try:
        data = json.loads(paths.config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return 1.0
    return engine_multiplier(data, "kokoro", KOKORO_DEFAULT)


def generate(text: str, voice: str, speed: float) -> tuple[str | None, str | float]:
    """Synthesize one utterance. Return ``(wav_path, seconds)`` or an error."""
    if pipeline is None:
        return None, "No model"
    try:
        import soundfile as sf
        import torch  # noqa: F401

        start = time.time()
        gen = pipeline(text, voice=voice, speed=speed, split_pattern=r"\n+")
        segs = []
        for _, (_, _, audio) in enumerate(gen):
            segs.append(audio)
        if not segs:
            return None, "No audio"
        audio = sum(segs)
        gain = kokoro_gain()
        if gain != 1.0:
            audio = audio * gain
        out = str(paths.wav_dir() / f"tts-voice-{uuid.uuid4()}.wav")
        sf.write(out, audio, 24000)
        return out, time.time() - start
    except Exception as exc:
        return None, str(exc)


def playback_worker() -> None:
    """Play queued files one at a time, then delete each file."""
    while running:
        try:
            audio_file = playback_queue.get(timeout=0.5)
            if audio_file is None:
                break
            with playback_lock:
                try:
                    command = (
                        f'aplay "{audio_file}" 2>/dev/null '
                        f'|| paplay "{audio_file}" 2>/dev/null'
                    )
                    proc = subprocess.Popen(
                        ["bash", "-c", command],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    proc.wait()
                except Exception as exc:
                    print(f"[Daemon] Playback error: {exc}", file=sys.stderr)
                finally:
                    try:
                        if os.path.exists(audio_file):
                            os.remove(audio_file)
                            print(
                                f"[Daemon] Cleaned up: {audio_file}",
                                file=sys.stderr,
                            )
                    except Exception as exc:
                        print(
                            f"[Daemon] Failed to delete {audio_file}: {exc}",
                            file=sys.stderr,
                        )
            playback_queue.task_done()
        except queue.Empty:
            continue
        except Exception as exc:
            print(f"[Daemon] Playback worker error: {exc}", file=sys.stderr)


def start_playback_worker() -> None:
    """Start the playback thread when it is missing or already finished."""
    global playback_thread
    if playback_thread is None or not playback_thread.is_alive():
        playback_thread = threading.Thread(target=playback_worker, daemon=True)
        playback_thread.start()


def play(path: str) -> None:
    """Queue one file. Playback stays in order and returns immediately."""
    playback_queue.put(path)


def handle(conn: socket.socket) -> None:
    """Read one length-prefixed request, synthesize it, and reply."""
    try:
        raw = conn.recv(4)
        if not raw:
            return
        msg_len = struct.unpack("!I", raw)[0]
        data = b""
        while len(data) < msg_len:
            chunk = conn.recv(msg_len - len(data))
            if not chunk:
                return
            data += chunk
        req = json.loads(data.decode())
        text = req.get("text", "Hello")
        voice = req.get("voice", DEFAULT_VOICE)
        speed = req.get("speed", DEFAULT_SPEED)
        out, elapsed = generate(text, voice, speed)
        if out:
            play(out)
        res = json.dumps(
            {
                "success": out is not None,
                "file": out,
                "time": elapsed,
                "error": elapsed if isinstance(elapsed, str) else None,
            }
        ).encode()
        conn.sendall(struct.pack("!I", len(res)))
        conn.sendall(res)
    except Exception:
        return
    finally:
        conn.close()


def run() -> None:
    """Serve the Unix socket until the process is asked to stop."""
    if is_running():
        print("Already running", file=sys.stderr)
        sys.exit(0)

    paths.pid_file().write_text(str(os.getpid()), encoding="utf-8")
    setup_signals()
    if not load():
        cleanup()
        sys.exit(1)

    start_playback_worker()
    print("[Daemon] Playback worker started", file=sys.stderr, flush=True)
    try:
        sock_path = paths.socket_file()
        if sock_path.exists():
            sock_path.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        srv.listen(5)
        srv.settimeout(1.0)
        print("[Daemon] Listening", file=sys.stderr, flush=True)
        while running:
            try:
                conn, _addr = srv.accept()
                handle(conn)
            except TimeoutError:
                # TimeoutError subclasses OSError, so it has to come first.
                continue
            except OSError:
                break
        srv.close()
    except Exception as exc:
        print(f"[Daemon] Error: {exc}", file=sys.stderr)
    cleanup()


def stop() -> None:
    """Ask a running daemon to exit."""
    pid_path = paths.pid_file()
    if not pid_path.exists():
        print("Not running")
        return
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
        print("Stopped")
    except Exception:
        cleanup()


def status() -> None:
    """Print whether the daemon process is up."""
    if is_running():
        pid = paths.pid_file().read_text(encoding="utf-8").strip()
        print(f"Running (PID {pid})")
    else:
        print("Not running")


def main() -> None:
    """Dispatch ``start``, ``stop``, and ``status``. No args starts the server."""
    warnings.filterwarnings("ignore")
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "start":
            run()
        elif cmd == "stop":
            stop()
        elif cmd == "status":
            status()
        else:
            print(f"Unknown: {cmd}")
            sys.exit(1)
    else:
        run()


if __name__ == "__main__":
    main()
