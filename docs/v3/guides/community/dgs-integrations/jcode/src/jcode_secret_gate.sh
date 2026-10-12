#!/usr/bin/env bash
# jcode-secret-gate: pre_tool hook that blocks tool calls which would expose
# credential material to the model context.
#
# Strategy (given pre_tool sees only INPUT):
# - read tool: block when the target path matches the credential denylist
# - bash tool: block when the command would cat/grep/print known credential
#   files or contains secret-shaped assignments that would echo into context
# - Honcho DB access via psql: allow (local DB) but block LIKE-scan patterns
#   containing the known password token (learned from the 2026-09-28 leak)
#
# Exit 0 = allow, exit 2 = block (stderr goes to the model), else fail-open.

set -u
input=$(cat)
TOOL="${JCODE_HOOK_TOOL_NAME:-}"
DENY_MSG="blocked: this path/operation matches the credential denylist. Credential files must not be read into model context. If you need a specific non-secret value, ask the user to paste it or use a scoped command that does not print secrets."

# Credential-path denylist (same tokens as the Honcho scrubbing policy)
is_denied_path() {
  p=$(echo "$1" | tr '[:upper:]' '[:lower:]')
  case "$p" in
    *auth.json*|*.env|*credential*|*.pem|*id_rsa*|*id_ed25519*|*.p12|*secrets/*|.env*|*".key") return 0 ;;
    *) return 1 ;;
  esac
}

case "$TOOL" in
  read)
    # extract file_path from input JSON
    path=$(printf '%s' "$input" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    v=d.get('file_path') or d.get('path') or d.get('target') or ''
    print(str(v))
except Exception:
    print('')")
    if [ -n "$path" ] && is_denied_path "$path"; then
      echo "$DENY_MSG (path: $path)" >&2
      exit 2
    fi
    ;;

  bash)
    # Block shell commands that READ credential files (cat/head/tail/less/echo
    # of a credential path). Mere mentions in writes/docs are allowed.
    if printf '%s' "$input" | grep -qiE '(cat|head|tail|less|more|echo|grep|rg|sed|awk|strings) [^|;]*\.(env|pem|p12|key)([" ]|$)|/(\.env|auth\.json|id_rsa|id_ed25519)[^a-z]'; then
      echo "$DENY_MSG" >&2
      exit 2
    fi
    # PGPASSWORD / KEY=value assignments echo secrets into tool output
    if printf '%s' "$input" | grep -qE 'PGPASSWORD=|OPENAI_API_KEY=sk-|GEMINI_API_KEY=AIza|HF_TOKEN=hf_|XAI_API_KEY=xai-v'; then
      echo "$DENY_MSG (secret assignment in command)" >&2
      exit 2
    fi
    # printing the known DB password pattern (leak-scan lesson)
    if printf '%s' "$input" | grep -qE "dsidlo:rexrabbit|LIKE '%.*rexrabbit"; then
      echo "blocked: this command references a credential value; use a masked pattern instead" >&2
      exit 2
    fi
    ;;

  grep)
    # grep tool with a path that is a credential file
    path=$(printf '%s' "$input" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    print(str(d.get('path') or ''))
except Exception:
    print('')
")
    if [ -n "$path" ] && is_denied_path "$path"; then
      echo "$DENY_MSG (path: $path)" >&2
      exit 2
    fi
    ;;
esac

exit 0