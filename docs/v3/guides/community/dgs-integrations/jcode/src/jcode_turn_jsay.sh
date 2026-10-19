#!/usr/bin/env bash
# Speak a short safe summary of the assistant's final response on turn_end.
# Observer hook: best effort only, never block the session.
set -u

EVENT="${JCODE_HOOK_EVENT:-}"
STATUS="${JCODE_HOOK_STATUS:-}"
TEXT="${JCODE_HOOK_LAST_ASSISTANT_TEXT:-}"
DURATION_MS="${JCODE_HOOK_DURATION_MS:-0}"
JSAY_BIN="${JSAY_BIN:-}"
if [ -z "$JSAY_BIN" ] && command -v jsay >/dev/null 2>&1; then
  JSAY_BIN=$(command -v jsay)
fi
JSAY_BIN="${JSAY_BIN:-$HOME/.local/bin/jsay}"
JCODE_JSAY_ENV_FILE="${JCODE_JSAY_ENV_FILE:-$HOME/.jcode/jsay.env}"
LOG_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/jcode"
LOG_FILE="$LOG_DIR/jcode-turn-jsay.log"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/jcode"
STAMP_FILE="$STATE_DIR/last-jsay-turn-end.stamp"

mkdir -p "$LOG_DIR" "$STATE_DIR" >/dev/null 2>&1 || true

# Optional persistent jsay preferences for Jcode-owned announcements.
# Format: shell-style KEY=VALUE lines, e.g. JSAY_ENGINE=kokoro
if [ -f "$JCODE_JSAY_ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$JCODE_JSAY_ENV_FILE" >/dev/null 2>&1 || true
  set +a
fi

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*" >> "$LOG_FILE" 2>/dev/null || true
}

[ "$EVENT" = "turn_end" ] || exit 0
[ "$STATUS" = "ok" ] || exit 0
[ -n "$TEXT" ] || exit 0
[ -x "$JSAY_BIN" ] || exit 0

# Gate what is said, not the workflow: content filters and word/sentence caps
# apply to the prepared spoken item. Turn duration is never a skip reason.
# No rate limit: every qualifying turn speaks. Item-level content filters
# and caps are the only gating.
# Skip likely unsafe or noisy content BEFORE the markdown scrub above can
# soften tokens like API_KEY (the scrub turns _ into spaces).
case "$TEXT" in
  *API_KEY*|*api_key*|*Bearer\ *|*token*|*password*|*passwd*|*SECRET*|*secret* )
    log "skip sensitive-looking text"
    exit 0
    ;;
esac

# Flatten whitespace and trim obvious markdown noise.
TEXT=$(printf '%s' "$TEXT" | tr '\n' ' ' | tr '\r' ' ')
TEXT=$(printf '%s' "$TEXT" | sed -E 's/```[^`]*```/ /g; s/`([^`]*)`/\1/g; s/https?:\/\/[^ ]+/ link /g; s/[#*_>|~]+/ /g; s/\[[^]]*\]\([^)]*\)/ /g; s/[[:space:]]+/ /g; s/^ +| +$//g')

# Keep only the first 1-2 sentences, capped to a short notifier length.
SPOKEN=$(TEXT="$TEXT" python3 - <<'PY'
import os, re
text = os.environ.get('TEXT', '').strip()
if not text:
    print('')
    raise SystemExit(0)
text = re.sub(r'\s+', ' ', text)
parts = re.split(r'(?<=[.!?])\s+', text)
out = []
for part in parts:
    part = part.strip()
    if not part:
        continue
    out.append(part)
    if len(out) >= 2:
        break
spoken = ' '.join(out).strip()
if len(spoken) > 220:
    spoken = spoken[:217].rsplit(' ', 1)[0].rstrip(' ,;:') + '...'
print(spoken)
PY
)

[ -n "$SPOKEN" ] || exit 0
WORDS=$(printf '%s\n' "$SPOKEN" | wc -w | tr -d ' ')
if [ "$WORDS" -gt 35 ]; then
  log "skip too many words words=$WORDS"
  exit 0
fi

# Duration floor on the single spoken item. Default 0 (never skip); set
# JCODE_JSAY_MIN_DURATION_MS to require a longer estimated speech time.
MIN_DURATION_MS="${JCODE_JSAY_MIN_DURATION_MS:-0}"
SPEECH_MS=$(( WORDS * 60000 / 150 ))
if [ "$MIN_DURATION_MS" -gt 0 ] && [ "$SPEECH_MS" -lt "$MIN_DURATION_MS" ]; then
  log "skip short speech speech_ms=$SPEECH_MS words=$WORDS"
  exit 0
fi

printf '%s' "$(date +%s 2>/dev/null || echo 0)" > "$STAMP_FILE" 2>/dev/null || true
"$JSAY_BIN" --quiet "$SPOKEN" >/dev/null 2>&1 || true
log "spoken duration_ms=$DURATION_MS words=$WORDS text=$SPOKEN"
exit 0
