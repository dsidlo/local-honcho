#!/bin/bash
# Voice response using Pi TTS Server (shared across PI sessions)
# Default voice configuration: am_michael at speed 1.1
# Auto-starts daemon if not running - shares daemon between all PI sessions

set -e

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_FILE="$SKILL_DIR/config.json"
# Shared across all PI sessions
PID_FILE="/tmp/pi-tts-server.pid"
SOCKET_FILE="/tmp/pi-tts-server.sock"

UV_PYTHON="$HOME/.pi/.venv/bin/python3"
TTS_DAEMON="$SKILL_DIR/scripts/tts-daemon.py"
DIRECT_TTS="$SKILL_DIR/scripts/speak.py"

# Default voice settings
DEFAULT_VOICE="am_michael"
DEFAULT_SPEED="1.1"

# Load config (can override defaults)
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

VOICE="${2:-${voice:-${CONFIG_VOICE:-$DEFAULT_VOICE}}}"
SPEED="${3:-${speed:-${CONFIG_SPEED:-$DEFAULT_SPEED}}}"
TEXT="${1:-Hello}"

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
