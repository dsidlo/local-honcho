#!/bin/bash
# Voice response using Pi TTS Server (shared across PI sessions)
# Auto-starts daemon if not running - shares daemon between all PI sessions

set -e

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Session voice state file: set-voice.sh writes it, this script reads it.
# VOICE_SKILL_CONFIG lets callers (and tests) redirect the state file;
# unset means the skill's own config.json.
CONFIG_FILE="${VOICE_SKILL_CONFIG:-$SKILL_DIR/config.json}"
# Shared across all PI sessions
PID_FILE="/tmp/pi-tts-server.pid"
SOCKET_FILE="/tmp/pi-tts-server.sock"

UV_PYTHON="${GROK_TTS_PYTHON:-$HOME/.pi/.venv/bin/python3}"
TTS_DAEMON="${GROK_TTS_DAEMON_SCRIPT:-$SKILL_DIR/scripts/tts-daemon.py}"
DIRECT_TTS="${GROK_TTS_DIRECT_TTS:-$SKILL_DIR/scripts/speak.py}"

# Load voice/speed from skill config
if [[ -f "$CONFIG_FILE" ]]; then
    CONFIG_JSON=$(cat "$CONFIG_FILE")
    if command -v jq >/dev/null 2>&1; then
        CONFIG_VOICE=$(echo "$CONFIG_JSON" | jq -r '.activeVoice // empty')
        CONFIG_SPEED=$(echo "$CONFIG_JSON" | jq -r '.activeSpeed // empty')
    else
        CONFIG_VOICE=$(echo "$CONFIG_JSON" | grep -o '"activeVoice"[^,]*' | cut -d'"' -f4)
        CONFIG_SPEED=$(echo "$CONFIG_JSON" | grep -o '"activeSpeed"[^,}]*' | grep -o '[0-9.]\+')
    fi
fi

# Engine state (engine, kokoro device, per-engine volume, piper voice) is
# Grok TTS's config - the single store the Kokoro daemon and the pi extension
# share. Each engine's volume persists separately, so switching engines keeps
# levels.
GROK_CONFIG="${GROK_TTS_CONFIG:-$HOME/.grok/tts/config.json}"

json_get() { # path default - read a (possibly nested) key from GROK_CONFIG
    local p="$1" dflt="$2" v=""
    if [[ -f "$GROK_CONFIG" ]] && command -v jq >/dev/null 2>&1; then
        v=$(jq -r ".${p} // empty" "$GROK_CONFIG" 2>/dev/null)
    elif [[ -f "$GROK_CONFIG" ]]; then
        v=$("$UV_PYTHON" - "$GROK_CONFIG" "$p" "$dflt" <<'PYEOF'
import json, sys
file, path, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    data = json.load(open(file))
except Exception:
    print(default)
    raise SystemExit
for key in path.split("."):
    if isinstance(data, dict):
        data = data.get(key)
    else:
        data = None
print(data if data is not None else default)
PYEOF
)
    fi
    if [[ -n "${v:-}" ]]; then
        echo "$v"
    else
        echo "$dflt"
    fi
}

ENGINE="${GROK_TTS_ENGINE:-$(json_get engine kokoro)}"
ENGINE="${ENGINE:-kokoro}"
PIPER_VOICE="$(json_get piper.voice en_GB-jenny_dioco-medium)"
PIPER_VOL="$(json_get piper.volume 8)"

VOICE="${2:-${voice:-${CONFIG_VOICE:-af_heart}}}"
SPEED="${3:-${speed:-${CONFIG_SPEED:-1.5}}}"
TEXT="${1:-Hello}"
RAW_CHARS="${#TEXT}"

# Clean the text before speaking: strip markdown, emoji, URLs and other
# symbols the engines read aloud awkwardly (shared sanitizer, see
# tts_sanitize.py). Keep raw input if sanitization fails; if nothing worth
# speaking remains (e.g. an all-code response), stop quietly.
if SANITIZED=$("$UV_PYTHON" "$SKILL_DIR/scripts/tts_sanitize.py" "$TEXT" 2>/dev/null); then
    TEXT="$SANITIZED"
fi
if [[ -z "$TEXT" ]]; then
    exit 0
fi

