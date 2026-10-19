#!/bin/bash
# Set the active voice and speed
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_FILE="${VOICE_SKILL_CONFIG:-$SKILL_DIR/config.json}"
VOICES_MALE="am_michael am_fenrir am_puck"
VOICES_FEMALE="af_bella af_sarah af_nicole af_heart af_kore af_aoede"
VOICES_BRITISH="bf_emma"
ALL_VOICES="$VOICES_MALE $VOICES_FEMALE $VOICES_BRITISH"

show_voices() {
    echo "Available voices:"
    echo "  Kokoro (daemon):   $ALL_VOICES"
    local piper_list
    piper_list=$(ls "$PIPER_VOICES"/*.onnx 2>/dev/null | while read -r f; do basename "$f" .onnx; done | sort | tr '\n' ' ')
    [[ -n "$piper_list" ]] && echo "  Piper (installed): $piper_list"
}

# A piper voice makes the pi session speak with its own voice while grok
# keeps the grok config's piper.voice; kokoro names fall back at speak time.
PIPER_VOICES="${GROK_TTS_PIPER_VOICES:-$HOME/.grok/tts/piper/voices}"

piper_voice_installed() {
    [[ -f "$PIPER_VOICES/$1.onnx" ]]
}

is_valid_voice() {
    for v in $ALL_VOICES; do [[ "$v" == "$1" ]] && return 0; done
    piper_voice_installed "$1"
}

if [[ "$1" == "--list" ]]; then show_voices; exit 0; fi
if [[ -z "$1" ]]; then show_voices; exit 0; fi

if ! is_valid_voice "$1"; then echo "Error: Invalid voice '$1'"; show_voices; exit 1; fi

SPEED="${2:-1.1}"
echo "{ \"defaultVoice\": \"am_michael\", \"defaultSpeed\": 1.1, \"activeVoice\": \"$1\", \"activeSpeed\": $SPEED }" > "$CONFIG_FILE"
echo "✓ Voice set to: $1 (speed: $SPEED)"
"$SKILL_DIR/scripts/speak.sh" "Voice set to $1"
