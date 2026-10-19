#!/bin/bash
# TTS Daemon Control
# Usage: tts-control.sh [start|stop|status|restart]

UV_PYTHON="$HOME/.local/share/anaconda3/bin/python3"
DAEMON_SCRIPT="$HOME/.pi/agent/skills/voice-response/scripts/tts-daemon.py"

case "${1:-status}" in
    start)
        "$UV_PYTHON" "$DAEMON_SCRIPT" start
        ;;
    stop)
        "$UV_PYTHON" "$DAEMON_SCRIPT" stop
        ;;
    restart)
        "$UV_PYTHON" "$DAEMON_SCRIPT" stop 2>/dev/null
        sleep 1
        "$UV_PYTHON" "$DAEMON_SCRIPT" start
        ;;
    status|*)
        "$UV_PYTHON" "$DAEMON_SCRIPT" status
        ;;
esac