# Gibberish guard: score the sanitized text for engine-hostile content.
# The shared sanitizer already strips markdown/emoji/URLs, but technical
# fragments it leaves behind - file paths, KEY=VALUE flags (exit=DONE),
# hex hashes, arrows, <= / >=, dense symbol runs - are what push
# piper/espeak-ng into trailing gibberish. tts_guard.py rewrites what it
# can to spoken words, drops sentences that are still risky, and falls
# back to a small digest with a canned closer when too much was dropped.
# See tts_guard.py; on failure keeps the sanitized text unchanged.
if GUARDED=$("$UV_PYTHON" "$SKILL_DIR/scripts/tts_guard.py" "$TEXT" 2>/dev/null); then
    TEXT="$GUARDED"
fi
if [[ -z "$TEXT" ]]; then
    exit 0
fi

# --- Piper engine: direct CPU synthesis via the grok-build piper.py CLI ---
PIPER_PYTHON="${GROK_TTS_PIPER_PYTHON:-$HOME/.grok/tts/piper/.venv/bin/python3}"
PIPER_SPEAK="${GROK_TTS_PIPER_SPEAK:-$HOME/workspace/honcho/docs/v3/guides/community/dgs-integrations/grok-build/src/tts/piper.py}"
PIPER_VOICES="${GROK_TTS_PIPER_VOICES:-$HOME/.grok/tts/piper/voices}"

# --- Session-scoped piper debug log: /tmp/pi-sess-<session-id>-piper.log ---
# Every utterance that reaches a piper synthesis is logged (the exact text
# handed to the piper CLI, after sanitize -> guard -> piper normalization)
# just before it is spoken, so gibberish output can be traced to its input.
# Session id: pi's PI_SESSION_ID, falling back to the UUID in
# PI_SESSION_FILE, else a shared "nosession" file. GROK_TTS_PIPER_LOG
# overrides the destination (tests, ad-hoc runs). Logging must never break
# synthesis: every append is guarded.
PIPER_SESSION_ID="${PI_SESSION_ID:-}"
if [[ -z "$PIPER_SESSION_ID" && -n "${PI_SESSION_FILE:-}" ]]; then
    PIPER_SESSION_ID=$(basename "$PI_SESSION_FILE" \
        | grep -oE '[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}' \
        | tail -n 1)
fi
PIPER_SESSION_ID=$(printf '%s' "${PIPER_SESSION_ID:-nosession}" | tr -c 'A-Za-z0-9_-' '-')
PIPER_LOG="${GROK_TTS_PIPER_LOG:-/tmp/pi-sess-${PIPER_SESSION_ID}-piper.log}"

