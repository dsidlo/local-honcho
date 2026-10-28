#!/usr/bin/env bash
# resolve-voice.sh — resolve the TTS engine + voice for a repo/project directory.
#
# Consults the per-repo voice map (~/.pi/voice-per-repo.json) — which now
# carries engine + voice + speed per repo — and falls back to the defaults /
# skill active voice when the repo isn't mapped.
#
# Outputs (to stdout), one per line, in this order:
#   VOICE
#   ENGINE
#   SPEED
#
# Usage:
#   resolve-voice.sh [cwd]
#     cwd: optional directory to match against the repo map (defaults to $PWD).
#
# Exit status: 0 on success.
#
# Flags:
#   --voice   print only the voice
#   --engine  print only the engine
#   --speed   print only the speed

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STYLE_FILE="${VOICE_SKILL_CONFIG:-$ROOT_DIR/config.json}"
REPO_MAP="${PI_VOICE_REPO_MAP:-$HOME/.pi/voice-per-repo.json}"
DEFAULT_ENGINE="kokoro"
DEFAULT_VOICE="am_michael"
DEFAULT_SPEED="1.1"

json_get() { # file default
  python3 -c '
import json, sys
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
except Exception:
    print(sys.argv[3]); sys.exit(0)
parts = sys.argv[2].split(".")
cur = data
for p in parts:
    if isinstance(cur, dict) and p in cur:
        cur = cur[p]
    else:
        print(sys.argv[3]); sys.exit(0)
print(cur if isinstance(cur, (str, int, float)) else sys.argv[3])
' "$1" "$2" "$3"
}

cwd=""
flag=""
for a in "$@"; do
  case "$a" in
    --voice|--engine|--speed) flag="$a" ;;
    *) cwd="$a" ;;
  esac
done
[[ -z "$cwd" ]] && cwd="$PWD"

# Match repo by longest-prefix of cwd.
repo=""
if [[ -f "$REPO_MAP" ]]; then
  repo=$(python3 -c '
import sys
cwd = sys.argv[1]
try:
    import json
    d = json.load(open(sys.argv[2]))
    best = ""
    for k in d.get("repos", {}):
        if cwd.startswith(k) and len(k) > len(best):
            best = k
    print(best)
except Exception:
    pass
' "$cwd" "$REPO_MAP")
fi

if [[ -n "$repo" ]]; then
  voice="$(json_get "$REPO_MAP" "repos.$repo.voice" "")"
  engine="$(json_get "$REPO_MAP" "repos.$repo.engine" "")"
  speed="$(json_get "$REPO_MAP" "repos.$repo.speed" "")"
fi

# Fallback to top-level defaults.
[[ -z "$voice" ]] && voice="$(json_get "$REPO_MAP" "default.voice" "")"
[[ -z "$engine" ]] && engine="$(json_get "$REPO_MAP" "default.engine" "")"
[[ -z "$speed" ]] && speed="$(json_get "$REPO_MAP" "default.speed" "")"

# Fallback to skill active voice / engine defaults.
[[ -z "$voice" ]] && voice="$(json_get "$STYLE_FILE" "activeVoice" "")"
[[ -z "$voice" ]] && voice="$(json_get "$STYLE_FILE" "defaultVoice" "$DEFAULT_VOICE")"
[[ -z "$engine" ]] && engine="$DEFAULT_ENGINE"
[[ -z "$speed" ]] && speed="$DEFAULT_SPEED"
[[ -z "$voice" ]] && voice="$DEFAULT_VOICE"

case "$flag" in
  --voice) echo "$voice"; exit 0 ;;
  --engine) echo "$engine"; exit 0 ;;
  --speed) echo "$speed"; exit 0 ;;
esac

printf '%s\n%s\n%s\n' "$voice" "$engine" "$speed"
