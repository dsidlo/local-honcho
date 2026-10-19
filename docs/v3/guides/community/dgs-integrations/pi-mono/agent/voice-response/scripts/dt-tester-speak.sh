#!/bin/bash
# Voice response using Pi TTS Server (shared across PI sessions)
# DT-Tester voice configuration: af_nicole at speed 1.6
# Auto-starts daemon if not running - shares daemon between all PI sessions

set -e

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Shared across all PI sessions
PID_FILE="/tmp/pi-tts-server.pid"
SOCKET_FILE="/tmp/pi-tts-server.sock"

UV_PYTHON="$HOME/.pi/.venv/bin/python3"
TTS_DAEMON="$SKILL_DIR/scripts/tts-daemon.py"
DIRECT_TTS="$SKILL_DIR/scripts/speak.py"

# DT-Tester voice settings (clear female voice, ignores config)
DT_TESTER_VOICE="af_nicole"
DT_TESTER_SPEED="1.6"

# DT-Tester uses af_nicole at speed 1.6 (role-specific defaults)
VOICE="${2:-${voice:-$DT_TESTER_VOICE}}"
SPEED="${3:-${speed:-$DT_TESTER_SPEED}}"
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