speak_with_piper() {
    local pvoice
    # Piper-specific normalization: currency, titles, decades, units, and a
    # guaranteed sentence-final period (espeak-ng gibberish fixes; see
    # tts_piper_normalize.py). Falls back to the shared-sanitized text if
    # the normalizer fails.
    if NORMALIZED=$("$UV_PYTHON" "$SKILL_DIR/scripts/tts_piper_normalize.py" "$TEXT" 2>/dev/null) && [[ -n "$NORMALIZED" ]]; then
        TEXT="$NORMALIZED"
    fi
    # Accept an installed piper voice name or a direct .onnx path; else use
    # the configured default voice. Kokoro voice names (af_heart, ...) have
    # no piper models, so unknown names fall back to PiperVoice.
    #
    # Model-mismatch guard: piper needs <model>.onnx paired with
    # <model>.onnx.json. When the json is missing, piper silently falls
    # back to espeak-ng synthesis - a classic gibberish source (espeak
    # phonemizes anything the neural model would handle). An unpaired
    # candidate warns, logs the fallback to the session piper log, and
    # reverts to the configured default voice.
    pvoice=""
    if [[ "$VOICE" == *.onnx ]]; then
        if [[ -f "$VOICE" && -f "$VOICE.json" ]]; then
            pvoice="$VOICE"
        else
            echo "speak.sh: piper voice '$VOICE' missing its .onnx.json; using $PIPER_VOICE" >&2
        fi
    elif [[ -f "$PIPER_VOICES/$VOICE.onnx" ]]; then
        if [[ -f "$PIPER_VOICES/$VOICE.onnx.json" ]]; then
            pvoice="$VOICE"
        else
            echo "speak.sh: piper voice '$VOICE' has no $VOICE.onnx.json; using $PIPER_VOICE" >&2
        fi
    else
        # Unknown name (e.g. a kokoro voice id): piper.py resolves its own
        # voices directory, which we cannot cheaply validate here.
        pvoice="$PIPER_VOICE"
    fi
    if [[ -z "$pvoice" ]]; then
        pvoice="$PIPER_VOICE"
        { printf '[%s] WARN unpaired piper voice: %s -> fallback %s\n' "$(date -Is 2>/dev/null || date)" "$VOICE" "$pvoice"; } >> "$PIPER_LOG" 2>/dev/null || true
    fi
    # Piper slows speech via length-scale; kokoro-style speed maps to 1/speed.
    local length_scale
    length_scale=$(awk "BEGIN{ if ($SPEED > 0) printf \"%.4f\", 1/$SPEED; else print \"1.0000\" }")
    # Log the final scrubbed text right before it is spoken (gibberish
    # debugging). Failure to log must never stop speech.
    {
        printf '[%s] voice=%s length_scale=%s volume=%s chars_raw=%d chars_scrubbed=%d\n' \
            "$(date -Is 2>/dev/null || date)" "$pvoice" "$length_scale" \
            "$PIPER_GAIN" "$RAW_CHARS" "${#TEXT}"
        printf '%s\n\n' "$TEXT"
    } >> "$PIPER_LOG" 2>/dev/null || true
    exec "$PIPER_PYTHON" "$PIPER_SPEAK" --voice "$pvoice" --length-scale "$length_scale" --volume "$PIPER_GAIN" "$TEXT"
}

# Piper volume: the saved level maps to a sample gain exactly like the
# kokoro daemon applies internally (tts.volume). Preferred path asks the
# volume module directly; the shell fallback repeats its rules.
PIPER_GAIN=$("$UV_PYTHON" "$SKILL_DIR/scripts/tts-gain.py" piper 2>/dev/null)
if ! [[ "${PIPER_GAIN:-}" =~ ^[0-9.]+$ ]]; then
    PIPER_GAIN=$(awk -v v="$PIPER_VOL" 'BEGIN{
        if (v == int(v)) gain = v / 10;
        else if (v <= 1) { l = int(v * 10 + 0.5); if (l < 1) l = 1; if (l > 10) l = 10; gain = l / 10 }
        else gain = v / 10;
        printf "%.2f", gain }')
fi

if [[ "$ENGINE" == "piper" ]]; then
    if [[ -x "$PIPER_PYTHON" && -f "$PIPER_SPEAK" ]]; then
        speak_with_piper
    fi
    echo "speak.sh: piper engine unavailable ($PIPER_PYTHON, $PIPER_SPEAK); falling back to kokoro" >&2
fi

is_daemon_running() {
    if [[ -f "$PID_FILE" ]]; then
        local pid=$(cat "$PID_FILE" 2>/dev/null)
        if kill -0 "$pid" 2>/dev/null && [[ -S "$SOCKET_FILE" ]]; then
            return 0
        fi
    fi
    return 1
}

start_daemon() {
    if ! is_daemon_running; then
        rm -f "$PID_FILE" "$SOCKET_FILE"
        # The daemon reads its device (gpu|cpu) from the TTS config
        # kokoro.device, so config changes apply on restart without env wiring.
        nohup "$UV_PYTHON" "$TTS_DAEMON" >/tmp/kokoro-daemon.log 2>&1 &
        for i in {1..40}; do
            if is_daemon_running; then
                sleep 2
                return 0
            fi
            sleep 1
        done
        return 1
    fi
    return 0
}

if [[ -f "$TTS_DAEMON" ]] && start_daemon; then
    exec "$UV_PYTHON" "$SKILL_DIR/scripts/tts-client.py" "$TEXT" "$VOICE" "$SPEED"
fi

if [[ -f "$DIRECT_TTS" ]]; then
    exec "$UV_PYTHON" "$DIRECT_TTS" "$TEXT" "$VOICE" "$SPEED"
fi

echo "Error: No TTS available" >&2
exit 1
