#!/usr/bin/env bash
# jcode-honcho-capture: jcode lifecycle hook (observer)
# Posts session start/end markers and cheap turn bookmarks to Honcho.
# Fire-and-forget: never blocks jcode; all failures silent.
set -u
EVENT="${JCODE_HOOK_EVENT:-unknown}"
SID="${JCODE_HOOK_SESSION_ID:-unknown}"
CWD="${JCODE_HOOK_CWD:-$PWD}"
MODEL="${JCODE_HOOK_MODEL:-}"
STATUS="${JCODE_HOOK_STATUS:-}"
DURATION="${JCODE_HOOK_DURATION_MS:-}"
HONCHO="${HONCHO_BASE_URL:-http://localhost:8300}/v3"

# Workspace from git origin (same rule as pi); fall back to jcode global ws
WS=$(python3 - "$CWD" <<'EOF'
import re, sys, os
d = os.path.abspath(sys.argv[1])
ws = "jcode"
while d and d != os.path.dirname(d):
    gc = os.path.join(d, ".git", "config")
    if os.path.isfile(gc):
        try:
            content = open(gc, encoding="utf-8", errors="ignore").read()
            m = re.search(r'\[remote "origin"\][^\[]*url\s*=\s*.*?(?:/|:)([^/]+?)(?:\.git)?\s*$', content, re.M)
            ws = re.sub(r"[^a-z0-9_-]", "-", m.group(1).strip().lower()) if m else re.sub(r"\s+", "-", os.path.basename(d).lower())
        except OSError:
            pass
        break
    d = os.path.dirname(d)
print(ws)
EOF
) || WS="jcode"

post() {
  curl -s -m 10 -X POST "$HONCHO/workspaces/$WS/sessions" \
    -H 'Content-Type: application/json' \
    -d "{\"id\":\"jcode-$SID\",\"peers\":{\"dsidlo\":{},\"agent-jcode\":{}}}" >/dev/null 2>&1
  # second arg optional: markers without extra payloads skip the message POST
  [ -n "${2:-}" ] || return 0
  curl -s -m 10 -X POST "$HONCHO/workspaces/$WS/sessions/jcode-$SID/messages" \
    -H 'Content-Type: application/json' \
    -d "$2" >/dev/null 2>&1
}

case "$EVENT" in
  session_start)
    post
    curl -s -m 10 -X POST "$HONCHO/workspaces/$WS/sessions/jcode-$SID/messages" \
      -H 'Content-Type: application/json' \
      -d '{"messages":[{"content":"session started (jcode)","peer_id":"agent-jcode","metadata":{"type":"thought","event":"session_start","source":"jcode-hooks"}}]}' \
      >/dev/null 2>&1
    ;;
  session_end)
    curl -s -m 10 -X POST "$HONCHO/workspaces/$WS/sessions/jcode-$SID/messages" \
      -H 'Content-Type: application/json' \
      -d '{"messages":[{"content":"session ended (jcode)","peer_id":"agent-jcode","metadata":{"type":"thought","event":"session_end","source":"jcode-hooks"}}]}' \
      >/dev/null 2>&1
    ;;
  turn_end)
    # cheap metadata bookmark only (content comes from the journal tailer)
    curl -s -m 10 -X POST "$HONCHO/workspaces/$WS/sessions/jcode-$SID/messages" \
      -H 'Content-Type: application/json' \
      -d "{\"messages\":[{\"content\":\"turn bookmark: model=$MODEL status=$STATUS duration=${DURATION}ms\",\"peer_id\":\"agent-jcode\",\"metadata\":{\"type\":\"thought\",\"event\":\"turn_end\",\"source\":\"jcode-hooks\"}}]}" \
      >/dev/null 2>&1
    ;;
esac
exit 0