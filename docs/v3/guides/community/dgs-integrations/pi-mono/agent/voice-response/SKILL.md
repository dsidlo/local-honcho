---
name: voice-response
description: Speaks agent responses aloud using Kokoro TTS. Use for audio feedback, accessibility, or hands-free coding sessions. Supports customizable voice selection and speech speed.
---

# Voice Response

Speaks text using the Kokoro TTS model via a high-performance daemon with GPU acceleration.

## Architecture

The voice response system uses a **client-daemon architecture** for fast, efficient text-to-speech:

```
┌─────────────┐        Socket Request        ┌──────────────┐
│ TTS-Client  │ ───────────────────────────▶ │ TTS Daemon   │
│  (speak.sh) │                              │  (GPU model) │
│             │ ◀─────────────────────────── │              │
└─────────────┤        Response (success)      └──────────────┘
              (returns immediately)                  │
                                                     │
                                              ┌──────────────┐
                                              │ GPU Inference│
                                              │ (~50-80ms)   │
                                              └──────────────┘
                                                     │
                                              ┌──────────────┐
                                              │ Playback     │
                                              │ Queue        │
                                              │ (1 at a time)│
                                              └──────────────┘
```

### Components

| Component | Purpose | Location |
|-----------|---------|----------|
| `tts-daemon.py` | GPU-accelerated TTS server, keeps model hot in memory | `scripts/tts-daemon.py` |
| `tts-client.py` | Sends text to daemon via Unix socket | `scripts/tts-client.py` |
| `speak.sh` | User-friendly wrapper, auto-starts daemon | `scripts/speak.sh` |
| `tts-toggle.py` | Engine/device/per-engine volume control (grok-build `toggle`) | `scripts/tts-toggle.py` |
| `tts-gain.py` | Per-engine volume-to-gain lookup used by `speak.sh` | `scripts/tts-gain.py` |
`tts_guard.py` | Gibberish guard: risk-scores text for engine-hostile content (paths, KEY=VALUE flags, hashes, arrows, symbol-dense fragments), rewrites/drops it, digests the remainder | `scripts/tts_guard.py` |
| Socket | Unix domain socket for IPC (shared across sessions) | `/tmp/pi-tts-server.sock` |

### Key Features

- **GPU Acceleration**: Model runs on CUDA when available (~50-80ms inference)
- **Model Hot-Loading**: Daemon keeps model in GPU memory for instant response
- **Playback Queue**: Sequential audio playback prevents overlapping speech
- **Fast Response**: Client returns in ~100ms while audio plays in background
- **Auto-Start**: speak.sh automatically starts daemon if not running
- **Text Sanitization**: shared sanitizer (`tts_sanitize.py`) runs at the engine boundary in `speak.sh`, `tts-client.py`, and `speak.py` - strips markdown formatting (`**`, headers, bullets, tables), fenced/inline code, URLs, emoji, and symbol noise (arrows, checkmarks, ellipses) so responses speak cleanly; run `python3 tts_sanitize.py --selftest` to verify the filter rules
- **Gibberish guard**: symbol-dense technical text that survives the sanitizer (file paths, `exit=DONE`-style flags, hex hashes, `≤`/`→` symbols, `snake_case` tokens) makes piper/espeak-ng trail off into gibberish. `tts_guard.py` runs after sanitization and before the engine branch in `speak.sh`, rewrites what it can to spoken words (arrows become "then", `KEY=VALUE` becomes "value flag", paths become "the file"), drops sentences still scoring at or above the risk threshold (default 50), and if too much was dropped speaks a one-sentence headline (the first plain sentence, in narrative order) plus a canned "details are in the chat transcript" closer. Every decision is appended as JSON to `scripts/guard.log`. Tune via `TTS_GUARD_MAX_RISK` / `TTS_GUARD_MAX_CHARS`; verify with `python3 tts_guard.py --selftest`

### Engine, Device, and Per-Engine Volume

Engine state lives in one place - the Grok TTS config (`~/.grok/tts/config.json`,
`GROK_TTS_CONFIG` override), shared by this skill, the Kokoro daemon, and the
pi voice-control extension:

- `engine`: `kokoro` (GPU daemon) or `piper` (CPU CLI)
- `kokoro.device`: `gpu` or `cpu` - the daemon reads it on start, so config
  changes apply on restart; `tts-toggle.py device <gpu|cpu>` restarts with
  rollback if the new device fails to boot
- `kokoro.volume` (default 10) and `piper.volume` (default 8): levels 1-10,
  **persisted per engine** - switching engines keeps each engine's level

Volume scaling is `gain = level/10`, applied by the running engine: the
Kokoro daemon multiplies its utterance samples in-process; speak.sh passes
the Piper gain via `tts-gain.py` (`--volume`). Legacy stored floats (e.g.
`0.8`) map to the nearest level, so 0.8 ≈ level 8.

```bash
"$SKILL_DIR/scripts/tts-toggle.py" volume 7   # set volume for the CURRENT engine
"$SKILL_DIR/scripts/tts-toggle.py" status      # one-line summary
"$SKILL_DIR/scripts/tts-gain.py" piper          # print the piper gain (e.g. 0.80)
```

The pi voice-control extension mirrors these with `/vr-settings volume
<1-10>`, `/vr-settings engine <kokoro|piper>`, and `/vr-settings device
<gpu|cpu>`, which delegate to the same toggle.

### Async Service, Sync Playback

The daemon separates **fast responses** from **sequenced audio**:

**Response Flow (Async - Fast):**
1. Client sends text via socket (~30ms)
2. GPU generates audio (~50-80ms)
3. File saved with UUID to /tmp (~10ms)
4. Audio queued for playback (~1ms)
5. **Client gets response immediately** (~100ms total)

**Playback Flow (Sync - Sequential):**
1. Audio clips queue up
2. Worker plays **one at a time**
3. Each clip waits for the previous to finish
4. Prevents overlapping speech
5. **Auto-cleanup**: Files deleted after playback

```bash
# Example: Rapid requests
speak.sh "One"   # Returns in 100ms, starts playing
speak.sh "Two"   # Returns in 100ms, queues (plays after "One")
speak.sh "Three" # Returns in 100ms, queues (plays after "Two")

# Result: Hear "One" [pause] "Two" [pause] "Three"
# Not: garbled overlapping audio
```

**Important**: 
- The `~100ms` response confirms TTS was generated
- The audio duration is separate (~800-1000ms) and plays in background
- Audio files use UUIDs: `/tmp/tts-voice-<uuid>.wav`
- Files are automatically cleaned up after playback
- Multiple PI sessions share the same daemon (saves GPU memory)

## Setup

Ensure the Kokoro Python environment is available:

```bash
# The daemon uses the UV environment at:
$HOME/.pi/.venv/bin/python3
```

### GPU Configuration

The daemon automatically uses GPU if available. GPU memory usage: ~8.2 GB (model + cache).

To verify GPU is being used:
```bash
nvidia-smi  # Check for python3 process using ~8GB memory
```

## Usage

### Speak Text

```bash
~/.pi/agent/skills/voice-response/scripts/speak.sh "Your text here"
```

**With options:**
```bash
voice=am_michael speed=1.6 ~/.pi/agent/skills/voice-response/scripts/speak.sh "Hello world"
```

### Available Voices

- `af_bella`, `af_sarah`, `af_nicole`, `af_heart`, `af_kore`, `af_aoede` (American female)
- `am_michael`, `am_fenrir`, `am_puck` (American male)
- `bf_emma` (British female)

### Environment Variables

- `voice` - Voice ID to use (default: `am_michael`)
- `speed` - Speech speed 0.5-2.0 (default: `1.1`)

### Example as Part of Agent Workflow

```bash
# After completing a task
result="Task completed: File created successfully"
~/.pi/agent/skills/voice-response/scripts/speak.sh "$result"
```

## Piper Debug Logging & Voice Pairing

- Every piper synthesis appends a line to `/tmp/pi-sess-<session>-piper.log`
  (session id from `PI_SESSION_ID`/`PI_SESSION_FILE`, else `nosession`;
  override with `GROK_TTS_PIPER_LOG`): a `voice=`/`volume=`/`chars_raw=`
  header plus the exact scrubbed text sent to the engine. `tail -f` it when
  speech sounds wrong. The gibberish guard's per-decision JSON still goes to
  `scripts/guard.log`.
- Piper voice models must ship as a pair: `<model>.onnx` + `<model>.onnx.json`.
  When the `.json` sidecar is missing, piper silently falls back to espeak-ng
  synthesis (a classic gibberish source), so `speak.sh` refuses unpaired
  models: it warns on stderr, logs `WARN unpaired piper voice` to the session
  log, and reverts to the configured default voice.
- `speak.sh` reads the session voice from `VOICE_SKILL_CONFIG` (same file
  `set-voice.sh` writes); unset means the skill's own `config.json`.

## MCP Integration

To add to MCP client configuration:

```json
{
  "mcpServers": {
    "speech": {
      "command": "node",
      "args": ["/tmp/koroko-test/dist/index.js"]
    }
  }
}
```
